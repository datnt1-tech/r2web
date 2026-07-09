#!/usr/bin/env bash
# r2web dependency installer: radare2 + r2ghidra (the `pdg` Ghidra decompiler).
#
#   Arch / CachyOS / Manjaro  -> pacman + r2pm
#   Debian / Ubuntu           -> apt (radare2) + r2pm
#   Fedora                    -> dnf  (radare2) + r2pm
#   anything else / fallback  -> official radare2 git installer + r2pm
#
# radare2 itself needs the system package manager (root). r2ghidra is then
# built/installed per-user with r2pm — no root required for that step.
set -uo pipefail

green(){ printf '\033[1;32m%s\033[0m\n' "$1"; }
yellow(){ printf '\033[1;33m%s\033[0m\n' "$1"; }
red(){ printf '\033[1;31m%s\033[0m\n' "$1"; }
have(){ command -v "$1" >/dev/null 2>&1; }

SUDO=""
[ "$(id -u)" -ne 0 ] && have sudo && SUDO="sudo"

# --------------------------------------------------------------------------- #
# 1. radare2
# --------------------------------------------------------------------------- #
install_radare2(){
  if have r2; then
    green "✓ radare2 already installed: $(r2 -qv 2>/dev/null | head -1)"
    return 0
  fi
  yellow "Installing radare2…"
  if   have pacman; then $SUDO pacman -S --needed --noconfirm radare2
  elif have apt-get; then $SUDO apt-get update && $SUDO apt-get install -y radare2
  elif have dnf;    then $SUDO dnf install -y radare2
  elif have zypper; then $SUDO zypper install -y radare2
  elif have brew;   then brew install radare2
  else
    yellow "No known package manager — using the official radare2 git installer."
    tmp="$(mktemp -d)"
    git clone --depth 1 https://github.com/radareorg/radare2 "$tmp/radare2"
    "$tmp/radare2/sys/install.sh"
  fi
  have r2 && green "✓ radare2 installed: $(r2 -qv 2>/dev/null | head -1)"
}

# --------------------------------------------------------------------------- #
# 2. r2ghidra (provides `pdg`)
# --------------------------------------------------------------------------- #
install_r2ghidra(){
  if r2 -qc 'pdg?' -- 2>/dev/null | grep -qi ghidra; then
    green "✓ r2ghidra already installed (pdg available)"
    return 0
  fi
  yellow "Installing r2ghidra via r2pm (this compiles Ghidra's decompiler — can take a few minutes)…"
  # try the distro package first on Arch (much faster than building)
  if have pacman && $SUDO pacman -S --needed --noconfirm r2ghidra 2>/dev/null; then
    green "✓ r2ghidra installed from pacman"
  else
    r2pm -U               # refresh the r2pm package index
    r2pm -ci r2ghidra     # clean install
  fi
  if r2 -qc 'pdg?' -- 2>/dev/null | grep -qi ghidra; then
    green "✓ r2ghidra installed (pdg available)"
  else
    red "✗ r2ghidra install could not be verified — 'pdg' not found."
    red "  Try manually:  r2pm -U && r2pm -ci r2ghidra"
    return 1
  fi
}

main(){
  green "== r2web dependency installer =="
  have git || { red "git is required for the fallback/r2pm path. Install git first."; }
  install_radare2 || { red "radare2 install failed — aborting."; exit 1; }
  install_r2ghidra || exit 1
  echo
  green "All set. radare2 + r2ghidra ready — run ./install.sh then 'r2web'."
}
main "$@"
