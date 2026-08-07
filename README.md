# Virtual-On (PC, 1997) - v_on.exe patcher

Gets *Cyber Troopers Virtual-On* running properly on a modern system: fixes
the crashes, the frame rate and the keyboard, adds XInput gamepad support for
both players, and reads the soundtrack from files instead of the disc.

Some of the byte edits come from the original VO_Patch 0.43 (2008) by
[UE2A-GEL](https://jaguarandi.xxxxxxxx.jp/). Rights to the
game belong to SEGA. `LICENSE` (CC0) covers this repository's own code, not
the game and not the bytes quoted from it.

<img width="418" height="417" alt="The patcher main window" src="https://github.com/user-attachments/assets/a363ca3f-e9d4-4da9-8bb6-a427067343c6" />

## Download

**Windows:** Get `vo-patch-*.exe` from the
[latest release](https://github.com/pairomaniac/vo_patch/releases/latest).

It is unsigned, so SmartScreen calls it an unknown publisher on the first
run.

**Linux:** check [Running from source](#running-from-source).

## What the patches do

**Essential** fix what is broken on modern systems; **Extra** are up to
taste. Everything starts ticked. Every patch's offsets and how it works are
in [NOTES.md](NOTES.md).

### Essential

- **Skip processor check** - lets the game start on a modern CPU, without you
having to set `ProcessorCheck=Off` in `v_on.ini` first.
- **Fix frame rate (60 FPS)** - three fixes for the same complaint:
    - the multimedia timer resolution, without which the game runs at about
      70% speed on Windows 2000 and later (not needed on Wine),
    - `Motion=` in `v_on.ini`, which was read correctly and then overwritten,
      so it never stuck,
    - the *Motion Type* radios on F5, which only ever offered 1/3 and 1/2
      speed and now read **30 FPS** and **60 FPS**.
- **Fix crash on round loss** - stops the crash when you lose as Temjin,
Viper II, Apharmd or Raiden.
- **Fix keyboard input after ALT+TAB** - without it, alt-tabbing away or
opening an F-key dialog kills the keyboard for the rest of the session.

### Extra

- **XInput gamepad support** - twelve bindable actions on a modern
controller, plus the arcade twin-stick scheme, for both players. Costs the
*Keyboard only(Simple)* profile. See [Gamepad](#gamepad).
- **No disc required** - removes the disc check and reads the soundtrack from
`music\trackNN.wav` beside the game instead. See [Music](#music).
- **Disable menu bar (Extras menu on F11)** - removes the menu bar and puts
the Debug options on F11 instead: No shot, SE, CD, Kill 1P, Kill 2P,
Scorekeeping and **Quit Program**. Motion is not among them; it has moved to
F5. Every other menu was already on a key:

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

- **Better defaults with no v_on.ini** - what the game falls back on when a
key is missing, which on a first run is all of them: Sky on, all three Texture
boxes on, Field Graphic Rich, Screen Large.
- **Sound fixes** - three small ones: the built-in delay before each sound
effect is removed, output goes from 22050 to 44100 Hz, and an enemy Fei-Yen
gets back the hypermode sound a bug left silent.
- **Hide loading screen text** - removes "Now Loading . . .".

## Using the patcher

Select `v_on.exe` and press **Apply patches**. Expand **ESSENTIAL PATCHES** or
**EXTRA PATCHES** and click the ⓘ beside a patch to read what it does, and
untick anything you do not want.

Only the unmodified disc file is accepted - 6,650,880 bytes, MD5
`a464b0ff32d5bab499f265e45658504e`. The original is copied to `v_on.exe.bak`
before anything is written, and **Restore original** puts it back so you can
change your selection. Nothing is written unless every selected patch applied
and the backup was made, so a failure leaves the game exactly as it was.

If **XInput gamepad support** was among them, `v_on.ini` is moved to
`v_on.ini.bak` at the same time and the game writes a fresh one. **Restore
original** puts that back too, keeping whatever the patched game wrote as
`v_on.ini.patched`.

## Running from source

Windows, with Python from python.org - Tk ships with it, nothing else needed:

```
py vo-patch.py
```

On Linux, Tk usually needs installing:

```bash
sudo apt install python3-tk        # Debian, Ubuntu, Mint
sudo dnf install python3-tkinter   # Fedora, RHEL
sudo pacman -S tk                  # Arch, EndeavourOS
python3 vo-patch.py
```

To build the Windows binary yourself, `pip install pyinstaller` and run
`pyinstaller vo-patch.spec`. The spec takes the version out of the script, so
that is the only place it is written down.

To change the machine code the patches install, see [asm/](asm/), which
`asm/build.py` builds into the hex strings in `vo-patch.py`. Never edit those
by hand. `python3 vo-patch.py --selfcheck` validates the patch tables without
a copy of the game; `python3 tools/selftest.py path/to/v_on.exe` applies them
to a real one.

## Gamepad

<img width="382" height="267" alt="F7 device list with the three profiles" src="https://github.com/user-attachments/assets/457643b7-f42b-49b2-993f-50b56733c59d" />
<img width="382" height="267" alt="F7 bind page for Gamepad (XInput)" src="https://github.com/user-attachments/assets/9fac5899-0465-4fbe-990f-65f978352632" />

**XInput gamepad support** rebuilds the F7 device list. The legacy joystick
profiles are hidden and three remain, for both players - pad 1 drives 1P,
pad 2 drives 2P.

| Profile | What it is |
| --- | --- |
| **Keyboard (Real)** | the game's own two-lever keyboard scheme, bindable |
| **Gamepad (XInput)** | twelve named actions, bound from the F7 screen |
| **Twin-stick (XInput)** | the arcade levers, nothing to bind |

*Keyboard only(Simple)* is gone for now: it is the only F7 page that binds
all twelve actions, so the gamepad profile takes it. *Real* keeps its own
page.

Four buttons work on every profile, Start on the pause screen included:

| Button | Does |
| --- | --- |
| **A** | Accept - confirms menus |
| **Select** | Camera |
| **Start** | Pause |
| **D-pad** | Moves, so it also drives menus |

The D-pad is not bindable. It is wired to the same four directions as the
movement binds, which is what the menus read.

**A** skips the intro movie, the same as Space. **Start** does not - the game
ignores F3 while the movie plays.

### Gamepad (XInput)

Sticks, triggers, bumpers and face buttons are all in the bind list; the
sticks are read as eight directions. Defaults on both sides: left stick
moves, right stick turns, LT and RB fire left and right, RT fires both, LB
dashes, A jumps, X guards. **Default** on the F7 page puts them back.

### Twin-stick (XInput)

Each thumbstick *is* a lever, the way the cabinet worked, so nothing is
bindable.

| Input | Does |
| --- | --- |
| Both sticks the same way | walk, strafe |
| Left down + right up, or the reverse | turn |
| Sticks apart | jump, and auto-face the opponent |
| Sticks together | crouch, which is the guard |
| **LT**, **RT** | left and right weapon; both at once is the centre weapon |
| **LB**, **RB** | the turbo buttons - dash in the direction you are moving |

### Keyboard (Real)

Keys cannot be shared between players. If 2P wants keys 1P already has,
rebind 1P first.

Applying the patch moves `v_on.ini` to `v_on.ini.bak`, because binds saved by
the unpatched game do not fit the new device list; the game writes a fresh
one. **Restore original** puts it back.

## Music

The BGM is Redbook CD audio, so unpatched it needs a disc or a virtual drive
with the audio tracks - a data-only ISO plays nothing. **No disc required**
reads it from WAV files beside the game instead. No drive, no extra DLL.

### Ripping the tracks

Use the **CD MUSIC** section of the patcher. Pick `v_on.exe` first so it
knows where the files go, put a cue sheet or a drive in **Source**, then
press **Rip tracks**. Closing the window mid-rip cancels it and discards the
part track.

Or from a terminal:

```bash
python3 vo-patch.py --rip VIRTUAL-ON.cue /path/to/VIRTUAL-ON
python3 vo-patch.py --rip /dev/sr0       /path/to/VIRTUAL-ON
python3 vo-patch.py --rip                # list drives
```

`bin`/`cue` is exact and needs no drive - sector offsets come from the sheet.
Drives are read with `CDROMREADAUDIO` on Linux and `IOCTL_CDROM_RAW_READ` on
Windows; a cdemu device behaves like a physical one.

Output is about 320 MB: 26 tracks, roughly 30 minutes, uncompressed.

```
VIRTUAL-ON\
    v_on.exe
    music\
        track02.wav ... track27.wav
```

Track 1 is the data track and has no file. The numbering must match the disc,
because the game asks for tracks by number.

### At runtime

With `music\` missing or empty, the game reads the drive as before. With
tracks present, they are used, disc or no disc. Under Wine they play through
`mciwave` - no `dosdevices` entry, raw device link or cdemu instance needed.

## cnc-ddraw

The game asks for 640x480 exclusive fullscreen and leaves the rest to the
display, which on a modern panel usually means a stretched picture. The 4:3
framebuffer is baked into the rasteriser, so no byte edit fixes it - it needs
something between the game and the graphics driver.

[cnc-ddraw](https://github.com/FunkyFr3sh/cnc-ddraw) is a separate download
that replaces the DirectDraw the game renders through. It adds windowed and
borderless modes, correct aspect ratio and upscaling. Unzip it beside
`v_on.exe`; nothing needs configuring, and every patch here works with it.

On Linux it runs under Wine and Proton. gamescope handles the scaling without
a DLL, if you would rather:

```bash
gamescope -W 1920 -H 1080 -w 640 -h 480 -f -S integer -- %command%
```

`-S fit` fills more of the screen without whole-number scaling.

---

Written with AI assistance. Every offset and byte sequence is checked against
the original executable before it is written, and the patcher refuses anything
that is not the unmodified disc file - but this is a hobby project poking at a
nearly 30-year-old binary so expect bugs.
