"""Translate the patch table and the asm sources' addresses through a
vomap.py map.

    python3 tools/votrans.py sites [MAP.pkl]     every FEATURES site
    python3 tools/votrans.py asm   [MAP.pkl]     every address in asm/*.asm
    python3 tools/votrans.py one VA... [MAP.pkl] single addresses

MAP.pkl defaults to vomap.pkl in the current directory; the executables are
read from the paths recorded in it.
"""
import pickle, re, sys, struct, bisect, importlib.util, collections, os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from vomap import Exe, md, BASE                              # noqa: E402

_args = [a for a in sys.argv[1:] if a.endswith('.pkl')]
M = pickle.load(open(_args[0] if _args else 'vomap.pkl', 'rb'))
A, B, pairs, votes, insmap, quality = (M[k] for k in ('A', 'B', 'pairs', 'votes', 'insmap', 'quality'))
exeA, exeB = Exe(M['retail']), Exe(M['jp'])
fstart = [f['s'] for f in A]
fmap = {A[ia]['s']: B[ib]['s'] for ia, ib in pairs}
inv_pairs = dict(pairs)


def import_map():
    """retail IAT slot VA -> jp IAT slot VA, by dll/name."""
    def iat(exe):
        d = exe.d
        n, va0, vs, ro, rs = exe.sec['.idata']
        out = {}
        i = ro
        while True:
            ilt, ts, fc, name, iatp = struct.unpack_from('<5I', d, i)
            if not name:
                break
            dll = d[exe.off(BASE + name):].split(b'\0')[0].decode().lower()
            t = exe.off(BASE + (ilt or iatp))
            k = 0
            while True:
                v = struct.unpack_from('<I', d, t)[0]
                if not v:
                    break
                fn = '#%d' % (v & 0xffff) if v & 0x80000000 else d[exe.off(BASE + v) + 2:].split(b'\0')[0].decode()
                out[(dll, fn)] = BASE + iatp + 4 * k
                t += 4
                k += 1
            i += 20
        return out
    a, b = iat(exeA), iat(exeB)
    return {va: b[k] for k, va in a.items() if k in b}, {va: k for k, va in a.items()}


IAT, IATNAME = import_map()


def enclosing(va):
    i = bisect.bisect_right(fstart, va) - 1
    if i >= 0 and A[i]['s'] <= va < A[i]['e']:
        return i
    return None


def translate(va):
    """Return (jp_va, how) or (None, why)."""
    if va in MANUAL_VA:
        return MANUAL_VA[va], 'manual'
    if va in IAT:
        return IAT[va], 'iat ' + IATNAME[va][1]
    if va in fmap:
        return fmap[va], 'func'
    if va in insmap:
        return insmap[va], 'insn'
    if exeA.text_lo <= va < exeA.text_hi:
        for back in range(1, 15):
            if va - back in insmap:
                # same instruction length on both sides -> same offset inside it
                la = next(md.disasm(exeA.d[exeA.off(va - back):exeA.off(va - back) + 15], va - back)).size
                jb = insmap[va - back]
                lb = next(md.disasm(exeB.d[exeB.off(jb):exeB.off(jb) + 15], jb)).size
                if la == lb and back < la:
                    return jb + back, 'insn+%d' % back
                break
        i = enclosing(va)
        if i is None:
            return None, 'code, no function'
        if i not in inv_pairs:
            return None, 'code, function %x unmatched' % A[i]['s']
        # inside a matched function but not on an aligned instruction
        return None, 'code, in %x (%s) not aligned' % (A[i]['s'], quality.get(A[i]['s']))
    if va in votes:
        c = votes[va].most_common(2)
        if len(c) == 1 or c[0][1] >= 3 * c[1][1]:
            return c[0][0], 'data %d votes' % c[0][1]
        return c[0][0], 'data CONTESTED %s' % c
    # untouched data: interpolate from neighbours with the same delta
    lo = [k for k in votes if k < va and abs(k - va) < 0x400]
    hi = [k for k in votes if k > va and abs(k - va) < 0x400]
    if lo and hi:
        kl, kh = max(lo), min(hi)
        dl = votes[kl].most_common(1)[0][0] - kl
        dh = votes[kh].most_common(1)[0][0] - kh
        if dl == dh:
            return va + dl, 'data interp delta %+x' % dl
        return None, 'data between %x(%+x) and %x(%+x)' % (kl, dl, kh, dh)
    return None, 'no data'


def translate_off(off):
    va = exeA.va(off)
    if va is None:
        return None, 'outside sections'
    r, how = translate(va)
    return (exeB.off(r) if r is not None else None), how


