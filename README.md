# Virtual-On (PC, 1997) - v_on.exe patcher

Gets *Cyber Troopers Virtual-On* running properly on a modern system: fixes
the crashes, the frame rate and the keyboard, adds XInput gamepad support for
both players, and reads the soundtrack from files instead of the disc.

<img height="700" alt="image" src="https://github.com/user-attachments/assets/187c8a52-e2f8-4121-a88e-f66993cfd9c9" />

## Download

**Windows:** Get `vo-patch-*.exe` from the
[latest release](https://github.com/pairomaniac/vo_patch/releases/latest).

It is unsigned, so SmartScreen calls it an unknown publisher on the first
run.

**Linux:** check [Running from source](#running-from-source).

## Using the patcher

Select `v_on.exe` and press **Apply patches**. Click the ⓘ beside a patch to
read what it does, and untick anything you do not want. Everything starts
ticked.

**Restore original** puts the game back, so you can change your selection and
apply again.

**ADD-ONS** is separate because nothing in it edits the game - those entries
write files beside it, and each has its own install and remove button.

Only the unmodified disc file is accepted. If yours is refused, see
[Which build](#which-build).


## What the patches do

**Essential** fix what is broken on modern systems; **Extra** are up to
taste. Every patch's offsets and how it works are in
[NOTES.md](docs/NOTES.md).

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
Scorekeeping and **Quit**. **Credits** is new - it jumps to the credit roll
from any match, so you can see it without finishing the game. Motion is
not there; it has moved to F5. Every other menu was already on a key:

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

- **Better defaults with no v_on.ini** - what the game falls back on for any
setting `v_on.ini` does not have, which on a first run is all of them: Sky on,
all three Texture boxes on, Field Graphic Rich, Screen Large.
- **Sound fixes** - three small ones: the built-in delay before each sound
effect is removed, output goes from 22050 to 44100 Hz, and an enemy Fei-Yen
gets back the hypermode sound a bug left silent.
- **Intro, loading and ending screens** - four fixes to the screens either
side of the fighting:
    - the intro movie is fitted to the window, not left small in a corner
    - "Now Loading . . ." is hidden
    - the ending credits can be skipped - hold **A**, **Select** or Space for
      a second. Stock has no way past them at all
    - the initials screen after the credits takes those same buttons, as well
      as the weapon trigger

**Show the version, and credit the patch in the ending roll**, on by default:
the patcher's version in the bottom right of the title screen, and two lines
under the title at the top of the ending credits. The credit lines rewrite
`scrstfcg.bin` and `scrstfmp.bin`, backing both up; **Restore original**
puts them back. If either file is missing the whole box is skipped, version
included, and the rest still applies.

Under the same list, **Resolution and windowing** is not a patch but a button:
it downloads [cnc-ddraw](#resolution-and-windowing-cnc-ddraw) and installs it
beside the game.

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
| **A** | Accept - confirms menus, and skips the win and lose screens |
| **Select** | Camera, and skips those screens too |
| **Start** | Pause |
| **D-pad** | Moves, so it also drives menus |

The D-pad is not bindable. It is wired to the same four directions as the
movement binds, which is what the menus read.

**A** skips the intro movie, the same as Space. **Start** does not - the game
ignores F3 while the movie plays.

<img height="200" alt="image" src="https://github.com/user-attachments/assets/1524c516-9252-4f57-83fe-0a47fdc7ad11" />
<img height="200" alt="image" src="https://github.com/user-attachments/assets/7008db66-3297-4aae-8952-b1d56855df44" />

The prompts follow the pad: the pause screen reads **PRESS START TO
UNPAUSE**, and the title and scoreboard screens read **Press A Button**. That
last one is artwork rather than text, so `escrgame.bin` is rewritten too -
see [What gets written](#what-gets-written) and [TEXT.md](docs/TEXT.md).

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

Two keyboard players cannot share a key - if 2P wants one 1P already has,
rebind 1P first. If 1P is on a pad, 2P can take 1P's keys, since nothing is
using them. **Default** resets whichever side you are editing.

Applying the patch clears `v_on.ini`, because binds saved by the unpatched
game do not fit the new device list. See
[What gets written](#what-gets-written).

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
A cdemu device is read like a physical one.

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

Link mode is two-player versus over a network, but stock it never leaves the
LAN: the game finds opponents by broadcasting, and no router forwards a
broadcast. Hence the usual advice to run a VPN and pretend everyone is on one
LAN.

<img height="250" alt="image" src="https://github.com/user-attachments/assets/4a27320d-0f12-4161-8c83-19c8d6f0a119" />

**Internet play**, under ADD-ONS, replaces that layer with plain UDP. One
player hosts, the other types an address, and the match runs the same as it
always did.

```bash
python3 vo-patch.py --netplay path/to/game            # install
python3 vo-patch.py --netplay path/to/game --remove   # put the stock one back
```

The game's `dpctrl.dll` is kept as `dpctrl.dll.stock`, so Remove puts you
back on LAN-and-VPN play.

### Playing

- **Both players need this, and the same patches.** The two machines run
  one simulation in step with each other. If they disagree about the rules,
  they will disagree about the match.
- **The host forwards UDP 47624.** Whoever joins needs nothing.
- The host picks *Host a game* and reads out their address. The dialog
  shows the local one, and will ask the internet for the public one if you
  press the button. The other player picks *Join a game* and types it in;
  host names work as well as addresses.

The joining side keeps trying until you cancel, so there is no rush to
press things at the same moment. Once a match is running, a player who
quits or crashes is noticed within a few seconds.

## Resolution and windowing (cnc-ddraw)

The game asks for 640x480 exclusive fullscreen and leaves the rest to the
display, which on a modern panel usually means a stretched picture. The 4:3
framebuffer is baked into the rasteriser, so no byte edit fixes it - it needs
something between the game and the graphics driver.

<img height="360" alt="image" src="https://github.com/user-attachments/assets/28c65fbf-99ed-4b11-b2a4-01fde3bf3d16" />

[cnc-ddraw](https://github.com/FunkyFr3sh/cnc-ddraw) replaces the DirectDraw
the game renders through, adding windowed and borderless modes, correct aspect
ratio and upscaling. Every patch here works with it.

**Install** under ADD-ONS downloads the current release and unpacks it
beside `v_on.exe` - `ddraw.dll`, `ddraw.ini`, `cnc-ddraw config.exe` and the
shaders. Once it is there the same button reads **Remove**, which deletes
them again and keeps `ddraw.ini`. From a terminal:

```bash
python3 vo-patch.py --ddraw path/to/game
```

It comes from
[the releases page](https://github.com/FunkyFr3sh/cnc-ddraw/releases), so it
is always current. An existing `ddraw.ini` is kept, so re-running to update
leaves your settings alone. Close the game first: Windows will not replace a
DLL that is loaded.

**On Linux there is a second step.** Set `ddraw` to native in `winecfg` for
that prefix, or run `cnc-ddraw config.exe` once. Without it Wine keeps using
its own DirectDraw and nothing changes.

A fresh `ddraw.ini` is cnc-ddraw's own file with a few settings changed:
`fullscreen`, `windowed`, `maintas`, `noactivateapp`, `toggle_borderless`,
`devmode` and `game_handles_close` are all set to `true`, giving a borderless
window at 4:3 that does not trap the cursor. Everything else,
including the comments and the per-game sections, is left as it comes. Change
any of it with `cnc-ddraw config.exe`.

`game_handles_close` is the one that is not about the picture: without it,
closing the window loses your settings and your records for that session. If
you already have a `ddraw.ini`, add the line by hand:

```ini
[ddraw]
game_handles_close=true
```

The intro movie does not go through DirectDraw, so upscaling leaves it small
and in the corner. **Intro, loading and ending screens** under Extra fits it
to the window; without that, skip it with Space, Enter, Escape or pad A.

gamescope handles the scaling without a DLL, if you would rather:

```bash
gamescope -W 1920 -H 1080 -w 640 -h 480 -f -S integer -- %command%
```

`-S fit` fills more of the screen without whole-number scaling.

## Which build

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

If your file is neither of these, the patcher shows both checksums side by
side and says which one it got.

### What gets written

The original is copied to `v_on.exe.bak` before anything is written, and
nothing is written unless every selected patch applied and the backup was
made, so a failure leaves the game exactly as it was.

**XInput gamepad support** touches two more files. `v_on.ini` is moved to
`v_on.ini.bak` and the game writes a fresh one, because binds saved by the
unpatched game do not fit the new device list. `escrgame.bin` is rewritten
with the new title artwork, after a copy is kept as `escrgame.bin.bak`.

**Restore original** puts all three back, keeping whatever the patched game
wrote as `v_on.ini.patched`. Restore rather than copying a `.bak` over by
hand: `escrgame.bin` and `v_on.exe` have to match, and putting back only one
draws the title prompt as scrambled letters. If `escrgame.bin` is missing,
the wrong size, or already modified with no backup beside it, the patcher
stops before writing anything and says so.

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

Everything the patcher does is also available without a window:

```bash
python3 vo-patch.py --rip SOURCE DIR   # soundtrack, from a cue sheet or drive
python3 vo-patch.py --ddraw DIR        # fetch and install cnc-ddraw
python3 vo-patch.py --netplay DIR      # install the UDP netplay DLL
python3 vo-patch.py --selfcheck        # validate the patch tables
```

To build the Windows binary yourself, `pip install pyinstaller` and run
`pyinstaller vo-patch.spec`. It builds as `vo-patch-dev.exe`: releases take
their version from the git tag, and a source tree has no tag to take it from.

To change the machine code the patches install, see [asm/](asm/), which
`asm/build.py` builds into the hex strings in `vo-patch.py`. Never edit those
by hand.

The netplay DLL is built the same way from [net/](net/): edit `net/dpctrl.c`
and run `python3 net/build.py`, which compiles it with mingw and bakes it back
into `vo-patch.py`.

`python3 tools/check.py` runs every check in the project - give it your game
folder and it runs the ones that need one.
[docs/DEVELOPING.md](docs/DEVELOPING.md) covers the rest of the workflow.

---

Written with AI assistance. Every offset and byte sequence is checked against
the original executable before it is written, and the patcher refuses anything
that is not the unmodified disc file - but this is a hobby project poking at a
nearly 30-year-old binary so expect bugs.

## Credits and licence

Some of the byte edits come from the original VO_Patch 0.43 (2008) by
[UE2A-GEL](https://jaguarandi.xxxxxxxx.jp/). Rights to the game belong to
SEGA. `LICENSE` (MIT) covers the patcher, its tools and its documentation -
not the game, not the bytes quoted from it, and not the letterforms traced
from its artwork.
