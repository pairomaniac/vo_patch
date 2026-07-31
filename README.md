# Virtual-On (PC, 1997) - v_on.exe patcher

New patcher for *Cyber Troopers Virtual-On* on Windows: four of original VO_Patch's byte edits plus fixes of my own.

VO_Patch 0.43 (2008) is by [UE2A-GEL](https://jaguarandi.xxxxxxxx.jp/). Rights to the game belong to SEGA.

<img width="473" height="622" alt="image" src="https://github.com/user-attachments/assets/b19d8759-c314-442a-8ed7-a478513ce429" />


## Download

**Windows:** Get `vo-patch-*.exe` from the
[latest release](https://github.com/pairomaniac/vo_patch/releases/latest).

It is unsigned, so SmartScreen calls it an unknown publisher on the first run - the build log can be found under this repository's Actions.

**Linux:** check [Running from source](https://github.com/pairomaniac/vo_patch/edit/main/README.md#running-from-source).

## What the patches do

**Essential** are ticked by default and fix things that are broken on modern
systems; **Extra** are up to taste. How each one works is under
[Notes](#notes).

### Essential

- **Skip processor check** - lets the game start on a modern CPU, without you
having to set `ProcessorCheck=Off` in `v_on.ini` first.
- **Raise multimedia timer resolution** - stops the game running in slow
motion on Windows 2000 and later. Not needed on Wine.
- **Allow v_on.ini to save Motion value** - makes `Motion=` work and stick.
It is an FPS divisor: 1/1 draws every frame, and anything less flickers.
- **Fix crash on round loss** - stops the crash when you lose as Temjin,
Viper II, Apharmd or Raiden.
- **Fix keyboard input after ALT+TAB** - without it, alt-tabbing away or
opening an F-key dialog kills the keyboard for the rest of the session.

### Extra

- **Disable menu bar (Extras menu on F11)** - drops the strip across the top
and puts the Debug options on F11 instead: Motion, No shot, SE, CD, Kill,
Scorekeeping and **Quit Program**. Every other menu was always on a key:

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

- **XInput gamepad support** - adds a *Gamepad (XInput)* device profile and
binds every action to a modern controller, for both players. See
[Gamepad](#gamepad).
- **Disable disc check** - skips the "Please insert VIRTUAL ON CD" prompt.
Mount the image anyway if you want the CD music.
- **Arcade sound effect timing** - sound effects fire without their built-in
delay, so rapid-fire weapons sound like the arcade.
- **Increase sound output frequency** - 22050 to 44100 Hz. Subtle.
- **Enemy Fei-Yen hypermode sound fix** - restores the sound an enemy Fei-Yen
makes when it powers up, which a bug left silent.
- **Hide loading screen text** - removes "Now Loading . . .".

## Running from source

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

To build the Windows binary yourself, `pip install pyinstaller` and run
`pyinstaller vo-patch.spec`. The spec takes the version out of the script, so
that is the only place it is written down.

Select `v_on.exe` and press Apply. Open a list and press the (i) next to a patch
to read what it does, and untick anything you do not want.

Only the unmodified disc file is accepted - 6,650,880 bytes, MD5
`a464b0ff32d5bab499f265e45658504e`. The original is copied to `v_on.exe.bak`
before anything is written, and **Restore original** puts it back so you can
change your selection.

## Gamepad

**XInput gamepad support** adds a *Gamepad (XInput)* profile to F7 alongside
*Keyboard only*, and hides the legacy joystick profiles. All twelve
actions are bound from the F7 screen, for both players, and the keyboard
keeps working alongside the pad.

Pad 1 drives 1P and pad 2 drives 2P. Sticks, triggers, bumpers and face
buttons are all in the bind list; the sticks are read as eight directions.

| Button | Does |
| --- | --- |
| **A** | Accept - skips the intro, confirms menus |
| **Select** | Camera |
| **Start** | Pause |

Defaults on both sides: left stick moves, right stick turns, LT and RB fire
left and right, RT fires both, LB dashes, A jumps, X guards. **Default** on
the F7 page puts them back.

Applying the patch moves `v_on.ini` to `v_on.ini.bak`, because binds saved by
the unpatched game do not survive the new device list; the game writes a fresh
one. **Restore original** puts it back.

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
| **XInput gamepad support** | `0x0001c4`, `0x0422a8`, `0x1bc13b`, `0x1c530e`, `0x0971bd`, F7 page constants, four `.rdata` caves | routine and tables in section padding, both players' profile dispatch repointed |
| **Menu bar off, F11 dialog** | `0x1c4d42`, `0x1c4d4b`, `0x1c4d7e`, `0x1f427c`, `0x1f42d8`, `0x1f43cf`, `0x23dce8`, `0x6036b0` | dialog built in unused section padding and over the dead menu |

Bold entries are not part of original VO_Patch.

## Notes

The short version of how each one works, in the order of the list above.

**Processor check.** `ProcessorCheck=Off` does not switch the check off, it
stops the game switching it *on*. One `or` sets the flag that the MMX,
Pentium and vendor branches all read; nopping it leaves the flag clear
whatever the ini says.

**Timer resolution.** The game calls `timeGetTime` but never
`timeBeginPeriod`, so it runs slow wherever the default timer period is
coarse. A stub in `.text` padding calls `timeBeginPeriod(1)` and jumps to the
real entry point. VO_Patch shipped `vo_speed.exe` for the same job. No-op
under Wine.

**Motion.** The value parsed out of `Motion=` was always correct - four
routines then overwrote it, one at start-up and three that fire on
resolution and view changes. Removing all four lets it stand:

```ini
[Option]
Motion=1
```

Its fallbacks for a missing or bad value wrote 3, so a typo put the flicker
back; they write 1 now. The Debug menu and the Extras dialog use another
path, so runtime changes still work.

**Lose-a-round crash.** Ten continue-screen routines read through a pointer
that is really a float constant:

```asm
mov eax, [ebp-4]        ; = 0xC000CDE4, the float -2.0126
fld dword ptr [eax+8]   ; access violation
```

That address was readable on Windows 9x and is not now. Each block only
undoes a translation the routine has already reset, so `nop` is safe. The ten
are similar but not identical, hence listed out one by one.

**Alt-tab.** The game acquires its DirectInput keyboard `DISCL_FOREGROUND`
and never re-acquires it after losing focus. `DISCL_BACKGROUND` removes the
condition.

**F11 dialog.** No dialog resource ever existed, so one is built at runtime
from a template written into unused space - over the old menu, which this
same patch unhooks. Every control carries the game's own command ID, so
clicks go straight to the main window and **Quit Program** is just the *Exit
Game* command; the check boxes read the game's own flags. F11 because F9
disconnects a network game and F10 is a Windows system key.

**No disc.** A helper returns -1 when no disc is found and the caller loops
on a message box. Removing the branch into that loop falls through to the
success path. The scan is untouched, so a mounted image is still found, which
is what the CD audio needs.

**XInput gamepad.** The game predates XInput and reads pads through the
Windows 95 joystick API, which on a modern controller reports a partial view -
one trigger unreachable, axis order inconsistent between Windows and Wine. So
the pad is not read through it at all. A routine in `.rdata` padding calls
`XInputGetState` and folds the result into the game's own action tables,
which is what makes both levers, dash and guard work from one pad.

It hangs off the *Keyboard only(Simple)* profile, the only F7 page that binds
all twelve actions, with its input list swapped for pad inputs. Bindings are
one byte per action, so pad entries occupy `0xE0`-`0xEF` in the scancode
space, which the game does not otherwise use. Player 2 is a full mirror -
its own handler, bindings, lever words and action tables - so both sides are
the same routine with a different parameter block.

Start and A are also posted as key messages from the message pump rather than
the input tick, because the tick does not run on the intro or while paused.

**Sample rate.** This is the DirectSound buffer format, not the samples,
which are 8-bit at 7500 or 11025 Hz either way. VO_Patch set only
`nSamplesPerSec`, leaving `nAvgBytesPerSec` inconsistent; both are set here.


---

Written with AI assistance. Every offset and byte sequence is checked against
the original executable before it is written, and the patcher refuses anything
that is not the unmodified disc file - but this is a hobby project poking at a
30-year-old binary so expect bugs.
