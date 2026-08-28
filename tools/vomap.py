"""Map retail v_on.exe addresses onto the Japanese rerelease.

    python3 tools/vomap.py RETAIL.exe JP.exe [MAP.pkl]

Splits both .text sections into functions (call targets, reloc-listed code
pointers, prologues), signs each by its opcode stream with addresses masked,
matches them in order, then pairs the absolute references inside matched
functions to derive the data map. Writes MAP.pkl (default vomap.pkl in the
current directory) for votrans.py. Needs capstone: pip install capstone.

Neither executable is in the repository.
"""
import struct, re, sys, os, difflib, collections, pickle, bisect
import capstone

md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
md.detail = True
BASE = 0x400000


class Exe:
    def __init__(self, path):
        self.d = open(path, 'rb').read()
        d = self.d
        pe = struct.unpack_from('<I', d, 0x3c)[0]
        nsec = struct.unpack_from('<H', d, pe + 6)[0]
        optsz = struct.unpack_from('<H', d, pe + 20)[0]
        opt = pe + 24
        self.entry = struct.unpack_from('<I', d, opt + 16)[0]
        self.secs = []
        for i in range(nsec):
            n, vs, va, rs, ro = struct.unpack_from('<8sIIII', d, opt + optsz + 40 * i)
            self.secs.append((n.rstrip(b'\0').decode(), va, vs, ro, rs))
        self.sec = {s[0]: s for s in self.secs}
        t = self.sec['.text']
        self.text_lo, self.text_hi = BASE + t[1], BASE + t[1] + t[2]
        self.relocs = self._relocs()
        self.text_relocs = self._jump_tables()

    def _jump_tables(self):
        """Dword positions of switch tables in .text: every jmp [reg*4+T]
        names one, and it runs while entries are reloc-listed code pointers."""
        rel = set(self.relocs)
        t = self.d[self.off(self.text_lo):self.off(self.text_hi)]
        out = set()
        for m in re.finditer(rb'\xff\x24[\x85\x8d\x95\x9d\xb5\xbd\x85]', t):
            T = struct.unpack_from('<I', t, m.start() + 3)[0]
            if not (self.text_lo <= T < self.text_hi):
                continue
            p = T
            while p in rel and self.text_lo <= self.u32(p) < self.text_hi:
                out.add(p); p += 4
        return sorted(out)

    def off(self, va):
        r = va - BASE
        for n, va0, vs, ro, rs in self.secs:
            if va0 <= r < va0 + max(vs, rs):
                return r - va0 + ro
        return None

    def va(self, off):
        for n, va0, vs, ro, rs in self.secs:
            if ro <= off < ro + rs:
                return off - ro + va0 + BASE
        return None

    def secname(self, va):
        r = va - BASE
        for n, va0, vs, ro, rs in self.secs:
            if va0 <= r < va0 + max(vs, rs):
                return n
        return None

    def u32(self, va):
        return struct.unpack_from('<I', self.d, self.off(va))[0]

    def _relocs(self):
        n, va0, vs, ro, rs = self.sec['.reloc']
        out = []
        p = ro
        end = ro + vs
        while p < end:
            page, size = struct.unpack_from('<II', self.d, p)
            if size == 0:
                break
            for q in range(p + 8, p + size, 2):
                e = struct.unpack_from('<H', self.d, q)[0]
                if e >> 12 == 3:
                    out.append(BASE + page + (e & 0xfff))
            p += size
        return out

    def functions(self):
        starts = set([self.entry + BASE])
        lo, hi = self.text_lo, self.text_hi
        # reloc-listed pointers into .text
        for r in self.relocs:
            v = self.u32(r)
            if lo <= v < hi:
                starts.add(v)
        # call targets: scan e8 in .text
        t = self.d[self.off(lo):self.off(lo) + (hi - lo)]
        for m in re.finditer(rb'\xe8', t):
            i = m.start()
            if i + 5 > len(t):
                break
            rel = struct.unpack_from('<i', t, i + 1)[0]
            tgt = lo + i + 5 + rel
            if lo <= tgt < hi:
                starts.add(tgt)
        # prologues 55 8b ec that follow ret/int3/nop padding
        for m in re.finditer(rb'(?<=[\xc3\xcc\x90])\x55\x8b\xec', t):
            starts.add(lo + m.start())
        for m in re.finditer(rb'(?<=\xc2..)\x55\x8b\xec', t, re.S):
            starts.add(lo + m.start())
        starts = sorted(s for s in starts if lo <= s < hi)
        funcs = []
        for i, s in enumerate(starts):
            e = starts[i + 1] if i + 1 < len(starts) else hi
            funcs.append((s, e))
        return funcs


