#!/usr/bin/env python3
"""Write a build's site map into vo_patch.py.

    python3 tools/buildsites.py NAME RETAIL.exe OTHER.exe [MAP.pkl]

NAME is the Build's name in vo_patch.py (JAPAN). For every site the table
names by retail offset: where it is in the other build, through votrans.py,
and the bytes that build has there. A site whose retail original is not
what the retail file holds is one an earlier site in the same patch wrote,
so its original is kept as the table has it. The result goes between the
`# SITES NAME BEGIN` and `END` markers, which must exist.
"""
import os
import re
import struct
import sys
import importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

TARGET = os.path.join(ROOT, 'vo_patch.py')


def main():
    name, retail_path, jp_path = sys.argv[1], sys.argv[2], sys.argv[3]
    sys.argv = ['votrans', 'one'] + sys.argv[4:]
    import votrans                                       # noqa: E402
    os.environ['VO_PATCH_BOOTSTRAP'] = '1'      # a site may be missing here
    spec = importlib.util.spec_from_file_location('vp', TARGET)
    vp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(vp)
    retail = open(retail_path, 'rb').read()
    jp = open(jp_path, 'rb').read()

    rows, problems = [], []
    for key, _label, _tip, sites in vp.FEATURES:
        for off, orig, new in sites:
            if isinstance(off, (vp.At, vp.In)):
                continue
            off = int(off)
            o = bytes.fromhex(str(orig))
            jo, how = votrans.translate_off(off)
            if off in votrans.MANUAL:
                jo, how = votrans.MANUAL[off], 'manual'
                if jo is None:                # the build has no such code
                    rows.append((off, None, None, key, how, False))
                    continue
            if jo is None and len(o) == 4:
                # a code pointer in a table: find the translated target
                p = struct.unpack('<I', o)[0]
                if vp.RETAIL.va(0x400) <= p:
                    q, _ = votrans.translate(p)
                    if q is not None:
                        pat = struct.pack('<I', q)
                        lo = max(0, off - 0x8000)
                        hits = [lo + m.start() for m in
                                re.finditer(re.escape(pat), jp[lo:off + 0x8000])]
                        prev = rows[-1] if rows and rows[-1][0] == off - 4 else None
                        if len(hits) == 1:
                            jo, how = hits[0], 'ptr'
                        elif prev and prev[1] + 4 in hits:
                            jo, how = prev[1] + 4, 'ptr next'
                        elif len(hits) > 1:
                            # the entry before it settles which table
                            po = struct.unpack_from('<I', retail, off - 4)[0]
                            pq, _ = votrans.translate(po)
                            ph = [h for h in hits if pq is not None and
                                  struct.unpack_from('<I', jp, h - 4)[0] == pq]
                            if len(ph) == 1:
                                jo, how = ph[0], 'ptr by prev'
            if jo is None and len(o) >= 4:
                # the same bytes, once, in the other file
                hits = [m.start() for m in re.finditer(re.escape(o), jp)]
                if len(hits) == 1:
                    jo, how = hits[0], 'raw unique'
            if jo is None:
                problems.append('%-11s 0x%08x %s' % (key, off, how))
                continue
            own = retail[off:off + len(o)] != o
            jorig = o.hex() if own else jp[jo:jo + len(o)].hex()
            rows.append((off, jo, jorig, key, how, own))
    if problems:
        raise SystemExit('unplaced:\n  ' + '\n  '.join(problems))

    out = ['%s.sites = {\n' % name]
    for off, jo, jorig, key, how, own in rows:
        if jo is None:
            out.append("    0x%08x: None,  # %s\n" % (off, key))
            continue
        note = '  # %s' % 'written by an earlier site' if own else ''
        if len(jorig) <= 40:
            out.append("    0x%08x: (0x%08x, '%s'),%s\n" % (off, jo, jorig, note))
        else:
            out.append("    0x%08x: (0x%08x,%s\n" % (off, jo, note))
            for i in range(0, len(jorig), 64):
                out.append("        '%s'\n" % jorig[i:i + 64])
            out.append("    ),\n")
    out.append('}\n')
    text = open(TARGET, encoding='utf-8').read()
    new, n = re.subn(r'(# SITES %s BEGIN\n).*?(# SITES %s END)' % (name, name),
                     lambda m: m.group(1) + ''.join(out) + m.group(2),
                     text, flags=re.S)
    if n != 1:
        raise SystemExit('# SITES %s BEGIN / END markers not found' % name)
    open(TARGET, 'w', encoding='utf-8').write(new)
    print('%d sites written' % len(rows))


if __name__ == '__main__':
    main()
