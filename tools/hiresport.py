#!/usr/bin/env python3
"""Port the resolution patch's offsets and addresses to another build.

    python3 tools/hiresport.py MAP.pkl

Prints a python dict for vo_patch.PORT: every site offset translated, the
build's own original bytes per site, and every named address hires embeds
in code or writes. A site in a function vomap could not match is found by
signature: its retail bytes with game-range immediates wildcarded, which
must hit the other build exactly once. Anything unresolved is fatal.
"""
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

GAME_LO, GAME_HI = 0x401000, 0x3700000


import capstone
_md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
_md.detail = True


def wildcard_pat(bs, loose=False):
    """Regex of bs keeping opcode structure, wildcarding displacements and
    large immediates (addresses, strides), so builds with different frame
    layouts or globals still match instruction-for-instruction."""
    keep = bytearray(b'\x01' * len(bs))
    end = 0
    for i in _md.disasm(bytes(bs), 0):
        end = i.address + i.size
        if i.disp_offset:
            keep[i.address + i.disp_offset:
                 i.address + i.disp_offset + i.disp_size] = b'\0' * i.disp_size
        if i.imm_offset and i.imm_size >= 2:
            imm = int.from_bytes(bs[i.address + i.imm_offset:
                                    i.address + i.imm_offset + i.imm_size],
                                 'little', signed=True)
            if loose or abs(imm) >= 0x1000 or i.bytes[0] in (0xe8, 0xe9) \
                    or i.bytes[0] == 0x0f and i.bytes[1] & 0xf0 == 0x80:
                keep[i.address + i.imm_offset:
                     i.address + i.imm_offset + i.imm_size] = \
                    b'\0' * i.imm_size
    keep[end:] = b'\0' * (len(bs) - end)     # a cut-off tail instruction
    out = b''
    for i, byte in enumerate(bs):
        out += re.escape(bs[i:i + 1]) if keep[i] else b'.'
    return re.compile(out, re.S)


MANUAL = {
    '345107fa': {                          # Japan rerelease
        # the revision dropped the 0.95 hardware case; SCALE_A only gets
        # its 1.0 default, at a different spot
        0x1c7775: None,
        0x1c7726: 0x1c1ff5,
        # engine B's roll keeps its count in edi, so the pattern that
        # finds engine A's site finds nothing here
        0x166a63: 0x161da3,
    },
    '3317246a': {                          # USA OEM
        # renderer A's row maths uses eax where retail uses ecx
        0x1c799b: 0x1c74cd,
    },
}


