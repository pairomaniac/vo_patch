# asm

Source for the machine code the patches install. `vo-patch.py` carries the
assembled bytes, so nobody running the patcher or building the exe needs nasm.
Only someone editing the assembly does.

| File | |
| --- | --- |
| `vocd.asm` | CD audio: setup, the `mciSendCommandA` hook, the handlers |
| `levers.asm` | gamepad: the lever cleanup that runs after each input tick |
| `layout.py` | data cave layout and string table, shared by `vocd.asm` and the blob |
| `build.py` | assembles both into `../vo-patch.py` |

## How the assembly gets into the patcher

`vo-patch.py` never reads these files. It carries the finished machine code as
hex strings, because it ships as a single file - bundled into the exe, and
downloaded on its own by Linux users - and has to run from a fresh checkout
with nothing installed. `build.py` is what copies one into the other.

There are two hex strings, each fenced off by a pair of comment markers that
`build.py` searches for. **The markers are load-bearing; do not remove them.**

```
# VOCD BLOB BEGIN        <- VOCD_MAGICS, VOCD_CODE, VOCD_DATA
# VOCD BLOB END

# LEVERS BLOB BEGIN      <- LEVERS_CODE
# LEVERS BLOB END
```

`build.py` runs nasm on both `.asm` files, formats the output as
`bytes.fromhex(...)`, and replaces everything between each pair of markers.
Nothing outside the markers is touched, so the patch tables around them are
safe. Neither mode writes anything into `asm/`; nasm works in a temporary
directory that is deleted afterwards.

## The loop

```
sudo dnf install nasm            # or: sudo apt install nasm

vim asm/vocd.asm                 # 1. edit the assembly
python3 asm/build.py             # 2. regenerate the blobs in vo-patch.py
git diff                         # 3. vo-patch.py's hex strings changed
```

Step 2 also runs `vo-patch.py --selfcheck`, which validates the patch tables,
so a run that prints `tables OK` has verified both halves.

You never edit the hex in `vo-patch.py` yourself. Both blob regions carry a
GENERATED banner saying so, and the next `build.py` run would overwrite the
edit anyway.

## What CI checks

The `verify` job in `.github/workflows/build.yml` runs on every push to main
and every pull request, and the Windows build will not start until it passes.
It installs nasm and runs the same two commands you would:

| Step | Catches |
| --- | --- |
| `vo-patch.py --selfcheck` | patch table broken: bad length, offset past the end, two patches on one byte, site list reordered |
| `asm/build.py --check` | assembly edited without the blobs being regenerated |
| `git diff --exit-code` | blobs regenerated but not committed |
| `pyflakes` | the usual |

The middle two are the ones that matter here. Without them the repository
stays self-consistent while the shipped patcher installs last week's code -
nothing else in the project would notice.

What CI cannot do is apply the patches to a real `v_on.exe`, because the game
is not in the repository. So before tagging:

```
python3 tools/selftest.py /path/to/v_on.exe
```

That is the only check that catches a wrong offset. It verifies every
`original` column against the real file, applies 350-odd combinations of
patches, and compares the fully patched MD5 against the one recorded in the
script.

## Where each blob lands

**`vocd.asm`** becomes a blob of its own in a new `.vocd` section that
`apply_cdaudio` appends to the executable. The code starts with two thunks:
`+0` is the hook the winmm import is redirected to, `+5` is the setup the
entry point is repointed at. The data blob is the string table plus the space
the code works in - track table, path and command buffers.

Absolute addresses are placeholders (`0xE1E1E1E1` and friends) that
`apply_cdaudio` fills in once it has read the executable: IAT slots, the
previous entry point, and where the blobs landed. Everything else is self
relative, so the section can go anywhere.

Setup resolves what it needs through the game's own `LoadLibraryA` and
`GetProcAddress` imports, walks `music\trackNN.wav` building the table of
contents from file sizes, then `VirtualProtect`s the `mciSendCommandA` IAT
slot and redirects it. With no tracks it changes nothing and every call
reaches the real winmm, which is the fallback to a disc.

**`levers.asm`** goes to one site inside the XInput patch table, at
`0x0020779e`. The table entry reads its length from `LEVERS_CODE`, so the
routine can change size without the run of `00` beside it needing a manual
edit - but it has to stay inside the `.rdata` cave (see below).

## Space left in the executable

There is no room to spare. Sites written into section padding, and what is
still free after them:

| Cave | File range | Size | Used | Free |
| --- | --- | --- | --- | --- |
| `.text` past VirtualSize | `0x1f423e`-`0x1f4400` | 450 | 426 | **24** |
| `.rdata` past VirtualSize | `0x23dce8`-`0x23de00` | 280 | 80 | 200 |
| `.rsrc` past VirtualSize | `0x60c258`-`0x60c400` | 424 | 4 | 420 |

The `.text` cave holds the timer stub and the three F11 dialog blobs and has
24 bytes left, which is why CD audio got a section of its own rather than
another cave. The XInput routine lives inside `.rdata` proper (`0x207460`,
830 bytes) rather than in padding, in a run of zeros that was already there.

All of these sit past their section's VirtualSize but inside SizeOfRawData,
so the loader maps them. Any tool that rebuilds the PE from VirtualSize will
silently drop them.

## levers.asm, what it does

The game's jump and guard are lever gestures, not buttons: both levers spread
outward and both squeezed inward. Each lever word is a bitmask where a clear
bit means pushed - `0x80` left, `0x40` right, `0x20` up, `0x10` down.

The XInput tick ORs every active input into those words, so a held direction
left `up` set alongside the jump bits and the result read as two diagonals
rather than a spread. This runs in place of the tick's epilogue and strips the
contamination back off, but only when a pad was actually read that tick, so
the keyboard path is untouched.

Because it replaces the epilogue, its site overlaps the end of the XInput
routine written by the site before it: `0x207702` expects the `5f5e5bc9c3`
that `0x207460` wrote there, not anything from the original file. The site
list is applied in order and `_check_table` enforces that relationship, so
sorting that list by offset will fail at import rather than at write time.
