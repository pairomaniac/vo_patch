# Notes

How the patches work, rather than how to use them. For using the
patcher see [README.md](../README.md); for the assembly sources and how
they are built see [asm/](../asm/); for the netplay DLL, which is a
replacement rather than a patch, see [net/](../net/).

## Patches

| Patch | Offsets | Change |
| --- | --- | --- |
| Remove SE playback wait | `0x2bba60` | `.data` `15` → `1` |
| Sound 22050 → 44100 Hz | `0x189546`, `0x189552` | `WAVEFORMATEX` `nSamplesPerSec` and `nAvgBytesPerSec` |
| Enemy Fei-Yen hypermode SE | `0x058189`, `0x170dc9` | `cmp [eax+0x68], 1` → `2` |
| **No disc required** | `0x1c76d4` | `je` past the nag → `nop` |
| **Skip processor check** | `0x107930` | `or [0xbf84c8], 1` → `nop`, so the check is never enabled |
| **Let v_on.ini set Motion** | `0x10afbe`, `0x10afeb`, `0x10b002`, `0x1c6941`–`0x1c8bd3` | three fallbacks `3` → `1`, eight stores in four routines → `nop` |
| **Raise timer resolution** | `0xa8` | entry point redirected to a stub in the annex |
| **Better ini defaults** | `0x10acd7`, `0x10b088`, `0x10b131`, `0x10b1b0`–`0x10b1c4` | fallback immediates changed, Field Graphic branched into the Rich path |
| **Motion Type 30 / 60 FPS** | `0x273c1`, `0x275d3`, `0x275e2`, `0x6035ac`, `0x60c064` | the radios write 2 and 1 instead of 3 and 2, dialog rebuilt with the new labels |
| **Fix crash on round loss** | ten sites, `0x077f5a`–`0x0c0ada` | 42-byte blocks → `nop` |
| **Fix keyboard input after ALT+TAB** | signature | `push 6` → `push 0xA` at `SetCooperativeLevel` |
| **Fix crash on ALT+TAB** | `0x1b0920`, `0x1c4aa2`, `0x1c5726`, `0x1c5412` | the intro movie's exit recreates the surfaces however the movie ended (`jne` → `jmp`); a recreate that fails pauses the game, arms the activation handler, and is retried from the idle pass |
| **XInput gamepad support** | `0x0422a8`, `0x0422ac`, `0x1bc13b`, `0x1bc13f`, `0x095bdc`, `0x095217`, `0x1c530e`, `0x1c52ac`, `0x0971bd`, `0x096731`, the keyboard profile's eleven config-block references, `0x094ea0`, `0x096b61`, `0x096c8e`, F7 page constants, the Simple slot's page, handler, validation and load-route entries, `0x0959f7`, `0x095604`, `0x0958aa`, `0x096253`, `0x09625b`, thirteen `.rdata` caves, `0x60b34e`, `0x285e04`, `0x2c7654`, `0x269b60`, `escrgame.bin` `0x21c000` | routine, twin-stick tables and lever cleanup in runs of zeros; handler, F7 page and picker tables repointed for both players; twin-stick's case sent past the joystick count; Keyboard (Simple) restored in the 2 Joysticks slot, with the shared bind page, its block and the live table forked by the pending device, its own "Simple Assign" ini line saved and loaded, and the list shown in display order through a position map; A writes the camera slot on the win and lose screens; two prompts renamed and the title banner redrawn |
| **Music from files** | new `.vocd` section, entry point, 37 call sites | every call to `mciSendCommandA` pointed at a routine that answers from WAV files |
| **Disable menu bar (Extras menu on F11)** | `0x1c4d42`, `0x1c4d4b`, `0x1c4d7e`, appended `.voxt` section | the window procedure hooked, the dialog in the annex and its template in a small appended section, run through the same pause and resume as the built-in F-key dialogs |
| **Version and credit in the game** | `0x1fcec8`, `0x1fcecc`, `0x2bbb54`, `0x1c5900`, `scrstfcg.bin`, `scrstfmp.bin` | the roll is a list of blocks, 12 bytes each as (flag, width, height) in cells, read from `0x6bcd48` and placed on 51 cells by the flag - `0x448e86` centres, `0x448f54` pushes flush right, and the roll's own text uses the latter where these two use the title's centring; the five blank spacers after the title become five entries carrying the same twenty rows with the lines centred in them, so nothing below moves and the roll keeps its length, the cells go into `scrstfmp.bin` at the same point, and the tiles on the end of `scrstfcg.bin`, whose indices the loader rebases at `0x483d9d`; the loader reads both files to byte counts held at `0x5fdac8` and `0x5fdacc` rather than to their size, so the two constants grow with them; separately, the load before the surface flip is diverted through a stub that prints the version in the corner of the title screen, in the tile font |
| **Intro, loading and ending screens** | `0x14dc42`, `0x23f`, `0x2c7678`, `0x18fc25`, `0x0d60c8`, `0x1c58e7` | placement routine calls a stub in the annex, which measures the window through cnc-ddraw's own bypass export and sends a destination rect; loading string's first byte → `NUL`; credits handler's opening write calls a stub that puts the sequence past its last phase once A has been held a second, read from the key buffer slot since the press edges are not maintained in that state; the initials screen's two trigger tests replaced by a stub that adds the same slot; the call before the surface flip diverted through a stub that draws HOLD TO SKIP while the button is down |

