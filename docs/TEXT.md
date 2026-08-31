# On-screen text

Where each string the patcher touches actually lives, and why some of them
are not strings at all.

The tables give file offsets into the English retail `v_on.exe`, unless
another file is named, because that is what the patch tables use; the other
builds' offsets come from their site maps in `vo_patch.py`. Routines and
globals are named by virtual address, the way a debugger shows them: add
`0x400c00` to a `.text` or `.rdata` offset and `0x401200` to a `.data` one.

## Three ways the game draws text

| Path | Used by | Source |
| --- | --- | --- |
| GDI | pause, loading, connecting, the credits prompt | C strings, `TextOutA` onto a DC from the DirectDraw surface; **Native widescreen** scales the two 24px faces with the height and recentres the wrap bounds |
| Tile font | mech data, tutorial lines | ASCII strings, one character per 8x8 cell |
| Tile artwork | title and scoreboard prompt | a table of tile indices; the pixels are in `escrgame.bin` |

The third is why searching the executable for `Press Space Bar` finds
nothing: the phrase is stored as 126 tile indices, and the letterforms are
8x8 fragments in an asset file.

## Strings

| Text | Screen | Offset | Notes |
| --- | --- | --- | --- |
| `To Resume Game, Press F3` | pause | `0x2c7654` | GDI. 24 bytes plus four of padding; `PAUSE` follows at `0x2c7670`, so 27 characters is the ceiling |
| `Now Loading . . .` | loading | `0x2c7678` | GDI. Hidden by writing `NUL` over the first byte |
| `Connecting...` | link play | `0x2c7644` | GDI |
| ` PRESS SPACE BAR` | scoreboard | `0x285e04` | tile font, 16 cells. Blanked by overwriting with the 16 spaces at `0x285df0`, so a replacement must be the same width |
| `HOLD TO SKIP` | ending credits | in the patch | GDI. Not the game's - it is carried in `overlay.asm` and drawn at 320, 440 of the picture, computed from the mode size, halved with everything else in low resolution |
| `Bindings - %dP side` | bind page title | `0x26c88c` | C string the page's `SetWindowTextA` formats; replaces the stock title so both sides are told apart |
| `1P side` → `Actions` | bind page label | `0x60b34e` | UTF-16, inside the dialog template in `.rsrc`. Baked-in text the stock game showed on the 2P pass too; the replacement must stay seven characters |

The tile font table around `0x285df0` also holds `INSERT COIN(S)`,
`TO BE CONTINUED ...`, `MOVE  FORWARD`, `DASH  BUTTON`, the mech names and
the weapon names.

## The ending roll

Two sequences draw it - `0x4489d6` for the roll a finished game reaches and
`0x58ecd0` for the one the **Credits** button jumps to - and both read the
same three files, so an edit lands in either.

Not text at all. `scrstfcg.bin` is 1129 tiles, 8x8 and 16bpp, holding two
values only - `0x0000` and `0xffbf` - because the whole roll is pre-rendered
white lettering chopped into cells. `scrstfmp.bin` gives one 16-bit index per
cell, bit 15 set where a cell has a tile, and the loader adds the tile base
to every non-zero entry at `0x483d9d`. It reads both files to hardcoded byte
counts at `0x5fdac8` and `0x5fdacc`, not to their size on disk - grow a file
without growing its constant and the tail silently never loads.

The layout is not a grid. `0x6bcd48` in the executable holds 12 bytes per
block - flag, width, height, in cells - and the map is those blocks end to
end. The roll's own text blocks are three cells tall, though the height is
read per block; a block of width 0 is a blank spacer.
The flag picks the placement: `0x448e5c` sends anything below zero to
`0x448e86`, which centres the block on the 51 cells, and `0x63` to
`0x448f54`, which pushes it flush right. The roll's own text carries `0x63`.
`0x44908e` reveals the next block every eight ticks per row, so the roll's
length is the sum of the heights.

Adding a line means all three: an entry in the block list, its cells in
`scrstfmp.bin`, and its tiles on the end of `scrstfcg.bin`, plus the two byte
counts above. `tools/vocredits.py` does it, cutting glyphs out of the
existing artwork so a new line matches the rest.

