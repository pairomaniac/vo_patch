#!/usr/bin/env python3
"""Compare every placed site's instruction shape between retail and a
build, whether or not the generator would print its table.

    python3 tools/portaudit.py maps/jpre.pkl

For each site: the instructions covering [off, off+len) in retail and at
the placed offset in the build, decoded from the enclosing function's
start, registers kept, immediates and displacements wildcarded. Lists
the ones that differ, and the placements that are neither an
instruction boundary nor inside one operand. A site the generator could
not place is listed as its FAIL line.
"""
import bisect
import os
import pickle
import sys

import capstone
from capstone import x86

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
md.detail = True


def cover(data, off, ln, funcs):
    """Instructions covering [off, off+ln), decoded linearly from the
    start of the enclosing function in the build's own function list."""
    va = 0x400c00 + off
    i = bisect.bisect_right(funcs, va) - 1
    s = funcs[i] - 0x400c00 if i >= 0 else off
    if off - s > 0x8000:
        s = off
    out = []
    for i in md.disasm(data[s:off + ln + 24], s):
        if i.address + i.size <= off:
            continue
        if i.address >= off + ln:
            break
        out.append(i)
    return out


def shape(i):
    ops = []
    for o in i.operands:
        if o.type == x86.X86_OP_REG:
            ops.append(i.reg_name(o.reg))
        elif o.type == x86.X86_OP_IMM:
            ops.append('imm%d' % o.size if abs(o.imm) >= 0x80 else
                       'imm=%d' % o.imm)
        elif o.type == x86.X86_OP_MEM:
            m = o.mem
            ops.append('mem[%s+%s*%d+%s]' % (
                i.reg_name(m.base) if m.base else '',
                i.reg_name(m.index) if m.index else '',
                m.scale, 'D' if m.disp else '0'))
    return '%s %s' % (i.mnemonic, ','.join(ops))


def in_operand(i, off, ln):
    for oa, sa in (('disp_offset', 'disp_size'), ('imm_offset', 'imm_size')):
        o, sz = getattr(i, oa), getattr(i, sa)
        if o and i.address + o <= off and off + ln <= i.address + o + sz:
            return True
    return False


def main():
    import hiresport
    import vo_patch_hires as hires
    pkl = sys.argv[1]
    r = hiresport.resolve(pkl)              # resets sys.argv for votrans
    for f in r['fails']:
        print('FAIL', f)
    retail = open(r['retail'], 'rb').read()
    other = open(r['other'], 'rb').read()
    M = pickle.load(open(pkl, 'rb'))
    fa = sorted(f['s'] for f in M['A'])
    fb = sorted(f['s'] for f in M['B'])
    sites = hires.build_sites(1920, 1080, 0x36c0000, 0x37c0000, 0x38c0000,
                              (0x39c0000, hires.HIRES_POLYS))
    bad_shape, bad_bound = [], []
    for off, old, new in sites:
        if off not in r['off']:
            continue
        ln = len(old) if old is not None else len(new)
        bo, _bytes, how = r['off'][off]
        if bo > 0x1f8000 or off > 0x1f8000:        # data: no shape
            continue
        sa = [shape(i) for i in cover(retail, off, ln, fa)]
        b = cover(other, bo, ln, fb)
        sb = [shape(i) for i in b]
        if sa != sb:
            bad_shape.append((off, bo, how, sa, sb))
        ok = bool(b) and (b[0].address == bo
                          or len(b) == 1 and in_operand(b[0], bo, ln))
        if not ok:
            bad_bound.append((off, bo, how, sb))
    print('shape mismatches: %d' % len(bad_shape))
    for off, bo, how, sa, sb in bad_shape:
        print('  0x%06x -> 0x%06x (%s)' % (off, bo, how))
        print('     retail: %s' % ' | '.join(sa))
        print('     build : %s' % ' | '.join(sb))
    print('boundary faults: %d' % len(bad_bound))
    for off, bo, how, sb in bad_bound:
        print('  0x%06x -> 0x%06x (%s): %s' % (off, bo, how, ' | '.join(sb)))


if __name__ == '__main__':
    main()
