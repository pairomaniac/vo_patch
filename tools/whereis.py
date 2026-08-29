#!/usr/bin/env python3
"""What is at a virtual address in a patched game, for reading a crash.

    python3 tools/whereis.py path/to/v_on.exe 0x36af3a1 [0x...]

The file is the patched executable (its .bak beside it decides the build).
Each address is reported as ours - which blob, how far in, the nearest
label before it - or as the game's own code with the section and the
offset in the file, which docs/NOTES.md's tools can take further.
"""
import hashlib
import importlib.util
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location(
    'vp', os.path.join(os.path.dirname(HERE), 'vo_patch.py'))
vp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vp)


def build_of(path):
    for candidate in (path + '.bak', path):
        try:
            digest = hashlib.md5(open(candidate, 'rb').read()).hexdigest()
        except OSError:
            continue
        if digest in vp.BUILDS:
            return vp.BUILDS[digest]
    raise SystemExit('neither %s nor its .bak is a build this knows' % path)


def sections(data):
    pe = struct.unpack_from('<I', data, 0x3c)[0]
    nsec = struct.unpack_from('<H', data, pe + 6)[0]
    optsz = struct.unpack_from('<H', data, pe + 20)[0]
    out = []
    for i in range(nsec):
        name, vs, va, rs, ro = struct.unpack_from(
            '<8sIIII', data, pe + 24 + optsz + 40 * i)
        out.append((name.rstrip(b'\0').decode(), 0x400000 + va, vs, ro))
    return out


def main():
    path = sys.argv[1]
    build = build_of(path)
    data = open(path, 'rb').read()
    secs = sections(data)

    # every blob's place: caves, the annex, and the two apply-time sections
    places = {}
    for name in vp.BLOBS:
        try:
            places[name] = vp.cave_va(name, build)
        except KeyError:
            pass
    voxt = next((va for n, va, vs, ro in secs if n == '.voxt'), None)
    if voxt is not None:
        places['EXTRAS_TPL'] = voxt
        places['VOXT'] = voxt + len(vp.EXTRAS_TPL) + (-len(vp.EXTRAS_TPL)) % 16
    vocd = next((va for n, va, vs, ro in secs if n == '.vocd'), None)
    if vocd is not None:
        places['VOCD'] = vocd

    for arg in sys.argv[2:]:
        va = int(arg, 16)
        hit = None
        for name, at in places.items():
            length = len(vp.BLOBS[name][0]) if name in vp.BLOBS else (
                len(vp.EXTRAS_TPL) if name == 'EXTRAS_TPL' else len(vp.VOCD_CODE))
            if at <= va < at + length:
                hit = (name, at)
        if hit:
            name, at = hit
            labels = vp.BLOBS[name][2] if name in vp.BLOBS else {}
            before = [(o, l) for l, o in labels.items() if o <= va - at]
            near = max(before)[1] + '+0x%x' % (va - at - max(before)[0]) \
                if before else '+0x%x' % (va - at)
            print('%08x  ours: %s at %08x, %s' % (va, name, at, near))
            continue
        for name, start, vs, ro in secs:
            if start <= va < start + vs:
                print('%08x  game: %s+0x%x, file offset 0x%x'
                      % (va, name, va - start, ro + va - start))
                break
        else:
            print('%08x  outside the image' % va)


if __name__ == '__main__':
    main()
