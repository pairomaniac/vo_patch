#!/usr/bin/env python3
"""Where a blob of N bytes can go.

    python3 tools/freespace.py /path/to/VIRTUAL-ON 91

Picking a cave by eye is how the XInput ini routine ended up on the attract
loop's scoreboard template through v0.10.1: zeros in the file, nothing
pointing inside it, and the game copying all 84 bytes of it every time the
demo match finished. The zeros were the data.

So this asks the questions that were not asked then, against a real file:

  - is the run long enough, and does it start on a dword boundary
  - is any of it already spoken for by a patch site
  - does anything point inside it
  - does anything point just before it, at a structure that may run into it

The last one is the one that matters and the one a scan of the cave itself
cannot answer, so candidates carrying it are listed separately rather than
dropped: some are bytes that only look like an address, and the rest are
usually short structures that stop where the run begins. Read the game at
that address before using such a cave.

A clean answer here is not a promise. Nothing sees a table reached by
pointer arithmetic, and nothing here knows what the game does at runtime.
"""

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import selftest                                     # noqa: E402

MIN_RUN = 16            # shorter runs are alignment padding, not caves
LOOKBACK = selftest.LOOKBACK

# Where a blob may go at all. .rdata is read-only data the patcher marks
# executable, and is where most of the caves are. .rsrc counts only past
# its VirtualSize, where the raw padding is - the zeros inside a resource
# belong to a bitmap. .data is excluded on purpose: it is full of arrays
# the game fills at runtime, and every one of them scans as a fine empty
# cave.
SECTIONS = {b'.rdata': 'all', b'.rsrc': 'padding'}


def operand_addresses(data, lo, hi):
    """Addresses in [lo, hi) that the code carries as an operand.

    The disp32 modrm form, mov reg, imm32 and push imm32 - the three ways
    an address reaches an instruction without a disassembler to find it.
    """
    found = {}
    for i in range(1, len(data) - 4):
        va = int.from_bytes(data[i:i + 4], 'little')
        if not lo <= va < hi:
            continue
        prev = data[i - 1]
        if prev & 0xC7 == 0x05 or 0xB8 <= prev <= 0xBF or prev == 0x68:
            found.setdefault(va, []).append(i)
    return found


def placeable(data):
    """(lo, hi) per section a blob may be written into."""
    pe = int.from_bytes(data[0x3c:0x40], 'little')
    count = int.from_bytes(data[pe + 6:pe + 8], 'little')
    optlen = int.from_bytes(data[pe + 20:pe + 22], 'little')
    base = int.from_bytes(data[pe + 24 + 28:pe + 24 + 32], 'little')
    out = []
    for i in range(count):
        h = pe + 24 + optlen + i * 40
        name = data[h:h + 8].rstrip(b'\x00')
        if name not in SECTIONS:
            continue
        rva = int.from_bytes(data[h + 12:h + 16], 'little')
        virt = int.from_bytes(data[h + 8:h + 12], 'little')
        raw = int.from_bytes(data[h + 16:h + 20], 'little')
        lo = base + rva + (virt if SECTIONS[name] == 'padding' else 0)
        out.append((lo, base + rva + raw))
    return out


def taken(vp, secs):
    """Every byte a patch site already writes, as a set of VAs."""
    out = set()
    for _label, _tip, sites in vp.BY_KEY.values():
        for off, old, _new in sites or ():
            va = selftest.to_va(secs, off)
            if va is not None:
                out.update(range(va, va + len(old) // 2))
    return out


def main(argv):
    if len(argv) != 2:
        raise SystemExit('usage: freespace.py GAMEDIR-or-EXE LENGTH')
    path, want = argv[0], int(argv[1])
    if os.path.isdir(path):
        path = os.path.join(path, 'v_on.exe')

    vp = selftest.load_patcher()
    data, source = selftest.pristine(path, vp)
    if data is None:
        raise SystemExit('%s is not the unmodified file and has no .bak'
                         % path)
    secs = selftest.sections(data)
    used = taken(vp, secs)

    where = placeable(data)
    runs = []
    for m in re.finditer(b'\x00{%d,}' % MIN_RUN, data):
        va = selftest.to_va(secs, m.start())
        if va is None:
            continue
        start, end = va, va + (m.end() - m.start())
        for lo_s, hi_s in where:
            if lo_s <= start < hi_s:
                end = min(end, hi_s)
                break
        else:
            continue
        free, at = [], start
        for p in range(start, end):
            if p in used:
                if p - at >= want:
                    free.append((at, p))
                at = p + 1
        if end - at >= want:
            free.append((at, end))
        runs.extend(free)

    lo = min(a for a, _b in runs) - LOOKBACK
    hi = max(b for _a, b in runs)
    ops = operand_addresses(data, lo, hi)

    clean, check, refused = [], [], []
    for a, b in runs:
        a = (a + 3) & ~3                        # dword-aligned start
        if b - a < want:
            continue
        inside = [p for p in range(a, b) if p in ops]
        near = [p for p in range(a - LOOKBACK, a) if p in ops]
        if inside:
            refused.append((a, b, inside[0]))
        elif near:
            check.append((a, b, max(near)))
        else:
            clean.append((a, b))

    print('%s, room for %d bytes' % (os.path.basename(source), want))
    print()
    print('free, with nothing pointing at or near them:')
    for a, b in clean:
        print('  0x%08x  %5d bytes' % (a, b - a))
    if not clean:
        print('  (none)')
    if check:
        print()
        print('free, but something points just before them - read the game '
              'there first:')
        for a, b, p in check:
            print('  0x%08x  %5d bytes   0x%08x is %d bytes back'
                  % (a, b - a, p, a - p))
    if refused:
        print()
        print('not free - the game reads inside these:')
        for a, b, p in refused:
            print('  0x%08x  %5d bytes   reads 0x%08x' % (a, b - a, p))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
