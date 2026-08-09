#!/usr/bin/env python3
"""Rewrite the Virtual-On title screen's Press Space Bar banner.

    python3 vonbanner.py /path/to/VIRTUAL-ON                 # preview only
    python3 vonbanner.py /path/to/VIRTUAL-ON --write
    python3 vonbanner.py /path/to/VIRTUAL-ON --restore
    python3 vonbanner.py /path/to/VIRTUAL-ON --text 'Press Start'

The banner is 42x3 cells of 8x8 pixels, 16bpp RGB565, drawn from tile bank B.
Two pieces change together:

  escrgame.bin  0x21c000  the artwork, 128 bytes per tile
  v_on.exe      0x269b60  126 entries of 16 bits, one tile index per cell

The executable's table holds indices relative to the bank; the loader adds
0x380 to each at start-up, so the values written here are 0-based.

Everything is rendered from a font, so the result is consistent across the
whole line and does not depend on what the original said.

Both files are backed up beside themselves on the first write.
"""

import argparse
import os
import shutil
import sys
import numpy as np
from PIL import Image, ImageDraw, ImageFont

# --- where things live -----------------------------------------------------
TILE_OFF = 0x21C000          # escrgame.bin, first tile slot of this banner
TILE_BASE = 17280            # its tile index within the file
TILE_MAX = 109               # slots this banner owns
SPILL_TILE = 24845           # a run of 116 empty tiles further into the file
SPILL_MAX = 116              # anything past TILE_MAX goes there instead
TABLE_OFF = 0x269B60         # v_on.exe, file offset of the index table
COLS, ROWS = 42, 3
EXE_SIZE = 6650880
GAME_SIZE = 4194304

# --- metrics measured from the original, so a new line sits the same -------
INK = 0xFCA0                 # the orange it uses
CAP_TOP, CAP_H = 2, 20       # cap height occupies rows 2..21 of 24
WORD_GAP = 5                 # pixels between words
TARGET_INK = 0.498           # fraction of the box that is ink

FONT_NAMES = ('DejaVuSans-BoldOblique.ttf', 'DejaVuSans-BoldOblique.otf',
              'FreeSansBoldOblique.ttf', 'Carlito-BoldItalic.ttf',
              'LiberationSans-BoldItalic.ttf', 'DejaVuSans-Bold.ttf')
FONT_DIRS = ('/usr/share/fonts', '/usr/local/share/fonts',
             os.path.expanduser('~/.local/share/fonts'),
             os.path.expanduser('~/.fonts'))


def find_font():
    """Distributions disagree about where fonts live, so look rather than
    hardcode. A bold oblique is wanted; upright bold is a poor last resort
    but better than failing."""
    for name in FONT_NAMES:
        for root in FONT_DIRS:
            for dirpath, _dirs, files in os.walk(root):
                if name in files:
                    return os.path.join(dirpath, name)
    for root in FONT_DIRS:
        for dirpath, _dirs, files in os.walk(root):
            for fn in sorted(files):
                low = fn.lower()
                if low.endswith(('.ttf', '.otf')) and 'bold' in low and \
                        ('oblique' in low or 'italic' in low):
                    return os.path.join(dirpath, fn)
    return None


def widen(mask, n):
    """Thicken horizontally by n pixels. The original face is heavier than
    any bold oblique to hand, so the strokes need help.

    Spread both ways rather than only right: a one-sided dilation fills in
    the space under a right-leaning diagonal and squares it off, which is
    very visible on a letter like A. An OR of shifted copies, to avoid
    pulling in scipy."""
    right = n // 2
    left = n - right
    out = mask.copy()
    for k in range(1, right + 1):
        out[:, k:] |= mask[:, :-k]
    for k in range(1, left + 1):
        out[:, :-k] |= mask[:, k:]
    return out


def word_bitmap(text, font_path, size, track, dilate, ss):
    """One word as a tight bitmap, rendered large and thickened."""
    f = ImageFont.truetype(font_path, size * ss)
    big = Image.new('L', (6000, 300), 0)
    d = ImageDraw.Draw(big)
    x = 0
    for ch in text:
        d.text((x, 20), ch, font=f, fill=255)
        x += f.getlength(ch) - track * ss
    a = np.asarray(big) > 110
    if not a.any():
        raise SystemExit('nothing rendered for %r' % text)
    ys, xs = np.flatnonzero(a.any(axis=1)), np.flatnonzero(a.any(axis=0))
    crop = a[ys[0]:ys[-1] + 1, xs[0]:xs[-1] + 1]
    if dilate:
        # Pad both sides first: widen() spreads either way, and without room
        # for it the outermost glyphs lose an edge.
        pad = dilate * ss
        crop = np.pad(crop, ((0, 0), (pad, pad)))
        crop = widen(crop, pad)
        x2 = np.flatnonzero(crop.any(axis=0))
        crop = crop[:, x2[0]:x2[-1] + 1]
    return crop


def scale_to(crop, w, h):
    im = Image.fromarray((crop * 255).astype(np.uint8))
    return np.asarray(im.resize((max(1, w), h), Image.BOX)) > 118


