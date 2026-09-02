#!/bin/sh
# gdb on the running game. Under Wine v_on.exe is an ordinary Linux
# process with the game image at 0x400000, so host gdb attaches to it by
# pid; no winedbg, prefix or Proton involved. Re-runs itself with sudo when
# ptrace is restricted (kernel.yama.ptrace_scope=1). Every mode detaches
# when done and the game goes on.
#
#     tools/vo-dbg.sh                         interactive gdb, attached
#     tools/vo-dbg.sh pixel [x y [hue]]       who draws viewport pixel x,y
#                                             (default 150 300): each new
#                                             writer, and the polygon
#                                             record when it is renderer
#                                             A's flush; hue red|green|
#                                             blue reports only writes of
#                                             that colour, any (default)
#                                             each new writer once
#     tools/vo-dbg.sh watch ADDR [SIZE]       writers of a game address
#                                             (1/2/4 bytes; default 4):
#                                             each new writer with its
#                                             value, stops after 30 or 20 s
#     tools/vo-dbg.sh break ADDR [N] [COND]   stop at a game address N
#                                             times (default 3), backtrace,
#                                             registers and the stack each
#                                             time; COND is a gdb condition
#                                             such as '$ebx == 0x64826482'
#     tools/vo-dbg.sh read ADDR [LEN]         hex dump (default 64 bytes)
#     tools/vo-dbg.sh cmd 'gdb command' ...   run gdb commands, print, detach
#
# Interactive gdb gets these commands: fb (surface base, pitch, size),
# pix X Y (the pixel's address and value), game (MODE/SUBMODE and the
# frame counter). ADDR is hex with or without 0x.
set -e
die() { echo "$*" >&2; exit 1; }

pid=$(pgrep -x v_on.exe | head -1 || true)
[ -n "$pid" ] || die "v_on.exe is not running"
command -v gdb >/dev/null || die "gdb not found"
if [ "$(cat /proc/sys/kernel/yama/ptrace_scope 2>/dev/null)" != 0 ] && [ "$(id -u)" != 0 ]; then
    exec sudo -E "$0" "$@"
