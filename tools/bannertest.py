#!/usr/bin/env python3
"""Apply the banner to real game files and read it back.

    python3 tools/bannertest.py /path/to/VIRTUAL-ON

CI cannot do this - neither v_on.exe nor escrgame.bin is in the repository -
so run it by hand before tagging, the same as selftest.py.

--selfcheck already proves the bitmap is the right size and that every tile
index is in range. What it cannot prove is that the two halves line up once
written: the executable holds the tile indices and escrgame.bin holds the
tiles, and a wrong offset in either draws the title prompt as scrambled
letters while every other check passes.

So this patches copies of both, decodes the banner back the way the game's
renderer does, and compares it against the bitmap it started from. It also
restores and checks both files come back byte for byte.
"""

import hashlib
import importlib.util
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PATCHER = os.path.join(os.path.dirname(HERE), 'vo-patch.py')


def pristine(path, want):
    """The unmodified file, from `path` or from the backup beside it.

    A development copy of the game is usually patched, which is the whole
    point of having one. Falling back to the .bak means the same folder can
    be played in and tested against without restoring it first."""
    for candidate in (path, path + '.bak'):
        try:
            with open(candidate, 'rb') as fh:
                data = fh.read()
        except OSError:
            continue
        if hashlib.md5(data).hexdigest() == want:
            return data, candidate
    return None, None


def load_patcher():
    spec = importlib.util.spec_from_file_location('vopatch', PATCHER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)             # runs _check_table
    return module


def decode(vp, exe, art):
    """The banner as the game would draw it: indices from the executable,
    tiles from the asset, laid out into a 336x24 bitmap."""
    table = []
    for i in range(vp.BANNER_W * vp.BANNER_H):
        off = vp.BANNER_TABLE + i * 2
        table.append(exe[off] | (exe[off + 1] << 8))
    rows = vp.BANNER_H * 8
    cols = vp.BANNER_W * 8
    out = bytearray(rows * cols)
    for cell, value in enumerate(table):
        tile = vp.BANNER_TILE_BASE + value
        base = tile * 128
        if base + 128 > len(art):
            raise AssertionError('cell %d points at tile %d, past the end of '
                                 '%s' % (cell, tile, vp.ESCRGAME))
        cy, cx = divmod(cell, vp.BANNER_W)
        for y in range(8):
            for x in range(8):
                lo = art[base + (y * 8 + x) * 2]
                hi = art[base + (y * 8 + x) * 2 + 1]
                out[(cy * 8 + y) * cols + cx * 8 + x] = 1 if (lo | hi) else 0
    return bytes(out)


def expected(vp):
    """The same bitmap, straight out of the script."""
    cols = vp.BANNER_W * 8
    out = bytearray(vp.BANNER_H * 8 * cols)
    for i in range(len(out)):
        out[i] = vp.BANNER_BITS[i >> 3] >> (7 - (i & 7)) & 1
    return bytes(out)


def render(bitmap, cols):
    """Rough picture, so a failure shows what went wrong rather than just
    reporting a count."""
    lines = []
    for y in range(0, len(bitmap) // cols, 2):
        row = bitmap[y * cols:(y + 1) * cols]
        lines.append(''.join('#' if row[x] else '.'
                             for x in range(0, cols, 4)))
    return lines


def main(gamedir):
    vp = load_patcher()
    if os.path.isfile(gamedir):
        gamedir = os.path.dirname(gamedir)
    exe_src = os.path.join(gamedir, 'v_on.exe')
    art_src = os.path.join(gamedir, vp.ESCRGAME)
    for path in (exe_src, art_src):
        if not os.path.exists(path):
            return 'not found: %s' % path

    exe_before, exe_used = pristine(exe_src, vp.ORIGINAL_MD5)
    art_before, art_used = pristine(art_src, vp.ESCRGAME_MD5)
    for path, data in ((exe_src, exe_before), (art_src, art_before)):
        if data is None:
            return ('%s is not the original and there is no %s.bak holding '
                    'it' % (path, os.path.basename(path)))
    for src, used in ((exe_src, exe_used), (art_src, art_used)):
        if used != src:
            print('note: read %s, not the patched file beside it'
                  % os.path.basename(used))

    work = tempfile.mkdtemp(prefix='vo-banner-')
    try:
        exe = os.path.join(work, 'v_on.exe')
        art = os.path.join(work, vp.ESCRGAME)
        with open(exe, 'wb') as fh:
            fh.write(exe_before)
        with open(art, 'wb') as fh:
            fh.write(art_before)

        patcher = vp.Patcher()
        note, ok = patcher.load(exe)
        if not ok:
            return 'the patcher refused the copy: %s' % note
        ok, log = patcher.apply({'padxinput': True})
        if not ok:
            return 'apply failed: %s' % '; '.join(log[-2:])
        print('applied: %s' % ', '.join(l for l in log if 'Wrote' in l))

        with open(exe, 'rb') as fh:
            exe_after = fh.read()
        with open(art, 'rb') as fh:
            art_after = fh.read()

        got = decode(vp, exe_after, art_after)
        want = expected(vp)
        cols = vp.BANNER_W * 8
        wrong = sum(1 for a, b in zip(got, want) if a != b)
        print('banner: %d tiles, %d of %d pixels differ'
              % (len(vp.BANNER_TILES), wrong, len(want)))
        if wrong:
            print('  read back from the patched files:')
            for line in render(got, cols):
                print('    %s' % line)
            return 'FAILED - the banner does not read back as it was written'

        untouched = sum(1 for a, b in zip(art_before, art_after) if a != b)
        print('escrgame.bin: %d bytes changed of %d'
              % (untouched, len(art_before)))

        for line in patcher.restore():
            print('restore: %s' % line)
        with open(exe, 'rb') as fh:
            exe_back = fh.read()
        with open(art, 'rb') as fh:
            art_back = fh.read()
        if exe_back != exe_before:
            return 'FAILED - v_on.exe did not restore byte for byte'
        if art_back != art_before:
            return 'FAILED - %s did not restore byte for byte' % vp.ESCRGAME
        print('restore: both files byte for byte')
    finally:
        shutil.rmtree(work, ignore_errors=True)

    print('OK')
    return None


if __name__ == '__main__':
    if len(sys.argv) != 2:
        sys.exit('Usage: python3 tools/bannertest.py /path/to/VIRTUAL-ON')
    sys.exit(main(sys.argv[1]))
