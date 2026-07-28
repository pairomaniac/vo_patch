# Virtual-On (PC, 1997) - v_on.exe patcher

New patcher for *Cyber Troopers Virtual-On* on Windows: four of original VO_Patch's byte edits plus fixes of my own.

VO_Patch 0.43 (2008) is by [UE2A-GEL](https://jaguarandi.xxxxxxxx.jp/). Rights to the game belong to SEGA.

<img width="454" height="416" alt="image" src="https://github.com/user-attachments/assets/cd5a80f7-a54a-45a6-a316-ef11d4e09de0" />

## What the patches do

- **Remove SE playback wait** - sound effects fire without their built-in
  delay, so rapid-fire weapons sound like the arcade.
- **Sound 22050 → 44100 Hz** - doubles the audio output rate. Subtle; the
  samples themselves are still 8-bit.
- **Hide "Now Loading . . ."** - removes the loading text.
- **Enemy Fei-Yen hypermode SE** - restores the sound an enemy Fei-Yen makes
  when it powers up. It was silent due to a bug.
- **Skip the processor check** - the same as `ProcessorCheck=Off` in
  `v_on.ini`, without having to edit the ini. Skips the "requires MMX
  Technology Pentium" check with it.
- **Let v_on.ini set Motion** - makes `Motion=` in `v_on.ini` work and stick,
  and falls back to full frame rate rather than one frame in three when the
  key is missing. Anything but 1/1 flickers.
- **Raise timer resolution** - stops the game running in slow motion on
  Windows 2000 and later. Not needed on Wine.
- **Fix the lose-a-round crash** - stops the game crashing when you lose as
  Temjin, Viper II, Apharmd or Raiden.
- **Keep input after alt-tab** - the keyboard keeps working after you alt-tab
  away or open one of the F-key dialogs. Without it, input dies for the rest
  of the session.
- **Remove the menu bar, Extras dialog on F11** - the menu bar is only ever a
  strip across the top, so it goes, and F11 opens a settings box instead:
  frame rate, No shot, SE, CD, Kill and Scorekeeping.

## Usage

Windows, with Python from python.org - Tk ships with it, nothing else needed:

```
py vo-patch.py
```

Linux, GTK4 if present (native Wayland), Tk otherwise:

```bash
sudo dnf install python3-gobject gtk4     # or python3-tkinter
python3 vo-patch.py
```

`VOPATCH_UI=gtk` or `VOPATCH_UI=tk` forces one.

Select `v_on.exe`, tick, Apply. Only the unmodified disc file is accepted -
6,650,880 bytes, MD5 `a464b0ff32d5bab499f265e45658504e`. The original is
copied to `v_on.exe.bak` first; restore it to change your selection.

## Gamepad

The game predates XInput and reads pads through the Windows 95 joystick API
(`joyGetPosEx`). Modern controllers still show up, but several of the device
profiles under F7 only read two buttons.

Mapping the pad to keyboard keys avoids the whole problem - `input-remapper`
on Linux, Joy2Key or Steam Input. Both feed the game ordinary key
events, so configure it as **Keyboard only** in F7 and bind as usual.

Synthetic keys arrive through the same DirectInput path as real ones, so
**Keep input after alt-tab** applies to a remapped pad too.

## Patches

| Patch | Offsets | Change |
| --- | --- | --- |
| Remove SE playback wait | `0x2bba60` | `.data` `15` → `1` |
| Sound 22050 → 44100 Hz | `0x189546`, `0x189552` | `WAVEFORMATEX` `nSamplesPerSec` and `nAvgBytesPerSec` |
| Hide "Now Loading . . ." | `0x2c7678` | first byte → `NUL` |
| Enemy Fei-Yen hypermode SE | `0x058189`, `0x170dc9` | `cmp [eax+0x68], 1` → `2` |
| **Skip the processor check** | `0x107930` | `or [0xbf84c8], 1` → `nop`, so the check is never enabled |
| **Let v_on.ini set Motion** | `0x10afbe`, `0x10afeb`, `0x10b002`, `0x1c6941`–`0x1c8bd3` | fallbacks `3` → `1`, four overwrites → `nop` |
| **Raise timer resolution** | `0x1f423e`, `0xa8` | stub in `.text` padding, entry point redirected |
| **Fix the lose-a-round crash** | ten sites, `0x077f5a`–`0x0c0ada` | 42-byte blocks → `nop` |
| **Keep input after alt-tab** | signature | `push 6` → `push 0xA` at `SetCooperativeLevel` |
| **Menu bar off, F11 dialog** | `0x1c4d42`, `0x1c4d4b`, `0x1c4d7e`, `0x1f427c`, `0x1f42d8`, `0x23dce8`, `0x60c258` | dialog built in unused section padding |

Bold entries are not part of original VO_Patch.

## Notes

**Lose-a-round crash.** Ten continue-screen routines dereference a stack slot
holding a float constant:

```asm
mov eax, [ebp-4]        ; = 0xC000CDE4, the float -2.0126
fld  dword ptr [eax+8]  ; access violation
```

The address falls in the old Windows 9x ring-0 range, where the read was
tolerated. Each block only undoes a translation the routine has already reset
and is self-balancing, so `nop` is safe.

**F11 dialog.** The Debug options only ever existed as menu items, so there
was no dialog to open. This builds one in the padding at the end of three
sections: the template in `.rsrc`, the window-proc hook and dialog procedure
in `.text`, tables in `.rdata`. `DialogBoxIndirectParamA` is fetched with
`GetProcAddress` so the template need not be a resource, and the font matches
the game's own dialogs.

Check box state is read from the game's own flags, found by scanning for the
sequence it uses to check-mark each menu item. Each control's ID is the game's
command ID, so clicks are posted straight to the main window.

F11 rather than F9 or F10: F9 disconnects a network game, and F10 is a system
key the message loop discards during a match.

**Processor check.** The four `[Processor]` keys build a bit mask at
`0xbf84c8`, one bit each, set when the key reads anything other than `Off` —
the bit means the check is enabled. Detection returns `0x33` straight away
when bit 0 is clear, so removing the `or` skips the MMX, Pentium and vendor
checks with it.

**Motion.** `MOTION` is logic ticks per rendered frame; 3 draws one frame in
three, and the picture flickers at anything but 1/1. The ini value is read
correctly, then four routines overwrite it with 2 or 3 - one at start-up and
three more that fire on resolution and view changes. Removing all four lets
the ini value stand:

```ini
[Option]
Motion=1
```

The value parsed from `Motion=` is left alone, but the three fallbacks - key
missing, above 5, below 0 - wrote 3, so a typo or a missing key put the
flicker back. They write 1 now.

The Debug menu and the Extras dropdown write `MOTION` through a separate path,
so they still change it at runtime.

**Alt-tab.** The game acquires its DirectInput keyboard `DISCL_FOREGROUND`
and never re-acquires after losing focus. `DISCL_BACKGROUND` removes the
condition.

**Sample rate.** This is the DirectSound buffer, not the samples, which are
8-bit at 7500 or 11025 Hz. VO_Patch leaves `nAvgBytesPerSec` at the 22050
value; this sets both.

**Timer resolution.** `v_on.exe` calls `timeGetTime` but never
`timeBeginPeriod`, so it runs slow where the default period is coarse.
VO_Patch shipped `vo_speed.exe` for this; the checkbox does it in-process
instead, via a stub at the entry point:

```asm
push  offset "winmm.dll"
call  [LoadLibraryA]
push  offset "timeBeginPeriod"
push  eax
call  [GetProcAddress]
test  eax, eax
jz    skip
push  1
call  eax
skip:
jmp   0x5e7930          ; original entry point
```

Hardcoded addresses are fine here: no `DYNAMICBASE`, so the image always
loads at `0x400000`. Not needed under Wine.
