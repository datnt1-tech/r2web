#!/usr/bin/env bash
# Launch r2web in the foreground: this terminal window IS the server.
# Close the window or press Ctrl+C to stop the server. Works on Linux and macOS.
set -uo pipefail

# resolve symlinks (r2web is a symlink in ~/.local/bin) to find the real script dir.
# macOS has no `readlink -f`; fall back to a portable resolver.
resolve(){
  if readlink -f "$1" >/dev/null 2>&1; then readlink -f "$1"; return; fi
  local p="$1"
  while [ -L "$p" ]; do local t; t="$(readlink "$p")"; case "$t" in /*) p="$t";; *) p="$(dirname "$p")/$t";; esac; done
  ( cd "$(dirname "$p")" && printf '%s/%s\n' "$(pwd)" "$(basename "$p")" )
}
SELF="$(resolve "$0")"
cd "$(dirname "$SELF")"

HOST="${R2WEB_HOST:-127.0.0.1}"
PORT="${R2WEB_PORT:-8765}"
URL="http://${HOST}:${PORT}/"

# pick a python + a browser-opener for this OS
PY="$(command -v python3 || command -v python)"
[ -z "$PY" ] && { echo "python3 not found — install Python 3."; exit 1; }
if command -v xdg-open >/dev/null; then OPEN=xdg-open      # Linux
elif command -v open >/dev/null;   then OPEN=open          # macOS
else OPEN=""; fi

green(){ printf '\033[1;32m%s\033[0m\n' "$1"; }
dim(){ printf '\033[2m%s\033[0m\n' "$1"; }

# Always start fresh: kill any previous r2web server holding the port so we never
# reconnect to a stale/old instance. `fuser` (Linux) or `lsof` (macOS/BSD).
if command -v fuser >/dev/null; then
  fuser -k "${PORT}/tcp" >/dev/null 2>&1 || true
elif command -v lsof >/dev/null; then
  pids="$(lsof -ti "tcp:${PORT}" 2>/dev/null || true)"
  [ -n "$pids" ] && kill $pids 2>/dev/null || true
fi
pkill -f "$(pwd)/server.py" >/dev/null 2>&1 || true
sleep 0.4

green "Starting r2web  ->  ${URL}"
dim   "This window IS the server. Close it or press Ctrl+C to stop."
echo

# open the browser once the server is up
if [ -n "$OPEN" ]; then
  ( for _ in $(seq 1 40); do curl -s -m 1 "${URL}api/health" >/dev/null 2>&1 && break; sleep 0.15; done
    "$OPEN" "$URL" >/dev/null 2>&1 || true ) &
fi

# replace this shell with the server so Ctrl+C / window-close kills it directly
exec "$PY" server.py
