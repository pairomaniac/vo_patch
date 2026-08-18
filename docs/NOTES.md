# Notes

How the patches work, rather than how to use them. For using the
patcher see [README.md](../README.md); for the assembly sources and how
they are built see [asm/](../asm/).

## Patches

| Patch | Offsets | Change |
| --- | --- | --- |
| Remove SE playback wait | `0x2bba60` | `.data` `15` → `1` |
| Sound 22050 → 44100 Hz | `0x189546`, `0x189552` | `WAVEFORMATEX` `nSamplesPerSec` and `nAvgBytesPerSec` |
| Enemy Fei-Yen hypermode SE | `0x058189`, `0x170dc9` | `cmp [eax+0x68], 1` → `2` |
| **No disc required** | `0x1c76d4` | `je` past the nag → `nop` |
| **Skip processor check** | `0x107930` | `or [0xbf84c8], 1` → `nop`, so the check is never enabled |
| **Let v_on.ini set Motion** | `0x10afbe`, `0x10afeb`, `0x10b002`, `0x1c6941`–`0x1c8bd3` | three fallbacks `3` → `1`, eight stores in four routines → `nop` |
| **Raise timer resolution** | `0x1f423e`, `0xa8` | stub in `.text` padding, entry point redirected |
| **Better ini defaults** | `0x10acd7`, `0x10b088`, `0x10b131`, `0x10b1b0`–`0x10b1c4` | fallback immediates changed, Field Graphic branched into the Rich path |
| **Motion Type 30 / 60 FPS** | `0x273c1`, `0x275d3`, `0x275e2`, `0x6035ac`, `0x60c064` | the radios write 2 and 1 instead of 3 and 2, dialog rebuilt with the new labels |
| **Fix crash on round loss** | ten sites, `0x077f5a`–`0x0c0ada` | 42-byte blocks → `nop` |
| **Fix keyboard input after ALT+TAB** | signature | `push 6` → `push 0xA` at `SetCooperativeLevel` |
| **XInput gamepad support** | `0x1c4`, `0x0422a8`, `0x0422ac`, `0x1bc13b`, `0x1bc13f`, `0x095bdc`, `0x095217`, `0x1c530e`, `0x1c52ac`, `0x0971bd`, `0x207702`, `0x20779e`, `0x23dd70`, `0x096731`, `0x23d1a0`, the keyboard profile's eleven config-block references, `0x094ea0`, `0x096b61`, `0x096c8e`, F7 page constants, `.rdata` caves, `0x285e04`, `0x2c7654`, `0x269b60`, `escrgame.bin` `0x21c000` | routine, twin-stick tables and lever cleanup in runs of zeros; handler, F7 page and picker tables repointed for both players; twin-stick's case sent past the joystick count; A writes the camera slot on the win and lose screens; two prompts renamed and the title banner redrawn |
| **Music from files** | new `.vocd` section, entry point, 37 call sites | every call to `mciSendCommandA` pointed at a routine that answers from WAV files |
| **Disable menu bar (Extras menu on F11)** | `0x1c4d42`, `0x1c4d4b`, `0x1c4d7e`, `0x1f427c`, `0x1f42d8`, `0x23dce8`, `0x6036b0` | dialog built in unused section padding and over the dead menu |
| **Show the version, and credit the patch in the ending roll** | `0x1fcec8`, `0x1fcecc`, `0x2bbb54`, `0x1c5900`, `0x223198`, `0x1c4`, `scrstfcg.bin`, `scrstfmp.bin` | the roll is a list of blocks, 12 bytes each as (flag, width, height) in cells, read from `0x6bcd48` and placed on 51 cells by the flag - `0x448e86` centres, `0x448f54` pushes flush right, and the roll's own text uses the latter where these two use the title's centring; the five blank spacers after the title become five entries carrying the same twenty rows with the lines centred in them, so nothing below moves and the roll keeps its length, the cells go into `scrstfmp.bin` at the same point, and the tiles on the end of `scrstfcg.bin`, whose indices the loader rebases at `0x483d9d`; the loader reads both files to byte counts held at `0x5fdac8` and `0x5fdacc` rather than to their size, so the two constants grow with them; separately, the load before the surface flip is diverted through a stub that prints the version in the corner of the title screen, in the tile font |
| **Intro, loading and ending screens** | `0x14dc42`, `0x60c25c`, `0x23f`, `0x2c7678`, `0x18fc25`, `0x23cad0`, `0x0d60c8`, `0x23d1c4`, `0x1c58e7`, `0x1f74e0`, `0x1c4` | placement routine calls a stub in `.rsrc` padding, which measures the window through cnc-ddraw's own bypass export and sends a destination rect; loading string's first byte → `NUL`; credits handler's opening write calls a stub that puts the sequence past its last phase once A has been held a second, read from the key buffer slot since the press edges are not maintained in that state; the initials screen's two trigger tests replaced by a stub that adds the same slot; the call before the surface flip diverted through a stub that draws HOLD TO SKIP while the button is down |