Bold entries are not part of original VO_Patch. Offsets are the English
retail build's, the ones the site table is keyed on; the other builds map
each to their own. The stubs the sites call live in the annex, the
section the patcher appends first - see "The other builds" below.

## Installing from a disc image

The disc carries Sega's generic installer, driven by `ssp.ini` in the root,
so the copy rules are read from there rather than guessed at. The four keys
that matter:

| Key | Meaning |
| --- | --- |
| `SourcePath1` | the game directory, copied whole - `v_on\` on every pressing |
| `Select1` | whether a language directory is copied too |
| `LangExeclusive` | which one, per language section |
| `IniFileName` | the file Sega's installer writes over; the patcher does not |

Retail pressings set `Select1 = SourceCopy, LangExeclusive` and keep the help
files in `english\` and friends; the USA OEM disc has neither and keeps them
in `v_on\`. One rule covers both: copy `SourcePath1`, plus the chosen
section's `LangExeclusive` directory when `Select1` names it. A section whose
directory is not on the disc is not offered.

The section list is no guide to what is on the disc: the Japanese rerelease
carries `[JAPANESE]` and `[ENGLISH]`, both with `LangExeclusive` empty, and
they differ only in which installer chrome they load. That is why the rule
reads `LangExeclusive` rather than the section names.

Discs offer smaller installs as well - `Select2` skips the AVI, `Select3`
copies the list in `MinimumCopy3` - and the patcher ignores both. It always
does the full copy, which is `Select1`.

Five pressings were read for this: USA, USA Alt, the EU rerelease, the USA
OEM and the Japanese rerelease, which has the OEM shape. The Japanese
original has not been, so nothing here is known about its `ssp.ini` - the
rule may well cover it, since none of it depends on the build or the
language, but that is a guess until a copy is read.

### The other builds

Three builds of `v_on.exe` patch: English retail, the USA OEM pressing and
the Japanese rerelease. They are compiles of the same source through the
same toolchain - the OEM a month before retail, the rerelease four months
after, link 3.0 against 3.10 - with the same section order and a `.reloc`
in each. A recompile is not a relayout: data moves by a different delta
per region, functions grow or lose locals, a few globals change order.
Nothing is a constant shift, so no address is derived from a neighbour's;
each build carries its own.

A build is a `Build` in `vo_patch.py`: its sections, where each blob goes,
what every symbol the blobs name resolves to there, its title artwork,
and for a build other than retail a site map - that build's offset and
original bytes for every site the table names by retail offset, `None`
where the build has no such code - and an annex. The blobs are one set of
bytes for all builds, linked from the build's tables when the patcher
loads; the site table's hooks and blob sites are expressions the build
fills in, and a site one build has and retail has not is written as
`In(md5, offset)`. The tables come from `tools/vomap.py`, which matches
the two executables function by function with addresses masked and votes
on where every address went, and `tools/votrans.py`, which runs the site
table, the asm symbols and the symbol table through the map; what the map
cannot settle - a function split at the wrong place, a site in a switch
table, ten bytes that occur twice - is in `HAND` there, per build, with a
reason each. `docs/DEVELOPING.md` has the recipe.

Two things are the same in every build:

- Every blob lives in an appended section, `.vojp`, written empty before
  any patch and filled through the site table. The annex's place is fixed
  by the file's headers, so it links at import; `.voxt` and `.vocd` land
  after it. Only the F7 device list and the levers tail, which the game
  reaches itself, are written in place. A run of zeros in `.rdata` is not
  used: one long enough for a blob is the NULL tail of a handler table -
  a code pointer just before it, and the game calling through the slots -
  or a constant that happens to be zero, and both have crashed the game.
- The XInput scratch sits in the page slack past `.data`, and the ending
  screens' two spare bytes in a run of `.data` nothing points at. Neither
  is proven free the way an appended section is.

The netplay DLL tells the builds apart by the PE timestamp and
fingerprints each one's own two sites (`fp_builds` in `net/dpctrl.c`).
The ending roll files are byte-identical across builds, so the harvested
glyphs stay valid.

**The USA OEM pressing** (March 1997, `0x3317246A`) is the closest to
retail: 7852 of 7934 functions match, 7593 identically, every frame is
laid out the same, and the map placed all but four sites. Its processor
check is a different one. Retail tests for `GenuineIntel`; the OEM has
`cpuid32.dll` classify the CPU and accepts two classes, neither of which a
modern CPU is, so its version of the processor check patch makes the
accept branch unconditional and sets the MMX flag the game would set for
the class that has it. Its title artwork and roll files are retail's,
byte for byte.

**The Japanese rerelease** (October 1997, `0x345107FA`): 7291 of 7934
functions match, 6329 identically. Frame layouts differ in the patched
functions - the bind page's loop counter is `[ebp-0x18]` for retail's
`[ebp-8]`, the F7 combo selection `[ebp-0x10]`, the OK handler's line
buffer `[ebp-0x18c]`, the movie placer's X and Y `[ebp-0x14]` and
`[ebp-0x18]` - which is what the frame-offset symbols are for. Its title
artwork is `jscrgame.bin`: the same 4 MB, 2304 tiles of it redrawn for
the Japanese logo, and the slots the banner patch writes identical to
retail's. Its roll files are retail's. Dropping the English `v_on.exe`
into a Japanese install does not work: it looks for `escrgame.bin`.

### Why no v_on.ini

`setup.exe` at `0x408acf` builds the three paths, asks a dialog in the
language DLL, and copies `v_on_a.ini` or `v_on_b.ini` over `v_on.ini` before
deleting both:

```
  DialogBoxParamA(<language dll>, 7140, ...)
  if result:  CopyFileA(v_on_a.ini -> v_on.ini)
  else:       CopyFileA(v_on_b.ini -> v_on.ini)
  DeleteFileA(v_on_a.ini)
  DeleteFileA(v_on_b.ini)
