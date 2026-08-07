# Notes

How the patches work, rather than how to use them. For using the
patcher see [README.md](README.md); for the assembly sources and how
they are built see [asm/](asm/).

## Patches

| Patch | Offsets | Change |
| --- | --- | --- |
| Remove SE playback wait | `0x2bba60` | `.data` `15` → `1` |
| Sound 22050 → 44100 Hz | `0x189546`, `0x189552` | `WAVEFORMATEX` `nSamplesPerSec` and `nAvgBytesPerSec` |
| Hide "Now Loading . . ." | `0x2c7678` | first byte → `NUL` |
| Enemy Fei-Yen hypermode SE | `0x058189`, `0x170dc9` | `cmp [eax+0x68], 1` → `2` |
| **No disc required** | `0x1c76d4` | `je` past the nag → `nop` |
| **Skip processor check** | `0x107930` | `or [0xbf84c8], 1` → `nop`, so the check is never enabled |
| **Let v_on.ini set Motion** | `0x10afbe`, `0x10afeb`, `0x10b002`, `0x1c6941`–`0x1c8bd3` | three fallbacks `3` → `1`, eight stores in four routines → `nop` |
| **Raise timer resolution** | `0x1f423e`, `0xa8` | stub in `.text` padding, entry point redirected |
| **Better ini defaults** | `0x10acd7`, `0x10b088`, `0x10b131`, `0x10b1b0`–`0x10b1c4` | fallback immediates changed, Field Graphic branched into the Rich path |
| **Motion Type 30 / 60 FPS** | `0x273c1`, `0x275d3`, `0x275e2`, `0x6035ac`, `0x60c064` | the radios write 2 and 1 instead of 3 and 2, dialog rebuilt with the new labels |
| **Fix crash on round loss** | ten sites, `0x077f5a`–`0x0c0ada` | 42-byte blocks → `nop` |
| **Fix keyboard input after ALT+TAB** | signature | `push 6` → `push 0xA` at `SetCooperativeLevel` |
| **XInput gamepad support** | `0x0001c4`, `0x0422a8`, `0x0422ac`, `0x1bc13b`, `0x1bc13f`, `0x095bdc`, `0x095217`, `0x1c530e`, `0x1c52ac`, `0x0971bd`, `0x207702`, `0x20779e`, `0x23dd70`, the keyboard profile's eleven config-block references, `0x094ea0`, `0x096b61`, `0x096c8e`, F7 page constants, six `.rdata` caves | routine, twin-stick tables and lever cleanup in runs of zeros; handler, F7 page and picker tables repointed for both players |
| **Music from files** | new `.vocd` section, entry point, 37 call sites | every call to `mciSendCommandA` pointed at a routine that answers from WAV files |
| **Disable menu bar (Extras menu on F11)** | `0x1c4d42`, `0x1c4d4b`, `0x1c4d7e`, `0x1f427c`, `0x1f42d8`, `0x23dce8`, `0x6036b0` | dialog built in unused section padding and over the dead menu |

Bold entries are not part of original VO_Patch.

## How each patch works

Not every row needs a note; the four inherited byte edits are self-explanatory.
These are the rest, in the order of the table above. The CD audio and gamepad
patches install assembled machine code rather than editing bytes; the sources
and a longer account of both are in [asm/](asm/).

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
end of `.text` has 24 bytes left after the timer stub and the F11 dialog, and
the zero runs in `.data` are globals the game writes at runtime. The
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
read through it at all. A routine in `.rdata` padding calls `XInputGetState`
and folds the result into the game's own action tables.

The device number keys three tables, not one, and all three had to move
together: the profile switch at `0x442ea4` picks the handler, `0x4967d4`
picks the F7 page, and `0x495e0f` decides whether the picker will let you
leave. The picker skips device slots whose name pointer is null, so hiding
the legacy profiles is zeroing the rest.

The gamepad profile takes *Keyboard only(Simple)*'s slot, the only F7 page
that binds all twelve actions, with its input list swapped for pad inputs.
Bindings are one byte per action, so pad entries occupy `0xE0`-`0xEF` in the
scancode space, which the game does not otherwise use. Player 2 is a full
mirror, so both sides are the same routine with a different parameter block.
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
It is 164 bytes, of which 116 are tables. It binds nothing, so it takes the
page-table entry that opens no dialog, which also disposes of the
`0x3651554 == 1` check that made **Next** refuse without a joystick attached.

Jump and guard are lever gestures rather than buttons - both levers spread
outward, both squeezed inward - so they share the words movement writes to,
and neither came out while moving. A second routine after each tick sorts that
out, and only when a pad was read, so the keyboard path is untouched.

**F11 dialog.** No dialog resource ever existed, so one is built at runtime
from a template written into unused space - over the old menu, which this same
patch unhooks. Every control carries the game's own command ID, so clicks go
straight to the main window and **Quit Program** is just the *Exit Game*
command; the check boxes read the game's own flags. F11 because F9 disconnects
a network game and F10 is a Windows system key.

Motion is not among them any more, the F5 page having taken it over. The
handler that filled the box stays and does nothing: with no control carrying
its ID, `GetDlgItem` hands back nothing to talk to.
