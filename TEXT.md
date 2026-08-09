# On-screen text

Where each string the patcher touches actually lives, and why some of them
are not strings at all. Offsets are file offsets into `v_on.exe` unless
another file is named.

## Three ways the game draws text

| Path | Used by | Source |
| --- | --- | --- |
| GDI | pause, loading, connecting | C strings, `TextOutA` onto a DC from the DirectDraw surface |
| Tile font | mech data, tutorial lines | ASCII strings, one character per 8x8 cell |
| Tile artwork | title and scoreboard prompt | a table of tile indices; the pixels are in `escrgame.bin` |

The third is why searching the executable for `Press Space Bar` finds
nothing: the phrase is stored as 126 tile indices, and the letterforms are
8x8 fragments in an asset file.

## Strings

| Text | Offset | Notes |
| --- | --- | --- |
| `To Resume Game, Press F3` | `0x2c7654` | GDI. 24 bytes plus four of padding; `PAUSE` follows at `0x2c7670`, so 27 characters is the ceiling |
| `Now Loading . . .` | `0x2c7678` | GDI. Hidden by writing `NUL` over the first byte |
| `Connecting...` | `0x2c7644` | GDI |
| ` PRESS SPACE BAR` | `0x285e04` | tile font, 16 cells. Blanked by overwriting with the 16 spaces at `0x285df0`, so a replacement must be the same width |

The tile font table around `0x285df0` also holds `INSERT COIN(S)`,
`TO BE CONTINUED ...`, `MOVE  FORWARD`, `DASH  BUTTON`, the mech names and
the weapon names.

Drawing routines: `0x5c991c` takes `(string, x, y, colour, flag)` and draws
through GDI. `0x4cd8c3(col, row)` sets the tile cursor and `0x4ce573(str)`
prints through it.

## The title and scoreboard banner

Not text. 42x3 cells of 8x8 pixels, 16bpp RGB565.

| Piece | Where |
| --- | --- |
| Tile indices | `v_on.exe` `0x269b60`, 126 entries of 16 bits |
| Artwork | `escrgame.bin` `0x21c000`, 109 tiles of 128 bytes |
| Spare tiles | `escrgame.bin` tile 24845, a run of 116 empty ones |

The loader adds `0x380` to every index at start-up, so the values in the
executable are 0-based. The renderer ORs `0xc000` into each entry, which
selects tile bank B; bank B is `scradd1.bin` followed by `escrgame.bin`,
based at `0x345d420`. Bank A holds `escradv.bin` and the title logo, which
is why painting that file changes the logo and not the prompt.

`0x4d0622` draws or clears the banner, gated on the screen id at `0x66c1ac`
being 1 to 3. It sits at column 10, row 40 of the 82-wide tilemap at
`0x1cc18ea`.

Renaming it needs both halves, so it rides with **XInput gamepad support**:
the patcher carries a 1bpp 336x24 bitmap, expands it into tiles at apply
time, writes the indices into the executable with every other patch, and
writes the tiles into `escrgame.bin` afterwards. That file is backed up to
`escrgame.bin.bak` and **Restore original** puts it back.

The two halves have to match. The executable holds the indices and
`escrgame.bin` holds the tiles, so restoring one alone draws the prompt as
scrambled letters. The patcher refuses to write anything if `escrgame.bin` is
missing or has been modified with no backup beside it, and **Restore
original** puts both back together.

## Changing the wording

```bash
python3 tools/vonbanner.py /path/to/VIRTUAL-ON --text 'Press Start'
python3 tools/vonbanner.py /path/to/VIRTUAL-ON --text 'Press Start' --write
```

That writes the game's files directly, which is enough to see it. To ship it,
take the bitmap it produces and replace `BANNER_BITS` in `vo-patch.py`; the
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
| every tile inside the file | an offset past the end of `escrgame.bin` |

## Not editable

The title screen's mixed-case `Press Space Bar` is the banner above. Nothing
else on that screen is text either: the logo, the kanji and the copyright
line are all artwork.

`PRESS  BUTTON` and `MACHINE SELECT` on the mech select screen are
pre-rendered word sprites, in the same family as `CREDIT`, `PLAYER`,
`GAME OVER` and `CONTINUE`. None of them appear as strings in any file.
