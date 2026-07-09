#!/usr/bin/env python3
"""
r2web — drag-and-drop decompiler web UI with projects.

Drop a binary (.so/.o/.a/ELF/.bin); radare2 analyzes it once, then N radare2
workers run the Ghidra decompiler (`pdg`) in parallel. Results are written into
a chosen project folder (one project = one directory) and the folder is opened
in the OS file manager (Dolphin / Nautilus / …). Pure Python standard library.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HOST = os.environ.get("R2WEB_HOST", "127.0.0.1")
PORT = int(os.environ.get("R2WEB_PORT", "8765"))
HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "static")
PROJECTS_ROOT = os.path.abspath(
    os.environ.get("R2WEB_PROJECTS", os.path.expanduser("~/r2web"))
)
DEFAULT_PROJECT = os.environ.get("R2WEB_DEFAULT_PROJECT", "tmp")
os.makedirs(PROJECTS_ROOT, exist_ok=True)
os.makedirs(os.path.join(PROJECTS_ROOT, DEFAULT_PROJECT), exist_ok=True)

R2 = shutil.which("r2") or shutil.which("radare2")

JOBS = {}
JOBS_LOCK = threading.Lock()

FUNC_RE = re.compile(r"RTWOWEB_FUNC_(0x[0-9a-fA-F]+)_END")
SAFE_NAME = re.compile(r"[^A-Za-z0-9._@-]+")


def repair_session_env():
    """Best-effort fill of the desktop-session vars a background server may be
    missing, so launching the file manager (kstart/dbus) actually works."""
    xrd = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    os.environ.setdefault("XDG_RUNTIME_DIR", xrd)
    if not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
        bus = os.path.join(xrd, "bus")
        if os.path.exists(bus):
            os.environ["DBUS_SESSION_BUS_ADDRESS"] = "unix:path=" + bus
    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        # common defaults; harmless if wrong (we still have DBus fallbacks)
        for wl in ("wayland-0", "wayland-1"):
            if os.path.exists(os.path.join(xrd, wl)):
                os.environ["WAYLAND_DISPLAY"] = wl
                break
        os.environ.setdefault("DISPLAY", ":0")


repair_session_env()


# --------------------------------------------------------------------------- #
# path safety — everything stays under PROJECTS_ROOT
# --------------------------------------------------------------------------- #
def safe_path(rel):
    rel = (rel or "").lstrip("/")
    full = os.path.abspath(os.path.join(PROJECTS_ROOT, rel))
    if full != PROJECTS_ROOT and not full.startswith(PROJECTS_ROOT + os.sep):
        raise ValueError("path escapes projects root")
    return full


def _fm_desktop_id():
    try:
        out = subprocess.run(["xdg-mime", "query", "default", "inode/directory"],
                             capture_output=True, text=True, timeout=4).stdout.strip()
    except Exception:
        out = ""
    return out[:-8] if out.endswith(".desktop") else out


def open_in_filemanager(path):
    """Open a folder — or reveal+select a file — in the file manager. (ok, detail)."""
    from urllib.parse import quote
    path = os.path.abspath(path)
    if not os.path.exists(path):
        return False, f"path not found: {path}"
    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        msg = "no DISPLAY/WAYLAND_DISPLAY — start r2web from your desktop session."
        print("[open] " + msg)
        return False, msg

    isdir = os.path.isdir(path)
    uri = "file://" + quote(path)
    # what DBus FileManager1 method to use: folders vs. files (reveal & select)
    method = "ShowFolders" if isdir else "ShowItems"

    # ordered list of (name, cmd) attempts that succeed on return code 0
    attempts = []

    # kstart with an activation token → the window actually raises on Wayland.
    # Only meaningful for folders (kstart --url on a file opens it in its app).
    fm_id = _fm_desktop_id() if shutil.which("kstart") else ""
    if isdir and fm_id:
        attempts.append((f"kstart:{fm_id}",
                         ["kstart", "--application", fm_id, "--url", uri]))
    # DBus FileManager1 — folders raise, files get revealed+selected.
    if shutil.which("gdbus"):
        attempts.append((f"gdbus/{method}", [
            "gdbus", "call", "--session", "--dest", "org.freedesktop.FileManager1",
            "--object-path", "/org/freedesktop/FileManager1",
            "--method", f"org.freedesktop.FileManager1.{method}",
            "['%s']" % uri, ""]))
    if shutil.which("dbus-send"):
        attempts.append((f"dbus/{method}", [
            "dbus-send", "--session", "--print-reply",
            "--dest=org.freedesktop.FileManager1", "/org/freedesktop/FileManager1",
            f"org.freedesktop.FileManager1.{method}",
            "array:string:%s" % uri, "string:"]))
    # for a file with no reveal path, fall back to opening its parent folder
    if not isdir and fm_id:
        parent_uri = "file://" + quote(os.path.dirname(path))
        attempts.append((f"kstart:{fm_id}(parent)",
                         ["kstart", "--application", fm_id, "--url", parent_uri]))

    last_err = ""
    for name, cmd in attempts:
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=6)
            if r.returncode == 0:
                print(f"[open] {path} via {name}")
                return True, name
            last_err = (r.stderr or b"").decode(errors="replace").strip() or f"exit {r.returncode}"
            print(f"[open] {name} failed: {last_err}")
        except Exception as e:  # noqa: BLE001
            last_err = f"{name}: {e}"
            print(f"[open] {last_err}")

    # last resort: spawn a file manager / xdg-open on the folder
    target = path if isdir else os.path.dirname(path)
    spawn = []
    for fm in ("dolphin", "nautilus", "nemo", "thunar", "pcmanfm-qt", "pcmanfm"):
        if shutil.which(fm):
            spawn.append([fm, target]); break
    spawn.append(["xdg-open", target])
    for cmd in spawn:
        try:
            p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.PIPE, start_new_session=True)
            try:
                rc = p.wait(timeout=1.2)
                if rc != 0:
                    last_err = (p.stderr.read().decode(errors="replace").strip()
                                if p.stderr else f"exit {rc}")
                    continue
            except subprocess.TimeoutExpired:
                pass
            print(f"[open] {target} with {cmd[0]}")
            return True, cmd[0]
        except FileNotFoundError:
            last_err = f"{cmd[0]} not found"
        except Exception as e:  # noqa: BLE001
            last_err = f"{cmd[0]}: {e}"
    return False, last_err or "no file manager available"


# --------------------------------------------------------------------------- #
# radare2 pipeline
# --------------------------------------------------------------------------- #
def r2_discover(binpath, analysis):
    cmd = [
        R2, "-q", "-e", "scr.color=0", "-e", "bin.cache=true",
        "-c", f"{analysis}; ?e RTWOWEB_INFO; ij; ?e RTWOWEB_FUNCS; aflj",
        binpath,
    ]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=1800).stdout

    info_raw, funcs_raw, section = "", "", None
    for line in out.splitlines():
        if line.startswith("RTWOWEB_INFO"):
            section = "info"; continue
        if line.startswith("RTWOWEB_FUNCS"):
            section = "funcs"; continue
        if section == "info":
            info_raw += line
        elif section == "funcs":
            funcs_raw += line

    info = {}
    try:
        info = json.loads(info_raw)
    except Exception:
        pass
    funcs = []
    try:
        funcs = json.loads(funcs_raw)
    except Exception:
        pass

    clean = []
    for f in funcs:
        name = f.get("name", "")
        size = f.get("size", 0) or 0
        if size <= 0 or name.startswith("sym.imp.") or name.startswith("loc."):
            continue
        clean.append({
            "addr": f.get("addr", 0), "name": name, "size": size,
            "nargs": f.get("nargs", 0), "nbbs": f.get("nbbs", 0),
            "ninstrs": f.get("ninstrs", 0),
        })
    return info, clean


def decompile_worker(job, binpath, chunk, analysis):
    lines = ["e scr.color=0", "e bin.cache=true", analysis]
    for fn in chunk:
        a = hex(fn["addr"])
        lines += [f"af @ {a}", f"?e RTWOWEB_FUNC_{a}_END", f"pdg @ {a}"]
    lines.append("?e RTWOWEB_ENDCHUNK")

    sf = tempfile.NamedTemporaryFile("w", suffix=".r2", delete=False)
    sf.write("\n".join(lines) + "\n"); sf.close()

    proc = subprocess.Popen(
        [R2, "-q", "-e", "scr.interactive=false", "-i", sf.name, binpath],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)

    budget = 30 + 25 * len(chunk)
    watchdog = threading.Timer(budget, proc.kill); watchdog.start()
    cur, buf = None, []

    def flush():
        if cur is None:
            return
        code = "\n".join(buf).strip("\n")
        with JOBS_LOCK:
            job["functions"][cur] = code if code else "// (no output)"
            job["done"] += 1

    try:
        for line in proc.stdout:
            line = line.rstrip("\n")
            m = FUNC_RE.match(line)
            if m:
                flush(); cur = m.group(1); buf = []
            elif line.startswith("RTWOWEB_ENDCHUNK"):
                flush(); cur = None; buf = []
            elif cur is not None:
                buf.append(line)
        flush()
    finally:
        watchdog.cancel()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        try:
            os.unlink(sf.name)
        except Exception:
            pass


def write_outputs(job):
    """Write ALL decompiled functions into a single .c file. Return its rel path."""
    project = job.get("project") or DEFAULT_PROJECT
    proj_dir = safe_path(project)
    os.makedirs(proj_dir, exist_ok=True)

    base = SAFE_NAME.sub("_", os.path.basename(job["filename"])) or "binary"
    # no timestamp: use "<base>.c", and on collision "<base>(1).c", "<base>(2).c", ...
    out_c = os.path.join(proj_dir, f"{base}.c")
    n = 1
    while os.path.exists(out_c):
        out_c = os.path.join(proj_dir, f"{base}({n}).c")
        n += 1

    funcs = []
    for addr_i, meta in job["meta"].items():
        a = hex(addr_i)
        funcs.append({**meta, "addr": a,
                      "code": job["functions"].get(a, "// (not decompiled)")})
    funcs.sort(key=lambda f: int(f["addr"], 16))

    bi = job.get("bininfo") or {}
    with open(out_c, "w") as fh:
        fh.write(f"// {job['filename']} — decompiled by r2web (ghidra / r2 pdg)\n")
        fh.write(f"// {bi.get('arch','?')} {bi.get('bits','')}-bit {bi.get('class','')}"
                 f" · {len(funcs)} functions · analysis={job['analysis']}\n")
        for f in funcs:
            fh.write(f"\n\n// {'='*66}\n"
                     f"// {f['name']}  @ {f['addr']}  ({f['size']} bytes, "
                     f"{f['nargs']} args)\n"
                     f"// {'='*66}\n{f['code']}\n")

    return os.path.relpath(out_c, PROJECTS_ROOT)


def run_job(job_id, binpath, workers, analysis):
    job = JOBS[job_id]
    try:
        job["state"] = "analyzing"
        info, funcs = r2_discover(binpath, analysis)
        core = (info.get("bin") or {})
        job["bininfo"] = {
            "arch": core.get("arch", "?"), "bits": core.get("bits", "?"),
            "class": core.get("class", "?"), "os": core.get("os", "?"),
            "type": core.get("bintype", core.get("type", "?")),
            "stripped": core.get("stripped", False),
        }
        job["meta"] = {f["addr"]: f for f in funcs}
        job["total"] = len(funcs)

        if funcs:
            funcs.sort(key=lambda f: f["size"], reverse=True)
            chunks = [[] for _ in range(max(1, workers))]
            for i, f in enumerate(funcs):
                chunks[i % len(chunks)].append(f)
            chunks = [c for c in chunks if c]
            job["state"] = "decompiling"
            job["decomp_start"] = time.monotonic()
            threads = [threading.Thread(target=decompile_worker,
                                        args=(job, binpath, c, analysis), daemon=True)
                       for c in chunks]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        job["state"] = "writing"
        job["outdir"] = write_outputs(job)
        if job.get("autoopen"):
            ok, detail = open_in_filemanager(safe_path(job["outdir"]))
            job["open_ok"], job["open_detail"] = ok, detail
        job["state"] = "done"
        job["finished"] = time.monotonic()
    except Exception as e:  # noqa: BLE001
        job["state"] = "error"
        job["error"] = f"{type(e).__name__}: {e}"
        job["finished"] = time.monotonic()
    finally:
        try:
            os.unlink(binpath)
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path, ctype):
        try:
            with open(path, "rb") as fh:
                body = fh.read()
        except OSError:
            return self._send(404, {"error": "not found"})
        self._send(200, body, ctype)

    def _json_body(self):
        n = int(self.headers.get("Content-Length", "0"))
        if n <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode() or "{}")
        except Exception:
            return {}

    # ---- projects / files ----
    def list_projects(self):
        out = []
        for name in sorted(os.listdir(PROJECTS_ROOT)):
            p = os.path.join(PROJECTS_ROOT, name)
            if os.path.isdir(p):
                try:
                    n = sum(len(fs) for _, _, fs in os.walk(p))
                except Exception:
                    n = 0
                out.append({"name": name, "nfiles": n, "mtime": os.path.getmtime(p)})
        out.sort(key=lambda x: x["mtime"], reverse=True)
        return out

    def list_tree(self, rel):
        full = safe_path(rel)
        entries = []
        if os.path.isdir(full):
            for name in sorted(os.listdir(full)):
                fp = os.path.join(full, name)
                isdir = os.path.isdir(fp)
                entries.append({
                    "name": name, "type": "dir" if isdir else "file",
                    "rel": os.path.relpath(fp, PROJECTS_ROOT),
                    "size": 0 if isdir else os.path.getsize(fp),
                })
        entries.sort(key=lambda e: (e["type"] != "dir", e["name"].lower()))
        return {"rel": rel, "entries": entries}

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        p = u.path

        if p in ("/", "/index.html"):
            return self._file(os.path.join(STATIC, "index.html"), "text/html; charset=utf-8")
        if p == "/api/health":
            return self._send(200, {"ok": bool(R2), "r2": R2, "cpus": os.cpu_count(),
                                    "root": PROJECTS_ROOT})
        if p == "/api/projects":
            return self._send(200, {"root": PROJECTS_ROOT, "projects": self.list_projects()})
        if p == "/api/tree":
            try:
                return self._send(200, self.list_tree(q.get("path", [""])[0]))
            except ValueError as e:
                return self._send(400, {"error": str(e)})
        if p == "/api/file":
            try:
                full = safe_path(q.get("path", [""])[0])
            except ValueError as e:
                return self._send(400, {"error": str(e)})
            if not os.path.isfile(full):
                return self._send(404, {"error": "not a file"})
            if os.path.getsize(full) > 4 * 1024 * 1024:
                return self._send(200, {"path": q.get("path", [""])[0],
                                        "text": "// file too large to preview (>4MB)"})
            try:
                with open(full, "r", errors="replace") as fh:
                    text = fh.read()
            except Exception as e:
                return self._send(400, {"error": str(e)})
            return self._send(200, {"path": q.get("path", [""])[0], "text": text})
        if p == "/api/status":
            job = JOBS.get(q.get("id", [""])[0])
            if not job:
                return self._send(404, {"error": "no such job"})
            ds = job.get("decomp_start")
            drate = 0.0
            if ds:
                dt = (job.get("finished") or time.monotonic()) - ds
                if dt > 0:
                    drate = job["done"] / dt
            elapsed = (job.get("finished") or time.monotonic()) - job["started"]
            return self._send(200, {
                "state": job["state"], "total": job["total"], "done": job["done"],
                "elapsed": round(elapsed, 2), "rate": round(drate, 1),
                "workers": job["workers"], "analysis": job["analysis"],
                "filename": job["filename"], "bininfo": job.get("bininfo"),
                "outdir": job.get("outdir"), "error": job.get("error"),
            })
        if p == "/api/result":
            job = JOBS.get(q.get("id", [""])[0])
            if not job:
                return self._send(404, {"error": "no such job"})
            funcs = []
            for addr_i, meta in job["meta"].items():
                a = hex(addr_i)
                funcs.append({"addr": a, "name": meta["name"], "size": meta["size"],
                              "nargs": meta["nargs"], "nbbs": meta["nbbs"],
                              "ninstrs": meta["ninstrs"],
                              "code": job["functions"].get(a, "// (not decompiled)")})
            funcs.sort(key=lambda f: int(f["addr"], 16))
            return self._send(200, {"filename": job["filename"], "bininfo": job.get("bininfo"),
                                    "outdir": job.get("outdir"), "functions": funcs,
                                    "open_ok": job.get("open_ok"), "open_detail": job.get("open_detail")})

        return self._file(os.path.join(STATIC, p.lstrip("/")), "application/octet-stream")

    def do_POST(self):
        u = urlparse(self.path)
        p = u.path

        if p == "/api/projects":
            b = self._json_body()
            name = SAFE_NAME.sub("-", (b.get("name") or "").strip()).strip("-")
            if not name:
                return self._send(400, {"error": "empty name"})
            try:
                os.makedirs(safe_path(name), exist_ok=True)
            except ValueError as e:
                return self._send(400, {"error": str(e)})
            return self._send(200, {"ok": True, "projects": self.list_projects()})

        if p == "/api/mkdir":
            b = self._json_body()
            rel = (b.get("path") or "").strip()
            name = SAFE_NAME.sub("-", (b.get("name") or "").strip()).strip("-")
            if not name:
                return self._send(400, {"error": "empty folder name"})
            try:
                os.makedirs(os.path.join(safe_path(rel), name), exist_ok=True)
            except ValueError as e:
                return self._send(400, {"error": str(e)})
            return self._send(200, {"ok": True})

        if p == "/api/open":
            b = self._json_body()
            try:
                full = safe_path(b.get("path", ""))
            except ValueError as e:
                return self._send(400, {"error": str(e)})
            if not os.path.exists(full):
                return self._send(404, {"error": "path not found"})
            ok, detail = open_in_filemanager(full)
            return self._send(200, {"ok": ok, "detail": detail})

        if p == "/api/decompile":
            if not R2:
                return self._send(500, {"error": "radare2 not found in PATH"})
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0:
                return self._send(400, {"error": "empty upload"})
            data, remaining = b"", length
            while remaining > 0:
                chunk = self.rfile.read(min(1 << 20, remaining))
                if not chunk:
                    break
                data += chunk
                remaining -= len(chunk)

            filename = self.headers.get("X-Filename", "binary")
            try:
                workers = int(self.headers.get("X-Workers", "0"))
            except ValueError:
                workers = 0
            workers = max(1, min(workers or (os.cpu_count() or 4), 64))
            analysis = "aaa" if self.headers.get("X-Analysis", "fast") == "deep" else "aa"
            project = SAFE_NAME.sub("-", self.headers.get("X-Project", DEFAULT_PROJECT).strip()).strip("-") or DEFAULT_PROJECT
            autoopen = self.headers.get("X-AutoOpen", "1") != "0"

            fd, binpath = tempfile.mkstemp(prefix="r2web_", suffix="_" + os.path.basename(filename))
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)

            job_id = uuid.uuid4().hex
            with JOBS_LOCK:
                JOBS[job_id] = {
                    "state": "queued", "total": 0, "done": 0, "functions": {}, "meta": {},
                    "started": time.monotonic(), "workers": workers, "analysis": analysis,
                    "filename": filename, "project": project, "autoopen": autoopen,
                }
            threading.Thread(target=run_job, args=(job_id, binpath, workers, analysis),
                             daemon=True).start()
            return self._send(200, {"job_id": job_id, "workers": workers,
                                    "analysis": analysis, "project": project})

        return self._send(404, {"error": "not found"})


def main():
    if not R2:
        print("WARNING: radare2 (r2) not found in PATH — decompilation will fail.")
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"r2web on http://{HOST}:{PORT}  (r2={R2}, cpus={os.cpu_count()}, projects={PROJECTS_ROOT})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
