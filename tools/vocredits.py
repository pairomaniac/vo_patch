"""Build the credit line out of the game's own lettering.

The ending roll is pre-rendered text chopped into 8x8 cells, not a font, so
a new line cannot just be typed. What it can be is cut back out: every text
block in the roll is known, its columns separate into characters at the
blank gaps, and matching the runs against the transcript gives a glyph for
each letter in the game's own hand.

    python3 tools/vocredits.py /path/to/VIRTUAL-ON            # preview
    python3 tools/vocredits.py /path/to/VIRTUAL-ON --write    # into vo-patch.py

Only the slash and the underscore are drawn here rather than harvested;
neither appears anywhere in the roll. They follow the same metrics as the
rest: 2px stems, caps spanning rows 0..16, descenders to 21.

Needs scrstfcg.bin, scrstfmp.bin and v_on.exe. None is in the repository, so
this is a by-hand tool - the bitmaps it writes are committed.
"""

import os
import struct
import sys

# (text, block width in cells, letter spacing, point size). 24 is the roll's
# body size. 11 is the smaller face the title uses for CYBER TROOPERS - a
# real face, not a reduction, but upper case only, so a line set in it has to
# be upper case too. Letters it does not have are reduced from their 24px
# capitals, which lands close: a derived CYBER TROOPERS comes out 140px
# against the genuine 139.
LINES = (('Patch by pairo', 42, 3, 24),
         ('GITHUB.COM/PAIROMANIAC/VO_PATCH', 42, 2, 11))

# The renderer centres a block of w cells at (51 - w) >> 1, so the title's 42
# cells end at column 46 - pixel 368. Every line is right-aligned to that.
RIGHT_EDGE = 46 * 8
USABLE = 51

# The small face: 11px capitals, and the band the title draws them in.
SMALL = 11
SMALL_TOP, SMALL_BOT = 10, 21
CAP = 17            # 24px capitals span rows 0..16
UNDER = 13          # where an underscore sits in the small line's 16px box

SPACE = 9       # a word space
HEIGHT = 24     # the harvested glyphs' height, three cells
CELLS_H = 3

# What each text block in the roll says, in block order. Four lines are left
# out of the harvest: the curly quotes segment as two runs each and the
# letters in "(HIC)" touch, so their runs do not line up with their
# characters. Everything needed is in the other 53.
ROLL = (
    'CYBER TROOPERS VIRTUAL-ON', 'PC version STAFF', 'Chief Programmer',
    'TOSHINORI  SUZUKI', 'Programmers', 'TAKAHIRO  NAGATA', 'HITOSHI  OHTA',
    'NORITAKA  YAKITA', 'YOSHIHIKO  TOYOSHIMA', 'KEI  TAKASHIMA',
    'Graphic Designers', 'KATSUFUMI  YOSHIMORI', 'TOMOHARU  TANAKA',
    'HISAYOSHI  YOSHIDA', 'TOMONORI  SAGUCHI', 'Director and Planner',
    'JUN-ETSU  KAKUTA', 'Sound Data Convert Engineer', 'MASARU  SETSUMARU',
    'Producer', 'SHUN  ARAI', 'TOSHINORI  ASAI', 'Publicity',
    'HIROYUKI  OTAKA', 'Marketing', 'YASUO  KOIKE', 'YASUHIDE  NAGASAWA',
    'RYOSUKE  KAJI', 'Character Design', 'HAJIME  KATOKI',
    'Program Technical Adviser', 'YOSHIHIRO  SONODA',
    'Design Technical Adviser', 'HIROSHI  YOSHIDA', 'KOH-ICHI  OZAKI',
    'NOBUKAZU  NARUKE', 'Music Adviser', 'KENTARO  KOYAMA', 'Supervisor',
    'JURO  WATARI', 'Testers', 'YOUICHIRO  INOUE', 'TOMOHISA  NAKAYASU',
    'KAZUYUKI  HAGIWARA', 'NORIKO  HORI', 'SPECIAL  THANKS:', None, None,
    None, 'SEGA  DIGITAL  MEDIA  PLANNIG &', 'DEVELOPMENT  DEPT.', None,
    'YASUSHI  NAGUMO', 'JUN  KASAHARA', 'and', 'ALL The Players of', None,
)

TABLE = 0x006bcd48


def read_blocks(exe):
    off = TABLE - 0x63f000 + 0x23de00
    out, total, n = [], 0, 0
    while total < 4644:
        flag, w, h = struct.unpack_from('<3I', exe, off + n * 12)
        out.append((w, h))
        total += w * h
        n += 1
    return out


