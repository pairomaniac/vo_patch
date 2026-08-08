# Virtual-On (PC, 1997) - v_on.exe patcher

Gets *Cyber Troopers Virtual-On* running properly on a modern system: fixes
the crashes, the frame rate and the keyboard, adds XInput gamepad support for
both players, and reads the soundtrack from files instead of the disc.

Some of the byte edits come from the original VO_Patch 0.43 (2008) by
[UE2A-GEL](https://jaguarandi.xxxxxxxx.jp/). Rights to the
game belong to SEGA. `LICENSE` (CC0) covers this repository's own code, not
the game and not the bytes quoted from it.

<img width="500" alt="Screenshot_20260808_144501" src="https://github.com/user-attachments/assets/343c2da7-a7ba-47ee-bb45-2c1848d0282a" />

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

Under the same list, **Resolution and windowing** is not a patch but a button:
it downloads [cnc-ddraw](#resolution-and-windowing-cnc-ddraw) and installs it
beside the game.

## Using the patcher

Select `v_on.exe` and press **Apply patches**. Expand **ESSENTIAL PATCHES** or
**EXTRA PATCHES** and click the ⓘ beside a patch to read what it does, and
untick anything you do not want.

Only the unmodified disc file is accepted - 6,650,880 bytes, MD5
`a464b0ff32d5bab499f265e45658504e`. The original is copied to `v_on.exe.bak`
before anything is written, and **Restore original** puts it back so you can
change your selection. Nothing is written unless every selected patch applied
and the backup was made, so a failure leaves the game exactly as it was.

### Which build

The patcher works on one build and refuses everything else. Every patch is a
fixed file offset, and those offsets belong to that build alone - applying
them to another would write into unrelated code.

<img height="175" alt="image" src="https://github.com/user-attachments/assets/cefb985b-b8ee-4b40-ae35-ab7431fde607" />
<img height="175" alt="image" src="https://github.com/user-attachments/assets/5c3acc21-0bf5-4961-acce-ae1990061c4f" />


| Build | Size | MD5 | |
| --- | --- | --- | --- |
| Retail disc | 6,650,880 | `a464b0ff32d5bab499f265e45658504e` | supported |
| USA OEM | 6,649,344 | `4c70f780a7f0d98d74be62304fb99021` | not supported |

The OEM release is a different build of the same game and is not supported.
Reinstalling from the same disc will not produce a different file.

If your file is neither of these, the patcher shows both checksums side by
side and says which one it got.

If **XInput gamepad support** was among them, `v_on.ini` is moved to
`v_on.ini.bak` at the same time and the game writes a fresh one. **Restore
original** puts that back too, keeping whatever the patched game wrote as
`v_on.ini.patched`.

Everything the patcher does is also available without a window:

```bash
python3 vo-patch.py --rip SOURCE DIR   # soundtrack, from a cue sheet or drive
python3 vo-patch.py --ddraw DIR        # fetch and install cnc-ddraw
python3 vo-patch.py --netplay DIR      # install the UDP netplay DLL
python3 vo-patch.py --selfcheck        # validate the patch tables
```

## Gamepad

**XInput gamepad support** rebuilds the F7 device list. The legacy joystick
profiles are hidden and three remain, for both players - pad 1 drives 1P,
pad 2 drives 2P.

<img width="350" alt="F7 device list with the three profiles" src="https://github.com/user-attachments/assets/457643b7-f42b-49b2-993f-50b56733c59d" />
<img width="350" alt="F7 bind page for Gamepad (XInput)" src="https://github.com/user-attachments/assets/9fac5899-0465-4fbe-990f-65f978352632" />

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

<img height="280" alt="image" src="https://github.com/user-attachments/assets/8d98b4ea-8092-4399-8794-81d858a0f5a5" />

### Ripping the tracks

Use the **CD MUSIC** section of the patcher. Pick `v_on.exe` first so it
knows where the files go, put a cue sheet or a drive in **Source**, then
press **Rip tracks**. Closing the window mid-rip cancels it and discards the
part track.

<img height="280" alt="image" src="https://github.com/user-attachments/assets/7754628c-028f-4471-8a33-59264e930e41" />

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

## Internet play

Two-player link mode works over a LAN and, in the stock game, only over a
LAN. It finds opponents by broadcasting on the local network, which no
router forwards, and DirectPlay itself is deprecated on Windows and only
partly implemented under Wine. Port forwarding does not help: the search
never leaves the building.

**Internet play** under EXTRA PATCHES replaces the game's DirectPlay layer
with plain UDP. One player hosts, the other types an address. Everything
above the transport - the frame exchange, the resend, the input delay the
game negotiates from the measured round trip - is unchanged, because that
part was never DirectPlay's; it was the game's own and it holds up well.

```bash
python3 vo-patch.py --netplay path/to/game            # install
python3 vo-patch.py --netplay path/to/game --remove   # put the stock one back
```

The original `dpctrl.dll` is kept as `dpctrl.dll.stock` and Remove restores
it, so you can go back to LAN-and-VPN play whenever you like.

### Playing

- **Both players need this installed**, and the same patches applied. The
  two machines run the same simulation in lockstep; if they disagree about
  the rules, they will disagree about the match.
- **The host forwards UDP 47624.** The joining player needs nothing.
- Host picks *Host a game*, reads out the address - the dialog shows the
  local one, and can ask the internet for the public one - and the other
  player picks *Join a game* and types it in. Host names work too.
- No lobby, no server, no accounts. Arrange the match however you already
  arrange it.

If the connection cannot be established, the joining side waits until you
cancel. Once a match is running, a player who quits or crashes is noticed
within a few seconds.

## Resolution and windowing (cnc-ddraw)

The game asks for 640x480 exclusive fullscreen and leaves the rest to the
display, which on a modern panel usually means a stretched picture. The 4:3
framebuffer is baked into the rasteriser, so no byte edit fixes it - it needs
something between the game and the graphics driver.

<img height="360" alt="image" src="https://github.com/user-attachments/assets/28c65fbf-99ed-4b11-b2a4-01fde3bf3d16" />

[cnc-ddraw](https://github.com/FunkyFr3sh/cnc-ddraw) replaces the DirectDraw
the game renders through, adding windowed and borderless modes, correct aspect
ratio and upscaling. Every patch here works with it.

**Install** under EXTRA PATCHES downloads the current release and unpacks it
beside `v_on.exe` - `ddraw.dll`, `ddraw.ini`, `cnc-ddraw config.exe` and the
shaders. Once it is there the same button reads **Remove**, which deletes
them again and keeps `ddraw.ini`. From a terminal:
shaders. From a terminal:

```bash
python3 vo-patch.py --ddraw path/to/game
```

Comes straight from
[the releases page](https://github.com/FunkyFr3sh/cnc-ddraw/releases), so it
is always the current version. An existing `ddraw.ini` is kept, so re-running
to update leaves your settings alone. Close the game first: Windows will not
replace a DLL that is loaded.

**On Linux there is a second step.** Set `ddraw` to native in `winecfg` for
that prefix, or run `cnc-ddraw config.exe` once. Without it Wine keeps using
its own DirectDraw and nothing changes.

A fresh `ddraw.ini` is cnc-ddraw's own file with five settings changed:
`fullscreen`, `windowed`, `maintas`, `noactivateapp` and `toggle_borderless`
are all set to `true`, giving a borderless window at 4:3. Everything else,
including the comments and the per-game sections, is left as it comes. Change
any of it with `cnc-ddraw config.exe`.

The intro movie is the one thing upscaling cannot help. The game plays
`von.avi` through MCI, which draws into a window of its own that never passes
through DirectDraw, and then pins that window to the top left corner at a
fixed size. So it plays small and in the corner over an upscaled picture.
Skip it with Space, Enter, Escape or pad A, or run at 1:1 if you want to
watch it.

**On Linux there is a second step.** Set `ddraw` to native in `winecfg` for
that prefix, or run `cnc-ddraw config.exe` once. Without it Wine keeps using
its own DirectDraw and nothing changes.

gamescope handles the scaling without a DLL, if you would rather:

```bash
gamescope -W 1920 -H 1080 -w 640 -h 480 -f -S integer -- %command%
```

`-S fit` fills more of the screen without whole-number scaling.

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
`pyinstaller vo-patch.spec`. It builds as `vo-patch-dev.exe`: releases take
their version from the git tag, and a source tree has no tag to take it from.

To change the machine code the patches install, see [asm/](asm/), which
`asm/build.py` builds into the hex strings in `vo-patch.py`. Never edit those
by hand. `python3 vo-patch.py --selfcheck` validates the patch tables without
a copy of the game; `python3 tools/selftest.py path/to/v_on.exe` applies them
to a real one.

Everything the patcher does is also available without a window:

```bash
python3 vo-patch.py --rip SOURCE DIR   # soundtrack, from a cue sheet or drive
python3 vo-patch.py --ddraw DIR        # fetch and install cnc-ddraw
python3 vo-patch.py --selfcheck        # validate the patch tables
```
---

Written with AI assistance. Every offset and byte sequence is checked against
the original executable before it is written, and the patcher refuses anything
that is not the unmodified disc file - but this is a hobby project poking at a
nearly 30-year-old binary so expect bugs.