# Resolved by hand from the disassembly; see docs/NOTES.md.
MANUAL = {
    0x000970bf: 0x00095c1c, 0x000970d5: 0x00095c32,   # bind page fill loop, [ebp-8] is [ebp-0x18] in JP
    0x00095ec7: 0x00094a26,                           # mov dl,[eax+ecx*2+block]: block moved
    0x00096b61: 0x000956bf,                           # 2P-key check, retail split this function in two
    0x000958aa: 0x0009440a,                           # jmp +0 at loop exit
    0x00189546: 0x0018491a, 0x00058189: 0x0005749d,   # sound: same code, no aligned insn
    0x006035ac: 0x005fd5ac,                           # .rsrc, whole section is -0x6000
    0x0000023f: 0x0000023f, 0x000000a8: 0x000000a8,   # PE header: same section order, same field
}
MANUAL_VA = {
    0x0049776e: 0x004962cc, 0x004977c6: 0x00496324,   # kbpage: 2P-key check labels
    0x00497c70: 0x004967cd, 0x00497cb0: 0x0049680d,   # pagesec: bind page loop heads
    0x00497cf7: 0x00496854,                           # bindlist: loop exit
    0x005c680b: 0x005c10da,                           # f11pause: GRESUME
}


if __name__ == '__main__':
    what = sys.argv[1]
    if what == 'sites':
        spec = importlib.util.spec_from_file_location('vp', os.path.join(ROOT, 'vo_patch.py'))
        vp = importlib.util.module_from_spec(spec); spec.loader.exec_module(vp)
        ok = bad = 0
        for key, label, tip, sites in vp.FEATURES:
            for off, orig, new in sites:
                o = bytes.fromhex(orig)
                if set(o) == {0}:
                    print('%-11s %08x cave %4d' % (key, off, len(o)))
                    continue
                jo, how = translate_off(off)
                if off in MANUAL:
                    jo, how = MANUAL[off], 'manual'
                if jo is None and len(o) == 4:
                    p = struct.unpack('<I', o)[0]
                    if exeA.text_lo <= p < exeA.text_hi:
                        q, qh = translate(p)
                        if q is not None:
                            # dword in jp within +-0x8000 of the retail file offset
                            lo, hi = max(0, off - 0x8000), off + 0x8000
                            hits = [lo + m.start() for m in re.finditer(re.escape(struct.pack('<I', q)), exeB.d[lo:hi])]
                            if len(hits) == 1:
                                jo, how = hits[0], 'ptr->%x (%s)' % (q, qh.split()[0])
                            elif len(hits) > 1:
                                # the entry before this one settles it
                                prev, ph = translate_off(off - 4)
                                if prev is None:
                                    po = struct.unpack_from('<I', exeA.d, off - 4)[0]
                                    pq, _ = translate(po) if exeA.text_lo <= po < exeA.text_hi else (None, None)
                                    if pq is not None:
                                        ph_ = [h for h in hits if struct.unpack_from('<I', exeB.d, h - 4)[0] == pq]
                                        if len(ph_) == 1:
                                            jo, how = ph_[0], 'ptr->%x (by prev entry)' % q
                                elif prev + 4 in hits:
                                    jo, how = prev + 4, 'ptr->%x (next to %x)' % (q, prev)
                            if jo is None:
                                how = 'ptr->%x %d hits' % (q, len(hits))
                if jo is None and len(o) >= 4:
                    hits = [m.start() for m in re.finditer(re.escape(o), exeB.d)]
                    if len(hits) == 1:
                        jo, how = hits[0], 'raw unique %+x' % (hits[0] - off)
                if jo is None:
                    print('%-11s %08x  ????????  %s' % (key, off, how)); bad += 1; continue
                got = exeB.d[jo:jo + len(o)]
                same = got == o
                # masked compare
                m = sum(1 for x, y in zip(o, got) if x == y)
                print('%-11s %08x  %08x  %-22s %s' % (key, off, jo, how, 'same' if same else '%d/%d bytes' % (m, len(o))))
                ok += 1
        print('translated %d, failed %d' % (ok, bad))
    elif what == 'asm':
        import glob
        addrs = collections.defaultdict(set)
        for f in glob.glob(os.path.join(ROOT, 'asm', '*.asm')):
            for m in re.finditer(r'0x0?([0-9a-fA-F]{6,8})', open(f).read()):
                addrs[int(m.group(1), 16)].add(f.split('/')[-1])
        ok = bad = 0
        for va in sorted(addrs):
            r, how = translate(va)
            sec = exeA.secname(va)
            if r is None:
                bad += 1
            else:
                ok += 1
            print('%08x -> %-8s %-6s %-32s %s' % (va, '%08x' % r if r else '????????', sec, how, ','.join(sorted(addrs[va]))))
        print('translated %d, failed %d' % (ok, bad))
    elif what == 'one':
        for a in [x for x in sys.argv[2:] if not x.endswith('.pkl')]:
            va = int(a, 16)
            r, how = translate(va); print('%08x -> %s  %s' % (va, '%08x' % r if r else '????????', how))
