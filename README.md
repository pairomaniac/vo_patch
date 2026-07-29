# Virtual-On (PC, 1997) - v_on.exe patcher

New patcher for *Cyber Troopers Virtual-On* on Windows: four of original VO_Patch's byte edits plus fixes of my own.

VO_Patch 0.43 (2008) is by [UE2A-GEL](https://jaguarandi.xxxxxxxx.jp/). Rights to the game belong to SEGA.

<img width="476" height="628" alt="image" src="https://github.com/user-attachments/assets/70a3a0b6-92c7-48b8-9a23-6e0939f0ae8f" />


## What the patches do

The patcher splits these into two groups. **Essential** are ticked by
default and fix things that are broken on modern systems; **Extra** are
up to taste.

### Essential

- **Skip processor check** - the same as `ProcessorCheck=Off` in `v_on.ini`,
without having to edit the ini. Skips the "requires MMX Technology Pentium"
check with it.
- **Raise multimedia timer resolution** - stops the game running in slow
motion on Windows 2000 and later. Not needed on Wine.
- **Allow v_on.ini to save Motion value** - `MOTION` is an FPS divisor: 1/1
draws every frame, 1/3 draws one frame in three. This makes `Motion=` in
`v_on.ini` work and stick, and falls back to full frame rate rather than one
frame in three when the key is missing. Anything but 1/1 flickers.
- **Fix crash on round loss** - stops the game crashing when you lose as
Temjin, Viper II, Apharmd or Raiden.
- **Fix keyboard input after ALT+TAB** - the keyboard keeps working after you
alt-tab away or open one of the F-key dialogs. Without it, input dies for the
rest of the session.

### Extra

- **Disable menu bar (Extras menu on F11)** - the menu bar was only ever a
strip across the top, so it goes, and F11 opens a dialog in its place holding
the Debug options: Motion, No shot, SE, CD, Kill, Scorekeeping and **Quit
Program**. Every other menu was always on a key as well, and still is:

| Key | Opens |
| --- | --- |
| **F1** | Help |
| **F3** | Pause |
| **F4** | High / low resolution |
| **F5** | Graphic Settings |
| **F6** | Mode Settings |
| **F7** | Device Settings |
| **F8** | Sound Test |
| **F11** | Extras, the new dialog |
- **Disable disc check** - skips the "Please insert VIRTUAL ON CD" prompt. The
drive is still looked for, so mount the image anyway if you want music.
- **Arcade sound effect timing** - sound effects fire without their built-in
delay, so rapid-fire weapons sound like the arcade.
- **Increase sound output frequency** - 22050 to 44100 Hz. Subtle; the
samples themselves are still 8-bit.
- **Enemy Fei-Yen hypermode sound fix** - restores the sound an enemy Fei-Yen
makes when it powers up. It was silent due to a bug.
- **Hide loading screen text** - removes the "Now Loading . . ." text.

## Usage

Windows, with Python from python.org - Tk ships with it, nothing else needed:

```
py vo-patch.py
```

On Linux it uses GTK4 if it can and Tk if it cannot.

| Distro | GTK4 | Tk |
| --- | --- | --- |
| Debian, Ubuntu, Mint | `python3-gi gir1.2-gtk-4.0` | `python3-tk` |
| Fedora, RHEL | `python3-gobject gtk4` | `python3-tkinter` |
| Arch, EndeavourOS | `python-gobject gtk4` | `tk` |

```bash
sudo apt install python3-gi gir1.2-gtk-4.0   # Debian, Ubuntu, Mint
sudo dnf install python3-gobject gtk4        # Fedora
python3 vo-patch.py
```

`VOPATCH_UI=gtk` or `VOPATCH_UI=tk` forces one rather than letting it pick.

Select `v_on.exe` and press Apply. Open a list and press the (i) next to a patch
to read what it does, and untick anything you do not want.

Only the unmodified disc file is accepted - 6,650,880 bytes, MD5
`a464b0ff32d5bab499f265e45658504e`. The original is copied to `v_on.exe.bak`
before anything is written, and **Restore original** puts it back so you can
change your selection.

## Gamepad

The game predates XInput and reads pads through the Windows 95 joystick API
(`joyGetPosEx`). Modern controllers still show up, but several of the device
profiles under F7 only read two buttons.

Mapping the pad to keyboard keys avoids the whole problem - `input-remapper`
on Linux, Joy2Key or Steam Input. Both feed the game ordinary key
events, so configure it as **Keyboard only** in F7 and bind as usual.

Synthetic keys arrive through the same DirectInput path as real ones, so
**Keep input after alt-tab** applies to a remapped pad too.

## Music

The BGM is Redbook CD audio: the game drives it with 37 `mciSendCommandA`
calls against the `cdaudio` device. It needs a disc, or a virtual drive that
presents the audio tracks - without it has nothing to play, so the game
runs silent but otherwise fine.

**You can also use a CD audio emulator, such as this fork of [ogg-winmm](https://github.com/ayuanx/ogg-winmm)**

## Resolution

The game asks for 640x480 exclusive fullscreen and the display stretches it.
The 4:3 framebuffer is baked into the rasteriser, so this is a scaling problem
rather than something to patch.

**Linux.** gamescope, integer-scaled and pillarboxed:

```bash
gamescope -W 1920 -H 1080 -w 640 -h 480 -f -S integer -- %command%
```

`-S fit` fills more of the screen without whole-number scaling.

**Windows.** GPU control panel, *Adjust desktop size and position* → aspect
ratio, with scaling performed on the GPU. Some monitors override this in their
own menu. dgVoodoo2 can force the aspect ratio if the driver will not.

## Patches

| Patch | Offsets | Change |
| --- | --- | --- |
| Remove SE playback wait | `0x2bba60` | `.data` `15` → `1` |
| Sound 22050 → 44100 Hz | `0x189546`, `0x189552` | `WAVEFORMATEX` `nSamplesPerSec` and `nAvgBytesPerSec` |
| Hide "Now Loading . . ." | `0x2c7678` | first byte → `NUL` |
| Enemy Fei-Yen hypermode SE | `0x058189`, `0x170dc9` | `cmp [eax+0x68], 1` → `2` |
| **No disc required** | `0x1c76d4` | `je` past the nag → `nop` |
| **Skip the processor check** | `0x107930` | `or [0xbf84c8], 1` → `nop`, so the check is never enabled |
| **Let v_on.ini set Motion** | `0x10afbe`, `0x10afeb`, `0x10b002`, `0x1c6941`–`0x1c8bd3` | fallbacks `3` → `1`, four overwrites → `nop` |
| **Raise timer resolution** | `0x1f423e`, `0xa8` | stub in `.text` padding, entry point redirected |
| **Fix the lose-a-round crash** | ten sites, `0x077f5a`–`0x0c0ada` | 42-byte blocks → `nop` |
| **Keep input after alt-tab** | signature | `push 6` → `push 0xA` at `SetCooperativeLevel` |
| **Menu bar off, F11 dialog** | `0x1c4d42`, `0x1c4d4b`, `0x1c4d7e`, `0x1f427c`, `0x1f42d8`, `0x1f43cf`, `0x23dce8`, `0x6036b0` | dialog built in unused section padding and over the dead menu |

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
was no dialog to open. This popup menu restores those options, while getting
rid of the top menu. Also added Quit Program option under this menu.

**No disc.** A helper scans the drives and returns -1 when the disc is
absent; the caller then loops on a message box. Removing the branch to that
loop lets it fall through to the success path. The scan itself is left in, so
the drive is still found and recorded when a disc or mounted image is present
- which is what the CD music needs.

**Processor check.** Disables the CPU detection - skips the MMX, Pentium and vendor
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

---

Written with AI assistance. Every offset and byte sequence is checked against
the original executable before it is written, and the patcher refuses anything
that is not the unmodified disc file - but this is a hobby project poking at a
30-year-old binary so expect bugs.
