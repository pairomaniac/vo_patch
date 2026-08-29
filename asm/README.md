# asm

Source for the machine code the patches install, and for the tables and
dialog templates that go with it. `vo_patch.py` carries the finished bytes, so
nobody running the patcher or building the exe needs nasm. Only someone
editing this directory does.

For what each patch changes see [NOTES.md](../docs/NOTES.md); for the build
and release workflow see [DEVELOPING.md](../docs/DEVELOPING.md).

| File | What it holds |
| --- | --- |
| `vocd.asm` | CD audio: setup, the `mciSendCommandA` hook, the handlers |
| `timer.asm` | frame rate: the entry point stub that asks for a 1 ms tick |
| `debugbox.asm` | F11 Extras: the window procedure hook and the dialog procedure |
| `padxinput.asm` | gamepad: the entry stubs, the message-pump stub and the input tick |
| `levers.asm` | gamepad: the lever cleanup that runs after each input tick |
| `twinstick.asm` | gamepad: the arcade twin-stick profile, two stubs and its tables |
| `introwait.asm` | gamepad: polls the pad while the intro movie blocks the message loop |
| `kbpage.asm` | gamepad: two fixes to the keyboard bind page |
| `bindlist.asm` | gamepad: the shared bind page's list, picked by device |
| `bindmap.asm` | gamepad: the same pick for the page's preselect, and the startup defaults |
| `bindblock.asm` | gamepad: which block owns the shared page's binds, and the Default source |
| `blockcur.asm` | gamepad: the same block pick for the store's fused index |
| `pagesec.asm` | gamepad: keeps the letter and digit sections off the gamepad page |
| `pagesel.asm` | gamepad: the preselect half of `pagesec.asm`, and the deadzone seed |
| `inisave.asm` | gamepad: writes Keyboard (Simple)'s own v_on.ini line on OK |
| `iniload.asm` | gamepad: loads both keyboard-page blocks from their lines at launch |
| `iniparse.asm` | gamepad: the line parser `iniload.asm` runs twice, and the deadzone write-back |
| `iniall.asm` | gamepad: runs the loader for both players, and seeds the deadzone |
| `commitdev.asm` | gamepad: reseeds the live table when OK commits a device switch |
| `devorder.asm` | gamepad: maps the F7 list's display order to the fixed device numbers |
| `f11pause.asm` | F11 Extras: pauses the game and music around the dialog, and ticks its check boxes |
| `voxt.asm` | F11 Extras: the dialog's long paths - deadzone read, ini save, Defaults, Quit - at the end of the `.voxt` section |
| `movie.asm` | intro movie: measures the real window and fits the movie to it |
| `credits.asm` | ending screens: ends the credits once the button has been held a second |
| `overlay.asm` | ending screens: draws HOLD TO SKIP over the credits while it is held |
| `titlever.asm` | credit: prints the patcher's version on the title screen |
| `nameentry.asm` | ending screens: adds A to the initials screen, beside the triggers |
| `camskip.asm` | gamepad: lets A skip the win and lose screens, as Select does |
| `layout.py` | data cave layout and string table, shared by `vocd.asm` and the blob |
| `padtables.py` | gamepad: what each pad input is, what it is called, the F7 device list |
| `dialogs.py` | the F11 Extras template and its tables, and the F5 frame rate labels |
| `build.py` | builds every blob in `../vo_patch.py`, from the `.asm` and `.py` sources above |

The prefix is the patch each file ships in, which is not always the obvious
one: `camskip.asm` goes out with **XInput gamepad support** because the tick
is what calls it, `f11pause.asm` with **Disable menu bar** because the F11
hook is what runs it, while the other three ending-screen files go out
with **Intro, loading and ending screens**.

## How the assembly gets into the patcher

`vo_patch.py` never reads these files. It carries the finished machine code as
hex strings, because it ships as a single file - bundled into the exe, and
downloaded on its own by Linux users - and has to run from a fresh checkout
with nothing installed.

`build.py` copies one into the other. It runs nasm on each `.asm` file, calls
`build()` on each `.py` one, formats the output as `bytes.fromhex(...)`, and
replaces everything between a pair of comment markers. Nothing outside the
markers is touched, so the patch tables around them are safe, and nothing is
written into `asm/` either - nasm works in a temporary directory.

Three regions, and `build.py` fails if a pair is missing:

```
# VOCD BLOB BEGIN        <- VOCD_MAGICS, VOCD_CODE, VOCD_DATA
# VOCD BLOB END

# BLOBS BLOB BEGIN       <- BLOBS: every other .asm file and every packed
# BLOBS BLOB END            table, one entry each

# DIALOGS BLOB BEGIN     <- EXTRAS_TPL, F5_STOCK, F5_FPS
# DIALOGS BLOB END
```