fi
mode=${1:-gdb}; [ $# -gt 0 ] && shift

py=/tmp/vo-dbg.py
cat > "$py" <<'PY'
import gdb, time
def u32(a): return int(gdb.parse_and_eval('*(unsigned int*)%d' % a)) & 0xffffffff
def u16(a): return int(gdb.parse_and_eval('*(unsigned short*)%d' % a)) & 0xffff
def u8(a): return int(gdb.parse_and_eval('*(unsigned char*)%d' % a)) & 0xff
def pc(): return int(gdb.parse_and_eval('$pc')) & 0xffffffff
def hue(v):
    """the dominant channel of a 16-bit pixel: 'red', 'green', 'blue' or ''"""
    r, g, b = (v >> 10) & 31, (v >> 5) & 31, v & 31
    if r >= 10 and g < 5 and b < 5: return 'red'
    if g >= 10 and r < 5 and b < 5: return 'green'
    if b >= 10 and r < 5 and g < 5: return 'blue'
    return ''
def fb(): return u32(0x6bf5a8), u32(0x6bf5ac), u32(0x6bf5b8), u32(0x6bf5bc)   # surface base (0x6bf5b0 is the row pointer), pitch, w, h
def where():
    gdb.execute('bt 8'); gdb.execute('x/12i $pc-30')
    gdb.execute('info registers eax ebx ecx edx esi edi ebp esp')
    for lbl, a in (('ebp-64', '$ebp-64'), ('esp', '$esp')):
        print('[%s]' % lbl); gdb.execute('x/24wx %s' % a)
    record()
def record(brief=False):
    """Inside renderer A's flush (0x5d1dd5 loop): the polygon's index is
    the edi the loop saved before calling the drawer, right after the
    0x5d1ecf return address on the stack; the record is that entry of
    the flush list, whose base the hires patch moved (read off the
    instruction). Returns (record, vertices); prints it."""
    sp = int(gdb.parse_and_eval('$esp')) & 0xffffffff
    for k in range(96):
        ret = u32(sp + 4 * k)                 # the loop's four drawer calls; the
        if ret in (0x5d1ecf, 0x5d1f2a):       # quad drawers push ecx over edi
            idx = u32(sp + 4 * k + 4)
        elif ret in (0x5d1ee7, 0x5d1f47):
            idx = u32(sp + 4 * k + 8)
        else:
            continue
        if True:
            rec = u32(u32(0x5d1dd8) + 4 * idx)
            v = [(u16(rec + 0x10 + 4 * i), u16(rec + 0x12 + 4 * i)) for i in range(4)]
            v = [(a - 65536 if a > 32767 else a, b - 65536 if b > 32767 else b) for a, b in v]
            attr = u32(rec + 4)
            print('  record %x (index %d) flags %08x attr %x [%08x %08x]  vertices %s'
                  % (rec, idx, u32(rec + 8), attr, u32(attr), u32(attr + 4), ' '.join('(%d,%d)' % p for p in v)))
            if not brief:
                gdb.execute('x/12wx %d' % rec); gdb.execute('x/8wx %d' % attr)
            return rec, v
    print('  no flush frame on the stack'); return None, None

# Wine drives threads with signals; gdb must hand them on without stopping
for sig in ('SIGUSR1', 'SIGUSR2', 'SIGSYS', 'SIG32', 'SIG33', 'SIG34', 'SIGSEGV'):
    gdb.execute('handle %s nostop noprint pass' % sig, to_string=True)
last = {}
gdb.events.stop.connect(lambda ev: last.__setitem__('ev', ev))
def go(bp):
    """continue; True when the stop is breakpoint/watchpoint bp; Ctrl-C
    raises KeyboardInterrupt"""
    last['ev'] = None
    gdb.execute('continue', to_string=True)
    ev = last.get('ev')
    if isinstance(ev, gdb.SignalEvent) and ev.stop_signal == 'SIGINT':
        raise KeyboardInterrupt
    return isinstance(ev, gdb.BreakpointEvent) and any(b.number == bp.number for b in ev.breakpoints)
def silent_last():
    bp = gdb.breakpoints()[-1]; bp.silent = True; return bp

def pixel(x, y, want):
    """Writers of viewport pixel (x, y). Every new writer is named; a
    write of the wanted hue ('any': every new writer) is reported with
    its polygon record when the writer is renderer A's flush."""
    b, p, w, h = fb()
    if not b: raise SystemExit('surface not locked at this instant; run again')
    ad = b + y * p + 2 * x
    print('surface %x pitch %d size %dx%d; pixel (%d,%d) at %x holds %04x' % (b, p, w, h, x, y, ad, u16(ad)))
    print('watching; play until it shows there (Ctrl-C ends)')
    gdb.execute('watch -l *(unsigned short*)%d' % ad); bp = silent_last()
    seen, hits, t0 = {}, set(), time.time()
    try:
        while time.time() - t0 < 120 and len(hits) < 8:
            if not go(bp): continue
            v, at = u16(ad), pc()
            new = at not in seen
            if new:
                seen[at] = v; print('writer %x (first value %04x)' % (at, v))
            if (want == 'any' and new) or (want != 'any' and hue(v) == want):
                print('%s %04x from %x:' % (hue(v) or 'value', v, at))
                rec, verts = record(brief=True)
                hits.add(rec or at)
    except KeyboardInterrupt:
        print('interrupted')
    print('done; writers: ' + ' '.join('%x' % k for k in seen))

def watch(addr, size):
    t = {1: 'char', 2: 'short', 4: 'int'}[size]
    print('watching %d bytes at %x (now %x)' % (size, addr, int(gdb.parse_and_eval('*(unsigned %s*)%d' % (t, addr))) & 0xffffffff))
    gdb.execute('watch -l *(unsigned %s*)%d' % (t, addr)); bp = silent_last()
    seen, t0 = {}, time.time()
    try:
        while len(seen) < 30 and time.time() - t0 < 20:
            if not go(bp): continue
            v, at = int(gdb.parse_and_eval('*(unsigned %s*)%d' % (t, addr))) & 0xffffffff, pc()
            seen[at] = seen.get(at, 0) + 1
            if seen[at] == 1: print('writer %x wrote %x' % (at, v))
    except KeyboardInterrupt:
        print('interrupted')
    print('writers: ' + ' '.join('%x x%d' % kv for kv in seen.items()))

def brk(addr, n, cond):
    gdb.execute('break *%d%s' % (addr, (' if ' + cond) if cond else '')); bp = silent_last()
    i, t0 = 0, time.time()
    try:
        while i < n and time.time() - t0 < 120:
            if not go(bp): continue
            i += 1; print('--- hit %d at %x ---' % (i, pc())); where()
    except KeyboardInterrupt:
        print('interrupted')

def read(addr, n):
    gdb.execute('x/%dxb %d' % (n, addr))
PY

hexarg() { printf '%d' "0x${1#0x}"; }
case $mode in
    gdb)   exec gdb -q -p "$pid" -x "$py" ;;
    pixel) x=${1:-150}; y=${2:-300}; tail="pixel($x, $y, '${3:-any}')" ;;
    watch) [ -n "$1" ] || die "watch ADDR [SIZE]"; tail="watch($(hexarg "$1"), ${2:-4})" ;;
    break) [ -n "$1" ] || die "break ADDR [N] [COND]"; tail="brk($(hexarg "$1"), ${2:-3}, '${3:-}')" ;;
    read)  [ -n "$1" ] || die "read ADDR [LEN]"; tail="read($(hexarg "$1"), ${2:-64})" ;;
    cmd)   [ -n "$1" ] || die "cmd 'gdb command' ..."
           tail=$(for c in "$@"; do printf "gdb.execute(%s)\n" "'$c'"; done) ;;
    *)     die "unknown mode $mode; see the header" ;;
esac
printf '%s\n' "$tail" "gdb.execute('delete'); gdb.execute('detach')" >> "$py"
exec gdb -q -batch -p "$pid" -x "$py"
