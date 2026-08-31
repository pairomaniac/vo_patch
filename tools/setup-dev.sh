#!/bin/sh
# Set up the development tooling in a venv at .venv, once.
#
#     sh tools/setup-dev.sh
#     . .venv/bin/activate          # then python3 tools/check.py ...
#
# Python packages go in the venv: pyflakes (lint) and capstone
# (UI_REFS regeneration in uibuild, and vomap/votrans/hiresport).
# Nothing is vendored into the repository; the venv is ignored by git.
# The system packages are printed, not installed: nasm rebuilds asm/
# including asm/ui.asm, mingw rebuilds the netplay DLL, xvfb runs the
# gui check headlessly.
set -e
cd "$(dirname "$0")/.."

if [ ! -x .venv/bin/python3 ]; then
    python3 -m venv .venv
fi
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet pyflakes capstone

missing=""
for tool in nasm i686-w64-mingw32-gcc xvfb-run; do
    command -v "$tool" >/dev/null 2>&1 || missing="$missing $tool"
done
echo "venv ready: . .venv/bin/activate"
if [ -n "$missing" ]; then
    echo "system tools not found:$missing"
    echo "  dnf: sudo dnf install nasm gcc-mingw64-i686 xorg-x11-server-Xvfb"
    echo "  apt: sudo apt install nasm gcc-mingw-w64-i686 xvfb"
fi
