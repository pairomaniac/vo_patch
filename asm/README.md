# asm

Source for the machine code the patches install, and for the tables and
dialog templates that go with it. `vo-patch.py` carries the finished bytes, so
nobody running the patcher or building the exe needs nasm. Only someone
editing this directory does.

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
| `movie.asm` | intro movie: measures the real window and fits the movie to it |
| `layout.py` | data cave layout and string table, shared by `vocd.asm` and the blob |
| `padtables.py` | gamepad: what each pad input is, what it is called, the F7 device list |
| `dialogs.py` | the F11 Extras template and its tables, and the F5 frame rate labels |
| `build.py` | builds every blob in `../vo-patch.py`, from the `.asm` and `.py` sources above |

## How the assembly gets into the patcher

`vo-patch.py` never reads these files. It carries the finished machine code as
hex strings, because it ships as a single file - bundled into the exe, and
downloaded on its own by Linux users - and has to run from a fresh checkout
with nothing installed.

`build.py` copies one into the other. It runs nasm on each `.asm` file, calls
`build()` on each `.py` one, formats the output as `bytes.fromhex(...)`, and
replaces everything between a pair of comment markers. Nothing outside the
markers is touched, so the patch tables around them are safe, and nothing is
written into `asm/` either - nasm works in a temporary directory.

One region per blob, and `build.py` fails if a pair is missing:

```
# VOCD BLOB BEGIN        <- VOCD_MAGICS, VOCD_CODE, VOCD_DATA
# VOCD BLOB END

# PADX BLOB BEGIN        <- PADX_CODE
# PADX BLOB END

# LEVERS BLOB BEGIN      <- LEVERS_CODE
# LEVERS BLOB END

# TWIN BLOB BEGIN        <- TWIN_CODE
# TWIN BLOB END

# INTROWAIT BLOB BEGIN   <- INTROWAIT_CODE
# INTROWAIT BLOB END

# KBPAGE BLOB BEGIN      <- KBPAGE_CODE
# KBPAGE BLOB END

# MOVIE BLOB BEGIN       <- MOVIE_CODE
# MOVIE BLOB END

# DEBUGBOX BLOB BEGIN    <- DEBUGBOX_HOOK, DEBUGBOX_PROC
# DEBUGBOX BLOB END

# TIMER BLOB BEGIN       <- TIMER_CODE
# TIMER BLOB END

# PADTABLES BLOB BEGIN   <- PAD_COND, PAD_BINDS, PAD_NAMES, PAD_DEVLIST
# PADTABLES BLOB END

# DIALOGS BLOB BEGIN     <- EXTRAS_TPL, EXTRAS_DATA, F5_STOCK, F5_FPS
# DIALOGS BLOB END
```

The three `.py` modules also emit an `.inc` file each, which the assembly
includes. An address both sides need - the condition table, the Extras
strings, a control id - is written once, by the module that packs the bytes
it points at.

## Addresses that cannot move

Most `.asm` files carry an `org` - `padxinput.asm`, `twinstick.asm`,
`introwait.asm`, `kbpage.asm`, `debugbox.asm`, `movie.asm` and `timer.asm`. Their stubs
jump to fixed addresses and their parameter blocks point at tables in the
same blob, so the code only works where it was assembled to sit. The `.py`
modules hardcode addresses for the same reason: `COND` and `TEMPLATE` are
read by the assembly, `NAMES` and `BINDS` are pointed at from inside the
blobs.

So the source names a place as a virtual address and the patch table names
the same place as a file offset. `build.py` checks the two agree, reading the
offsets out of the patch table rather than keeping a second copy here.
Nothing downstream would notice a mismatch: the bytes would be written, and
every address into them would be a few bytes out.

## The loop

```
sudo dnf install nasm            # or: sudo apt install nasm

vim asm/vocd.asm                 # 1. edit an .asm file, or a .py one
python3 asm/build.py             # 2. rebuild the blobs in vo-patch.py
git diff                         # 3. vo-patch.py's hex strings changed
```

Step 2 also runs `vo-patch.py --selfcheck`, which validates the patch tables,
so a run that prints `tables OK` has verified both halves.

You never edit the hex in `vo-patch.py` yourself. The generated regions carry
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
| `asm` | a source edited without the blobs being regenerated; a blob's site and the address its source names disagreeing |
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
both point at, and the device list in `.data`. **`dialogs.py`** fills the `.rdata` cave the Extras box reads, the
`.rsrc` run the dead menu resource left behind, and the tail of the F5
resource that the frame rate labels live in. That last one is the only site
whose `original` column is generated too: the stock labels packed by the same
code, which is the check that this packing matches the resource compiler's.

**`padxinput.asm`**, **`levers.asm`**, **`twinstick.asm`** and **`kbpage.asm`**
each go to one site inside the XInput patch table, at `0x00207460`,
`0x0020779e`, `0x00223dc4` and `0x0023dd38`. Every entry reads its length from the blob, so a routine can
change size without the run of `00` beside it needing a manual edit - but it
has to stay inside its cave, and the last two carry an `org`, so growing them
past the cave is a source edit and not just a rebuild.

## Space left in the executable

Sites written into section padding, and what is still free after them:

| Cave | File range | Size | Used | Free |
| --- | --- | --- | --- | --- |
| `.text` past VirtualSize | `0x1f423e`-`0x1f4400` | 450 | 426 | **24** |
| `.rdata` past VirtualSize | `0x23dce8`-`0x23de00` | 280 | 272 | **8** |
| `.rsrc` past VirtualSize | `0x60c25c`-`0x60c400` | 420 | 396 | **24** |