Each `BLOBS` entry is `(code, fixups, labels)`. The code has its address
slots empty; a fixup says which slot holds which symbol and how (absolute,
or relative to the end of the slot); the labels are the offsets of the
source's labels, for another blob or the site table to name. `vo_patch.py`
links each one for the build being patched - `PADX_CODE = link('PADX',
RETAIL)` is the retail copy the names refer to, and `features(build)` links
the same blob for another build - and that is what the site table writes.

The three `.py` modules also emit an `.inc` file each, which the assembly
includes. An address both sides need - the condition table, the Extras
strings, a control id - is written once, by the module that packs the bytes
it points at.

## Addresses

No `.asm` file names an address in the game. Every place it touches is an
`extern` - `call GRESUME`, `cmp dword [DEVICES], 1` - and nasm assembles the
file as an ELF object, whose relocations say which bytes want which symbol.
`build.py` reads those out and writes them into `vo_patch.py` beside the
code as the fixup list. The addresses themselves live in one place per
build, the `symbols` table of its `Build` in `vo_patch.py`: a virtual
address for a place in the game, or `(blob, label)` for a place in one of
ours. Where a blob goes is the `caves` table beside it, or for a build
that appends a section for its blobs, the `annex` list.

The locals of the game's own functions that a stub reads - a loop counter
at `[ebp-8]` - are the frame-offset symbols, plain constants from
`frames.inc`, which `build.py` writes from the retail table. It finds
where each one sits in the code by assembling the source once more with
that constant moved and diffing; a relocation would do it, but nasm
versions disagree on what an 8-bit relocation against an extern encodes.

The `.py` modules work the same way: `padtables.py` emits the bind list
with a fixup on `PAD_NAMES` for every pointer, `dialogs.py` a fixup on each
check box's game flag. Their `.inc` files declare the labels the assembly
reads as `extern`.

So one set of machine code serves any build of the game; a second build is
a second `Build` with its own caves and symbols. The site table names its
hooks the same way - `call(0x0009703f, ('PAGESEC', 'fillsec'), 5)` computes
the rel32 from the cave, and `site('PADX')` the file offset a blob is
written at - so there is no hand-computed address for the two to disagree
on.

Two things still carry a fixed shape. `padxinput.asm` pins six offsets
inside itself with `times`, because the site table and other blobs name
them, and pads to `PADX_LEN` because `levers.asm` is written straight after
it. `debugbox.asm` pins its dialog procedure one byte past the hook. `build.py`
checks both against the labels.

## The loop

```
sudo dnf install nasm            # or: sudo apt install nasm

vim asm/vocd.asm                 # 1. edit an .asm file, or a .py one
python3 asm/build.py             # 2. rebuild the blobs in vo_patch.py
git diff                         # 3. vo_patch.py's hex strings changed
```

Step 2 also runs `vo_patch.py --selfcheck`, which validates the patch tables,
so a run that prints `tables OK` has verified both halves.

You never edit the hex in `vo_patch.py` yourself. The generated regions carry
a GENERATED banner saying so, and the next `build.py` run would overwrite the
edit anyway.

## What CI checks

The `verify` job in `.github/workflows/build.yml` runs on every push to main,
every `v*` tag and every pull request, and the Windows build will not start
until it passes. It installs nasm and runs `python3 tools/check.py`, the same
runner you would run locally:

| Check | Catches |
| --- | --- |
| `tables` | patch table broken: bad length, offset past the end, two patches on one byte, site list reordered; banner bitmap the wrong size or its tiles out of range |
| `asm` | a source edited without the blobs being regenerated; a blob's site and the address its source names disagreeing; a blob grown past a `CEILINGS` pin; a call the site table writes into a cave landing on no assembled label |
| `net` | the baked netplay DLL not built from the current `net/dpctrl.c` |
| `lint` | pyflakes: unused names, undefined names, bad imports |
| `tree` | blobs regenerated but not committed |

`asm` is the one that matters here. Without it the repository stays
self-consistent while the shipped patcher installs last week's code - nothing
else in the project would notice.

What CI cannot do is touch a real `v_on.exe` or `escrgame.bin`, because the
game is not in the repository, so two checks are skipped there. Run them
before tagging by giving the runner a game folder:

```
python3 tools/check.py /path/to/VIRTUAL-ON
```

`offsets` is the only check that catches a wrong offset. It verifies every
`original` column against the real file, applies 350-odd combinations of
patches, and compares the fully patched MD5 against `EXPECTED_ALL` in
`tools/selftest.py`. `banner` is the only one that proves the tile indices in
the executable and the artwork in `escrgame.bin` still line up.

## Where each blob lands

**`vocd.asm`** becomes a blob of its own in a new `.vocd` section that
`apply_cdaudio` appends to the executable. The code starts with two thunks:
`+0` is the hook, which the game's 37 `mciSendCommandA` call sites are
rewritten to call, and `+5` is the setup the entry point is repointed at. The
data blob is the string table plus the space the code works in - track table,
path and command buffers.

