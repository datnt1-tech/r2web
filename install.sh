#!/usr/bin/env bash
# r2web installer — symlinks the launcher, installs the desktop entry + icons.
# No root required; everything goes under ~/.local.
set -euo pipefail

DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
BIN="$HOME/.local/bin"
APPS="$HOME/.local/share/applications"
ICONS="$HOME/.local/share/icons"

green(){ printf '\033[1;32m%s\033[0m\n' "$1"; }

# --- dependency check (informational) ---
missing=()
command -v python3 >/dev/null || missing+=("python3")
command -v r2       >/dev/null || missing+=("radare2")
r2 -qc 'pdg?' --   2>/dev/null | grep -qi ghidra || missing+=("r2ghidra (radare2 ghidra plugin)")
if [ "${#missing[@]}" -gt 0 ]; then
  printf '\033[1;33m! missing/uncertain deps:\033[0m %s\n' "${missing[*]}"
  echo "  Arch/CachyOS:  sudo pacman -S radare2 python  &&  r2pm -Uci r2ghidra"
  echo
fi

mkdir -p "$BIN" "$APPS" "$ICONS/hicolor"

# launcher symlink
ln -sf "$DIR/run.sh" "$BIN/r2web"
green "✓ launcher: $BIN/r2web -> $DIR/run.sh"

# icons
install -Dm644 "$DIR/assets/r2web.svg" "$ICONS/r2web.svg"
for s in 32 48 64 128 256; do
  install -Dm644 "$DIR/assets/icons/r2web-$s.png" "$ICONS/hicolor/${s}x${s}/apps/r2web.png"
done
gtk-update-icon-cache -q "$ICONS/hicolor" 2>/dev/null || true
green "✓ icons installed"

# desktop entry (expand the launcher path)
sed "s#__R2WEB_DIR__#$DIR#g" "$DIR/assets/r2web.desktop" > "$APPS/r2web.desktop"
update-desktop-database -q "$APPS" 2>/dev/null || true
green "✓ desktop entry: $APPS/r2web.desktop"

echo
green "Done. Run 'r2web' (ensure ~/.local/bin is on PATH) or launch \"r2web decompiler\" from your app menu."