def sign(exe, s, e, loose=False):
    """Opcode stream with immediates that look like addresses replaced by
    their kind; rel32 targets and (if loose) ebp displacements masked."""
    sig = []
    refs = []      # (insn_va, kind, value) for absolute refs and calls
    # cut out reloc-listed dwords (switch tables) so they are not decoded
    rl = exe.text_relocs
    i0 = bisect.bisect_left(rl, s); i1 = bisect.bisect_left(rl, e)
    chunks = []; p = s
    for r in rl[i0:i1]:
        if r > p: chunks.append((p, r))
        p = max(p, r + 4)
    if p < e: chunks.append((p, e))
    def insns():
        for a, b in chunks:
            for ins in md.disasm(exe.d[exe.off(a):exe.off(b)], a):
                yield ins
    for ins in insns():
        parts = [ins.mnemonic]
        for op in ins.operands:
            if op.type == capstone.x86.X86_OP_REG:
                parts.append('r%d' % op.reg)
            elif op.type == capstone.x86.X86_OP_IMM:
                v = op.imm & 0xffffffff
                if ins.mnemonic in ('call', 'jmp') or ins.mnemonic.startswith('j'):
                    parts.append('rel')
                    if exe.text_lo <= v < exe.text_hi:
                        refs.append((ins.address, 'code', v))
                elif BASE <= v < BASE + 0x3400000:
                    parts.append('A')
                    refs.append((ins.address, 'imm', v))
                else:
                    parts.append('i%x' % v)
            elif op.type == capstone.x86.X86_OP_MEM:
                m = op.mem
                disp = m.disp & 0xffffffff
                if m.base == 0 and m.index == 0 or BASE <= disp < BASE + 0x3400000:
                    parts.append('m[%d+%d*%d+A]' % (m.base, m.index, m.scale))
                    refs.append((ins.address, 'mem', disp))
                elif loose and m.base == capstone.x86.X86_REG_EBP:
                    parts.append('m[ebp+?]')
                else:
                    parts.append('m[%d+%d*%d+%x]' % (m.base, m.index, m.scale, disp))
        sig.append(' '.join(parts))
        if ins.mnemonic in ('ret', 'int3') and ins.address + ins.size >= e:
            break
    return tuple(sig), refs


def addrs(exe, s, e):
    rl = exe.text_relocs
    i0 = bisect.bisect_left(rl, s); i1 = bisect.bisect_left(rl, e)
    out = []; p = s
    chunks = []
    for r in rl[i0:i1]:
        if r > p: chunks.append((p, r))
        p = max(p, r + 4)
    if p < e: chunks.append((p, e))
    for a, b in chunks:
        out += [i.address for i in md.disasm(exe.d[exe.off(a):exe.off(b)], a)]
    return out


def analyse(path):
    exe = Exe(path)
    funcs = exe.functions()
    data = []
    for s, e in funcs:
        tight, refs = sign(exe, s, e)
        loose, _ = sign(exe, s, e, loose=True)
        data.append(dict(s=s, e=e, tight=tight, loose=loose, refs=refs))
    return exe, data


def match(A, B):
    """Return list of (ia, ib) index pairs."""
    pairs = {}
    # 1. unique tight signatures on both sides
    ca = collections.Counter(f['tight'] for f in A)
    cb = collections.Counter(f['tight'] for f in B)
    ib_by_sig = {f['tight']: i for i, f in enumerate(B) if cb[f['tight']] == 1}
    for i, f in enumerate(A):
        if ca[f['tight']] == 1 and f['tight'] in ib_by_sig and len(f['tight']) >= 4:
            pairs[i] = ib_by_sig[f['tight']]
    # 2. between consecutive anchors, align by loose signature with difflib
    anchors = sorted(pairs.items())
    segs = []
    pa, pb = -1, -1
    for ia, ib in anchors + [(len(A), len(B))]:
        segs.append(((pa + 1, ia), (pb + 1, ib)))
        pa, pb = ia, ib
    for (a0, a1), (b0, b1) in segs:
        if a1 <= a0 or b1 <= b0:
            continue
        la = [A[i]['loose'] for i in range(a0, a1)]
        lb = [B[i]['loose'] for i in range(b0, b1)]
        sm = difflib.SequenceMatcher(None, la, lb, autojunk=False)
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == 'equal':
                for k in range(i2 - i1):
                    pairs[a0 + i1 + k] = b0 + j1 + k
            elif tag == 'replace' and i2 - i1 == j2 - j1:
                # same count: pair positionally if they are close in shape
                for k in range(i2 - i1):
                    sa, sb = A[a0 + i1 + k]['loose'], B[b0 + j1 + k]['loose']
                    r = difflib.SequenceMatcher(None, sa, sb, autojunk=False).ratio()
                    if r >= 0.8:
                        pairs[a0 + i1 + k] = b0 + j1 + k
    # 3. leftovers: best loose-ratio candidate between matched neighbours
    pb = {b: a for a, b in pairs.items()}
    matched_a = sorted(pairs)
    for ia in [i for i in range(len(A)) if i not in pairs]:
        k = bisect.bisect_left(matched_a, ia)
        lo_b = pairs[matched_a[k - 1]] if k > 0 else -1
        hi_b = pairs[matched_a[k]] if k < len(matched_a) else len(B)
        best = None
        for ib in range(lo_b + 1, hi_b):
            if ib in pb:
                continue
            r = difflib.SequenceMatcher(None, A[ia]['loose'], B[ib]['loose'], autojunk=False).ratio()
            if best is None or r > best[0]:
                best = (r, ib)
        if best and best[0] >= 0.6:
            pairs[ia] = best[1]; pb[best[1]] = ia
    return sorted(pairs.items())


