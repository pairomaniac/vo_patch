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
#     tools/vo-dbg.sh frame x y [N]           every write to viewport pixel
#                                             x,y in order with its polygon
#                                             record - the paint order at
#                                             that spot - N writes (default
#                                             40), frame breaks marked
#     tools/vo-dbg.sh wedge [x y]             log every write to viewport
#                                             pixel x,y (default the middle
#                                             of the lower quarter) with its
#                                             polygon record to
#                                             /tmp/vo-wedge.log, to read
#                                             afterwards; 3 minutes, 5000
#                                             writes or Ctrl-C
#     tools/vo-dbg.sh submit ATTR [N] [MINEXT] stop at renderer A's inserts
#                                             for records of attribute
#                                             block ATTR at least MINEXT
#                                             px across (default 300):
#                                             vertices, the scratch they
#                                             were packed from, and the
#                                             return addresses that name
#                                             the submitter; N hits
#                                             (default 20) or 60 s.
#                                             FLAGBITS: only records with
#                                             any of those flag bits
#     tools/vo-dbg.sh clip ATTR [N]           stop at renderer A's vertical
#                                             clipper for polygons of
#                                             attribute block ATTR: the
#                                             vertices handed in, matrix,
#                                             projection and the return
#                                             addresses that name the
#                                             submitter; N hits (default
#                                             10) or 60 s
#     tools/vo-dbg.sh proj2d ATTR [N]         stop in the 2D quad submit
#                                             for quads of attribute block
#                                             ATTR whose transformed z is
#                                             not 1.0: input vertex, the
#                                             transform matrix, projection
#                                             and return addresses
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
import gdb, time, struct
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
def grey(v):
    """a light, near-neutral 16-bit pixel (555 or 565): the wedge's colour"""
    for r, g, b in (((v >> 11) & 31, (v >> 6) & 31, v & 31),     # 565, green halved
                    ((v >> 10) & 31, (v >> 5) & 31, v & 31)):    # 555
        if min(r, g, b) >= 20 and max(r, g, b) - min(r, g, b) <= 3: return True
    return False
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

def frame(x, y, n):
    """Every write to viewport pixel (x, y), in order, with its polygon
    record: the paint order at one spot. A gap of over 20 ms between
    writes is marked as a frame break. Stops after n writes or 30 s."""
    b, p, w, h = fb()
    if not b: raise SystemExit('surface not locked at this instant; run again')
    ad = b + y * p + 2 * x
    print('surface %x pitch %d size %dx%d; pixel (%d,%d) at %x holds %04x' % (b, p, w, h, x, y, ad, u16(ad)))
    print('logging every write; play until it shows there (Ctrl-C ends)')
    gdb.execute('watch -l *(unsigned short*)%d' % ad); bp = silent_last()
    i, t0, tl = 0, time.time(), None
    try:
        while i < n and time.time() - t0 < 30:
            if not go(bp): continue
            t = time.time()
            if tl is not None and t - tl > 0.02: print('--- frame break (%d ms) ---' % int((t - tl) * 1000))
            tl = t; i += 1
            print('%2d: %04x from %x' % (i, u16(ad), pc())); record(brief=True)
    except KeyboardInterrupt:
        print('interrupted')

def wedge(x, y):
    """Log every write to viewport pixel (x, y) - value, writer, and the
    polygon record when the writer is renderer A's flush - to
    /tmp/vo-wedge.log, for reading afterwards. Ends after three minutes,
    5000 writes or Ctrl-C."""
    b, p, w, h = fb()
    if not b: raise SystemExit('surface not locked at this instant; run again')
    if x < 0: x, y = w // 2, h * 3 // 4
    ad = b + y * p + 2 * x
    log = open('/tmp/vo-wedge.log', 'w')
    log.write('surface %x pitch %d size %dx%d; pixel (%d,%d) at %x holds %04x\n' % (b, p, w, h, x, y, ad, u16(ad)))
    print('logging every write to (%d,%d) into /tmp/vo-wedge.log; make the wedge appear (Ctrl-C ends)' % (x, y))
    gdb.execute('watch -l *(unsigned short*)%d' % ad); bp = silent_last()
    n, t0, tl = 0, time.time(), None
    try:
        while time.time() - t0 < 180 and n < 5000:
            if not go(bp): continue
            t = time.time()
            if tl is not None and t - tl > 0.02: log.write('--- %d ms ---\n' % int((t - tl) * 1000))
            tl = t; n += 1
            sp = int(gdb.parse_and_eval('$esp')) & 0xffffffff
            line = '%4d %04x pc %x' % (n, u16(ad), pc())
            rec = None
            for k in range(96):
                ret = u32(sp + 4 * k)
                if ret in (0x5d1ecf, 0x5d1f2a): idx = u32(sp + 4 * k + 4)
                elif ret in (0x5d1ee7, 0x5d1f47): idx = u32(sp + 4 * k + 8)
                else: continue
                rec = u32(u32(0x5d1dd8) + 4 * idx); break
            if rec is not None:
                v = [(u16(rec + 0x10 + 4 * i), u16(rec + 0x12 + 4 * i)) for i in range(4)]
                v = [(a - 65536 if a > 32767 else a, c - 65536 if c > 32767 else c) for a, c in v]
                attr = u32(rec + 4)
                line += ' rec %x idx %d flags %08x attr %x [%08x %08x] z %08x  %s' % (
                    rec, idx, u32(rec + 8), attr, u32(attr), u32(attr + 4), u32(rec + 0xc),
                    ' '.join('(%d,%d)' % q for q in v))
            else:
                line += ' (not the flush)'
            log.write(line + '\n')
    except KeyboardInterrupt:
        print('interrupted')
    log.close()
    print('%d writes logged to /tmp/vo-wedge.log' % n)