The hook reads the winmm import slot to forward what it does not answer,
but does not own it: any DLL hooking the same import overwrites the slot,
whereas rewritten call sites cannot be undone. Forwarding through the slot as
it stands leaves whoever does own it in the chain below.

Absolute addresses are placeholders (`0xE1E1E1E1` and friends) that
`apply_cdaudio` fills in once it has read the executable: import slots, the
previous entry point, and where the blobs landed. Everything else is self
relative, so the section can go anywhere.

**`padtables.py`** fills the condition table, the bind list and the strings
both point at, and the device list in `.data`. **`dialogs.py`** fills the
`.rdata` cave the Extras box reads, the template payload for the `.voxt`
section the patch appends, and the tail of the F5
resource that the frame rate labels live in. That last one is the only site
whose `original` column is generated too: the stock labels packed by the same
code, which is the check that this packing matches the resource compiler's.

**`padxinput.asm`**, **`levers.asm`**, **`twinstick.asm`** and **`kbpage.asm`**
each go to one site inside the XInput patch table, at `0x00207460`,
`0x0020779e`, `0x00223dc4` and `0x0023dd38`. Every entry reads its length
from the blob, so a routine can change size without the run of `00` beside it
needing a manual edit - but it
has to stay inside its cave, so growing one past the cave means a new cave
in the `caves` table and not just a rebuild. The thirteen keyboard profile
and F11 files in the cave table above work the same way: one site each,
length read from the blob, the cave named in the table.

## Space left in the executable

Sites written into section padding, and what is still free after them:

| Cave | File range | Size | Used | Free |
| --- | --- | --- | --- | --- |
| `.text` past VirtualSize | `0x1f423e`-`0x1f4400` | 450 | 328 | 122 |
| `.rdata` past VirtualSize | `0x23dce8`-`0x23de00` | 280 | 252 | 28 |
| `.rsrc` past VirtualSize | `0x60c25c`-`0x60c400` | 420 | 396 | **24** |

The `.text` cave holds `timer.asm` and `debugbox.asm`, and has room again:
the dialog procedure is a dispatcher now, its long paths - the deadzone
read, the ini save, Quit's teardown order - living in `voxt.asm` at the
end of the template's section, reached through a rel32 the patcher fills;
the run of zeros
continuing past
`0x1f4400` is not more cave - `0x5f5000` is a `qword` 0.0 that `0x401ce4`
compares against, and `0x5f5008` a 1.0 that three sites read. `BOXLEN` stops
at the first of them and `build.py` checks it. This is why CD audio got a
section of its own rather than another cave. The `.rdata` one holds the
Extras box's strings and tables, the keyboard page fixes and the intro-movie
message wait; the `.rsrc` one holds `movie.asm`. The dialog template is in
neither - it goes in the `.voxt` section the patch appends, with
`voxt.asm` after it. Anything else of any size wants its own section, the
way `vocd.asm` has one.

Inside the `.vocd` data blob there is room: `D_CMD` holds 448 bytes against a
worst case of 318, and `D_TOC` is an exact fit at 100 dwords. Changing
`layout.py` costs nothing in the file until the section passes 3 KB, because
it is page aligned.

Most of the gamepad patch's caves are runs of zeros inside `.rdata` proper
rather than padding, which is why that section's characteristics get the
execute bit:

| Cave | File range | Size | Used | Free |
| --- | --- | --- | --- | --- |
| routine and lever cleanup | `0x207460`-`0x2077e0` | 896 | 881 | **15** |
| input names, profile names and deadzone keys | `0x223f9b`-`0x224058` | 189 | 177 | 12 |
| bind list table | `0x223c43`-`0x223d00` | 189 | 128 | 61 |
| condition table | `0x22411b`-`0x2241cb` | 176 | 128 | 48 |
| twin-stick stubs, binds, masks, blocks | `0x223dc4`-`0x223e73` | 175 | 164 | **11** |
| keyboard page fixes | `0x23dd38`-`0x23dd6d` | 53 | 53 | 0 |
| intro-movie message wait | `0x23dd70`-`0x23de00` | 144 | 136 | 8 |
| win and lose skip, initials | `0x23d1a0`-`0x23d23c` | 156 | 91 | 65 |

The last two of the first seven are the `.rdata` padding from the table
above, so they appear twice. The last row is shared: `camskip.asm` ships with
the gamepad patch and `nameentry.asm` with the ending screens, so either can
be present without the other.

The keyboard profile work sits in thirteen more runs on the same terms, one
file per run - which is why there are so many small files: the free zero
runs of any size are scattered, and a routine only works at the address it
was assembled for. Grouping is by concern in the sections at the end of
this file, not by address:

