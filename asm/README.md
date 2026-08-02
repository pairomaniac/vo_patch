# asm

Source for the machine code the patches install. `vo-patch.py` carries the
assembled bytes, so nobody running the patcher or building the exe needs nasm.
Only someone editing the assembly does.

| File | What it holds |
| --- | --- |
| `vocd.asm` | CD audio: setup, the `mciSendCommandA` hook, the handlers |
| `levers.asm` | gamepad: the lever cleanup that runs after each input tick |
| `twinstick.asm` | gamepad: the arcade twin-stick profile, two stubs and its tables |
| `kbpage.asm` | gamepad: two fixes to the keyboard bind page |
| `layout.py` | data cave layout and string table, shared by `vocd.asm` and the blob |
| `build.py` | assembles all four sources into `../vo-patch.py` |

## How the assembly gets into the patcher

`vo-patch.py` never reads these files. It carries the finished machine code as
hex strings, because it ships as a single file - bundled into the exe, and
downloaded on its own by Linux users - and has to run from a fresh checkout
with nothing installed. `build.py` is what copies one into the other.

There are four hex strings, each fenced off by a pair of comment markers that
`build.py` searches for. **The markers are load-bearing; do not remove them.**

```
# VOCD BLOB BEGIN        <- VOCD_MAGICS, VOCD_CODE, VOCD_DATA
# VOCD BLOB END

# LEVERS BLOB BEGIN      <- LEVERS_CODE
# LEVERS BLOB END

# TWIN BLOB BEGIN        <- TWIN_CODE
# TWIN BLOB END

# KBPAGE BLOB BEGIN      <- KBPAGE_CODE
# KBPAGE BLOB END
```

`twinstick.asm` and `kbpage.asm` carry an `org`, because their stubs jump to
fixed addresses and the twin-stick parameter blocks point at its own tables. `build.py` checks each `org` against the offset of the site that
writes it, since nothing downstream would notice the mismatch: the bytes
would be written and every address inside them would be wrong.

`build.py` runs nasm on each `.asm` file, formats the output as
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

**`levers.asm`**, **`twinstick.asm`** and **`kbpage.asm`** each go to one
site inside the XInput patch table, at `0x0020779e`, `0x00223dc4` and
`0x0023dd38`. Every entry reads its length from the blob, so a routine can
change size without the run of `00` beside it needing a manual edit - but it
has to stay inside its cave, and the last two carry an `org`, so growing them
past the cave is a source edit and not just a rebuild.

## Space left in the executable

There is no room to spare. Sites written into section padding, and what is
still free after them:

| Cave | File range | Size | Used | Free |
| --- | --- | --- | --- | --- |
| `.text` past VirtualSize | `0x1f423e`-`0x1f4400` | 450 | 426 | **24** |
| `.rdata` past VirtualSize | `0x23dce8`-`0x23de00` | 280 | 133 | 147 |
| `.rsrc` past VirtualSize | `0x60c258`-`0x60c400` | 424 | 4 | 420 |

The `.text` cave holds the timer stub and the three F11 dialog blobs and has
24 bytes left, which is why CD audio got a section of its own rather than
another cave. The `.rdata` one holds the F11 dialog template and the keyboard
page fixes.

Inside the `.vocd` data blob there is room: `D_CMD` holds 448 bytes against a
worst case of 318, and `D_TOC` is an exact fit at 100 dwords. Changing
`layout.py` costs nothing in the file until the section passes 3 KB, because
it is page aligned.

The gamepad patch does not use padding at all. It lives in six runs of zeros
inside `.rdata` proper, which is why the section's characteristics get the
execute bit:

| Cave | File range | Size | Used | Free |
| --- | --- | --- | --- | --- |
| routine and lever cleanup | `0x207460`-`0x2077e0` | 928 | 881 | **15** |
| input names and profile names | `0x223f9b`-`0x224058` | 189 | 135 | 54 |
| bind list table | `0x223c43`-`0x223d00` | 189 | 125 | 64 |
| condition table | `0x22411b`-`0x2241cb` | 176 | 126 | 50 |
| twin-stick stubs, binds, masks, blocks | `0x223dc4`-`0x223e73` | 175 | 164 | **11** |
| keyboard page fixes | `0x23dd38`-`0x23de00` | 200 | 53 | 147 |

The last of those is the `.rdata` padding the F11 dialog already uses, so it
appears in both tables.

Both of the tight ones are tight. If either grows, the next free runs of this
size are `0x1f74e0` and `0x223198`, 174 bytes each.

**A run of zeros is not free space at all until you have checked that nothing
points into it.** `0x6083e0` sits in the middle of the run the XInput routine
lives in and is a base address the geometry code indexes off; the run reads as
zeros because that is what the game expects to find there. The routine stops
at `0x6083d1` and is fine. Fifty-three bytes dropped after it were not, and
the symptom was corrupted loading screens, nothing to do with input. So the
usable tail of that cave is fifteen bytes, not sixty-five.

Check a candidate cave by searching the whole file for a dword equal to any
address inside the span. That search has false positives on long spans - any
four bytes of data can happen to equal an address - so read the instruction at
each hit rather than trusting the count.

**And a run of zeros is not free until you round its start up to a multiple
of four, either.** Addresses in this image are all below `0x01000000`, so every
pointer has a zero top byte, and a scan for the longest run of `00` will
happily start you one byte inside the last pointer of a table. Writing there
turns `0x00623bb0` into `0x11623bb0` and the game dies when it dereferences
it. That cost a release. `build.py` now refuses any `org` whose site is not
four-aligned, and the same rule applies to any cave picked by hand.

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

## twinstick.asm, what it does

There is no logic in it. The XInput tick already walks twelve bind slots,
tests each against a condition, and clears the bits its mask names in the two
lever words. Feed that engine a different set of binds and masks and it
becomes the arcade scheme: one thumbstick direction per slot instead of one
named action, so the sticks land straight in the levers and the game works
out walking, turning, jump and crouch from the pair.

So the file is two entry stubs, a bind list, two mask tables and a parameter
block per player - 164 bytes, 116 of them tables. It carries an `org` because
the stubs jump to fixed addresses and the blocks point at its own tables.

## kbpage.asm, what it does

Two unrelated repairs to the keyboard bind page, sharing a cave.

The first is the duplicate-key test. The page refuses a key for 2P if 1P
already holds it, which is right when both are on the keyboard and needlessly
strict when 1P is on a pad and its keys are dormant. The stub runs the test
only when 1P is actually on the keyboard profile. It governs what may be
entered and nothing more: if 1P later switches back, both sides can hold the
same key and one press drives both mechs. Catching that means validating on
the device switch as well, which is a separate job.

The second is the **Default** button, which passed a hardcoded player 0. On
the 2P side it reset 1P's binds and left 2P's alone. The gamepad and joystick
pages both pass `ds:0xbf6bac`; this one is the odd one out.

The two slots are a fixed 32 bytes apart, padded with `nop`, because the
second one's site names its address. Let the first grow and the second moves,
and nothing downstream would notice.
