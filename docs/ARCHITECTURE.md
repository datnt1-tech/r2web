# r2web — Architecture

![r2web architecture](architecture.svg)

> Diagram source: [`architecture.drawio`](architecture.drawio) (editable) ·
> also exported as [PNG](architecture.drawio.png) / [PDF](architecture.pdf).

r2web is a single-machine, drag-and-drop decompiler. You drop a native binary in the
browser; a small Python server drives **radare2 + r2ghidra** to decompile every
function **in parallel across all CPU cores**, and writes the result as one `.c` file
into a per-project folder you can open in your file manager.

The system is four tiers — **Browser → Backend → Decompile pipeline → Storage/OS** —
with a strictly local trust boundary: the server binds `127.0.0.1:8765` and all file
I/O is confined under `~/r2web/`.

---

## 1. Browser (`static/index.html`)

A single self-contained HTML file (dark-green theme, centered layout). No build step,
no framework, no external assets. Four functional areas:

| Area | Role |
|------|------|
| **Drop zone / picker** | Accepts a file dropped anywhere on the window, or via the picker button. |
| **Project dropdown** | Selects the active project = a folder directly under `~/r2web/`. Switching re-lists that folder's `.c` files. |
| **Progress modal** | While a job runs, polls status every 250 ms and shows functions done, funcs/sec, elapsed, thread count. |
| **Result list** | Lists the project's `.c` files. Clicking a row asks the server to reveal that file's folder in the OS file manager. |

The upload is a **raw-body POST** (the file bytes are the request body; the filename
travels in an `X-Filename` header) — no multipart parsing needed.

## 2. Backend (`server.py`)

Pure Python **standard library** — `http.server.ThreadingHTTPServer`, zero pip
dependencies. Two responsibilities:

- **HTTP API router** — one handler dispatching a small REST surface:
  `POST /api/decompile`, `GET /api/status`, `GET /api/result`,
  `GET|POST /api/projects`, `GET /api/tree`, `POST /api/open`.
- **Job manager** — decompile is asynchronous. `POST /api/decompile` creates a job,
  kicks off a background thread, and returns a `job_id` immediately. The frontend then
  polls `/api/status` until `done`. The job store tracks per-job state, functions
  completed, rate, and worker count.

**Security boundary — `safe_path()`**: every path derived from a request (project name,
tree path, open target) is resolved and asserted to stay under `~/r2web/`, so a crafted
name like `../../etc` is rejected. Combined with the `127.0.0.1` bind, the server is not
reachable off-host and cannot touch files outside the projects root.

## 3. Decompile pipeline (`radare2` + `r2ghidra`)

The heart of the throughput story. For each job:

1. **Analyze once** — a single radare2 pass runs `aaa` (deep analysis) and enumerates
   functions with `aflj`, filtering out imports/relocation stubs and zero-size entries.
2. **Split** — the function list is divided into **N chunks, where N = CPU core count**
   (auto-detected; no user knob).
3. **Fan out** — N radare2 worker subprocesses run concurrently. Each worker is fed a
   script that, per function, runs `af` then `pdg @ <addr>` (r2ghidra's Ghidra
   decompiler) and emits a plain-text marker after each function. The server reads each
   worker's stdout live, counting markers to drive the progress bar in real time.
4. **Concatenate** — all workers' output is stitched into **one** `<binary>.c` file with
   a header (arch, function count, analysis level). Duplicate names get `(1)`, `(2)`, …
   suffixes rather than overwriting.

Why split *functions* rather than run one r2 per file: analysis (the expensive,
non-parallel part) happens exactly once; only the per-function `pdg` work — which
dominates wall-clock on large libraries — is parallelized. Measured scaling on
`liblzma.so.5`: ~44 func/s single-threaded → ~113 func/s at 12 workers.

## 4. Storage & OS integration

- **Projects are plain folders.** `~/r2web/` is the root; each project is a sibling
  directory (the default `tmp` is created on first start). Output `.c` files land in the
  selected project. Nothing is hidden in a database — you can `cd` in and use any tool.
- **Open in file manager.** `POST /api/open` reveals a folder using, in order:
  `kstart --application <fm> --url` (requests an XDG activation token so the window
  actually raises under KDE/Wayland's focus-stealing prevention) → DBus
  `org.freedesktop.FileManager1` `ShowFolders`/`ShowItems` → direct spawn → `xdg-open`.
  A session-env repair step reconstructs `DBUS_SESSION_BUS_ADDRESS` / `WAYLAND_DISPLAY`
  when the server is launched from a bare environment.

---

## Request lifecycle (happy path)

1. User drops `libfoo.so` → browser `POST /api/decompile` (raw bytes + `X-Filename`).
2. Server creates a job, spawns the background worker thread, returns `job_id`.
3. Worker: radare2 `aaa` + `aflj` → split functions into N chunks.
4. N radare2 workers run `pdg` per function in parallel, streaming progress markers.
5. Browser polls `GET /api/status` every 250 ms, updating the progress modal.
6. Server concatenates output → `~/r2web/<project>/libfoo.so.c`.
7. Job flips to `done`; browser refreshes the result list.
8. User clicks the `.c` row → `POST /api/open` → the folder opens in Dolphin; open the
   file in whatever IDE you like.

## Design properties

- **Zero external dependencies** (Python stdlib only) — clone, install radare2/r2ghidra,
  run. See [`install-deps.sh`](../install-deps.sh) and [`install.sh`](../install.sh).
- **Parallelism where it pays** — analyze once, parallelize `pdg`; scales with cores.
- **Local-only, sandboxed** — `127.0.0.1` bind + `safe_path()` under `~/r2web/`.
- **Transparent storage** — projects are folders; output is one readable `.c` file.
- **Foreground process model** — `run.sh` runs the server in its terminal window;
  Ctrl+C or closing the window stops it (not a background daemon).
