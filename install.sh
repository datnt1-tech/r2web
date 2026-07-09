#!/usr/bin/env bash
# r2web installer (Linux + macOS) — symlinks the launcher and installs a
# desktop launcher: a .desktop entry + hicolor icons on Linux, or a proper
# .app bundle with an .icns icon on macOS. No root required.
set -euo pipefail

# portable script-dir resolver (macOS has no `readlink -f`)
resolve(){
  if readlink -f "$1" >/dev/null 2>&1; then readlink -f "$1"; return; fi
  local p="$1"
  while [ -L "$p" ]; do local t; t="$(readlink "$p")"; case "$t" in /*) p="$t";; *) p="$(dirname "$p")/$t";; esac; done
  ( cd "$(dirname "$p")" && printf '%s/%s\n' "$(pwd)" "$(basename "$p")" )
}
DIR="$(cd "$(dirname "$(resolve "$0")")" && pwd)"
BIN="$HOME/.local/bin"
OS="$(uname -s)"

green(){ printf '\033[1;32m%s\033[0m\n' "$1"; }
yellow(){ printf '\033[1;33m%s\033[0m\n' "$1"; }

# --- dependency check (informational) ---
missing=()
command -v python3 >/dev/null || command -v python >/dev/null || missing+=("python3")
command -v r2       >/dev/null || missing+=("radare2")
r2 -qc 'pdg?' --   2>/dev/null | grep -qi ghidra || missing+=("r2ghidra (radare2 ghidra plugin)")
if [ "${#missing[@]}" -gt 0 ]; then
  yellow "! missing/uncertain deps: ${missing[*]}"
  echo   "  Run ./install-deps.sh   (macOS: brew install radare2 && r2pm -Uci r2ghidra)"
  echo
fi

mkdir -p "$BIN"
# launcher symlink (both OSes)
ln -sf "$DIR/run.sh" "$BIN/r2web"
green "✓ launcher: $BIN/r2web -> $DIR/run.sh"

# ===================== macOS: .app bundle + .icns ===========================
install_macos(){
  local APPDIR="$HOME/Applications/r2web.app"
  local C="$APPDIR/Contents"
  rm -rf "$APPDIR"
  mkdir -p "$C/MacOS" "$C/Resources"

  # executable: open a Terminal window running run.sh (so Ctrl+C / close = stop)
  cat > "$C/MacOS/r2web" <<EOF
#!/bin/bash
osascript -e 'tell application "Terminal" to do script "\"$DIR/run.sh\""' \
          -e 'tell application "Terminal" to activate'
EOF
  chmod +x "$C/MacOS/r2web"

  cat > "$C/Info.plist" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>r2web</string>
  <key>CFBundleDisplayName</key><string>r2web decompiler</string>
  <key>CFBundleIdentifier</key><string>dev.r2web.app</string>
  <key>CFBundleVersion</key><string>1.1</string>
  <key>CFBundleShortVersionString</key><string>1.1</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>r2web</string>
  <key>CFBundleIconFile</key><string>r2web</string>
</dict></plist>
EOF

  # build r2web.icns from the source icon (sips + iconutil ship with macOS)
  if command -v iconutil >/dev/null && command -v sips >/dev/null; then
    local iset; iset="$(mktemp -d)/r2web.iconset"; mkdir -p "$iset"
    local src="$DIR/assets/icons/r2web-256.png"
    for sz in 16 32 64 128 256 512; do
      sips -z "$sz" "$sz" "$src" --out "$iset/icon_${sz}x${sz}.png" >/dev/null 2>&1 || true
    done
    # @2x variants (retina) reuse the next size up
    cp "$iset/icon_32x32.png"   "$iset/icon_16x16@2x.png"   2>/dev/null || true
    cp "$iset/icon_64x64.png"   "$iset/icon_32x32@2x.png"   2>/dev/null || true
    cp "$iset/icon_256x256.png" "$iset/icon_128x128@2x.png" 2>/dev/null || true
    cp "$iset/icon_512x512.png" "$iset/icon_256x256@2x.png" 2>/dev/null || true
    iconutil -c icns "$iset" -o "$C/Resources/r2web.icns" 2>/dev/null \
      && green "✓ icon: r2web.icns" \
      || cp "$src" "$C/Resources/r2web.png"
  else
    cp "$DIR/assets/icons/r2web-256.png" "$C/Resources/r2web.png"
  fi

  # refresh Launch Services so it shows in Spotlight/Launchpad
  /System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister \
    -f "$APPDIR" >/dev/null 2>&1 || true
  green "✓ app bundle: $APPDIR"
  echo
  green "Done. Launch \"r2web decompiler\" from Spotlight/Launchpad, or run 'r2web' in a terminal."
  green "(ensure ~/.local/bin is on PATH: echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.zshrc)"
}

# ===================== Linux: .desktop + hicolor icons ======================
install_linux(){
  local APPS="$HOME/.local/share/applications"
  local ICONS="$HOME/.local/share/icons"
  mkdir -p "$APPS" "$ICONS/hicolor"

  install -Dm644 "$DIR/assets/r2web.svg" "$ICONS/r2web.svg"
  for s in 32 48 64 128 256; do
    install -Dm644 "$DIR/assets/icons/r2web-$s.png" "$ICONS/hicolor/${s}x${s}/apps/r2web.png"
  done
  gtk-update-icon-cache -q "$ICONS/hicolor" 2>/dev/null || true
  green "✓ icons installed"

  sed "s#__R2WEB_DIR__#$DIR#g" "$DIR/assets/r2web.desktop" > "$APPS/r2web.desktop"
  update-desktop-database -q "$APPS" 2>/dev/null || true
  green "✓ desktop entry: $APPS/r2web.desktop"
  echo
  green "Done. Run 'r2web' (ensure ~/.local/bin is on PATH) or launch \"r2web decompiler\" from your app menu."
}

case "$OS" in
  Darwin) install_macos ;;
  Linux)  install_linux ;;
  *)      yellow "Unknown OS '$OS' — launcher symlinked; skipping desktop integration." ;;
esac
