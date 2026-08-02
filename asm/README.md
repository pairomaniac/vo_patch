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
another cave.

Inside the `.vocd` data blob there is room: `D_CMD` holds 448 bytes against a
worst case of 318, and `D_TOC` is an exact fit at 100 dwords. Changing
`layout.py` costs nothing in the file until the section passes 3 KB, because
it is page aligned. The XInput routine lives inside `.rdata` proper (`0x207460`,
830 bytes) rather than in padding, in a run of zeros that was already there.

All of these sit past their section's VirtualSize but inside SizeOfRawData,
so the loader maps them. Any tool that rebuilds the PE from VirtualSize will
silently drop them.

## vocd.asm, what it does

The game's music is Redbook CD audio - real audio tracks on the disc, not
files. It asks Windows to play them over MCI, the old Media Control Interface,
with calls like *play device `cdaudio` from track 5*. No disc means nothing to
play, and a data-only ISO has no audio tracks either, so the game runs silent.

This impersonates the CD drive and plays WAV files instead. Two entry points.

**Setup** runs at the entry point, before the game's own start-up. It resolves
what it needs through the game's `LoadLibraryA` and `GetProcAddress` imports -
adding to the import table would have meant rebuilding it - then finds the
game folder from `GetModuleFileNameA`, and walks `music\track02.wav` up to
`track99.wav`.

It never opens a track. Redbook audio is exactly 44100 Hz 16-bit stereo, so a
CD frame is exactly 2352 bytes, so after the 44-byte WAV header **the file
size is the track length**. The whole table of contents comes from
`GetFileSize`. Nothing is parsed and nothing can be misread.

Then it makes the `mciSendCommandA` import slot writable, saves what was in
it, and points it at the hook. If no tracks were found it stops before that
step, every call reaches the real winmm, and the game reads a disc as it
always did - the fallback is the absence of a patch rather than a code path.
Last thing it does is jump to whatever the entry point used to be, which is
why this patch has to be applied after all the others.

**The hook** sees every `mciSendCommandA` the game makes. It watches for an
open of the `cdaudio` device and hands back a fake device ID, `0xFACE`. From
then on, calls carrying that ID belong to it and everything else - sound
effects, anything else using MCI - is forwarded to the real winmm untouched.
One device is impersonated; nothing else is disturbed.

Play requests are turned into MCI *string* commands against `waveaudio`:

```
open "<gamedir>\music\track05.wav" type waveaudio alias vocdbgm
set vocdbgm time format milliseconds
play vocdbgm
```

So it does not implement playback at all - no buffers, no mixing, no streaming
thread. It rewrites a CD command as a WAV command and lets the same MCI
subsystem do the work. That is also why it needs nothing from Wine beyond
`mciwave`: Wine can play a WAV, it just cannot invent a CD drive.

Status queries are answered from the table: track count, track lengths, media
present, time format, and track 1 reported as data so the game does not try to
play it. Only *is it still playing* cannot be answered from state, so that one
sends `status vocdbgm mode` and compares the answer against `playing`. The
game's own polling drives everything; nothing here runs on its own.

Unrecognised messages return success without doing anything. Failing them
would make the game give up on music entirely.

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
