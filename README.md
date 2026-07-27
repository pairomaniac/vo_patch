# Virtual-On (PC, 1997) - v_on.exe patcher

New patcher for *Cyber Troopers Virtual-On* on Windows: five of original VO_Patch's byte edits plus four fixes of my own.

VO_Patch 0.43 (2008) is by [UE2A-GEL](https://jaguarandi.xxxxxxxx.jp/). Rights to the game belong to SEGA.

## What the patches do

- **Remove SE playback wait** - sound effects fire without their built-in
  delay, so rapid-fire weapons sound like the arcade.
- **Sound 22050 → 44100 Hz** - doubles the audio output rate. Subtle; the
  samples themselves are still 8-bit.
- **Hide "Now Loading . . ."** - removes the loading text.
- **Always show Debug menu** - adds the Debug menu, with display toggles, a
  sound test and framerate options.
- **Enemy Fei-Yen hypermode SE** - restores the sound an enemy Fei-Yen makes
  when it powers up. It was silent due to a bug.
- **Let v_on.ini set Motion** - makes `Motion=1` in the ini actually work, so
  the game runs at full framerate instead of drawing one frame in three.
- **Raise timer resolution** - stops the game running in slow motion on
  Windows 2000 and later. Not needed on Wine.
- **Fix the lose-a-round crash** - stops the game crashing when you lose as
  Temjin, Viper II, Apharmd or Raiden.
- **Keep input after alt-tab** - the keyboard keeps working after you alt-tab
  away or open one of the F-key dialogs. Without it, input dies for the rest
  of the session.

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
| Always show Debug menu | `0x1c4d57` | menu resource `102` → `101` |
| Enemy Fei-Yen hypermode SE | `0x058189`, `0x170dc9` | `cmp [eax+0x68], 1` → `2` |
| **Let v_on.ini set Motion** | `0x1c8bc4`, `0x1c8bd3` | two stores → `nop` |
| **Raise timer resolution** | `0x1f423e`, `0xa8` | stub in `.text` padding, entry point redirected |
| **Fix the lose-a-round crash** | ten sites, `0x077f5a`–`0x0c0ada` | 42-byte blocks → `nop` |
| **Keep input after alt-tab** | signature | `push 6` → `push 0xA` at `SetCooperativeLevel` |

Bold entries are not part of original VO_Patch.

## Notes

**Lose-a-round crash.** Ten continue-screen routines dereference a stack slot
holding a float constant:

```asm
mov eax, [ebp-4]        ; = 0xC000CDE4, the float -2.0126
fld  dword ptr [eax+8]  ; access violation
```

The address falls in the old Windows 9x ring-0 range, where the read was
tolerated. Each block only undoes a translation the routine has already
reset, and is self-balancing - every push matched by its own `add esp, 0xc`,
every `fld` by an `fstp` - so `nop` is safe. Of 21 sites with this shape,
these ten are the ones in functions that store a float in the slot.

**Motion.** `MOTION` is logic ticks per rendered frame; 3 draws one frame in
three. The ini value is read, then overwritten with 2 or 3 by a later
routine. Removing those stores lets it stand:

```ini
[Option]
Motion=1
```

The Debug ▸ Motion menu works mid-session either way.

**Alt-tab.** The game acquires its DirectInput keyboard `DISCL_FOREGROUND`
and never re-acquires after losing focus. `DISCL_BACKGROUND` removes the
condition. Function keys were unaffected - they arrive as window messages.

**Sample rate.** This is the DirectSound buffer, not the samples, which are
8-bit at 7500 or 11025 Hz. VO_Patch leaves `nAvgBytesPerSec` at the 22050
value, which some DirectSound implementations reject; this sets both.

**Timer resolution.** `v_on.exe` calls `timeGetTime` but never
`timeBeginPeriod`, so it runs slow where the default period is coarse
(Windows 2000 onwards). VO_Patch shipped `vo_speed.exe` as a launcher for
this; the checkbox does it in-process instead, via a stub at the entry point:

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
loads at `0x400000`. Not needed under Wine, where the tick count already
comes from the host monotonic clock.
