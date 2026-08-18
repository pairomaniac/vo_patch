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

LINES = ('Patch by pairo', 'github.com/pairomaniac/vo_patch')

GAP = 3         # between letters, the roll's most common spacing
SPACE = 9       # a word space
HEIGHT = 24     # three cells

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


def drawn():
    """The two characters the roll does not contain."""
    slash = set()
    for y in range(17):
        x = 7 - (y * 6) // 16
        slash.update({(x, y), (x + 1, y)})
    under = {(x, y) for x in range(11) for y in (20, 21)}
    return {'/': (9, frozenset(slash)), '_': (11, frozenset(under))}


def render(text, glyphs):
    out, x = set(), 0
    for i, ch in enumerate(text):
        if ch == ' ':
            x += SPACE
            continue
        width, lit = glyphs[ch]
        out |= {(px + x, py) for px, py in lit}
        x += width
        if i + 1 < len(text) and text[i + 1] != ' ':
            x += GAP
    return x, out


def main(folder, write):
    import collections
    found = harvest(folder)
    glyphs = {}
    for ch, seen in found.items():
        best = collections.Counter(seen).most_common(1)[0][0]
        glyphs[ch] = (max(x for x, _y in best) + 1, best)
    glyphs.update(drawn())

    missing = {c for line in LINES for c in line if c != ' '} - set(glyphs)
    if missing:
        print('not in the roll and not drawn: %s' % ''.join(sorted(missing)))
        return 1
    print('harvested %d characters from the roll' % len(found))

    blobs = []
    for n, text in enumerate(LINES, 1):
        px, lit = render(text, glyphs)
        cells = -(-px // 8)
        bits = bytearray(cells * HEIGHT)
        for x, y in lit:
            bits[y * cells + (x >> 3)] |= 1 << (7 - (x & 7))
        print('  %-32r %3d px, %2d cells, %d lit' % (text, px, cells,
                                                     len(lit)))
        blobs.append((n, cells, bytes(bits)))
        for y in range(HEIGHT):
            print('    ' + ''.join('#' if (x, y) in lit else '.'
                                   for x in range(px)))

    if not write:
        print('\npreview only; pass --write to update vo-patch.py')
        return 0

    text = []
    for n, cells, bits in blobs:
        h = bits.hex()
        body = '\n'.join("    '%s'" % h[i:i + 64] for i in range(0, len(h), 64))
        text.append("CREDIT%d_W = %d\nCREDIT%d_BITS = bytes.fromhex(\n%s\n)"
                    % (n, cells, n, body))
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