| Cave | File range | Size | Used | Free |
| --- | --- | --- | --- | --- |
| `bindlist.asm` | `0x1fcbe4`-`0x1fcc58` | 116 | 103 | 13 |
| `bindmap.asm` | `0x1fcd04`-`0x1fcd78` | 116 | 114 | **2** |
| `bindblock.asm` | `0x1fe64c`-`0x1fe6c0` | 116 | 94 | 22 |
| `blockcur.asm` | `0x1fcc64`-`0x1fccb8` | 84 | 71 | 13 |
| `commitdev.asm` | `0x1fcb4c`-`0x1fcb98` | 76 | 57 | 19 |
| `iniall.asm` | `0x23b9f4`-`0x23ba4c` | 88 | 88 | **0** |
| `iniparse.asm` | `0x200f0c`-`0x200f6c` | 96 | 92 | **4** |
| `pagesec.asm` | `0x200f70`-`0x200fd0` | 96 | 60 | 36 |
| `pagesel.asm` | `0x200fd4`-`0x201034` | 96 | 91 | **5** |
| `inisave.asm` | `0x201038`-`0x201098` | 96 | 86 | **10** |
| `iniload.asm` | `0x20642c`-`0x2064a0` | 116 | 79 | 37 |
| `devorder.asm` | `0x203b34`-`0x203b90` | 92 | 54 | 38 |
| `f11pause.asm` | `0x23b324`-`0x23b37c` | 88 | 87 | **1** |

`inisave.asm`'s cave ends early: `0x601c98` is a live address `0x4cf61b`
reads, inside what scans as a longer run - the trap the second check below
describes. `iniall.asm` is full, and `bindmap.asm` and `f11pause.asm` have
a byte or two each; growing any of them is a rehoming job.

`iniall.asm` moved to this cave in v0.10.2. It sat at `0x1fa544` before,
which is zeros in the file and not free: the attract loop's scoreboard state
copies 21 dwords from `0x5fb140` into its own record and reads an index out
of them, so the blob's bytes were the index and the loop crashed on its way
back to the title screen.

The ending screens and the credit use three more runs of zeros in `.rdata`,
on the same terms:

| Cave | File range | Size | Used | Free |
| --- | --- | --- | --- | --- |
| credits skip | `0x23cad0`-`0x23cb6c` | 156 | 99 | 57 |
| HOLD TO SKIP overlay | `0x1f74e0`-`0x1f7588` | 168 | 142 | 26 |
| title screen version | `0x223198`-`0x223240` | 168 | 109 | 59 |

The last two are the pair the three checks below warn about, and the last
runs of that size. Picking a new cave means finding a run of zeros and
proving it is free, which takes those three checks.

Addresses below are virtual; the tables above are file offsets. The two
differ by `0x400c00`.

**Nothing points into it.** Search the file for a dword equal to any address
in the span, and read the instruction at each hit - four bytes of data can
happen to equal an address, so counting hits is not enough. `selftest.py`
does this for every cave already in use, on the real file; the three checks
here are for picking a new one.

`0x6083e0` sits in the middle of the run the XInput routine's cave was cut
from. It is a base address the geometry code indexes off, reading as zeros
because that is what the game expects there. Writing over it corrupts the
loading screens, so the routine stops short at `0x6083d1` and the usable tail
is fifteen bytes rather than sixty-five.

**It does not end inside one.** A run that abuts a small constant scans as
longer than it is, so check what the bytes after it belong to.

`0x1f74e0` and `0x223198` scan as free 174-byte runs and are neither free nor
174 bytes; both are in use now, at the 168 they really hold. `0x623d08` is a
table of twenty-byte entries the code at `0x5be302` walks, and each run ends
inside a `qword` the FPU loads: both `0x5f8188` and `0x623e40` hold `480.0`,
which is `00 00 00 00 00 00 7e 40`.
Six leading zero bytes, so the scan overshoots by six.

**Its start is a multiple of four.** Every address in this image is below
`0x01000000`, so every pointer has a zero top byte, and the longest run of
`00` begins one byte inside the last pointer of a table. Writing there turns
`0x00623bb0` into `0x11623bb0` and the game dies dereferencing it. A cave
picked by hand needs the same.

All of these sit past their section's VirtualSize but inside SizeOfRawData,
so the loader maps them. Any tool that rebuilds the PE from VirtualSize will
silently drop them.

## vocd.asm

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

It never opens a track. Redbook audio is 44100 Hz 16-bit stereo and a CD frame
is 2352 bytes, so after the 44-byte WAV header the file size is the track
length. The whole table of contents comes from `GetFileSize`, with no WAV
parsing.

It installs nothing: the call sites already point at the hook. Finding no
tracks just leaves the track count at zero, which the hook reads as "forward
everything", and the game reads a disc as it always did. Last thing it does is
jump to whatever the entry point used to be, which is why this patch has to be
applied after all the others.

**The hook** sees every `mciSendCommandA` the game makes. It watches for an
open of the `cdaudio` device and hands back a fake device ID, `0xFACE`. From
then on, calls carrying that ID belong to it, and every other call, sound
effects included, goes on through the import slot untouched.

