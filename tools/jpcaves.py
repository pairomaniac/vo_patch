"""Pick caves in the Japanese rerelease for every blob the retail build sites.

    python3 tools/jpcaves.py path/to/jp/v_on.exe

Zero runs in .rdata (and .rsrc for the movie stub), dword-aligned, usable up
to the first thing .reloc points at inside them, with nothing .reloc points
at and no code pointer in the 64 bytes before them - a run of zeros after a
code pointer is the NULL tail of a handler table, and the game calls through
those. Longest blob first, best fit, the rest of a run going back into the
pool. What it prints is the JAPAN caves table in vo_patch.py.
"""
import sys, re, bisect, importlib.util, os, struct

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from vomap import Exe, BASE                                  # noqa: E402

jp = Exe(sys.argv[1])
spec = importlib.util.spec_from_file_location(
    'vp', os.path.join(os.path.dirname(HERE), 'vo_patch.py'))
vp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vp)

targets = sorted(set(jp.u32(r) for r in jp.relocs))


def runs(secname, minlen):
    n, va0, vs, ro, rs = jp.sec[secname]
    out = []
    for m in re.finditer(rb'\x00{%d,}' % minlen, jp.d[ro:ro + rs]):
        start = BASE + va0 + m.start()
        end = BASE + va0 + m.end()
        a = (start + 3) & ~3                     # dword-align the start
        # nothing may point inside
        i = bisect.bisect_left(targets, a)
        inside = [t for t in targets[i:] if t < end]
        # the nearest pointer target before the run, and whether a code
        # pointer sits in the 64 bytes before it: a run of zeros after one
        # is the NULL tail of a handler table, and the game calls through
        # those slots. The rerelease's nameentry cave was one.
        before = targets[i - 1] if i else 0
        padding = start >= BASE + va0 + vs        # section padding: no field in front
        o = jp.off(a)                             # from the aligned start: a run
        after_code = any(                         # often begins inside a pointer
            jp.text_lo <= struct.unpack_from('<I', jp.d, o - k)[0] < jp.text_hi
            for k in range(4, 65, 4))
        # usable up to the first thing that points inside
        top = inside[0] if inside else end
        out.append(dict(sec=secname, start=a, end=top, free=top - a,
                        inside=inside, gap=a - before, padding=padding,
                        after_code=after_code))
    return out


def allocate(needs):
    """needs: [(name, length, section)] -> {name: va}. Longest first, best
    fit; a run is used once, and never one something points into."""
    pool = [r for s in ('.rdata', '.rsrc') for r in runs(s, 24)]
    out, notes = {}, {}
    for name, length, sec in sorted(needs, key=lambda n: -n[1]):
        cands = [r for r in pool if r['sec'] == sec and r['free'] >= length + 1
                 and (r['padding'] or r.get('ours')
                      or (r['gap'] >= 64 and not r['after_code']))]
        if not cands:
            notes[name] = 'NO CAVE for %d bytes in %s' % (length, sec)
            continue
        r = min(cands, key=lambda r: r['free'])
        out[name] = r['start']
        notes[name] = '%s run %x..%x (%d free, %d gap before%s)' % (
            r['sec'], r['start'], r['end'], r['free'], r['gap'],
            ', padding' if r['padding'] else '')
        pool.remove(r)
        # the rest of the run goes back, dword-aligned, after a 4-byte gap
        rest = dict(r)
        rest['start'] = (r['start'] + length + 4 + 3) & ~3
        rest['free'] = rest['end'] - rest['start']
        rest['gap'] = 4
        rest['ours'] = True                  # what precedes it is our own blob
        if rest['free'] >= 24:
            pool.append(rest)
    return out, notes


if __name__ == '__main__':
    needs = []
    for name, (code, fix, labels) in vp.BLOBS.items():
        if name not in vp.RETAIL.caves or name in ('LEVERS', 'PAD_DEVLIST'):
            continue
        sec = '.rsrc' if name == 'MOVIE' else '.rdata'
        length = len(code) + (len(vp.BLOBS['LEVERS'][0]) if name == 'PADX' else 0)
        needs.append((name, length, sec))
    caves, notes = allocate(needs)
    for name, length, sec in sorted(needs, key=lambda n: -n[1]):
        print('%-14s %4d  %s' % (name, length, notes[name]))
    print(len(caves), 'of', len(needs), 'placed')