```

Doing the same would fight the patches. `v_on_a.ini` carries `Motion=3`, the
frame divisor at `0x6c84d0` that the frame rate patch sets to 1, and the ini
wins over the default in the code - so a freshly installed and patched game
would run at a third speed. Both files are copied as the disc has them and no
`v_on.ini` is written; the game writes its own on first run, against whatever
defaults are patched in.

### What setup.exe writes that the game reads

Nothing. Its only `RegSetValueExA` calls register the Indeo Video 4.1 codec
in `Drivers32` and `drivers.desc`, and they sit behind a `GetVersionExA`
guard at `0x4021dd` testing `dwPlatformId == VER_PLATFORM_WIN32_WINDOWS`, so
they never run on NT, Wine or Proton. The `SOFTWARE\SEGA\`, App Paths and
Uninstall keys go through `SSP.dll!CreateRegistryString` and are
`vouninst.exe` registering itself.

`v_on.exe` imports `RegCreateKeyExA` and `RegQueryValueExA` but writes
nothing. Both call sites are at `0x495b82`-`0x495bf3`, reading `OEMName`
under `MediaResources\Joystick` and `MediaProperties\...\Joystick\OEM` and
comparing against `Microsoft SideWinder game pad` - Windows' own joystick
table, not anything an installer puts there. The paths are assembled at
runtime from `%s\%s\%s`, which is why they do not show up in a string dump.

That IV41 registration is what identifies `von.avi` as Indeo Video 4.1, and
`ir41_32.dll` is not on the disc - it came from Windows. Wine has no Indeo
decoder, so that movie cannot play on a clean prefix however the game is
installed.

## How each patch works

In the order of the table above; rows that are a single obvious byte edit are
skipped. The CD audio and gamepad patches install assembled machine code
rather than editing bytes, and the sources and a longer account of both are
in [asm/](../asm/).

### Sample rate

This is the DirectSound buffer format, not the samples, which
are 8-bit at 7500 or 11025 Hz either way. VO_Patch set only `nSamplesPerSec`,
leaving `nAvgBytesPerSec` inconsistent; both are set here.

### No disc, music from files

A helper returns -1 when no disc is found and the
caller loops on a message box; removing the branch into that loop falls
through to the success path. The scan itself is untouched, so a mounted image
is still found.

The music logic is small: open `cdaudio`, set TMSF, read the track count and
every length once, then whole-track plays, stops, the occasional pause, and a
mode query to see whether a track is still running. No notifications and no
position polling, so a finished track goes quiet, as it did with the disc.
That is little enough to answer from WAV files instead, which a routine in a
new `.vocd` section does.

The routine and its data are 3 KB, more than the annex is for, so the
executable gets a section of its own, and the entry point is repointed at
the setup thunk, which chains to whatever it was before - hence this patch
running after all the others.

#### Getting called

The obvious way in is the import table: overwrite the
entry the loader fills with the address of `mciSendCommandA` and every call
lands on the routine instead. That is what this patch did until 0.7.3, and it
is one slot of memory any loaded DLL can overwrite - a wrapper that hooks the
same function by name silently takes the routine out of the chain, leaving a
game that runs with no music.

So `apply_cdaudio` rewrites the calls instead. The 37 sites are all the
six-byte indirect form, and become a direct call plus a `nop` - same six
bytes, nothing moves, nothing written later can undo it. The routine still
forwards through the import slot as it finds it, so a wrapper that does own it
keeps working underneath. A count that is not exactly 37 aborts the patch.

### Processor check

In retail, `ProcessorCheck=Off` does not switch the check off, it stops
the game switching it *on*. One `or` sets the flag the MMX, Pentium and
vendor branches all read; nopping it leaves the flag clear whatever the
ini says. The OEM's check is a different one, above.

### Crash on ALT+TAB

The intro movie is played by `mciavi` in the game's window, with the
DirectDraw surfaces released (`0x5b1320`) so the player can have the
screen, and recreated when the movie ends (`0x5b1510`). The window
procedure handles a switch away during the movie itself: `0x54ea39` stops
the movie and sets `0x6bead4`, "stopped by deactivation"; coming back,
`0x54e516` resumes it and clears the flag. Between the two, the intro
state polls the movie, finds it stopped, takes that for the end, and
calls `0x5b1510` - whose first test is that flag: set, it clears it and
returns without recreating. Movie mode ends, the normal loop runs, and no
surface is ever created again: the activation handler's own recreate
(`0x5c6d2d`) is gated on `0x6bf570`, which the movie's release cleared and
only a successful recreate sets. The next frame reads a null back buffer
at `0x5c8103`; guarded, it would read a null primary at `0x5c650b`, then
a null `IDirectSound` at `0x58a244`. cnc-ddraw does not see it because the
window does not lose the display, so the movie is never stopped.

The patch is in two parts. The `jne` at `0x5b1520` is made a `jmp`, so
the movie's exit recreates the surfaces however the movie ended. But that
recreate runs the instant the stop is noticed, with the window still in
the background, and a DirectDraw that will not give exclusive mode to a
background window returns from `0x5c56a2` with a plain primary and no
back buffer - it tries three surface descriptions and takes the first
that works - and a zero result nobody reads. So `asm/activate.asm`, three
hooks at function entries: `0x5c56a2` has its caller's return address
swapped for the stub's, re-asserts the cooperative level the game set once
at start-up - `SetCooperativeLevel(hwnd, EXCLUSIVE | FULLSCREEN)`, which a
DirectDraw that let it lapse on the switch would otherwise leave lapsed
and draw the new surfaces into a plain window at the top left - moves
the window to the mode's size after a recreate that worked, the same
DirectDraw having shrunk it, and on a zero result sets the inactive flag
`0x1add128` (the loop idles on it), `PENDING`, and `0x6bf570`, the
"surfaces exist" flag the activation handler's own recreate is gated on
and a failed recreate leaves clear; `setactive` (`0x5c6326`, the pause on
1 and the resume on 0 that `GRESUME`, the dialogs and the movie player
all call) refuses a resume while the back buffer is null; and the idle
pass the loop makes each iteration while inactive (`0x5c6012` calling
`0x5c63aa`) retries the recreate, choosing the resolution from the same
two flags the handler does and skipping while the window is iconic, and
resumes when it takes. cnc-ddraw sees none of it: the window never loses
the display, so the movie is never stopped.

### Frame rate

Three things kept the game off 60 fps, and the patch does all
three.

Each frame is gated on `timeGetTime`, and the game advances only once a budget
has elapsed: 33 ms at `Motion=2`, 16.7 ms at `Motion=1`. It never calls
`timeBeginPeriod`, so the clock ticks every 15.6 ms and a 33 ms budget waits
for the third tick at 46.8 ms - about 70% speed. A stub in the annex
calls `timeBeginPeriod(1)` and jumps to the real entry point. VO_Patch shipped
`vo_speed.exe` for the same job. No-op under Wine.

`Motion=` was always parsed correctly; four routines then overwrote it, one at
start-up and three on resolution and view changes. Removing all four lets it
stand. Its fallbacks wrote 3, so a missing or mistyped value put the flicker
back; they write 1 now.

The F5 *Motion Type* radios edit a staging copy - opening the page copies
`Motion` from `0x6c84d0` to `0xbe4308`, the radios write that, OK copies it
back. *Fast* wrote 3 and *Smooth* 2, a third of full speed and a half, so 60
fps was unreachable from the interface. They write 2 and 1, and the test
choosing which radio starts selected goes from 3 to 2.

`30 FPS` is four bytes longer than `Fast`, so the dialog template has to grow.
It is the last resource in the file and ends on the section's virtual size
with mapped padding after it, so it can; the size in the resource directory is
updated and the *Fast* radio widened to fit.

### Ini defaults

One routine reads `v_on.ini`; every key is the same block,
look the string up and write a hardcoded value if it is absent. Several of
those default to the worse setting - Sky off, every texture off, Field
Graphic Normal. A four-byte edit each.

Field Graphic is the exception. Rich clears `0x6817f0`, sets `0x6817c8` and
calls the routine that loads the richer field, while the missing-key path only
does the middle one. So that branch is replaced with a jump into the Rich
block and the fifteen bytes padded out.

`ScrSize` is a bit field rather than a size: bit 0 is Screen Normal, bit 2 is
low resolution, the 320x240 mode F4 toggles. A default of 0 is Screen Large at
640x480.

### Lose-a-round crash

Ten continue-screen routines read through a pointer
that is really a float constant:

```asm
mov eax, [ebp-4]        ; = 0xC000CDE4, the float -2.0126
fld dword ptr [eax+8]   ; access violation
```

That address was readable on Windows 9x and is not now. Each block only undoes
a translation the routine has already reset, so `nop` is safe. The ten are
similar but not identical, hence listed out one by one.

### Alt-tab

The game acquires its DirectInput keyboard `DISCL_FOREGROUND` and
never re-acquires it after losing focus. `DISCL_BACKGROUND` removes the
condition.

### Gamepad

The game predates XInput and reads pads through the Windows 95
joystick API, which on a modern controller reports a partial view: one trigger
unreachable, axis order inconsistent between Windows and Wine. So it is not
read through it at all. A routine in the annex calls
`XInputGetState` and folds the result into the game's own action tables.
Bindings are one byte per action, so pad entries occupy `0xE0`-`0xEF` in the
scancode space, which the game does not otherwise use. Player 2 is a full
mirror, so both sides are the same routine with a different parameter block.

#### The device tables

The device number keys three tables, not one, and all three had to move
together: the profile switch at `0x442ea4` picks the handler, `0x4967d4`
picks the F7 page, and `0x495e0f` validates the device saved in `v_on.ini`
at startup. The picker skips device slots whose name pointer is null, so
hiding the legacy profiles is zeroing the rest. Two validation entries skip
the joystick-presence check now, not one: a keyboard in device 3 must not
have its saved selection reset for lack of a stick. A last common check
also demanded the DirectInput joystick subsystem whenever either player's
saved device was 3 or 7, forcing both to the gamepad and skipping the whole
ini load when a controller was off at boot - it is 7-only now.

The F7 page has a check of its own, and twin-stick failed it. Before reading
the two combo boxes, the OK handler at `0x49716e` counts the joysticks
enumerated at startup, then spends one per selection through a second table
at `0x497331`, refusing the page if a counter goes negative. It refuses by
putting focus back on the combo, with no message, so the button looked dead.
Twin-stick spent a joystick it did not need - it reads the pad through
XInput - and a pad plugged in after launch was never enumerated, which is
why a restart appeared to fix it. Its case now goes straight to the check,
where the keyboard and gamepad selections already arrive.

The F7 list shows the profiles as gamepad, twin-stick, Simple, Real; the
device numbers underneath stay what the executable and v_on.ini always
used, and `asm/devorder.asm` maps list positions and devices into each
other at the page's preselect and its OK translate.

#### The four profiles

The gamepad profile takes *Keyboard only(Simple)*'s slot, the only F7 page
that binds all twelve actions.

*Keyboard (Simple)* itself returns in slot 3, the hidden *2 Joysticks*
profile's: its handler stubs still exist and the slot's page, validation and
joystick-spend table entries only needed repointing. Startup fills its
block through the call that used to write 2 Joysticks defaults there,
redirected to the tail of `asm/bindmap.asm`.

*Keyboard (Real)* is the game's other keyboard profile, untouched except for
where it keeps its binds. It shared one twenty-four byte block with Simple,
which the gamepad now owns, so it moves to the block belonging to the hidden
*Joystick + Keyboard* profile: eleven sites, each changing a `+0x08` to a
`+0x20` or an address by the same amount. Its page, defaults and live table
were always its own, and the *Joy+Key Assign* v_on.ini line keeps the block
persistent. Two consequences. The startup defaults run every profile's set
in turn and *Joystick + Keyboard* writes that block after *Real* does, so
its call is dropped - it is unreachable anyway. And **Default** on the
keyboard page passed a hardcoded player 0, resetting 1P's binds from the 2P
side; the other two pages pass the current player, so this one is corrected
to match.

*Twin-stick* adds no logic at all. The tick is a bind -> condition -> lever
mask engine, and the arcade scheme is just a different set of binds and
masks: each of the twelve slots drives one lever direction or button instead
of a named action, so the thumbsticks land straight in the two lever words.
It is mostly tables. It binds nothing, so it takes the page-table entry
that opens no dialog, which also disposes of the `0x3651554 == 1` check
that made **Next** refuse without a joystick attached. Jump and guard are
lever gestures rather than buttons - both levers spread outward, both
squeezed inward - so they share the words movement writes to, and neither
came out while moving. A second routine after each tick sorts that out, and
only when a pad was read, so the keyboard path is untouched.

#### The shared bind page

Simple and the gamepad share one bind page, told apart by device:
`asm/bindlist.asm` and `asm/bindmap.asm` pick the input list - the game's
33 named keys or the 16 pad inputs - and `asm/bindblock.asm` (and
`asm/blockcur.asm` for the store, whose call site passes a fused
player-and-slot index) picks the saved block, `+0x08` for the gamepad and
`+0x38`, the slot's own, for Simple. The device consulted is the
structure's own `+0x00` dword, the pending pick the F7 screen edits, not
the committed copy at `0x3651540`: the bind page and the live-table apply
both run before OK commits, and against the committed device they would
serve the profile being switched away from.

The letter and digit sections are generated, not listed, and belong to
Simple alone: `asm/pagesec.asm` and `asm/pagesel.asm` skip them for the
gamepad in the fill, the store and the preselect together, since combo
indices are positional. The gamepad page lists exactly its sixteen pad
inputs, starting at index zero. The Default button's source table follows
the same pending-device pick, or the keyboard page would be handed pad ids
that match nothing it lists.

The page seeds its block from the live table at open - and with two
profiles sharing one live table, an unconditional seed would copy the
active profile's binds into the other's block, so `asm/blockcur.asm`'s
wrapper runs it only when the page's device is the committed one. The same
sharing is why the device page's plain OK needs help: stock, it commits
the device number and writes the "NP Device No." lines only, since every
original device family kept its own live table. `asm/commitdev.asm` wraps
that commit and reseeds the live table from the new device's block when it
is one of the keyboard-page pair, so a switch takes effect without a trip
through the bind page.

One relaxation narrows: 2P may reuse 1P's key only while 1P is on a pad
profile, since device 3 makes 1P's keys live again.

#### Saving and loading

Persistence needed its own channel: the structure's blocks reach
`v_on.ini` as one "Assign" line per player through a per-device dispatch
at `0x496e4f`, and the slot Simple took over had none - a second copy of
the startup defaults call re-filled `+0x38` with legacy joystick data on
every launch. On OK, devices 1 and 3 both route through `asm/inisave.asm`,
which writes "NP Simple Assign" as hex pairs and falls into the stock
device 1 case, so "NP Keyboard Assign" always carries the gamepad's set
and neither profile loses its binds while the other is selected. The hex
text is built in the dialog frame's own line buffer rather than a static
one of its own.

On launch, the loader routes each player by saved device, and slot 3's
route was "load nothing" - right for 2 Joysticks, whose data was re-derived
from the pad type, wrong for a profile with saved binds. One index byte
routes it through the padtype section instead, where the old re-fill call
now runs `asm/iniload.asm`: each block's own line is parsed back through
`asm/iniparse.asm`, "NP Simple Assign" into `+0x38` and "NP Keyboard
Assign" into `+0x08` too, which the stock loader only parses into the live
table. A missing line keeps the shipped set. When the saved device is
Simple, the live table is then seeded from `+0x38`, overriding the seed
the Keyboard Assign line left for the gamepad. And whatever the saved
devices, `asm/iniall.asm` runs the same loader for both players at the
load loop's exit, so an inactive profile's saved set survives restarts
spent on other devices.

It lives at `0x63c5f4`. Through v0.10.1 it sat at `0x5fb144`, which is
zeros in the file but not free: the attract loop's scoreboard state copies
21 dwords from `0x5fb140` into its own record and reads an index out of
them, so with the patch in the blob's bytes were the index and the loop
crashed on its way back to the title screen.

The stick deadzones load on the same exit: 40% per player unless that
player's `1P Deadzone=` or `2P Deadzone=` line says otherwise - two
digits, 05 to 95, anything else keeps the default; an entry the F11 box
rejects is re-seeded to the percent in force, so it neither lingers nor
blanks. The key strings ride
in the names blob rather than beside the Assign keys, whose run turns
out to end within nine bytes of them. The thresholds the
tick compares against - per player, indexed by the parameter block's
own index - are the percent of 32767, and the condition table's axis
values only pick the side of zero now. The F11 dialog writes both
lines back through the game's own line writer when it closes; the
lines are also hand-editable.

#### Pad buttons outside the binds

The win and lose screens read the camera key rather than the accept key, so
Select skipped them and A did not. The tick writes the camera slot for A as
well, gated on the two sub-states those screens use, since everywhere else
that key swings the camera.

Start and A are also posted as key messages from the message pump, because
the input tick does not run while the game is paused.

The intro movie is a third case: it plays asynchronously and leaves the game
blocked in `GetMessageA`, on the branch the pump stub is not on, so that call
is hooked as well. Space, Enter and Escape all skip the movie, so A does; F3
is ignored while it plays, so Start does not.

### F11 dialog

No dialog resource ever existed, so one is built at runtime
from a template written into unused space - over the old menu, which this same
patch unhooks. Every control carries the game's own command ID, so clicks go
straight to the main window; the
check boxes read the game's own flags. **Credits** is the one control with no
menu item behind it, so the dialog procedure writes the sub-state itself -
the title machine's, so it shows that sequence rather than the one a finished
game runs. It is in the *Debug* box with the rest all the same, since what it
does to a running match is the same kind of thing. F11 because F9
disconnects a network game and F10 is a Windows system key. The dialog
runs through `asm/f11pause.asm`, which wraps it in the same pause and
resume calls the built-in F-key dialogs use, so the game and the music
stop and restart around it identically.

Motion is not among them any more, the F5 page having taken it over.

The dialog also carries each player's stick deadzone as a two-digit
percent, read back and written to that player's v_on.ini line when the
box closes - a Deadzone group of its own, `1P [40] %  2P [40] %`.

The template lives in a small read-only section the patch appends, the
way the CD-audio patch's does. The dead menu resource it used to squeeze into
capped it at 460 bytes, which had been pricing every label; that is left
as it came now. `asm/f11pause.asm` carries a placeholder where the
template's address belongs, filled at apply time once the section exists.

The section carries code at its tail too. The dialog's long paths live in
`asm/voxt.asm`, position independent, reached through a rel32 placeholder
in the dialog procedure: the close-time read, the ini save, the Defaults
button that seeds both players back to 40 with the dialog still open, and
Quit, which is back and still closes the dialog before posting, the game
tearing the window down under it. The check-box init stayed in
`asm/f11pause.asm`'s tail.

The values and their digits live in the gamepad patch's `.data` scratch,
and the ini write-back in `asm/iniparse.asm`, so the close path calls it
behind a test of that blob's first byte: zero is the stock run,
nothing to call. With the gamepad patch out the boxes show empty and
their values land in scratch nothing reads, which is harmless either way,
since the addresses are free in the stock executable whatever is
installed.

### Intro movie

The movie is not drawn through DirectDraw. The game opens
`von.avi` with `MCI_ANIM_OPEN_WS` and `MCI_ANIM_OPEN_PARENT`, so mciavi makes
a `WS_CHILD` window of the main window and everything after that is plain
Win32. The game then moves that window itself, to an offset it reads from two
globals, each written as a hardcoded centre for one movie size in a 640x480
picture: `0`/`0x28` for 640x400, `0xa0`/`0x8c` for 320x200, `0`/`0x14` for
640x440.

Scaled up, the main window is the whole screen and the picture is drawn
centred inside it, which the child window knows nothing about, so the movie
stays in the corner at its original size.

The routine does call `GetClientRect` on the parent, and throws the result
away, but reading it would not have helped: cnc-ddraw hooks that call and
answers with the game's own 640x480 whenever it is asked about the game
window. So there is no honest geometry to be had through any call the game
makes. cnc-ddraw exports `DDGetProcAddress` for this, which forwards to the
real `GetProcAddress`, and asking it for user32's `GetClientRect` gives the
unhooked one.

That is more than an edit, so it is a stub in the annex - see
[asm/](../asm/). Without cnc-ddraw the import is already the real function and
the result is what the game did before. mciavi does not follow the window, so
a `MCI_PUT` destination rect goes with the resize; the game never sends one
of its own.

### Ending screens

There are two credit sequences. The one the **Credits**
button reaches is sub-state `0x20` of the title machine `0x1ae3690`, whose
handler at `0x59081f` is a phase machine on `0x1ad0964`: 0 and 1 are the
ending cutscene and the mission complete screen, 2 is the roll, and anything
else falls through to a tail that stops the music and moves on. The one a
finished game reaches is state 32 of the main-game machine `0x1ef9eb0`, whose
draw table is `0x606fa0`; entry 32 is `0x44a523`, a phase machine on
`0xbf073c` that runs the roll through `0x4489d6` in its phase 2. The title
machine's logic table is `0x5ff1c0`.

Both read the same block list and the same map, so the credit line lands in
either; only the scenery behind them differs. Skipping is one write,
putting the phase past 2, so the game ends the sequence its own way.

The input is not the press edges at `0x1ed5ec4` that the game over and
ranking screens test. Those are built at `0x56207a` out of the lever words,
and that routine does not run in this state - the word reads zero for the
whole sequence. The DirectInput keyboard state at `0xbf0448` is live
throughout, so the stub reads the slot for Space, which is also where the
XInput tick writes A.

The initials screen after it, sub-state `0x17`, takes a letter only on the
weapon triggers: `0x4d6cc8` for 1P and the test after it for 2P. Both go to a
stub answering the same question with that slot folded in, so the triggers
still work.

A key slot is a level and not an edge, so both stubs share one byte of
`.data` holding last frame's reading. That byte is why skipping the credits
with A does not also enter the first letter: A is still held when the
initials screen opens, and only a press that starts there counts. Skipping
is a hold rather than a press for the same reason it is not instant: a
button already down when the roll opens never starts a count, so the press
that skipped the win screen does not carry through.

Both stubs read 1P's slots and only 1P's, so 2P skips nothing and enters
initials on RT alone.

`HOLD TO SKIP` appears while the button is down, drawn through GDI rather
than the tile font because the roll scrolls the tilemap and anything printed
into it would climb the screen. It goes on the frame about to be shown, so
the hook is the call five bytes before the surface flip at `0x5c650d`, with
the primary-surface global pointed at the back buffer across the call.

### Version on the title screen

Not GDI, unlike the overlay above it, but
the game's own tile font: `0x4cd8c3` sets the cursor to a cell and `0x4ceeeb`
prints through it, as `0x44b757` does for the menu items on the same screen.
`0x5c991c` takes an index into the two fonts built at `0x5c8cd7`, `century`
and `modern` bold at 24px, so it offers no third face and no smaller size.

The hook is the load at `0x5c6500`, four along from the overlay's site and
still ahead of the flip, so the two can be applied independently. It is gated
on machine 1 and its states `0x06`, `0x17` and `0x11` - the logo with the
banner, the same screen later in the attract loop, and the logo with the
menu. `0x07` between them is the demo match. `0x66c1ac`, the value the banner
routine tests, names the loaded asset set rather than the screen.

The string is not in the blob, since the version comes from the git tag, so
the patcher writes it into a field of zeros on the end after the rest of the
patch is applied. That keeps `EXPECTED_ALL` in `selftest.py` one digest
rather than one per release.

### Prompt text

Two prompts name a key the pad covers, so they change with
the gamepad patch and not on their own: the pause screen's and the
scoreboard's. The title and scoreboard banner is a third case and not text at
all. All three are set out in [TEXT.md](TEXT.md).

The mech select screen's *PRESS  BUTTON* and *MACHINE SELECT* are
pre-rendered word sprites rather than text, and appear in no file as a
string, so they are left as they are.
