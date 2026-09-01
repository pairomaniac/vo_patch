#!/usr/bin/env python3
"""Compare every placed site's instruction shape between retail and the build.

    python3 tools/portaudit.py maps/jp_dump.pkl RETAIL.exe OTHER.exe

For each site: the instructions covering [off, off+len) in retail and at
the placed offset in the build, registers kept, immediates and
displacements wildcarded. Prints the ones that differ, and the ones whose
placed offset is neither an instruction boundary nor inside one operand.
"""
import bisect
import pickle
import sys

import capstone
from capstone import x86

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


def main():
    dump = pickle.load(open(sys.argv[1], 'rb'))
    retail = open(sys.argv[2], 'rb').read()
    other = open(sys.argv[3], 'rb').read()
    sites = pickle.load(open('maps/retail_sites.pkl', 'rb'))
    M = pickle.load(open(sys.argv[4], 'rb'))
    fa = sorted(f['s'] for f in M['A'])
    fb = sorted(f['s'] for f in M['B'])
    bad_shape, bad_bound = [], []
    for off, old, new in sites:
        if off not in dump['off']:
            continue
        ln = len(old) if old is not None else len(new)
        bo, how = dump['off'][off]
        if bo > 0x1f8000 or off > 0x1f8000:
            continue
        a = cover(retail, off, ln, fa)
        b = cover(other, bo, ln, fb)
        sa = [shape(i) for i in a]
        sb = [shape(i) for i in b]
        if sa != sb:
            bad_shape.append((off, bo, how, sa, sb))
        # boundary: placed offset is an instruction start, or the whole
        # range sits in one operand
        ok = bool(b) and b[0].address == bo
        if not ok and len(b) == 1:
            i = b[0]
            for oa, sa_ in (('disp_offset', 'disp_size'),
                            ('imm_offset', 'imm_size')):
                o, sz = getattr(i, oa), getattr(i, sa_)
                if o and i.address + o <= bo and \
                        bo + ln <= i.address + o + sz:
                    ok = True
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