def build_resolvers(votrans, retail, other):
    """Fallbacks for what the map alone cannot place.

    Functions vomap left unmatched are found through their callers: every
    e8 to the retail function from inside a matched function is translated
    and the build's own call target read back; the majority wins. A site
    inside such a function - or one the instruction map cannot align - is
    placed linearly from the function starts and verified by its wildcarded
    bytes at that exact spot, falling back to a search inside the function.
    """
    import bisect
    A = votrans.A
    fstart = [f['s'] for f in A]
    fmap = votrans.fmap

    def enclosing(va):
        i = bisect.bisect_right(fstart, va) - 1
        return i if i >= 0 and A[i]['s'] <= va < A[i]['e'] else None

    calls = {}
    text = retail
    i = 0
    while True:
        i = text.find(b'\xe8', i + 1)
        if i < 0 or i > 0x1f4400:
            break
        rel = struct.unpack_from('<i', text, i + 1)[0]
        tgt = 0x400c00 + i + 5 + rel
        if 0x401000 <= tgt < 0x5f5000:
            calls.setdefault(tgt, []).append(0x400c00 + i)

    def same_shape(roff, ooff, n=4):
        a = [i.mnemonic for i in _md.disasm(retail[roff:roff + 24], 0)][:n]
        b = [i.mnemonic for i in _md.disasm(other[ooff:ooff + 24], 0)][:n]
        return a and a == b

    def func_start_via_callers(va):
        votes = {}
        for site in calls.get(va, ())[:24]:
            t, _how = votrans.translate(site)
            if t is None:
                continue
            fo = t - 0x400c00
            if other[fo:fo + 1] != b'\xe8':
                continue
            rel = struct.unpack_from('<i', other, fo + 1)[0]
            votes[t + 5 + rel] = votes.get(t + 5 + rel, 0) + 1
        if not votes:
            return None
        best = max(votes, key=votes.get)
        return best if votes[best] >= max(1, sum(votes.values()) // 2) else None

    def ctx_pat(off, loose=False):
        for back in range(16, 25):
            s = off - back
            ends = set()
            for ins in _md.disasm(retail[s:off + 16], 0):
                ends.add(ins.address + ins.size)
            if back in ends or back == 24:
                pat = wildcard_pat(retail[s:off + 16], loose=loose)
                return pat, back
        return wildcard_pat(retail[off - 16:off + 16], loose=loose), 16

    def place(off):
        """(build_off, why) for a file offset the map could not."""
        va = 0x400c00 + off
        for d in range(1, 7):           # an operand: map its instruction
            t, _how = votrans.translate(va - d)
            if t is not None:
                cand = t - 0x400c00 + d
                if wildcard_pat(retail[off:off + 8]).match(other, cand):
                    return cand, 'via instruction'
        fi = enclosing(va)
        if fi is None:
            return None, 'no enclosing function'
        fs = A[fi]['s']
        bs = fmap.get(fs)
        if bs is None:
            bs = func_start_via_callers(fs)
            if bs is None:
                # reached without an e8 anywhere the map knows: place the
                # site by its own surroundings, over the whole build
                pat = wildcard_pat(retail[off - 8:off + 32])
                hits = [m.start() + 8 for m in pat.finditer(other)]
                if len(hits) == 1:
                    return hits[0], 'global signature'
                return None, ('function unmatched, no caller vote, '
                              '%d global hits' % len(hits))
        cand = (bs - 0x400c00) + (off - (fs - 0x400c00))
        pat = wildcard_pat(retail[off:off + 12])
        if pat.match(other, cand):
            return cand, 'linear'
        if other[cand:cand + 8] == retail[off:off + 8]:
            return cand, 'linear, literal bytes'
        lo = bs - 0x400c00
        hi = lo + (A[fi]['e'] - fs) + 0x400
        for loose in (False, True):
            pat, back = ctx_pat(off, loose)
            hits = [lo + m.start() + back
                    for m in pat.finditer(other[lo - back:hi])]
            if len(hits) == 1:
                return hits[0], 'context window' + (' loose' if loose else '')
        return None, 'function found, no unique context'

    def resolve_va(va):
        t, how = votrans.translate(va)
        if t is not None:
            if va >= 0x63f000 + 0x400c00 or \
                    same_shape(va - 0x400c00, t - 0x400c00):
                return t
        fi = enclosing(va)
        if fi is not None and A[fi]['s'] == va:
            bs = func_start_via_callers(va)
            if bs is not None:
                if same_shape(va - 0x400c00, bs - 0x400c00) \
                        or other[bs - 0x400c00:bs - 0x400c00 + 3] \
                        == b'\x55\x8b\xec':
                    return bs
        bo, why = place(va - 0x400c00)
        return None if bo is None else bo + 0x400c00

    return place, resolve_va


def main():
    sys.argv = ['votrans', 'one'] + sys.argv[1:]
    import votrans                                       # noqa: E402
    import vo_patch_hires as hires                       # noqa: E402
    other = open(votrans.M['other'], 'rb').read()
    retail = open(votrans.M['retail'], 'rb').read()
    place, resolve_va = build_resolvers(votrans, retail, other)

    def stamp(data):
        pe = struct.unpack_from('<I', data, 0x3c)[0]
        return struct.unpack_from('<I', data, pe + 8)[0]

    fails = []

    def pool_sites():
        """The three cmp eax,2500 copies and their two pool leas, plus the
        render-list insert loads, paired by order of appearance."""
        out = {}
        cmp_pat = b'\x3d\xc4\x09\x00\x00\x0f\x8d'
        r_hits = [m.start() for m in re.finditer(re.escape(cmp_pat), retail)]
        o_hits = [m.start() for m in re.finditer(re.escape(cmp_pat), other)]
        if len(r_hits) == len(o_hits):
            for r, o in zip(r_hits, o_hits):
                out[r + 1] = o + 1                       # the 2500 itself
                for delta, opc in ((0xb, b'\x8d\x15'),   # lea edx, [pool]
                                   (0x13, b'\x8d\x0c\xc5')):  # lea ecx
                    if retail[r + delta:r + delta + len(opc)] == opc \
                            and other[o + delta:o + delta + len(opc)] == opc:
                        out[r + delta + len(opc)] = o + delta + len(opc)
        for pat_r in (re.compile(re.escape(b'\x8b\x34\x9d') + b'....'
                                 + re.escape(b'\x89\x0c\x9d'), re.S),):
            r_hits = [m.start() for m in pat_r.finditer(retail)]
            o_hits = [m.start() for m in pat_r.finditer(other)]
            if len(r_hits) == len(o_hits):
                for r, o in zip(r_hits, o_hits):
                    out[r] = o
        # mov [ebp-0x50], 480 in the GDI strip routine: the map knows the
        # function but not this row, and 480 alone is everywhere
        pat = re.compile(re.escape(b'\xc7') + b'(?:\x45.|\x85....)'
                         + re.escape(b'\xe0\x01\x00\x00\xf6\x05')
                         + b'....' + re.escape(b'\x04'), re.S)
        def imm(m):
            return m.start() + (3 if m.group(0)[1] == 0x45 else 6)
        r = [imm(m) for m in pat.finditer(retail)]
        o = [imm(m) for m in pat.finditer(other)]
        # hits the map already places pair themselves; the leftovers pair
        # in order, counts permitting
        left_r, left_o = [], list(o)
        for roff in r:
            jo, _how = votrans.translate_off(roff)
            if jo is not None and jo in left_o:
                left_o.remove(jo)
            else:
                left_r.append(roff)
        if len(left_r) == len(left_o):
            out.update(dict(zip(left_r, left_o)))
        # the coverage-mask advances: the span's own bytes with the mask
        # pointer swapped for the build's, paired in order
        mp_r = struct.pack('<I', hires.ADDR['MASKPTR'])
        mp_o = struct.pack('<I', va_map[hires.ADDR['MASKPTR']])
        done = set()
        for off, n, _between in hires.MASK_ADVANCE:
            span = retail[off:off + n]
            if mp_r not in span or n in done:
                continue
            done.add(n)
            pr = re.compile(re.escape(span), re.S)
            po = re.compile(re.escape(span).replace(re.escape(mp_r),
                                                    re.escape(mp_o)), re.S)
            rh = [m.start() for m in pr.finditer(retail)]
            oh = [m.start() for m in po.finditer(other)]
            same = [o2 for o2, n2, _b in hires.MASK_ADVANCE
                    if retail[o2:o2 + n2] == span]
            if sorted(same) == rh and len(rh) == len(oh):
                for r2, o2 in zip(rh, oh):
                    out[r2] = o2
        # twin code: a site whose surroundings occur more than once in
        # retail (the two renderer copies) is paired by order of
        # occurrence with the same surroundings in the build, since the
        # map collapses identical copies onto one of them
        for off, old, new in sites:
            if off in out or off > 0x1f4400:
                continue
            ln = len(old) if old is not None else len(new)
            back = min(off, 12)
            pat = wildcard_pat(retail[off - back:off + ln + 12])
            rh = [m.start() + back for m in pat.finditer(retail)]
            if len(rh) < 2 or off not in rh:
                continue
            oh = [m.start() + back for m in pat.finditer(other)]
            if len(oh) == len(rh):
                out[off] = oh[rh.index(off)]
        # pmaddwd interleave in the MMX rasteriser: the span-buffer
        # advance immediate rides at +13
        pm = re.compile(re.escape(b'\x0f\xf5\xe1\xa1') + b'....'
                        + re.escape(b'\x0f\xf5\xf3\x81\xc6') + b'....'
                        + re.escape(b'\x0f\xf5\xcd\xc1\xe0\x10'), re.S)
        rh = [m.start() + 13 for m in pm.finditer(retail)]
        oh = [m.start() + 13 for m in pm.finditer(other)]
        if len(rh) == len(oh):
            out.update(dict(zip(rh, oh)))
        # flush-bucket reads: dec edi; jl; mov esi,[edi*4+heads];
        # mov ebx,[esi+4]
        fb = re.compile(re.escape(b'\x4f\x0f\x8c') + b'....'
                        + re.escape(b'\x8b\x34\xbd') + b'....'
                        + re.escape(b'\x8b\x5e\x04'), re.S)
        rh = [m.start() + 10 for m in fb.finditer(retail)]
        oh = [m.start() + 10 for m in fb.finditer(other)]
        if len(rh) == len(oh):
            out.update(dict(zip(rh, oh)))
        # the strip loaders' watermark stores: cdq; and edx,0x7f;
        # add eax,edx; sar eax,7; mov [watermark],eax. 48 per build,
        # 24 to each engine's pair of globals; the first engine's,
        # in order
        wm = re.compile(re.escape(b'\x99\x83\xe2\x7f\x03\xc2\xc1\xf8'
                                  b'\x07\xa3') + b'(....)', re.S)

        def first_engine(data):
            hits = [(m.start() + 1, m.group(1)) for m in wm.finditer(data)]
            globs = set()
            for _o, g in hits:
                if len(globs) < 2:
                    globs.add(g)
            return [o for o, g in hits if g in globs]
        rh, oh = first_engine(retail), first_engine(other)
        if len(rh) == len(oh) == 24:
            out.update(dict(zip(rh, oh)))
        # pool freelist walk: mov edx,[eax+4]; mov eax,[eax];
        # mov [ebx*4+pool],edx; dec ebx; test eax,eax
        fl = re.compile(re.escape(b'\x8b\x50\x04\x8b\x00\x89\x14\x9d')
                        + b'....' + re.escape(b'\x4b\x85\xc0'), re.S)
        rh = [m.start() + 8 for m in fl.finditer(retail)]
        oh = [m.start() + 8 for m in fl.finditer(other)]
        if len(rh) == len(oh):
            out.update(dict(zip(rh, oh)))
        return out


    # Every named address hires bakes into new bytes, the ui blob, or its
    # hook tables. Parsed out of vo_patch.py's ADDR dict so the two cannot
    # drift: this script only translates what the runtime will ask for.
    va_map = {}
    targets = {('ADDR:' + n): v for n, v in hires.ADDR.items()}
    for o, v in hires.UI_REFS:
        targets.setdefault('blob+0x%x' % o, v)
    for name, va in sorted(targets.items()):
        t = resolve_va(va) if va < 0x63f000 + 0x400000 else None
        if t is None:
            t, how = votrans.translate(va)
        if t is None:
            fails.append('address %s 0x%x: %s' % (name, va, how))
            continue
        va_map[va] = t

    # The coverage-mask pointer, from the build's own advance spans: the
    # sixteen mov/add/mov idioms share one global, and that is it. The
    # map's vote once landed on a neighbouring global for JP, and every
    # mask site went wrong from there.
    seen = {}
    for pat in (rb'\xa1(....)\x46\x83\xc0\x50\x5b\xa3(....)',
                rb'\xa1(....)\x83\xc0\x50\x46\xa3(....)'):
        for m in re.finditer(pat, other, re.S):
            if m.group(1) == m.group(2):
                p = struct.unpack('<I', m.group(1))[0]
                seen[p] = seen.get(p, 0) + 1
    if len(seen) == 1 and list(seen.values())[0] == 16:
        va_map[hires.ADDR['MASKPTR']] = next(iter(seen))
    else:
        fails.append('mask pointer: spans found %s' %
                     {hex(k): v for k, v in seen.items()})

    # The sites, at a representative size: offsets and originals do not
    # depend on it.
    sites = hires.build_sites(1920, 1080, 0x36c0000, 0x37c0000, 0x38c0000,
                              (0x39c0000, hires.HIRES_POLYS))
    ORDERED = pool_sites()

    # The four 2D-layer call targets, from the build's own call sites:
    # the engines are near-identical code and the map can collapse them,
    # which pairs one engine's pre with the other's post.
    calls = {}
    for (roff, _site, _stub), name in zip(
            hires.UI_STUBS, ('CALL_PRE1', 'CALL_POST1',
                             'CALL_PRE2', 'CALL_POST2')):
        jo, _how = votrans.translate_off(roff)
        if jo is None or other[jo:jo + 1] != b'\xe8':
            fails.append('%s: stub site 0x%x untranslatable' % (name, roff))
            continue
        rel = struct.unpack_from('<i', other, jo + 1)[0]
        calls[hires.ADDR[name]] = 0x400c00 + jo + 5 + rel
    if len(set(calls.values())) != len(calls):
        fails.append('2D call targets collapsed: %s'
                     % {hex(k): hex(v) for k, v in calls.items()})
    va_map.update(calls)


    off_map, old_map = {}, {}
    manual = MANUAL.get('%08x' % stamp(other), {})
    absent = []
    for off, old, new in sites:
        if off in manual:
            if manual[off] is None:
                absent.append(off)
                continue
            off_map[off] = manual[off]
            n = len(old) if old is not None else len(new)
            old_map[off] = other[manual[off]:manual[off] + n].hex()
            continue
        if 0x400c00 + off in va_map:      # a named address: keep them equal
            jo, how = va_map[0x400c00 + off] - 0x400c00, 'named'
        elif off in ORDERED:
            # anchored on the build's own bytes, so it outranks the map,
            # which collapses identical code copies onto one of them
            jo, how = ORDERED[off], 'ordered pattern'
        else:
            jo, how = votrans.translate_off(off)
        if jo is None:
            jo, why = place(off)
            if jo is None:
                fails.append('site 0x%06x: %s; %s' % (off, how, why))
                continue
        n = len(old) if old is not None else len(new)
        off_map[off] = jo
        old_map[off] = other[jo:jo + n].hex()

    seen = {}
    for off, jo in off_map.items():
        if jo in seen:
            fails.append('sites 0x%06x and 0x%06x both map to 0x%06x'
                         % (seen[jo], off, jo))
        seen[jo] = off

    # Pass prologue lengths, before the gate below: a function with no
    # clean 5-byte boundary must fail the run, not print a table.
    lens = {}
    for n, (site, ln) in enumerate(hires.UI_PASS_FUNCS):
        bva = va_map.get(site)
        if bva is None:
            continue
        take = 0
        for ins in _md.disasm(other[bva - 0x400c00:bva - 0x400c00 + 20],
                              0):
            take = ins.address + ins.size
            if take >= 5:
                break
        if take < 5:
            fails.append('PASS%d 0x%x: no 5-byte boundary' % (n, site))
        elif take != ln:
            lens[site] = take

    if fails:
        for f in fails:
            print('FAIL', f)
        sys.exit(1)

    print("    '%08x': {                       # %s" %
          (stamp(other), os.path.basename(votrans.M['other'])))
    print("        'va': {")
    for va in sorted(va_map):
        print("            0x%08x: 0x%08x," % (va, va_map[va]))
    print("        },")
    print("        'off': {")
    for off in sorted(off_map):
        print("            0x%06x: (0x%06x, '%s')," %
              (off, off_map[off], old_map[off]))
    print("        },")
    if lens:
        print("        'passlen': {")
        for site in sorted(lens):
            print("            0x%08x: %d," % (site, lens[site]))
        print("        },")
    if absent:
        print("        'absent': (%s,)," %
              ', '.join('0x%06x' % a for a in sorted(absent)))
    print("    },")


if __name__ == '__main__':
    main()