def compose(text, font_path, size, track, dilate, gap, ss=4):
    """The whole line laid out at supersampled resolution and scaled once.

    Scaling each word separately rounds its width independently, which
    resamples the glyphs at slightly different ratios and eats a column off
    the narrow ones. One bitmap, one resize."""
    words = [w for w in text.split(' ') if w]
    blocks = [word_bitmap(w, font_path, size, track, dilate, ss)
              for w in words]
    h = max(b.shape[0] for b in blocks)
    # the word gap is a final-pixel measurement, so hold it at that ratio
    gap_ss = int(round(gap * h / float(CAP_H)))
    total = sum(b.shape[1] for b in blocks) + gap_ss * (len(blocks) - 1)
    line = np.zeros((h, total), bool)
    x = 0
    for b in blocks:
        line[h - b.shape[0]:, x:x + b.shape[1]] = b
        x += b.shape[1] + gap_ss
    canvas = np.zeros((ROWS * 8, COLS * 8), bool)
    w = COLS * 8 - 4
    canvas[CAP_TOP:CAP_TOP + CAP_H, 2:2 + w] = scale_to(line, w, CAP_H)
    return canvas


def slice_tiles(canvas):
    """Cells -> unique tiles, plus the index for each cell.

    Slots past the 109 this banner owns go to a run of empty tiles elsewhere
    in the file, rather than overwriting the neighbouring banner."""
    img = np.where(canvas, INK, 0).astype('<u2')
    tiles, table = [], []
    for r in range(ROWS):
        for c in range(COLS):
            raw = img[r * 8:r * 8 + 8, c * 8:c * 8 + 8].tobytes()
            if raw not in tiles:
                tiles.append(raw)
            table.append(tiles.index(raw))
    if len(tiles) > TILE_MAX + SPILL_MAX:
        return tiles, None
    spill = SPILL_TILE - TILE_BASE
    table = [t if t < TILE_MAX else spill + (t - TILE_MAX) for t in table]
    return tiles, table


def tile_dest(i):
    if i < TILE_MAX:
        return TILE_OFF + i * 128
    return (SPILL_TILE + i - TILE_MAX) * 128


def preview(canvas, path):
    rgb = np.zeros(canvas.shape + (3,), np.uint8)
    rgb[canvas] = [255, 150, 0]
    im = Image.fromarray(rgb)
    im.resize((im.width * 3, im.height * 3), Image.NEAREST).save(path)
    return path


def backup(path):
    bak = path + '.banner-bak'
    if not os.path.exists(bak):
        shutil.copy2(path, bak)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('gamedir')
    ap.add_argument('--text', default='Press A Button')
    ap.add_argument('--write', action='store_true')
    ap.add_argument('--restore', action='store_true')
    ap.add_argument('--font', help='path to a bold oblique .ttf')
    ap.add_argument('--size', type=int, default=32)
    ap.add_argument('--track', type=int, default=2)
    ap.add_argument('--dilate', type=int, default=2)
    ap.add_argument('--gap', type=int, default=WORD_GAP)
    ap.add_argument('--exact', action='store_true',
                    help='use the given weight and tracking as-is')
    args = ap.parse_args()

    exe = os.path.join(args.gamedir, 'v_on.exe')
    asset = os.path.join(args.gamedir, 'escrgame.bin')
    for p in (exe, asset):
        if not os.path.exists(p):
            sys.exit('not found: %s' % p)

    if args.restore:
        n = 0
        for p in (exe, asset):
            if os.path.exists(p + '.banner-bak'):
                shutil.copy2(p + '.banner-bak', p)
                print('restored %s' % os.path.basename(p))
                n += 1
        if not n:
            print('no backups to restore')
        return

    font_path = args.font or find_font()
    if not font_path:
        sys.exit('no usable font found; pass --font /path/to/x.ttf')
    print('font: %s' % font_path)

    # Weight is chosen by measurement: try a few and keep whichever lands
    # closest to the original's ink density. The tile count is no longer the
    # binding constraint, so quality wins.
    weights = [(args.dilate, args.track)] if args.exact else \
        [(d, t) for d in (1, 2, 3, 0, 4) for t in (2, 1, 3)]
    best = None
    for dil, trk in weights:
        c = compose(args.text, font_path, args.size, trk, dil, args.gap)
        tiles, table = slice_tiles(c)
        if table is None:
            continue
        score = abs(c.mean() - TARGET_INK)
        if best is None or score < best[0]:
            best = (score, c, tiles, table, dil, trk)
    if best is None:
        sys.exit('too many unique tiles - try a shorter string')
    _s, canvas, tiles, table, dil, trk = best

    print('"%s": %d unique tiles (%d own + %d spare), --dilate %d --track %d'
          % (args.text, len(tiles), min(len(tiles), TILE_MAX),
             max(0, len(tiles) - TILE_MAX), dil, trk))
    print('ink %.1f%% (original %.1f%%)'
          % (canvas.mean() * 100, TARGET_INK * 100))
    print('preview: %s' % preview(canvas, 'banner-preview.png'))
    if not args.write:
        print('\npreview only; pass --write to apply')
        return

    d = bytearray(open(asset, 'rb').read())
    if len(d) != GAME_SIZE:
        sys.exit('escrgame.bin is %d bytes, expected %d' % (len(d), GAME_SIZE))
    backup(asset)
    for i, raw in enumerate(tiles):
        o = tile_dest(i)
        d[o:o + 128] = raw
    for i in range(len(tiles), TILE_MAX):          # blank the unused slots
        o = tile_dest(i)
        d[o:o + 128] = b'\x00' * 128
    open(asset, 'wb').write(d)
    print('wrote %d tiles to escrgame.bin' % len(tiles))

    e = bytearray(open(exe, 'rb').read())
    if len(e) != EXE_SIZE:
        print('warning: v_on.exe is %d bytes, expected %d (already patched?)'
              % (len(e), EXE_SIZE))
    backup(exe)
    e[TABLE_OFF:TABLE_OFF + COLS * ROWS * 2] = \
        np.array(table, dtype='<u2').tobytes()
    open(exe, 'wb').write(e)
    print('wrote %d table entries to v_on.exe' % len(table))


if __name__ == '__main__':
    main()