Play requests are turned into MCI *string* commands against `waveaudio`:

```
open "<gamedir>\music\track05.wav" type waveaudio alias vocdbgm
set vocdbgm time format milliseconds
play vocdbgm
```

So it does not implement playback at all - no buffers, no mixing, no streaming
thread. It rewrites a CD command as a WAV command and lets the same MCI
subsystem do the work, which is also why it needs nothing from Wine beyond
`mciwave`.

Status queries are answered from the table: track count, track lengths, media
present, time format, and track 1 reported as data so the game does not try to
play it. Only *is it still playing* cannot be answered from state, so that one
sends `status vocdbgm mode` and compares the answer against `playing`. The
game's own polling drives everything; nothing here runs on its own.

Unrecognised messages return success without doing anything. Failing them
would make the game give up on music entirely.

## timer.asm

The entry point the frame rate patch redirects to. It calls
`timeBeginPeriod(1)` and jumps to the entry point that was there before.
Windows 2000 and later default to a 15.6 ms scheduler tick, and the game's
frame pacing sleeps in milliseconds against it, so the wait rounds up and the
game runs at about 70 per cent speed. Wine already ticks at 1 ms.

`winmm.dll` is resolved through `LoadLibraryA` rather than imported, and a
failure is ignored: on a system that does not need the call there is nothing
to fail over.

`nodisc` chains this same entry point in turn, which is why it is applied
after every other patch.

## debugbox.asm

The Debug options only ever existed as menu items, so once the menu bar goes
there is nothing to open them with. This builds a dialog instead.

**The hook** replaces the window procedure pointer at `0x1c4d7e`. It watches
for F11 and passes everything else to the handler that was there before. On
F11 it fetches `DialogBoxIndirectParamA` through `LoadLibraryA` and
`GetProcAddress` - the import table has no room and rebuilding it for one
export is not worth it - and opens the template from the `.voxt` section
the same patch appends; `f11pause.asm` holds a placeholder for its address,
filled at apply time.

