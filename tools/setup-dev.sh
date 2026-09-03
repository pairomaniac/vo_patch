#!/bin/sh
# Reports what the development toolchain is missing; installs nothing.
#
#     sh tools/setup-dev.sh
#
# Everything comes from the distribution, no venv: python3-pyflakes
# lints, python3-capstone regenerates UI_REFS in uibuild and drives
# vomap/votrans/hiresport (4.x and 5.x both work), python3-unicorn
# runs the resolution blob in tools/uiemu.py, tkinter is the window
# and the gui check. nasm rebuilds asm/ including asm/ui.asm, mingw
# the netplay DLL, xvfb runs the gui check headlessly. None of
# them is needed to run the patcher itself - the blobs are baked into
# vo_patch.py as text.
set -e
cd "$(dirname "$0")/.."

missing=""
for tool in nasm i686-w64-mingw32-gcc xvfb-run; do
    command -v "$tool" >/dev/null 2>&1 || missing="$missing $tool"
done
for mod in pyflakes capstone unicorn tkinter; do
    python3 -c "import $mod" >/dev/null 2>&1 || missing="$missing $mod"
done

if [ -z "$missing" ]; then
    echo "toolchain complete"
else
    echo "not found:$missing"
    echo "  apt: sudo apt install nasm gcc-mingw-w64-i686 xvfb" \
         "python3-tk python3-pyflakes python3-capstone python3-unicorn"
    echo "  dnf: sudo dnf install nasm mingw32-gcc" \
         "xorg-x11-server-Xvfb python3-tkinter python3-pyflakes" \
         "python3-capstone python3-unicorn"
fi