def submit(attr, n, minext, flagbits):
    """Stop at renderer A's two render-list inserts for records of the
    given attribute block; for each, the record's vertices, the scratch
    the tail packed them from (x, y, z per vertex, 32-bit), and the
    return addresses on the stack, which name the submitter and its
    caller. Records under minext px across are skipped. n hits or 60 s."""
    for site in (0x5d4628, 0x5d5360):
        gdb.execute('break *%d if *(unsigned int*)($edx+4) == %d' % (site, attr))
        gdb.breakpoints()[-1].silent = True
    bps = gdb.breakpoints()[-2:]
    print('waiting for attribute %x records %d px or more across (Ctrl-C ends)' % (attr, minext))
    i, t0 = 0, time.time()
    try:
        while i < n and time.time() - t0 < 60:
            last['ev'] = None
            gdb.execute('continue', to_string=True)
            ev = last.get('ev')
            if isinstance(ev, gdb.SignalEvent) and ev.stop_signal == 'SIGINT': raise KeyboardInterrupt
            if not (isinstance(ev, gdb.BreakpointEvent) and any(b.number in (x.number for x in bps) for b in ev.breakpoints)): continue
            rec = int(gdb.parse_and_eval('$edx')) & 0xffffffff
            v = [(u16(rec + 0x10 + 4 * k), u16(rec + 0x12 + 4 * k)) for k in range(4)]
            v = [(a - 65536 if a > 32767 else a, c - 65536 if c > 32767 else c) for a, c in v]
            xs, ys = [a for a, c in v], [c for a, c in v]
            if max(max(xs) - min(xs), max(ys) - min(ys)) < minext: continue
            if flagbits and not (u32(rec + 8) & flagbits): continue
            i += 1
            sc = u32(0x7085f8)
            print('--- hit %d at %x: record %x flags %08x  %s' % (i, pc(), rec, u32(rec + 8), ' '.join('(%d,%d)' % q for q in v)))
            for k in range(4):
                b = sc + 0x14 * k
                z = struct.unpack('<f', struct.pack('<I', u32(b + 8)))[0]
                x = struct.unpack('<i', struct.pack('<I', u32(b + 0xc)))[0]
                y = struct.unpack('<i', struct.pack('<I', u32(b + 0x10)))[0]
                print('    scratch %d: x %d y %d z %.4f' % (k, x, y, z))
            print('    proj %s  centre %d,%d' % (' '.join('%.3f' % struct.unpack('<f', struct.pack('<I', u32(0x6db4c8 + 4 * j)))[0] for j in (0, 2, 3)), u32(0x6db530), u32(0x6db534)))
            sp = int(gdb.parse_and_eval('$esp')) & 0xffffffff
            rets = []
            for k in range(200):
                w = u32(sp + 4 * k)
                if 0x401000 <= w < 0x5f5000 and u8(w - 5) == 0xe8: rets.append(w)
            print('    returns: ' + ' '.join('%x' % r for r in rets[:12]))
    except KeyboardInterrupt:
        print('interrupted')