**The dialog procedure** ticks each check box from the game's own flag on
`WM_INITDIALOG` (through the loop in `f11pause.asm`'s tail), shows both
players' deadzone digits, and forwards clicks. Close, Defaults and Quit go
to the annex in `voxt.asm`, which reads the boxes back on close - two
digits clamped to 5-95 each, into the thresholds the tick compares per
player and out to their v_on.ini lines through `iniparse.asm`'s tail, a
rejected entry re-seeded to the percent in force - seeds 40s on Defaults,
and says whether to post. Every
control's id is the game's own command id, so a click is posted straight to
the main window as `WM_COMMAND` and needs no lookup table; the deadzone
edits are the exception, their notifications being the dialog's own.
Closing writes the boxes back and, when the gamepad patch's `iniparse.asm`
cave is patched - its first byte says - calls the write-back in its tail,
so the values land in their v_on.ini lines.

Credits is the exception to the rule. There is no menu item behind it, so the
procedure acts on it rather than posting it: `0x1f` into the sub-state at
`0x1ae3690`, which sets the ending up and steps to the credits, `0x20`, on
its own. Only while `0x1ae3594` reads 4, since that state number means
something else in the title and attract tables. It is written with a
`push`/`pop` pair rather than a `mov` because the cave has exactly two bytes
to spare; see `BOXLEN`.

It assembles as one run but lands as two sites, since the byte in front of the
dialog procedure is alignment padding the patch has never written. `build.py`
splits the blob there and refuses to do it if the source puts anything but a
zero in that byte.

The strings, the two tables it reads and the dialog template are data, packed
by `dialogs.py`.

## padtables.py

Sixteen pad inputs, described once:

```python
('LT',       TRIGGER, LTRIGGER, PULL),
('LS Up',    ABOVE,   LY,  DEADZONE),
('LS Down',  BELOW,   LY, -DEADZONE),
```

From that list it packs the condition table the tick reads, the bind list the
F7 page offers, and the strings both point at, computing the pointers between
them. The four profile names sit in the same string blob, so the device list
is built from it too.

An input's id is `0xe0` plus its position in the list, which is also its index
into the condition table. Reordering the list moves everyone's saved binds.

`DEADZONE` is what a stick axis has to pass to count as pushed, out of 32767.
It is per axis rather than radial, so a 45 degree push puts 23170 on each and
diagonals have room to spare. 13000 is about 40%, above Microsoft's own 7849
and 8689, which are loose enough to pick up drift on a worn stick. Since the
threshold went runtime - `asm/iniall.asm` seeds it, the F11 boxes change it
per player, and the tick indexes the pair by the parameter block's player -
the table's axis values only pick the side of zero, and this constant is the
shipped default's ancestor rather than what the tick compares.

## dialogs.py

Both dialogs, from a control list each.

The Extras box is built outright, and the ids in it are the game's own command
ids, so the dialog procedure can post a click to the main window with no
lookup table. The same list carries the flag each check box reflects, which
becomes the table `debugbox.asm` walks on `WM_INITDIALOG`. `dialogs.inc` gives
the assembly the addresses of both. The template goes to the `.voxt` section
the patch appends, so nothing prices the labels any more: the font block is
back, the buttons carry their full names, and the deadzone group -
*Stick Deadzone % [ XInput ]* - holds `1P`, `2P` and `%` labels around the
two digits-only edits, its own Defaults button, and the min/max hint below
in the dialog's one font, templates carrying a single font block. The bottom
row keeps the dangerous away from the habitual: Quit Game at the left, Close
alone at the right where the closing hand goes. The
dead menu resource the template used to squeeze into is left as it came.

The F5 frame rate labels are the other case: an edit to a resource the game
already has. Sega's *Fast* and *Smooth* read **30 FPS** and **60 FPS** now.
The first is wider than *Fast*, so everything after it in the resource shifts
by four bytes, which is what the size field at `0x6035ac` gains.

## padxinput.asm

The gamepad patch's own code: two profile entry stubs, the message-pump stub,
the per-player input tick, and a parameter block per player. Everything else
in the patch is tables.

The **tick** runs through the F7 profile dispatch, once per player per frame.
It resolves `XInputGetState`, polls the pad, writes Space and the camera key
into that player's key buffer if A or Back is held - before calling the game's
keyboard handler, because that is the code which reads them - and then walks
twelve bind slots, testing each against the condition table and clearing the
lever bits its mask names.

Not every slot is live in every game state. The stock keyboard handler at
`0x443074` runs all twelve only when `[0x1ae3594]` is 4 and `[0x1ae3690]` is
8 to 12; everywhere else it skips turn and stops after slot 7, so jump, dash
and guard do nothing. The tick applies the same test. Without it a button
bound to jump walks the cursor in a menu, because the menus are read out of
the lever words and the jump masks are lever bits like any other.

After those twelve it applies the D-pad to the first four slots' masks
directly. The menus and the mech list are read out of the lever words rather
than out of keys, which is why the sticks navigate them and why this does too.
It cannot be a bind: the engine is one input per slot and the left stick
already holds those four.

The **pump stub** replaces one `call PeekMessageA` in the main loop. The tick
does not run while the game is paused, so Start is posted as F3 from here,
and A as Space alongside it.

Only keys the window procedure handles itself are worth posting. Everything
else the game reads through DirectInput, where a posted message never
arrives. F3 works because it is the same handler the F5, F7 and F11 dialogs
hang off.

Both pollers read the same resolved import but keep separate `XINPUT_STATE`
buffers and separate edge state, so neither can eat the other's press.

Several addresses in the file are named from outside it: the entry stubs by
the profile dispatch, the pump stub by the `PeekMessageA` call site, the poll
by `introwait.asm`, the tick by `twinstick.asm`, and the epilogue by
`levers.asm`, whose site expects those five bytes where they are. The blob is
also padded to a fixed 830 bytes, because `levers.asm` is written immediately
after it. `times` pins all of it, so nasm fails rather than quietly shifting
anything.

## levers.asm

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

## introwait.asm

The pad cannot reach the intro movie, and the reason is the message loop
rather than the pad. `0x6bc598` picks between two loops; the movie leaves it
at 1, which blocks in `GetMessageA`, and the pump stub is on the other
branch. A blocked loop only turns over when a message arrives, and a button
press is not one.

So this replaces that call. It polls the pad, checks the queue with
`PM_NOREMOVE`, sleeps 8 ms, and makes the real call once something is there,
so the game receives exactly what `GetMessageA` would have returned. The
stack it is entered on is already the frame `GetMessageA` expects, so the
last step is a jump and not a call.

`Sleep` is not imported and comes through `GetProcAddress`; the pointer is
cached in the `.data` scratch rather than in the blob, which is executable
here but not writable. If it cannot be resolved the stub falls through to the
blocking call, which is what the game did before.

## twinstick.asm

There is no logic in it. The XInput tick already walks twelve bind slots,
tests each against a condition, and clears the bits its mask names in the two
lever words. Feed that engine a different set of binds and masks and it
becomes the arcade scheme: one thumbstick direction per slot instead of one
named action, so the sticks land straight in the levers and the game works
out walking, turning, jump and crouch from the pair.

So the file is two entry stubs, a bind list, two mask tables and a parameter
block per player, mostly tables.

## kbpage.asm

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

## The bind page files

Six files, one concern: the bind page Simple and the gamepad share, told
apart by the pending device. `bindlist.asm` picks the (name, id) input
list the fill and store walk - the game's 33 named keys or the 16 pad
inputs. `bindmap.asm` is the same pick for the preselect that maps a saved
bind back to a combo index, and carries the startup defaults writer for
Simple's block in its tail. `bindblock.asm` picks which saved block a
route touches, `+0x08` or `+0x38`, and the Default button's source table;
`blockcur.asm` is the same pick for the store, whose call site passes a
fused player-and-slot index the shared check cannot use. `pagesec.asm`
and `pagesel.asm` keep the letter and digit sections off the gamepad
page - fill and store in the first, preselect in the second, split only
because no free run held both. The mechanism is in NOTES.md under *The
shared bind page*.

## The ini files

Four files, one concern: Keyboard (Simple)'s binds surviving a restart,
which the stock game never had to do for that slot. `inisave.asm` writes
the "NP Simple Assign" line when OK commits, then falls into the stock
serializer. `iniload.asm` runs at launch in place of the call that used
to overwrite the block with joystick defaults, parsing each keyboard-page
block's line back through `iniparse.asm`, the 48-hex-character parser it
runs twice. `iniall.asm` is a trampoline at the ini loader's exit that
runs the same loader for both players whatever the saved devices, since
the stock loader runs one device-picked section per player and would skip
it otherwise. The full story is in NOTES.md under *Saving and loading*.

## commitdev.asm and devorder.asm

Both serve the F7 device page. `commitdev.asm` wraps the plain-OK commit
and reseeds the shared live table from the new device's block, so a
switch between Simple and the gamepad takes effect without a trip through
the bind page - the stock commit only stored the device number, because
no two stock devices shared a live table. `devorder.asm` maps the list's
display order (gamepad, twin-stick, Simple, Real) to the fixed device
numbers and back, at the page's preselect and its OK translate; the
numbers themselves stay what the executable and `v_on.ini` always used.

## f11pause.asm

The F11 Extras dialog's runner. The built-in F-key dialogs pause the game
and the music around their DialogBox call and resume after; the F11 hook
in `debugbox.asm` had no room left in its cave for the same calls, so the
whole DialogBox block moved here and gained them. The tail is the dialog's
check-box init, evicted from the same cave when the second deadzone box
needed the room. Ships with **Disable menu bar**, not the gamepad patch.

## movie.asm

Places the intro movie's window. The game does this itself from `0x54e817`,
using an offset out of two globals that are each a hardcoded centre for one
movie size in a 640x480 picture, so under any upscaler the movie ends up
small and in a corner.

The obvious fix is to read the window's real size, and the obvious call for
that is the `GetClientRect` the routine already makes and discards. It does
not work: cnc-ddraw hooks `GetClientRect` and answers with the game's own
640x480 whenever it is asked about the game window, which is the whole point
of the hook. `GetWindowRect`, `GetSystemMetrics`, `MoveWindow` and
`SetWindowPos` are hooked with it, so no call the game can make tells the
truth.

cnc-ddraw exports `DDGetProcAddress` for this, forwarding to the real
`GetProcAddress`. So this resolves `ddraw.dll`, asks it for user32's
`GetClientRect`, and measures with that. Every step falls back to the
imported one, which without cnc-ddraw is the real function anyway and gives
what the game did before.

From the real client rect it takes the biggest rectangle of the movie's own
shape that fits, centres it, and writes the result back into the caller's
frame - so the game's own `MoveWindow` a few instructions later does the
work, with the values it was going to use replaced. mciavi does not follow
the window, so the last thing the stub does is send `MCI_PUT` with a
destination rect. The game sends no `MCI_PUT` of its own, and this is the
only one.

It reaches `mciSendCommandA` through a register rather than the six-byte
indirect call, because `apply_cdaudio` counts those and aborts on anything
but 37.

## credits.asm

Makes the ending credits skippable, which they are not in the stock game.

The credits are sub-state `0x20`, and its handler at `0x59081f` is a phase
machine on `0x1ad0964`: 0 and 1 are the ending cutscene and the mission
complete screen, 2 is the roll, and anything else falls through to the tail
at `0x5908f2` that stops the music and moves on to the name entry. So the
skip is one write - put the phase past 2 and the game ends the sequence its
own way on the next frame. None of that teardown is repeated here.

The input is not the one the game over and ranking screens test. Those read
the press edges at `0x1ed5ec4`, which `0x56207a` builds from the 1P input
block - and that does not run in this state, so the word sits at zero for
the whole sequence. The key buffer is live throughout - it is the
DirectInput keyboard state at `0xbf0448`, filled at `0x442d19` - so the
slots read here are Space's and the camera key's, which the tick also
writes for A and Select.

It is a hold rather than a press. A press that starts during the roll
begins a count, the phase is written once the count reaches `HOLD`, and
releasing zeroes it; the sub-state runs once a tick whatever Motion is set
to, so 60 is about a second. A button already held when the roll opens
never starts a count, which is what keeps the press that skipped the win
screens from carrying through. `PREV` is shared with `nameentry.asm`, which
needs the same reading for its own edge.

It runs in place of the write that opens the handler, so that write is
repeated at the end of the stub.

## nameentry.asm

Adds A to the initials screen, which stock takes a letter on only from the
weapon triggers: LT for 1P at `0x4d6cc8` and RT for 2P immediately after it.
Both tests are replaced by a call to this, which answers the same question
with A folded in, so the triggers keep working.

A is not in the press edges those tests read. `0x1ed5ec4` is built from the
lever words and A is a key, so it arrives in the key buffer slot instead -
which is the DirectInput keyboard state at `0xbf0448`, filled at `0x442d19`,
so the slot is Space's and a keyboard player gets this without the gamepad
patch. A level rather than an edge, worked out here the same way
`credits.asm` does.

Both read the camera slot alongside the accept one, so the pair that skips
the win and lose screens is the pair that works here too. Both read 1P's
slots and only 1P's, so 2P skips nothing and enters initials on RT alone.

The two share `PREV` on purpose. Skipping the credits with A lands on this
screen a frame or two later with A still held, and one shared byte is what
keeps that press from being taken as the first letter as well. They ship in
the same patch, so they are always both there or neither.

## camskip.asm

Lets A skip the win and lose screens, which stock only takes on the camera
key.

Those screens do not read the accept key. They test bit 4 of the input word
`0x56207a` builds, which the camera key sets and A does not, so Select skips
them and A does nothing. The tick already writes the camera slot when Back
is held; this writes the same slot for A.

Only on those screens, though. Everywhere else the camera key swings the
camera, and A is jump by default, so it is gated on `MODE` 4 with `SUBMODE`
`0x0c` or `0x14` - a round is `0x0a`. The tick calls it from inside the
branch that has already established A is held, with `ebx` still holding the
parameter block the camera slot comes out of.

## overlay.asm

Draws `HOLD TO SKIP` over the credits while the button is down.

The tile font was the obvious way and the wrong one: that layer is what the
roll scrolls, so anything printed into it climbs the screen with the
credits. `0x5c991c` takes screen pixels instead, the same call the pause
text uses, including the halving at `0x5c9a98` for low resolution.

Two things about when and where it paints. It paints at the moment it is
called, so it has to run after the frame is drawn: the hook is the call at
`0x5c64e7`, five bytes before the surface is flipped at `0x5c650d`, and the
stub makes that call first with its argument untouched. And it paints on
`0x1ae5f40`, the primary surface, which suits the pause screen because that
is not flipping - here the back buffer is flipped over it the same frame, so
that global is pointed at the back buffer, `0x1ae5f5c`, across the call and
put back after.

The gate is `MODE` 4, `SUBMODE` `0x20` and phase 2, and only then the hold
count. The count alone is not enough: `HELD` sits in a run of zeros in
`.data` that something else in the game writes through, so it reads nonzero
on the title screen and in a match. `credits.asm` owns it while the roll is
running, which is the only place this looks at it.

## titlever.asm

Prints `vo_patch <version>` in the bottom right of the title screen.

The game's own tile font, the one the menu items on that screen are set in:
`0x4cd8c3` puts the cursor at a cell and `0x4ceeeb` prints through it, the
pair `0x44b757` uses with the table at `0x6537c0`. The map is 81 cells wide;
the copyright line is 59 of them from column 1 at row 44, which is what the
row here is measured against. `0x4ceeeb` picks the glyph set out of the four
at `0x600ec8` by scanning the string for lower case, so this one can be mixed
case.

The column is worked out from the string's length rather than fixed, so the
line sits against the right edge whatever the version is. It has to be: a
character is two cells and nothing wraps the column, so a fixed column that
suits a tag reading `0.8.7` runs a commit build's longer SHA past the end of
the row and into the start of the next one.

Not GDI. `0x5c991c` takes an index into the two fonts the game builds at
`0x5c8cd7`, `century` and `modern` bold at 24px, rather than a handle, so
there is no third face and no smaller size.

The gate is machine 1, whose dispatcher is `0x44b38c` and whose table is
`0x5fb238`. `0x1ae3594` picks the machine through `0x5fe5e0` and `0x1ae3690`
is its state.

| State | Handler | Screen |
| --- | --- | --- |
| `0x06` | `0x545dfa` | the logo with the blinking banner |
| `0x17` | `0x44b89d` | the same screen later in the loop; it calls `0x545dfa` |
| `0x11` | `0x44b5bc` | the logo with the menu |
| `0x07` | `0x54618b` | the demo match, which is why this is three tests and not a range |

Those four were read off the game, from a build that printed both globals
with the gate removed. `0x66c1ac`, which the banner routine tests, is the
asset set that is loaded rather than the screen.

The hook is the load at `0x5c6500`, four instructions past the call
`overlay.asm` took and still ahead of the flip at `0x5c650d`, so the two
patches share no bytes and either can be applied without the other. The
displaced load is repeated before the `ret`.

The version string is not in the blob: the blob carries zeros where it goes
and `stamp_version` writes it after the patch table has been applied. That is
what leaves `EXPECTED_ALL` in `selftest.py` one digest rather than one per
release.