def derive(A, B, pairs, exeA, exeB):
    """From matched functions, pair up refs -> address votes."""
    votes = collections.defaultdict(collections.Counter)   # retail va -> Counter(jp va)
    insmap = {}                                             # retail insn va -> jp insn va
    quality = {}
    for ia, ib in pairs:
        fa, fb = A[ia], B[ib]
        ra, rb = fa['refs'], fb['refs']
        # pair refs positionally when the streams are the same length
        if fa['loose'] == fb['loose'] and len(ra) == len(rb):
            for (va, ka, xa), (vb, kb, xb) in zip(ra, rb):
                if ka == kb:
                    votes[xa][xb] += 1
            quality[fa['s']] = 'exact'
        else:
            # align by loose stream, then pair refs that fall on aligned insns
            sm = difflib.SequenceMatcher(None, fa['loose'], fb['loose'], autojunk=False)
            # need insn index -> ref: rebuild by re-disassembling positions
            ia_refs = {v: (k, x) for v, k, x in ra}
            ib_refs = {v: (k, x) for v, k, x in rb}
            addrsA = addrs(exeA, fa['s'], fa['e'])
            addrsB = addrs(exeB, fb['s'], fb['e'])
            for tag, i1, i2, j1, j2 in sm.get_opcodes():
                if tag != 'equal':
                    continue
                for k in range(i2 - i1):
                    if i1 + k < len(addrsA) and j1 + k < len(addrsB):
                        va, vb = addrsA[i1 + k], addrsB[j1 + k]
                        insmap[va] = vb
                        if va in ia_refs and vb in ib_refs and ia_refs[va][0] == ib_refs[vb][0]:
                            votes[ia_refs[va][1]][ib_refs[vb][1]] += 1
            quality[fa['s']] = 'aligned %.2f' % sm.ratio()
        # instruction map for exact functions
        if fa['loose'] == fb['loose']:
            addrsA = addrs(exeA, fa['s'], fa['e'])
            addrsB = addrs(exeB, fb['s'], fb['e'])
            for va, vb in zip(addrsA, addrsB):
                insmap[va] = vb
    return votes, insmap, quality


if __name__ == '__main__':
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    out = sys.argv[3] if len(sys.argv) > 3 else 'vomap.pkl'
    exeA, A = analyse(sys.argv[1])
    exeB, B = analyse(sys.argv[2])
    print('functions: retail %d, jp %d' % (len(A), len(B)))
    pairs = match(A, B)
    print('matched %d' % len(pairs))
    exact = sum(1 for ia, ib in pairs if A[ia]['loose'] == B[ib]['loose'])
    print('  identical loose stream: %d' % exact)
    votes, insmap, quality = derive(A, B, pairs, exeA, exeB)
    pickle.dump(dict(A=A, B=B, pairs=pairs, votes=dict(votes), insmap=insmap,
                     quality=quality, retail=os.path.abspath(sys.argv[1]),
                     jp=os.path.abspath(sys.argv[2])),
                open(out, 'wb'))
    # consistency of data votes
    amb = sum(1 for k, c in votes.items() if len(c) > 1 and c.most_common(2)[1][1] > 1)
    print('data/code addresses voted on: %d, contested: %d' % (len(votes), amb))