Bold entries are not part of original VO_Patch.

## How each patch works

In the order of the table above; rows that are a single obvious byte edit are
skipped. The CD audio and gamepad patches install assembled machine code
rather than editing bytes, and the sources and a longer account of both are
in [asm/](../asm/).

**Sample rate.** This is the DirectSound buffer format, not the samples, which
are 8-bit at 7500 or 11025 Hz either way. VO_Patch set only `nSamplesPerSec`,
leaving `nAvgBytesPerSec` inconsistent; both are set here.

**No disc, music from files.** A helper returns -1 when no disc is found and the
caller loops on a message box; removing the branch into that loop falls
through to the success path. The scan itself is untouched, so a mounted image
is still found.

The music logic is small: open `cdaudio`, set TMSF, read the track count and
every length once, then whole-track plays, stops, the occasional pause, and a
mode query to see whether a track is still running. No notifications and no
position polling, so a finished track goes quiet, as it did with the disc.
That is little enough to answer from WAV files instead, which a routine in a
new `.vocd` section does.

This is the one patch that cannot be a byte edit in place: the padding at the
end of `.text` is nearly spent on the timer stub and the F11 dialog, and the
zero runs in `.data` are globals the game writes at runtime. The
executable gets a section of its own and grows by about 3 KB, and the entry
point is repointed at the setup thunk, which chains to whatever it was before
- hence this patch running after all the others.

**Getting called.** The obvious way in is the import table: overwrite the
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

**Processor check.** `ProcessorCheck=Off` does not switch the check off, it
stops the game switching it *on*. One `or` sets the flag the MMX, Pentium and
vendor branches all read; nopping it leaves the flag clear whatever the ini
says.

**Frame rate.** Three things kept the game off 60 fps, and the patch does all
three.

Each frame is gated on `timeGetTime`, and the game advances only once a budget
has elapsed: 33 ms at `Motion=2`, 16.7 ms at `Motion=1`. It never calls
`timeBeginPeriod`, so the clock ticks every 15.6 ms and a 33 ms budget waits
for the third tick at 46.8 ms - about 70% speed. A stub in `.text` padding
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

**Ini defaults.** One routine reads `v_on.ini`; every key is the same block,
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

**Lose-a-round crash.** Ten continue-screen routines read through a pointer
that is really a float constant:

```asm
mov eax, [ebp-4]        ; = 0xC000CDE4, the float -2.0126
fld dword ptr [eax+8]   ; access violation
```

That address was readable on Windows 9x and is not now. Each block only undoes
a translation the routine has already reset, so `nop` is safe. The ten are
similar but not identical, hence listed out one by one.

**Alt-tab.** The game acquires its DirectInput keyboard `DISCL_FOREGROUND` and
never re-acquires it after losing focus. `DISCL_BACKGROUND` removes the
condition.

**Gamepad.** The game predates XInput and reads pads through the Windows 95
joystick API, which on a modern controller reports a partial view: one trigger
unreachable, axis order inconsistent between Windows and Wine. So it is not
read through it at all. A routine in a run of zeros inside `.rdata` calls
`XInputGetState` and folds the result into the game's own action tables.

The device number keys three tables, not one, and all three had to move
together: the profile switch at `0x442ea4` picks the handler, `0x4967d4`
picks the F7 page, and `0x495e0f` validates the device saved in `v_on.ini`
at startup. The picker skips device slots whose name pointer is null, so
hiding the legacy profiles is zeroing the rest.

