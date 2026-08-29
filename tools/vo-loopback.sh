#!/usr/bin/env bash
# vo-loopback.sh - two local instances of the game, for netplay testing.
#
#   tools/vo-loopback.sh build     compile net/dpctrl.dll (actions chain:
#                                  build install status)
#   tools/vo-loopback.sh install   put the fresh DLL into both installs
#   tools/vo-loopback.sh a         launch A, which hosts on 127.0.0.1
#   tools/vo-loopback.sh b         launch B, which joins 127.0.0.1
#   tools/vo-loopback.sh restore   put the stock dpctrl.dll back in both
#   tools/vo-loopback.sh status    what is installed, the runner, the port
#
# Two separate installs with two separate prefixes are required. Both
# instances write v_on.ini and save state, and sharing either produces
# failures that look like netcode bugs and are not.
#
# What this proves: the ABI, the ring handling, and the copy-out. What it
# does not: at 0 ms round trip the delay negotiation computes 1 every time,
# so the whole delay path runs at its minimum and is never exercised. Shape
# the loopback with tc to test that, and read net/README.md first.
#
# Configure with environment variables, or a ~/.vo-test file that sets
# them - it is sourced if present:
#
#   VO_GAME_A  VO_GAME_B    the two game folders          (required)
#   VO_PFX_A   VO_PFX_B     their Wine prefixes           (required)
#   VO_UMU                  umu-run, if not on PATH
#   VO_PROTON               Proton directory; a Proton-CachyOS build is
#                           looked for when this is unset
#   VO_WINE                 plain wine instead of umu, for a normal prefix

set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=$(dirname "$HERE")

# shellcheck source=/dev/null
[ -f ~/.vo-test ] && . ~/.vo-test

GAME_A=${VO_GAME_A:-}
GAME_B=${VO_GAME_B:-}
PFX_A=${VO_PFX_A:-}
PFX_B=${VO_PFX_B:-}
UMU=${VO_UMU:-$(command -v umu-run || true)}
PROTON=${VO_PROTON:-}
WINE=${VO_WINE:-}

die() { echo "$*" >&2; exit 1; }

need_config() {
    local missing=
    [ -n "$GAME_A" ] || missing="$missing VO_GAME_A"
    [ -n "$GAME_B" ] || missing="$missing VO_GAME_B"
    [ -n "$PFX_A" ]  || missing="$missing VO_PFX_A"
    [ -n "$PFX_B" ]  || missing="$missing VO_PFX_B"
    [ -z "$missing" ] || die "set these first, in the environment or
~/.vo-test:$missing

    # ~/.vo-test
    VO_GAME_A=/games/VIRTUAL-ON
    VO_GAME_B='/games/VIRTUAL-ON P2'
    VO_PFX_A=\$HOME/prefixes/virtual-on
    VO_PFX_B=\$HOME/prefixes/virtual-on-p2"
}

game_dir() { [ "$1" = a ] && echo "$GAME_A" || echo "$GAME_B"; }
pfx_dir()  { [ "$1" = a ] && echo "$PFX_A"  || echo "$PFX_B"; }

find_proton() {
    [ -n "$PROTON" ] && { echo "$PROTON"; return; }
    # Proton-CachyOS is not a umu alias, so PROTONPATH needs a directory.
    # Globbed into an array rather than parsed out of ls: these directories
    # have spaces in their names more often than not.
    local dirs=() d newest=
    for d in \
        ~/.steam/root/compatibilitytools.d/*[Cc]achy* \
        ~/.local/share/Steam/compatibilitytools.d/*[Cc]achy* \
        ~/.local/share/faugus-launcher/*[Cc]achy* \
        /usr/share/steam/compatibilitytools.d/*[Cc]achy*
    do
        [ -d "$d" ] && dirs+=("$d")
    done
    for d in "${dirs[@]+"${dirs[@]}"}"; do
        [ -z "$newest" ] || [ "$d" -nt "$newest" ] && newest=$d
    done
    [ -n "$newest" ] || die "no Proton found; set VO_PROTON, or VO_WINE to use wine"
    echo "$newest"
}

build() {
    cd "$REPO/net"
    i686-w64-mingw32-gcc -O2 -s -shared -o dpctrl.dll dpctrl.c dpctrl.def \
        -lws2_32 -lwinmm
    echo "--- linked against ---"
    i686-w64-mingw32-objdump -p dpctrl.dll | awk '/DLL Name/ {print "  " $3}'
}

install_dll() {
    need_config
    [ -f "$REPO/net/dpctrl.dll" ] || die "build it first"
    local i d
    for i in a b; do
        d=$(game_dir $i)
        [ -d "$d" ] || die "missing: $d"
        # snapshot the stock DLL once, the way the patcher does
        [ -f "$d/dpctrl.dll.stock" ] || cp -a "$d/dpctrl.dll" "$d/dpctrl.dll.stock"
        cp -f "$REPO/net/dpctrl.dll" "$d/dpctrl.dll"
        echo "installed -> $d"
    done
}

restore() {
    need_config
    local i d
    for i in a b; do
        d=$(game_dir $i)
        if [ -f "$d/dpctrl.dll.stock" ]; then
            cp -f "$d/dpctrl.dll.stock" "$d/dpctrl.dll"
            echo "restored  -> $d"
        else
            echo "no stock backup in $d" >&2
        fi
    done
}

status() {
    need_config
    local i d
    for i in a b; do
        d=$(game_dir $i)
        printf '%-32s ' "$i: $(basename "$d")"
        if [ ! -f "$d/dpctrl.dll" ]; then
            echo "no dpctrl.dll"
        elif cmp -s "$d/dpctrl.dll" "$REPO/net/dpctrl.dll" 2>/dev/null; then
            echo "UDP build"
        elif cmp -s "$d/dpctrl.dll" "$d/dpctrl.dll.stock" 2>/dev/null; then
            echo "stock DirectPlay"
        else
            echo "unknown ($(md5sum < "$d/dpctrl.dll" | cut -c1-8))"
        fi
    done
    echo "--- runner ---"
    if [ -n "$WINE" ]; then
        echo "  wine: $WINE"
    else
        echo "  umu:    ${UMU:-not found}"
        echo "  proton: $(find_proton 2>&1)"
    fi
    echo "--- port 47624 ---"
    ss -ulnp 2>/dev/null | grep 47624 || echo "  free"
}

run() {
    need_config
    local i=$1 d p
    d=$(game_dir "$i"); p=$(pfx_dir "$i")
    [ -d "$d" ] || die "missing: $d"
    cd "$d"
    if [ -n "$WINE" ]; then
        WINEPREFIX="$p" "$WINE" "$d/v_on.exe" 2>&1 | tee "/tmp/vo-$i.log"
    else
        [ -n "$UMU" ] || die "umu-run not found; set VO_UMU or VO_WINE"
        local proton
        proton=$(find_proton)
        echo "runner: $proton"
        WINEPREFIX="$p" GAMEID=umu-0 PROTONPATH="$proton" \
            "$UMU" "$d/v_on.exe" 2>&1 | tee "/tmp/vo-$i.log"
    fi
}

# Each argument is one action, in order: `build install status` is the
# usual sequence.
[ $# -gt 0 ] || { sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 0; }
for action in "$@"; do
    case "$action" in
        build)   build ;;
        install) install_dll ;;
        restore) restore ;;
        status)  status ;;
        a)       run a ;;
        b)       run b ;;
        *)       die "unknown action: $action" ;;
    esac
done