def clip(attr, n):
    """Stop at renderer A's vertical clipper (0x5d4680) for polygons of
    the given attribute block that reach past the top or bottom of the
    picture - the clipper replaces such a polygon with pieces, so the
    insert never sees the original. Prints the four
    vertices it was handed (edi), the world matrix, the projection, and
    the return addresses up the stack, deep enough to reach the
    submitter. n hits or 60 s."""
    gdb.execute('break *%d if *(unsigned int*)0x7086c4 == %d' % (0x5d4680, attr))
    bp = silent_last()
    print('waiting for attribute %x polygons at the clipper (Ctrl-C ends)' % attr)
    i, t0 = 0, time.time()
    try:
        while i < n and time.time() - t0 < 120:
            if not go(bp): continue
            edi = int(gdb.parse_and_eval('$edi')) & 0xffffffff
            hgt = u32(0x6bf5bc)
            ys = [struct.unpack('<i', struct.pack('<I', u32(edi + 0x14 * k + 0x10)))[0] for k in range(4)]
            if min(ys) >= 0 and max(ys) < hgt: continue     # nothing to clip: not the one
            i += 1
            print('--- hit %d: vertices at %x' % (i, edi))
            for k in range(4):
                b = edi + 0x14 * k
                f = lambda a: struct.unpack('<f', struct.pack('<I', u32(a)))[0]
                d = lambda a: struct.unpack('<i', struct.pack('<I', u32(a)))[0]
                print('    v%d cam (%.3f %.3f %.3f) screen (%d,%d)' % (k, f(b), f(b + 4), f(b + 8), d(b + 0xc), d(b + 0x10)))
            m = [struct.unpack('<f', struct.pack('<I', u32(0x6db480 + 4 * j)))[0] for j in range(12)]
            print('    matrix %s | %s | %s | t %s' % tuple(' '.join('%.3f' % x for x in m[j:j + 3]) for j in (0, 3, 6, 9)))
            print('    proj %s  centre %d,%d  flags %08x' % (' '.join('%.3f' % struct.unpack('<f', struct.pack('<I', u32(0x6db4c8 + 4 * j)))[0] for j in (0, 2, 3)), u32(0x6db530), u32(0x6db534), u32(0x7086d0)))
            sp = int(gdb.parse_and_eval('$esp')) & 0xffffffff
            rets = []
            for k in range(600):
                w = u32(sp + 4 * k)
                if 0x401000 <= w < 0x5f5000 and u8(w - 5) == 0xe8: rets.append(w)
            print('    returns: ' + ' '.join('%x' % r for r in rets[:16]))
    except KeyboardInterrupt:
        print('interrupted')

def proj2d(attr, n):
    """Stop in the 2D quad submit (0x5d79a0, once the first vertex's z is
    in ecx) for quads of the given attribute block whose transformed
    z is not the 1.0 the 2D callers pass: the input vertex, the transform
    matrix 0x6db450, the projection, and the return addresses. n hits or
    120 s."""
    gdb.execute('break *%d if *(unsigned int*)($ebp+0xc) == %d && $ecx != 0x3f800000' % (0x5d7a29, attr))
    bp = silent_last()
    print('waiting for attribute %x 2D quads projected at z != 1.0 (Ctrl-C ends)' % attr)
    i, t0 = 0, time.time()
    f = lambda a: struct.unpack('<f', struct.pack('<I', u32(a)))[0]
    try:
        while i < n and time.time() - t0 < 120:
            if not go(bp): continue
            i += 1
            ebp = int(gdb.parse_and_eval('$ebp')) & 0xffffffff
            z = f(0x708608)
            print('--- hit %d: in (%.3f %.3f %.3f) -> cam (%.3f %.3f %g)' % (
                i, f(ebp + 0x10), f(ebp + 0x14), f(ebp + 0x18), f(0x708600), f(0x708604), z))
            m = [f(0x6db450 + 4 * j) for j in range(12)]
            print('    matrix %s | %s | %s | t %s' % tuple(' '.join('%.4g' % x for x in m[j:j + 3]) for j in (0, 3, 6, 9)))
            print('    proj %s' % ' '.join('%.3f' % f(0x6db4c8 + 4 * j) for j in (0, 2, 3)))
            sp = int(gdb.parse_and_eval('$esp')) & 0xffffffff
            rets = []
            for k in range(300):
                w = u32(sp + 4 * k)
                if 0x401000 <= w < 0x5f5000 and u8(w - 5) == 0xe8: rets.append(w)
            print('    returns: ' + ' '.join('%x' % r for r in rets[:12]))
    except KeyboardInterrupt:
        print('interrupted')

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
    frame) [ -n "$2" ] || die "frame X Y [N]"; tail="frame($1, $2, ${3:-40})" ;;
    wedge) tail="wedge(${1:--1}, ${2:--1})" ;;
    submit) [ -n "$1" ] || die "submit ATTR [N] [MINEXT] [FLAGBITS]"; tail="submit($(hexarg "$1"), ${2:-20}, ${3:-300}, $(hexarg "${4:-0}"))" ;;
    clip)   [ -n "$1" ] || die "clip ATTR [N]"; tail="clip($(hexarg "$1"), ${2:-10})" ;;
    proj2d) [ -n "$1" ] || die "proj2d ATTR [N]"; tail="proj2d($(hexarg "$1"), ${2:-10})" ;;
    watch) [ -n "$1" ] || die "watch ADDR [SIZE]"; tail="watch($(hexarg "$1"), ${2:-4})" ;;
    break) [ -n "$1" ] || die "break ADDR [N] [COND]"; tail="brk($(hexarg "$1"), ${2:-3}, '${3:-}')" ;;
    read)  [ -n "$1" ] || die "read ADDR [LEN]"; tail="read($(hexarg "$1"), ${2:-64})" ;;
    cmd)   [ -n "$1" ] || die "cmd 'gdb command' ..."
           tail=$(for c in "$@"; do printf "gdb.execute(%s)\n" "'$c'"; done) ;;
    *)     die "unknown mode $mode; see the header" ;;
esac
printf '%s\n' "$tail" "gdb.execute('delete'); gdb.execute('detach')" >> "$py"
exec gdb -q -batch -p "$pid" -x "$py"