The `.text` cave holds `timer.asm` and `debugbox.asm` and has 24 bytes left,
which is why CD audio got a section of its own rather than another cave. The
`.rdata` one holds the F11 dialog template, the keyboard page fixes and the
intro-movie message wait, and is now nearly full too. Anything else of any
size wants its own section, the way `vocd.asm` has one.

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
| input names and profile names | `0x223f9b`-`0x224058` | 189 | 135 | 54 |
| bind list table | `0x223c43`-`0x223d00` | 189 | 128 | 61 |
| condition table | `0x22411b`-`0x2241cb` | 176 | 128 | 48 |
| twin-stick stubs, binds, masks, blocks | `0x223dc4`-`0x223e73` | 175 | 164 | **11** |
| keyboard page fixes | `0x23dd38`-`0x23dd6d` | 53 | 53 | 0 |
| intro-movie message wait | `0x23dd70`-`0x23de00` | 144 | 136 | 8 |

The last two are the `.rdata` padding from the table above, so they appear
twice.

There is no comfortable room left anywhere. Picking a new cave means finding
a run of zeros and proving it is free, which takes three checks.

Addresses below are virtual; the tables above are file offsets. The two
differ by `0x400c00`.

**Nothing points into it.** Search the file for a dword equal to any address
in the span, and read the instruction at each hit - four bytes of data can
happen to equal an address, so counting hits is not enough.

`0x6083e0` sits in the middle of the run the XInput routine's cave was cut
from. It is a base address the geometry code indexes off, reading as zeros
because that is what the game expects there. Writing over it corrupts the
loading screens, so the routine stops short at `0x6083d1` and the usable tail
is fifteen bytes rather than sixty-five.

**It does not end inside one.** A run that abuts a small constant scans as
longer than it is, so check what the bytes after it belong to.

`0x1f74e0` and `0x223198` scan as free 174-byte runs and are neither free nor
174 bytes. `0x623d08` is a table of twenty-byte entries the code at
`0x5be302` walks, and each run ends inside a `qword` the FPU loads: both
`0x5f8188` and `0x623e40` hold `480.0`, which is `00 00 00 00 00 00 7e 40`.
Six leading zero bytes, so the scan overshoots by six.

**Its start is a multiple of four.** Every address in this image is below
`0x01000000`, so every pointer has a zero top byte, and the longest run of
`00` begins one byte inside the last pointer of a table. Writing there turns
`0x00623bb0` into `0x11623bb0` and the game dies dereferencing it. `build.py`
refuses any `org` whose site is not four-aligned, and a cave picked by hand
needs the same.

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

## timer.asm, what it does

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

## debugbox.asm, what it does

The Debug options only ever existed as menu items, so once the menu bar goes
there is nothing to open them with. This builds a dialog instead.

**The hook** replaces the window procedure pointer at `0x1c4d7e`. It watches
for F11 and passes everything else to the handler that was there before. On
F11 it fetches `DialogBoxIndirectParamA` through `LoadLibraryA` and
`GetProcAddress` - the import table has no room and rebuilding it for one
export is not worth it - and opens the template that the same patch writes
into the `.rsrc` cave.

**The dialog procedure** ticks each check box from the game's own flag on
`WM_INITDIALOG`, fills the frame rate list, and forwards clicks. Every
control's id is the game's own command id, so a click is posted straight to
the main window as `WM_COMMAND` and needs no lookup table. Quit Program is the
one special case: the dialog is closed first, because the game tears the
window down under it.

It assembles as one run but lands as two sites, since the byte in front of the
dialog procedure is alignment padding the patch has never written. `build.py`
splits the blob there and refuses to do it if the source puts anything but a
zero in that byte.

The strings, the two tables it reads and the dialog template are data, packed
by `dialogs.py`.

## padtables.py, what it does

Sixteen pad inputs, described once:

```python
('LT',       TRIGGER, LTRIGGER, PULL),
('LS Up',    ABOVE,   LY,  DEADZONE),
('LS Down',  BELOW,   LY, -DEADZONE),
```

From that list it packs the condition table the tick reads, the bind list the
F7 page offers, and the strings both point at, computing the pointers between
them. The three profile names sit in the same string blob, so the device list
is built from it too.

An input's id is `0xe0` plus its position in the list, which is also its index
into the condition table. Reordering the list moves everyone's saved binds.

`DEADZONE` is what a stick axis has to pass to count as pushed, out of 32767.
It is per axis rather than radial, so a 45 degree push puts 23170 on each and
diagonals have room to spare. 13000 is about 40%, above Microsoft's own 7849
and 8689, which are loose enough to pick up drift on a worn stick.

## dialogs.py, what it does

Both dialogs, from a control list each.

The Extras box is built outright, and the ids in it are the game's own command
ids, so the dialog procedure can post a click to the main window with no
lookup table. The same list carries the flag each check box reflects, which
becomes the table `debugbox.asm` walks on `WM_INITDIALOG`. `dialogs.inc` gives
the assembly the addresses of both.

The F5 frame rate labels are the other case: an edit to a resource the game
already has. Sega's *Fast* and *Smooth* read **30 FPS** and **60 FPS** now.
The first is wider than *Fast*, so everything after it in the resource shifts
by four bytes, which is what the size field at `0x6035ac` gains.

## padxinput.asm, what it does

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

## introwait.asm, what it does

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

## twinstick.asm, what it does

There is no logic in it. The XInput tick already walks twelve bind slots,
tests each against a condition, and clears the bits its mask names in the two
lever words. Feed that engine a different set of binds and masks and it
becomes the arcade scheme: one thumbstick direction per slot instead of one
named action, so the sticks land straight in the levers and the game works
out walking, turning, jump and crouch from the pair.

So the file is two entry stubs, a bind list, two mask tables and a parameter
block per player, mostly tables. It carries an `org` because the stubs jump
to fixed addresses and the blocks point at its tables.

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

## movie.asm, what it does

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