There are two faces. The 24px body face covers everything the roll says, so
a line set in it is harvested whole. The 11px capitals the title sets CYBER
TROOPERS in are a real face rather than a reduction, but they exist only in
that phrase, so anything outside `CYBERTOPS` is drawn: it is monoline 1px,
and reducing a 2px stem lands on one or two pixels unevenly and reads as
bold. Drawn widths come from the 24px capital at 11/17 - 13px wide there is
8 here, 15 is 9 or 10, 17 is 11 - and edges step a column every three rows.

Drawing routines: `0x5c991c` takes `(string, x, y, colour, flag)` and draws
through GDI. `0x4cd8c3(col, row)` sets the tile cursor and two printers draw
through it: `0x4ce573(str)`, and `0x4ceeeb(str)`, which also picks among the
four glyph sets at `0x600ec8` and is what the version line uses.

`0x5c991c` paints on whichever surface `0x1ae5f40` names, and returns without
drawing if that is null. The pause screen is not flipping, so it wants the
primary surface; anything drawn during a running frame has to point that
global at the back buffer for the length of the call. This is the whole
reason the credits prompt is not a tile: the tile layer is what the roll
scrolls, so the text would climb the screen with the credits.

## The title and scoreboard banner

Not text. 42x3 cells of 8x8 pixels, 16bpp RGB565.

| Piece | Where |
| --- | --- |
| Tile indices | `v_on.exe` `0x269b60`, 126 entries of 16 bits |
| Artwork | the title artwork, `0x21c000`, 109 tiles of 128 bytes |
| Spare tiles | the same file, tile 24845, a run of 116 empty ones |

The artwork is `escrgame.bin` in the English retail and OEM builds and
`jscrgame.bin` in the Japanese rerelease; the tiles this touches are in
the same place in each, and each `Build` in `vo_patch.py` names its own.
Addresses below are the retail build's.

The loader adds `0x380` to every index at start-up, so the values in the
executable are 0-based. The renderer ORs `0xc000` into each entry, which
selects tile bank B; bank B is `scradd1.bin` followed by the title artwork,
based at `0x345d420`. Bank A holds `escradv.bin` and the title logo, which
is why painting that file changes the logo and not the prompt.

`0x4d0622` draws or clears the banner, gated on the screen id at `0x66c1ac`
being 1 to 3. It sits at column 10, row 40 of the 82-wide tilemap at
`0x1cc18ea`.

Renaming it needs both halves, so it rides with **XInput gamepad support**:
the patcher carries a 1bpp 336x24 bitmap, expands it into tiles at apply
time, writes the indices into the executable with every other patch, and
writes the tiles into the artwork afterwards. That file is backed up to
`.bak` and **Restore original** puts it back.

The two halves have to match. The executable holds the indices and the
artwork holds the tiles, so restoring one alone draws the prompt as
scrambled letters. The patcher refuses to write anything if the artwork is
missing or has been modified with no backup beside it, and **Restore
original** puts both back together.

## Changing the wording

```bash
python3 tools/vonbanner.py /path/to/VIRTUAL-ON --text 'Press Start'
python3 tools/vonbanner.py /path/to/VIRTUAL-ON --text 'Press Start' --write
```

That writes the game's files directly, which is enough to see it. To ship it,
take the bitmap it produces and replace `BANNER_BITS` in `vo_patch.py`; the
patcher expands it into tiles and the index table at import.

The rendering depends on which font is installed, so it is generated once and
checked in rather than rebuilt. CI cannot reproduce it byte for byte, and
does not try. What `--selfcheck` does check, at import:

| Check | Catches |
| --- | --- |
| bitmap is 1008 bytes | a truncated or hand-edited blob |
| table is 126 entries | a bitmap of the wrong shape |
| unique tiles fit 109 + 116 spare | wording too detailed to fit |
| every index inside 14 bits | an index the renderer would mask into another tile |
| every tile inside the file | an offset past the end of the artwork |

## Left alone

Nothing else on the title screen is text: the logo, the kanji and the
copyright line are artwork, and not tiles either.

`PRESS  BUTTON` and `MACHINE SELECT` on the mech select screen are
pre-rendered word sprites, in the same family as `CREDIT`, `PLAYER`,
`GAME OVER` and `CONTINUE`. None of them appear as a string in any file, and
none is a tile table, so changing them means repainting the sprites.
