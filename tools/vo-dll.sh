#!/usr/bin/env bash
# vo-dll.sh - build net/dpctrl.dll and install it into one game folder.
#
#   tools/vo-dll.sh build          compile net/dpctrl.dll
#   tools/vo-dll.sh install [DIR]  put the fresh DLL into DIR, keeping a .stock backup
#   tools/vo-dll.sh restore [DIR]  put the stock dpctrl.dll back
#   tools/vo-dll.sh status  [DIR]  what is installed
#
# DIR defaults to VO_GAME, from the environment or ~/.vo-test, which is
# sourced if present. For two local instances use vo-loopback.sh instead.

set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=$(dirname "$HERE")
DLL=$REPO/net/dpctrl.dll

# shellcheck source=/dev/null
[ -f ~/.vo-test ] && . ~/.vo-test

die() { echo "$*" >&2; exit 1; }

game_dir() {
    local d=${1:-${VO_GAME:-}}
    [ -n "$d" ] || die "give a game folder, or set VO_GAME in the environment or ~/.vo-test"
    [ -f "$d/v_on.exe" ] || die "no v_on.exe in $d"
    echo "$d"
}

build() {
    cd "$REPO/net"
    i686-w64-mingw32-gcc -O2 -s -shared -o dpctrl.dll dpctrl.c dpctrl.def \
        -lws2_32 -lwinmm
    echo "--- linked against ---"
    i686-w64-mingw32-objdump -p dpctrl.dll | awk '/DLL Name/ {print "  " $3}'
}

install_dll() {
    local d=$1
    [ -f "$DLL" ] || die "build it first"
    [ "$DLL" -nt "$REPO/net/dpctrl.c" ] || die "dpctrl.dll is older than dpctrl.c; build first"
    # snapshot the stock DLL once, the way the patcher does
    [ -f "$d/dpctrl.dll.stock" ] || cp -a "$d/dpctrl.dll" "$d/dpctrl.dll.stock"
    cp -f "$DLL" "$d/dpctrl.dll"
    echo "installed -> $d"
}

restore() {
    local d=$1
    [ -f "$d/dpctrl.dll.stock" ] || die "no stock backup in $d"
    cp -f "$d/dpctrl.dll.stock" "$d/dpctrl.dll"
    echo "restored -> $d"
}

status() {
    local d=$1
    printf '%s: ' "$d"
    if [ ! -f "$d/dpctrl.dll" ]; then
        echo "no dpctrl.dll"
    elif cmp -s "$d/dpctrl.dll" "$DLL" 2>/dev/null; then
        echo "UDP build"
    elif cmp -s "$d/dpctrl.dll" "$d/dpctrl.dll.stock" 2>/dev/null; then
        echo "stock DirectPlay"
    else
        echo "unknown ($(md5sum < "$d/dpctrl.dll" | cut -c1-8))"
    fi
}

case "${1:-}" in
    build)   build ;;
    install) d=$(game_dir "${2:-}"); install_dll "$d" ;;
    restore) d=$(game_dir "${2:-}"); restore "$d" ;;
    status)  d=$(game_dir "${2:-}"); status "$d" ;;
    *)       sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//' ;;
esac