def block_pixels(cells, sheet, w, h):
    """One block as a set of lit (x, y)."""
    lit = set()
    for i, cell in enumerate(cells):
        if not cell:
            continue
        tile = (cell & 0x7fff) * 128
        cx, cy = (i % w) * 8, (i // w) * 8
        for k in range(64):
            if struct.unpack_from('<H', sheet, tile + k * 2)[0]:
                lit.add((cx + k % 8, cy + k // 8))
    return lit


def harvest(folder):
    """A bitmap for every character the roll contains."""
    exe = open(os.path.join(folder, 'v_on.exe'), 'rb').read()
    sheet = open(os.path.join(folder, 'scrstfcg.bin'), 'rb').read()
    raw = open(os.path.join(folder, 'scrstfmp.bin'), 'rb').read()
    cells = struct.unpack('<%dH' % (len(raw) // 2), raw)

    glyphs, at, bi = {}, 0, 0
    for w, h in read_blocks(exe):
        if not w:
            continue
        text = ROLL[bi] if bi < len(ROLL) else None
        bi += 1
        lit = block_pixels(cells[at:at + w * h], sheet, w, h)
        at += w * h
        if text is None:
            continue
        cols = [any((x, y) in lit for y in range(HEIGHT))
                for x in range(w * 8)]
        runs, start = [], None
        for x in range(w * 8 + 1):
            on = x < w * 8 and cols[x]
            if on and start is None:
                start = x
            if not on and start is not None:
                runs.append((start, x))
                start = None
        chars = [c for c in text if c != ' ']
        if len(runs) != len(chars):
            continue                # quotes and touching letters; skipped
        for (x0, x1), ch in zip(runs, chars):
            glyphs.setdefault(ch, []).append(
                frozenset((x - x0, y) for x, y in lit if x0 <= x < x1))
    return glyphs


def small_face(folder):
    """The 11px capitals the title sets CYBER TROOPERS in.

    A genuine smaller face rather than a reduction, but it exists only in
    that one line, so it covers CYBERTOPS and nothing else."""
    exe = open(os.path.join(folder, 'v_on.exe'), 'rb').read()
    sheet = open(os.path.join(folder, 'scrstfcg.bin'), 'rb').read()
    raw = open(os.path.join(folder, 'scrstfmp.bin'), 'rb').read()
    cells = struct.unpack('<%dH' % (len(raw) // 2), raw)
    w, h = read_blocks(exe)[0]
    lit = block_pixels(cells[:w * h], sheet, w, h)
    lit = {(x, y) for x, y in lit if SMALL_TOP <= y < SMALL_BOT}
    cols = [any((x, y) in lit for y in range(SMALL_TOP, SMALL_BOT))
            for x in range(w * 8)]
    runs, start = [], None
    for x in range(w * 8 + 1):
        on = x < w * 8 and cols[x]
        if on and start is None:
            start = x
        if not on and start is not None:
            if x - start > 1:
                runs.append((start, x))
            start = None
        # The large VIRTUAL-ON shares these rows, so stop at the wide gap
        # that separates the two halves of the title.
        if start is None and len(runs) == len('CYBERTROOPERS'):
            break
    out = {}
    for (x0, x1), ch in zip(runs, 'CYBERTROOPERS'):
        out.setdefault(ch, frozenset((x - x0, y - SMALL_TOP)
                                     for x, y in lit if x0 <= x < x1))
    return out


# The letters the small face lacks, drawn in its own conventions rather than
# reduced from the 24px capitals: that face is monoline 1px, and any
# reduction of a 2px stem lands on 1 or 2 px unevenly and reads as bold.
#
# Shapes follow the 24px capitals at 11/17. Widths come from that ratio
# rather than from the eye: 13px wide at 24 is 8 here, 15 is 9 or 10, 17 is
# 11 - which is why U is a narrow letter and not a round one, and why it is
# B's width rather than O's. Edges step a column every three rows.
#
# Two places where a straight reduction reads wrong at 1px and the drawing
# departs from it: the A's apex is two pixels for one row, because the
# original's three rows of it turn into a blob at this weight, and the V
# closes to a single pixel, because the original's strokes are three wide
# and nearly touch where 1px strokes a pixel apart just look broken.
SMALL_DRAWN = {
    'A': ["...##...", "..#..#..", "..#..#..", "..#..#..", ".#....#.",
          ".#....#.", ".#....#.", ".######.", "#......#", "#......#",
          "#......#"],
    'G': ["...####...", "..#....#..", ".#......#.", "#........#",
          "#.........", "#....#####", "#........#", "#........#",
          ".#......#.", "..#....#..", "...####..."],
    'H': ["#......#", "#......#", "#......#", "#......#", "#......#",
          "########", "#......#", "#......#", "#......#", "#......#",
          "#......#"],
    'I': ["#"] * 11,
    'M': ["#.......#", "##.....##", "#.#...#.#", "#.#...#.#", "#.#...#.#",
          "#..#.#..#", "#..#.#..#", "#..#.#..#", "#...#...#", "#...#...#",
          "#...#...#"],
    'N': ["#......#", "##.....#", "#.#....#", "#.#....#", "#..#...#",
          "#..#...#", "#...#..#", "#...#..#", "#....#.#", "#....#.#",
          "#.....##"],
    'U': ["#......#", "#......#", "#......#", "#......#", "#......#",
          "#......#", "#......#", "#......#", "#......#", ".#....#.",
          "..####.."],
    'V': ["#.........#", "#.........#", "#.........#", ".#.......#.",
          ".#.......#.", ".#.......#.", "..#.....#..", "..#.....#..",
          "...#...#...", "....#.#....", ".....#....."],
    '.': ["."] * 10 + ["#"],
    '/': [".....#", ".....#", "....#.", "....#.", "...#..", "...#..",
          "..#...", "..#...", ".#....", ".#....", "#....."],
}


def drawn():
    """The two characters the roll does not contain."""
    slash = set()
    for y in range(17):
        x = 7 - (y * 6) // 16
        slash.update({(x, y), (x + 1, y)})
    under = {(x, y) for x in range(11) for y in (20, 21)}
    return {'/': (9, frozenset(slash)), '_': (11, frozenset(under))}


def render(text, glyphs, gap, space=None):
    out, x = set(), 0
    for i, ch in enumerate(text):
        if ch == ' ':
            x += space or SPACE
            continue
        width, lit = glyphs[ch]
        out |= {(px + x, py) for px, py in lit}
        x += width
        if i + 1 < len(text) and text[i + 1] != ' ':
            x += gap
    return x, out


def small_glyphs(folder):
    """The small face: what the title provides, plus the drawn rest."""
    out = {}
    for ch, lit in small_face(folder).items():
        out[ch] = (max(x for x, _y in lit) + 1, lit)
    for ch, rows in SMALL_DRAWN.items():
        out.setdefault(ch, (len(rows[0]),
                            frozenset((x, y) for y, row in enumerate(rows)
                                      for x, c in enumerate(row) if c == '#')))
    # The underscore sits below the baseline, outside the capital box, so it
    # belongs to the line's 16px box rather than to the face.
    out['_'] = (8, frozenset((x, UNDER) for x in range(8)))
    return out


def main(folder, write):
    import collections
    found = harvest(folder)
    glyphs = {}
    for ch, seen in found.items():
        best = collections.Counter(seen).most_common(1)[0][0]
        glyphs[ch] = (max(x for x, _y in best) + 1, best)
    glyphs.update(drawn())

    print('harvested %d characters from the roll' % len(found))

    small = small_glyphs(folder)
    blobs = []
    for n, (text, cells_w, gap, size) in enumerate(LINES, 1):
        face = glyphs if size == HEIGHT else small
        box = HEIGHT if size == HEIGHT else 16
        cells_h = CELLS_H if size == HEIGHT else 2
        missing = {c for c in text if c != ' '} - set(face)
        if missing:
            print('%dpx face has no %s' % (size, ''.join(sorted(missing))))
            return 1
        px, lit = render(text, face, gap, space=SPACE if size == HEIGHT else 6)
        # Where the renderer will put this block, and how far into it the
        # text has to start so it ends on the title's right edge.
        x0 = ((USABLE - cells_w) >> 1) * 8
        pad = (RIGHT_EDGE - x0) - px
        if pad < 0:
            print('%r is %dpx, %dpx too wide for its %d cells'
                  % (text, px, -pad, cells_w))
            return 1
        if pad + px > cells_w * 8:
            print('%r overflows its block' % text)
            return 1
        lit = {(x + pad, y) for x, y in lit}
        bits = bytearray(cells_w * box)
        for x, y in lit:
            bits[y * cells_w + (x >> 3)] |= 1 << (7 - (x & 7))
        print('  %-32r %3d px at %dpx, gap %d, %d cells, ends at pixel %d'
              % (text, px, size, gap, cells_w, x0 + pad + px))
        blobs.append((n, cells_w, cells_h, bytes(bits)))
        for y in range(box):
            print('    ' + ''.join('#' if (x, y) in lit else '.'
                                   for x in range(pad, pad + px)))

    if not write:
        print('\npreview only; pass --write to update vo-patch.py')
        return 0

    text = []
    for n, cells, cells_h, bits in blobs:
        h = bits.hex()
        body = '\n'.join("    '%s'" % h[i:i + 64] for i in range(0, len(h), 64))
        text.append("CREDIT%d_W = %d\nCREDIT%d_H = %d\n"
                    "CREDIT%d_BITS = bytes.fromhex(\n%s\n)"
                    % (n, cells, n, cells_h, n, body))
    path = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), 'vo-patch.py')
    src = open(path, encoding='utf-8').read()
    head = '# CREDITLINE BLOB BEGIN - tools/vocredits.py\n'
    tail = '# CREDITLINE BLOB END'
    a, b = src.index(head) + len(head), src.index(tail)
    open(path, 'w', encoding='utf-8').write(
        src[:a] + '\n\n'.join(text) + '\n' + src[b:])
    print('\nwrote the bitmaps into vo-patch.py')
    return 0


if __name__ == '__main__':
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if len(args) != 1:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(args[0], '--write' in sys.argv))
