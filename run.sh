#!/usr/bin/env bash
# Launch r2web in the foreground: this terminal window IS the server.
# Close the window or press Ctrl+C to stop the server.
set -uo pipefail
# resolve symlinks (r2web is a symlink in ~/.local/bin) to find the real script dir
SELF="$(readlink -f "$0")"
cd "$(dirname "$SELF")"

HOST="${R2WEB_HOST:-127.0.0.1}"
PORT="${R2WEB_PORT:-8765}"
URL="http://${HOST}:${PORT}/"

green(){ printf '\033[1;32m%s\033[0m\n' "$1"; }
dim(){ printf '\033[2m%s\033[0m\n' "$1"; }

# Always start fresh: kill any previous r2web server holding the port so we never
# reconnect to a stale/old instance.
if command -v fuser >/dev/null; then fuser -k "${PORT}/tcp" >/dev/null 2>&1 || true; fi
pkill -f "$(pwd)/server.py" >/dev/null 2>&1 || true
sleep 0.4

green "Starting r2web  ->  ${URL}"
dim   "This window IS the server. Close it or press Ctrl+C to stop."
echo

# open the browser once the server is up
( for _ in $(seq 1 40); do curl -s -m 1 "${URL}api/health" >/dev/null 2>&1 && break; sleep 0.15; done
  xdg-open "$URL" >/dev/null 2>&1 || true ) &

# replace this shell with the server so Ctrl+C / window-close kills it directly
exec python3 server.py