The F7 page has a check of its own, and twin-stick failed it. Before reading
the two combo boxes, the OK handler at `0x49716e` counts the joysticks
enumerated at startup, then spends one per selection through a second table
at `0x497331`, refusing the page if a counter goes negative. It refuses by
putting focus back on the combo, with no message, so the button looked dead.
Twin-stick spent a joystick it did not need - it reads the pad through
XInput - and a pad plugged in after launch was never enumerated, which is
why a restart appeared to fix it. Its case now goes straight to the check,
where the keyboard and gamepad selections already arrive.

The gamepad profile takes *Keyboard only(Simple)*'s slot, the only F7 page
that binds all twelve actions, with its input list swapped for pad inputs.
Bindings are one byte per action, so pad entries occupy `0xE0`-`0xEF` in the
scancode space, which the game does not otherwise use. Player 2 is a full
mirror, so both sides are the same routine with a different parameter block.

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

*Keyboard (Real)* is the game's other keyboard profile, untouched except for
where it keeps its binds. It shared one twenty-four byte block with Simple,
which the gamepad now owns, so it moves to the block belonging to the hidden
*Joystick + Keyboard* profile: eleven sites, each changing a `+0x08` to a
`+0x20` or an address by the same amount. Its page, defaults and live table
were always its own. The block sits inside the structure written to
`v_on.ini`, so it persists without any new storage.

Two consequences. The startup defaults run every profile's set in turn and
*Joystick + Keyboard* writes that block after *Real* does, so its call is
dropped - it is unreachable anyway. And **Default** on the keyboard page
passed a hardcoded player 0, resetting 1P's binds from the 2P side; the other
two pages pass the current player, so this one is corrected to match.

*Twin-stick* adds no logic at all. The tick is a bind -> condition -> lever
mask engine, and the arcade scheme is just a different set of binds and
masks: each of the twelve slots drives one lever direction or button instead
of a named action, so the thumbsticks land straight in the two lever words.
It is mostly tables. It binds nothing, so it takes the
page-table entry that opens no dialog, which also disposes of the
`0x3651554 == 1` check that made **Next** refuse without a joystick attached.

Jump and guard are lever gestures rather than buttons - both levers spread
outward, both squeezed inward - so they share the words movement writes to,
and neither came out while moving. A second routine after each tick sorts that
out, and only when a pad was read, so the keyboard path is untouched.

**F11 dialog.** No dialog resource ever existed, so one is built at runtime
from a template written into unused space - over the old menu, which this same
patch unhooks. Every control carries the game's own command ID, so clicks go
straight to the main window and **Quit** is just the *Exit Game* command; the
check boxes read the game's own flags. **Credits** is the one control with no
menu item behind it, so the dialog procedure writes the sub-state itself -
the title machine's, so it shows that sequence rather than the one a finished
game runs. It is in the *Debug* box with the rest all the same, since what it
does to a running match is the same kind of thing. F11 because F9
disconnects a network game and F10 is a Windows system key.

Motion is not among them any more, the F5 page having taken it over. The
handler that filled the box stays and does nothing: with no control carrying
its ID, `GetDlgItem` hands back nothing to talk to.

**Intro movie.** The movie is not drawn through DirectDraw. The game opens
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

That is more than an edit, so it is a stub in the `.rsrc` padding - see
[asm/](../asm/). Without cnc-ddraw the import is already the real function and
the result is what the game did before. mciavi does not follow the window, so
a `MCI_PUT` destination rect goes with the resize; the game never sends one
of its own.

**Ending screens.** There are two credit sequences. The one the **Credits**
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

**Version on the title screen.** Not GDI, unlike the overlay above it, but
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

**Prompt text.** Two prompts name a key the pad covers, so they change with
the gamepad patch and not on their own: the pause screen's and the
scoreboard's. The title and scoreboard banner is a third case and not text at
all. All three are set out in [TEXT.md](TEXT.md).

The mech select screen's *PRESS  BUTTON* and *MACHINE SELECT* are
pre-rendered word sprites rather than text, and appear in no file as a
string, so they are left as they are.
