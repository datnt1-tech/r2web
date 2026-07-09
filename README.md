# r2web

A drag-and-drop web UI for decompiling native binaries with **Ghidra + radare2**,
running the decompiler in **parallel across every CPU core**. Drop a `.so` / ELF /
`.o` / `.a` / `.bin` onto the page and get a single, ready-to-read `.c` file.

- **Zero pip dependencies** — pure Python standard library (`http.server`).
- **Parallel decompilation** — analyzes once, then splits the functions across
  N radare2 worker processes (N = your CPU core count) that stream progress live.
- **Projects = folders** — every project is a plain directory under `~/r2web/`.
  Create projects, switch between them, and the output `.c` lands in the selected one.
- **Open in your file manager** — click a result to reveal its folder in Dolphin
  (or your OS default), then open it in whatever IDE you like.
- Dark-green terminal-flavored UI, centered workspace.

📐 **Full architecture write-up + diagram:** [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)

## How it works

```
drop file ──HTTP POST /api/decompile──▶ radare2 analysis (aaa)
                                             │  discover functions (aflj)
                                             ▼
                    split functions ──▶ N × [ r2 -i script  (af; pdg @addr) ]
                                             │  each worker streams markers
                                             ▼
                    concatenate ──▶ one <binary>.c  in ~/r2web/<project>/
```

`pdg` is the Ghidra decompiler exposed inside radare2 by the **r2ghidra** plugin.

## Requirements

| Tool | Purpose | Arch / CachyOS |
|------|---------|----------------|
| `python3` | backend (stdlib only) | `sudo pacman -S python` |
| `radare2` | analysis + orchestration | `sudo pacman -S radare2` |
| `r2ghidra` | the `pdg` Ghidra decompiler | `r2pm -Uci r2ghidra` |

Optional: a file manager (Dolphin/Nautilus/…) for the "open folder" button.

Don't have radare2/r2ghidra yet? Run the bundled installer (Arch/Debian/Fedora/…
auto-detected; radare2 via the system package manager, r2ghidra via `r2pm`):

```bash
./install-deps.sh
```

## Install

```bash
git clone https://github.com/datnt1-tech/r2web.git
cd r2web
./install-deps.sh     # (optional) install radare2 + r2ghidra
./install.sh          # symlinks `r2web`, installs the icon + .desktop entry (no root)
```

Then run from a terminal:

```bash
r2web                 # starts the server and opens the browser at http://127.0.0.1:8765
```

…or launch **“r2web decompiler”** from your application menu. The launcher runs the
server in the foreground of its terminal window — **press Ctrl+C or close the window
to stop it** (it is not a background daemon).

You can also run it directly without installing:

```bash
./run.sh
```

## Usage

1. Pick or create a **project** (the dropdown in the header — projects are folders
   directly under `~/r2web/`, siblings of the default `tmp`).
2. **Drop a binary** anywhere on the page (or click **⬆ Decompile file**).
3. Watch the live progress (functions/sec, threads, elapsed).
4. When done, the new `<binary>.c` appears in the file list. Duplicate names get
   `(1)`, `(2)`, … suffixes. Click it to open its folder in your file manager.

## Configuration

Environment variables (read by `run.sh` / `server.py`):

| Var | Default | Meaning |
|-----|---------|---------|
| `R2WEB_HOST` | `127.0.0.1` | bind address |
| `R2WEB_PORT` | `8765` | port |

Projects root is `~/r2web/`; the default project `tmp` is created on first start.

## Layout

```
server.py            # backend: HTTP API + parallel decompile orchestration
run.sh               # foreground launcher (kills any stale server, opens browser)
static/index.html    # single-file frontend (UI + JS)
install.sh           # installs launcher, icon, desktop entry under ~/.local
assets/              # icon (svg + png) and .desktop template
```

## License

MIT — see [LICENSE](LICENSE).
