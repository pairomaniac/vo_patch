#!/usr/bin/env python3
"""Run hiresport.py's resolution without its gate and hand back the maps.

    import portdump; P = portdump.run('maps/jp.pkl')
    P['off'] retail_off -> (build_off, how), P['va'], P['fails'], P['absent']

For auditing what the generator would place, including on a build whose
table it refuses to print."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))


def run(pkl):
    src = open(os.path.join(HERE, 'hiresport.py')).read()
    # keep 'how' beside the offset, drop the gate and the printing
    src = src.replace("        off_map[off] = jo\n",
                      "        off_map[off] = jo\n        how_map[off] = how\n")
    src = src.replace("    off_map, old_map = {}, {}\n",
                      "    off_map, old_map, how_map = {}, {}, {}\n")
    src = src.replace("            off_map[off] = manual[off]\n",
                      "            off_map[off] = manual[off]\n"
                      "            how_map[off] = 'manual'\n")
    src = src.replace("            jo, why = place(off)\n",
                      "            jo, why = place(off)\n"
                      "            how = why\n")
    i = src.index("    if fails:\n        for f in fails:")
    src = src[:i] + ("    return dict(off={o: (off_map[o], how_map[o]) "
                     "for o in off_map}, old=old_map, va=va_map, "
                     "fails=fails, absent=absent, passlen=lens, "
                     "sites=sites, ordered=ORDERED)\n\n\n"
                     "if __name__ == '__main__':\n    main()\n")
    ns = {'__name__': 'portdump_inner', '__file__':
          os.path.join(HERE, 'hiresport.py')}
    sys.argv = ['hiresport', pkl]
    for m in ('votrans', 'vo_patch_hires'):
        sys.modules.pop(m, None)
    exec(compile(src, 'hiresport.py', 'exec'), ns)
    return ns['main']()
