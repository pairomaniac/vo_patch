# Virtual-On (PC, 1997) - v_on.exe patcher

Gets *Cyber Troopers Virtual-On* running properly on a modern system: fixes
the crashes, the frame rate and the keyboard, adds XInput gamepad support for
both players, reads the soundtrack from files instead of the disc, and puts
two-player versus on the internet - a code to share, no port forwarding.

In a nutshell - the patch makes the game <i>just work ™️</i>

<img height="700" alt="The patcher window" src="https://github.com/user-attachments/assets/63514261-53a8-43df-adda-6018bdbc5a26" />

<p align="center">
  <a href="#quick-start">Quick start</a> &nbsp;·&nbsp;
  <a href="#what-the-patches-do">Patches</a> &nbsp;·&nbsp;
  <a href="#internet-play">Internet play</a> &nbsp;·&nbsp;
  <a href="#gamepad">Gamepad</a> &nbsp;·&nbsp;
  <a href="#music">Music</a> &nbsp;·&nbsp;
  <a href="#resolution-and-windowing-cnc-ddraw">Resolution</a> &nbsp;·&nbsp;
  <a href="#which-build">Which build</a>
</p>

## Quick start

1. **Download** `vo-patch-*.exe` from the
   [latest release](https://github.com/pairomaniac/vo_patch/releases/latest).
   It is unsigned, so SmartScreen calls it an unknown publisher on the first
   run. On Linux, see [Running from source](#running-from-source).
2. **Run it and select your `v_on.exe`.** Only the unmodified disc file is
   accepted - if yours is refused, see [Which build](#which-build).
3. **Press Apply patches.** Everything starts ticked. Click the ⓘ beside a
   patch to read what it does, and untick anything you do not want.
4. **Want internet play, or a proper fullscreen picture?** Click the
   **ADD-ONS** header to open it - the section starts collapsed - and press
   **Install** on the row you want:
    - **Internet play** - two-player versus over the internet. Both players
      need it. See [Internet play](#internet-play).
    - **Resolution and windowing** - installs cnc-ddraw. See
      [Resolution and windowing](#resolution-and-windowing-cnc-ddraw).
5. **Play.**

Changed your mind? **Restore original** puts the game back, then apply again
with a different selection.

Add-ons are separate because they write files beside the game rather than
editing it, so Apply and Restore leave them alone. Select `v_on.exe` first
either way - that is how the patcher knows which folder to write to.

## What the patches do

**Essential** fixes what is broken on modern systems; **Extra** is down to
taste. Every patch's offsets and internals are in [NOTES.md](docs/NOTES.md).

### Essential

- **Skip processor check** - the game refuses to start on a modern CPU.
Removes the check, so `ProcessorCheck=Off` in `v_on.ini` is not needed.
- **Fix frame rate (60 FPS)** - three fixes for the same complaint:
    - raises the multimedia timer resolution. Without it the game runs at
      about 70% speed on Windows 2000 and later (not needed on Wine).
    - makes `Motion=` in `v_on.ini` stick. The game read it, then overwrote it.
    - relabels the *Motion Type* radios on F5, which only ever offered 1/3
      and 1/2 speed. They now read **30 FPS** and **60 FPS**.
- **Fix crash on round loss** - the game crashes when you lose as Temjin,
Viper II, Apharmd or Raiden.
- **Fix keyboard input after ALT+TAB** - alt-tabbing away, or opening an
F-key dialog, kills the keyboard for the rest of the session.

### Extra

- **XInput gamepad support** - a modern controller for both players: twelve
bindable actions, plus the arcade twin-stick scheme. See
[Gamepad](#gamepad).
- **No disc required** - removes the disc check and plays the soundtrack from
`music\trackNN.wav` beside the game. See [Music](#music).
- **Disable menu bar (Extras menu on F11)** - hides the menu bar and moves
the Debug options to a new F11 dialog: No shot, SE, CD, Kill 1P, Kill 2P,
Scorekeeping and Quit Game. **Credits** is new - it jumps to the credit roll
from any match, so you can see it without finishing the game. Motion has
moved to F5. With the gamepad patch in, F11 also sets each player's
[stick deadzone](#stick-deadzone). Every other menu was already on a key:

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
setting `v_on.ini` does not have, which on a first run is all of them: Sky
on, all three Texture boxes on, Field Graphic Rich, Screen Large.
- **Sound fixes** - the built-in delay before each sound effect is removed,
output goes from 22050 to 44100 Hz, and an enemy Fei-Yen gets back the
hypermode sound a bug left silent.
- **Intro, loading and ending screens** - four fixes to the screens either
side of the fighting:
    - the intro movie is fitted to the window instead of sitting in a corner
    - "Now Loading . . ." is hidden
    - the ending credits can be skipped - hold **A**, **Select** or Space for
      a second. Stock has no way past them
    - the initials screen after them takes those buttons too, and the weapon
      trigger

### About

**Show the version, and credit the patch in the ending roll** sits under
ABOUT rather than with the patches, and is on by default. It prints the
patcher's version in the bottom right of the title screen and adds two lines
under the title of the ending credits.

The credit lines rewrite `scrstfcg.bin` and `scrstfmp.bin`, backing both up;
**Restore original** puts them back. If either file is missing the whole box
is skipped, version included, and everything else still applies.

### Add-ons

Open the collapsed **ADD-ONS** header and press **Install** on a row. The
same button reads **Remove** once installed.

| Row | What it is |
| --- | --- |
| **Internet play** | two-player versus over the internet. Both players need it. See [Internet play](#internet-play) |
| **Resolution and windowing** | downloads and installs cnc-ddraw beside the game. See [Resolution and windowing](#resolution-and-windowing-cnc-ddraw) |

**CD MUSIC**, further down the window, rips the soundtrack for the **No disc
required** patch. See [Music](#music).

## Internet play

Link mode is two-player versus over a network, but stock it never leaves the
LAN: the game finds opponents by broadcasting, and no router forwards a
broadcast. Hence the usual advice to run a VPN and pretend everyone is on one
LAN.

**Internet play**, under ADD-ONS, replaces that layer with plain UDP. One
player hosts and gets a short code, the other types it in - no port
forwarding, no VPN. Direct IP is still there for LAN play.

<img height="360" alt="netplay dialog" src="https://github.com/user-attachments/assets/8b71e77f-42d9-40c0-bd1e-8222fd7c98de" />
<br /><br />

### Before you start

- **Both players install the add-on.** Select `v_on.exe`, open **ADD-ONS**
  and press **Install** on the *Internet play* row. Nothing downloads - the
  DLL is inside the patcher - and the row then reads **Remove**. If it says
  an older netplay DLL is installed, press **Install** to update it.

  Or from a terminal:

    ```bash
    python3 vo-patch.py --netplay path/to/game            # install
    python3 vo-patch.py --netplay path/to/game --remove   # put the stock one back
    ```

- **Both players need the same two gameplay patches**, Fix frame rate and
  Fix crash on round loss. Each machine runs its own copy of the game on
  both players' inputs, so those two have to agree. Nothing else does -
  sound, video and controls are each machine's own business. A mismatch is
  refused with a note saying so, so there is nothing to check by hand.
- The stock `dpctrl.dll` is kept as `dpctrl.dll.stock`, so **Remove** puts
  you back on LAN-and-VPN play.

### Playing with a code

This is the default, and nobody forwards anything.

1. **Host:** leave the connection on **Matchcode**, pick a **Region** -
   Europe or America, whichever is nearer the host - then choose **Host a
   game** and press **OK**.
2. The dialog shows a code like `EU-ABCDE`, with a **Copy** button. Send it
   to the other player.
3. **Guest:** choose **Join a game**, type or paste the code in, and press
   **OK**.

The `EU` or `US` in front is the server the code lives on, so the code is
all the guest needs; hyphens, spaces and case do not matter. The two
machines talk directly where the routers allow it and through the server
where they do not, so it works from anywhere with UDP.

The guest keeps trying until you cancel, so there is no rush to press things
at the same moment. Once a match is running, a player who quits or crashes is
noticed within a few seconds.

**Custom** points both players at a server that is not one of ours. Both
enter the same address, and codes from it read `CUST-ABCDE`.

### Playing by IP

For a LAN, or when you would rather not depend on the server. The host
forwards **UDP 47624**, picks *Host a game* and reads out their address - the
dialog shows the local one and looks up the public one on request. The guest
types it in and needs nothing forwarded.

### If a match freezes or plays badly

Force the relay: the match goes through the server instead of connecting
directly, which is often steadier over a long distance.

1. Open `vo-net.ini` beside `v_on.exe` - create it if it is not there.
2. Add `relay=1` under `[net]`, using the heading already in the file if
   there is one:

    ```ini
    [net]
    relay=1
    ```

3. Connect as usual.

One side setting it is enough. Take the line out again for a nearby
opponent - direct is faster when it works. Matchcode games only.

### If it will not connect at all

Create an empty file called `vo-net.log` beside `v_on.exe` and try again. The
DLL writes what it did into it, and that file is what to send with a bug
report.

## Gamepad

**XInput gamepad support** rebuilds the F7 device list. The legacy joystick
profiles are hidden and four remain, for both players - pad 1 drives 1P, pad
2 drives 2P.

<img width="350" alt="F7 device list with the profiles" src="https://github.com/user-attachments/assets/457643b7-f42b-49b2-993f-50b56733c59d" />
&nbsp;
<img width="350" alt="F7 bind page for Gamepad (XInput)" src="https://github.com/user-attachments/assets/9fac5899-0465-4fbe-990f-65f978352632" />
<br /><br />

| Profile | What it is |
| --- | --- |
| **Gamepad (XInput)** | twelve named actions, bound from the F7 screen |
| **Twin-stick (XInput)** | the arcade levers, nothing to bind |
| **Keyboard (Simple)** | every action on a bindable key |
| **Keyboard (Real)** | the game's own two-lever keyboard scheme, bindable |

Four buttons work on every profile, Start on the pause screen included:

| Button | Does |
| --- | --- |
| **A** | Accept - confirms menus, and skips the win and lose screens |
| **Select** | Camera, and skips those screens too |
| **Start** | Pause |
| **D-pad** | Moves, so it also drives menus |

The D-pad is not bindable: it is wired to the same four directions as the
movement binds, which is what the menus read.

**A** also skips the intro movie, the same as Space. **Start** does not - the
game ignores F3 while the movie plays.

<img height="220" alt="Pause screen prompt" src="https://github.com/user-attachments/assets/6f6443ba-1ea2-45f3-a012-88df008d7e39" />
&nbsp;
<img height="220" alt="Title screen prompt" src="https://github.com/user-attachments/assets/dd50a0dd-a98e-4054-8ec1-96d6160b4f07" />
<br /><br />

The prompts follow the pad: the pause screen reads **PRESS START TO
UNPAUSE**, and the title and scoreboard screens read **Press A Button**. That
last one is artwork rather than text, so `escrgame.bin` is rewritten too -
see [TEXT.md](docs/TEXT.md). Applying the patch also moves `v_on.ini` aside,
because binds saved by the unpatched game do not fit the new device list. See
[What gets written](#what-gets-written).

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

### Keyboard (Simple)

The game's original all-keys profile. It shares the bind page with the
gamepad, but each sees only its own inputs - the pad's sixteen on one page,
the keyboard's letters, digits and named keys on the other - and each keeps
its own saved set in `v_on.ini`, so switching between them costs nothing.

### Keyboard (Real)

Keeps its own bind page. Two keyboard players cannot share a key: if 2P wants
one 1P already has, rebind 1P first. If 1P is on a pad, 2P can take 1P's
keys, since nothing is using them. **Default** resets whichever side you are
editing.

### Stick deadzone

<img height="270" alt="F11 Extras dialog" src="https://github.com/user-attachments/assets/a2482765-37bc-46d3-8763-4923c8e5449b" />
<br /><br />

How far a stick has to move before it counts, 40% out of the box. Set it per
player in the *Stick Deadzone % [ XInput ]* box of the F11 Extras dialog
(with **Disable menu bar** installed); that box's **Defaults** button puts
both back to 40.

Closing the dialog saves each to its own `v_on.ini` line, editable by hand:

```ini
1P Deadzone=25
2P Deadzone=40
```

Two digits, `05` to `95` - lower is more sensitive, higher rides out a worn
stick's drift.

## Music

The BGM is Redbook CD audio, so unpatched it needs a disc or a virtual drive
with the audio tracks - a data-only ISO plays nothing. **No disc required**
reads it from WAV files beside the game. No drive, no extra DLL.

<img height="360" alt="CD MUSIC section" src="https://github.com/user-attachments/assets/69d7501f-b16c-4394-a9ef-a5120c224775" />

### Ripping the tracks

Use the **CD MUSIC** section of the patcher. Pick `v_on.exe` first so it
knows where the files go, put a cue sheet or a drive in **Source**, then
press **Rip tracks**. Closing the window mid-rip cancels it and discards the
part-written track.

<img height="300" alt="Ripping in progress" src="https://github.com/user-attachments/assets/363be68c-fcab-4ddd-af3f-09f125d9911f" />

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
`mciwave` - no `dosdevices` entry, raw device link or cdemu instance.

## Resolution and windowing (cnc-ddraw)

The game asks for 640x480 exclusive fullscreen and leaves the rest to the
display, which on a modern panel usually means a stretched picture. The 4:3
framebuffer is baked into the rasteriser, so no byte edit fixes it - it takes
something between the game and the graphics driver.

<img height="360" alt="cnc-ddraw row under ADD-ONS" src="https://github.com/user-attachments/assets/ee0e5c12-2db3-4a85-bc23-8ba4d859c6ce" />
<br /><br />

[cnc-ddraw](https://github.com/FunkyFr3sh/cnc-ddraw) replaces the DirectDraw
the game renders through, adding windowed and borderless modes, correct aspect
ratio and upscaling. Every patch here works with it.

**Install** under ADD-ONS downloads the current release and unpacks it beside
`v_on.exe` - `ddraw.dll`, `ddraw.ini`, `cnc-ddraw config.exe` and the
shaders. The same button then reads **Remove**, which deletes them again and
keeps `ddraw.ini`. From a terminal:

```bash
python3 vo-patch.py --ddraw path/to/game
```

It comes straight from
[the releases page](https://github.com/FunkyFr3sh/cnc-ddraw/releases), so it
is always current, and an existing `ddraw.ini` is kept - re-running to update
leaves your settings alone. Close the game first: Windows will not replace a
DLL that is loaded.

**On Linux there is a second step.** Set `ddraw` to native in `winecfg` for
that prefix, or run `cnc-ddraw config.exe` once. Without it Wine keeps using
its own DirectDraw and nothing changes.

### The settings it ships with

A fresh `ddraw.ini` is cnc-ddraw's own file with a few settings changed:
`fullscreen`, `windowed`, `maintas`, `noactivateapp`, `toggle_borderless`,
`devmode` and `game_handles_close` are all `true`, giving a borderless window
at 4:3 that does not trap the cursor. Everything else, comments and per-game
sections included, is left as it comes. Change any of it with `cnc-ddraw
config.exe`.

`game_handles_close` is the one that is not about the picture: without it,
closing the window loses your settings and records for that session. If you
already have a `ddraw.ini`, add the line by hand:

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
fixed file offset belonging to that build alone; on another build it would
write into unrelated code.

<kbd><img height="150" alt="Rejected executable, side by side checksums" src="https://github.com/user-attachments/assets/00a718b1-1193-45ea-a152-8f62ecaf304a" /></kbd>
&nbsp;
<kbd><img height="150" alt="Accepted executable" src="https://github.com/user-attachments/assets/97b416fd-b2fe-41e5-957a-50a574bffff6" /></kbd>

| Build | Size | MD5 | |
| --- | --- | --- | --- |
| Retail disc | 6,650,880 | `a464b0ff32d5bab499f265e45658504e` | supported |
| USA OEM | 6,649,344 | `4c70f780a7f0d98d74be62304fb99021` | not supported |

If your file is neither of these, the patcher shows both checksums side by
side and says which one it got.

### What gets written

The original is copied to `v_on.exe.bak` first, and nothing is written
unless every selected patch applied and the backup was made, so a failure
leaves the game exactly as it was.

**XInput gamepad support** touches two more files. `v_on.ini` is moved to
`v_on.ini.bak` and the game writes a fresh one, because binds saved by the
unpatched game do not fit the new device list. `escrgame.bin` is rewritten
with the new title artwork, after a copy is kept as `escrgame.bin.bak`.

**Restore original** puts all three back, keeping whatever the patched game
wrote as `v_on.ini.patched`. Use it rather than copying a `.bak` over by
hand: `escrgame.bin` and `v_on.exe` have to match, and putting back only one
draws the title prompt as scrambled letters. If `escrgame.bin` is missing,
the wrong size, or already modified with no backup beside it, the patcher
stops before writing anything.

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
`pyinstaller vo-patch.spec`. It builds as `vo-patch-dev.exe` - releases take
their version from the git tag, and a source tree has none.

To change the machine code the patches install, see [asm/](asm/); `asm/build.py`
builds it into the hex strings in `vo-patch.py`. Never edit those by hand.

The netplay DLL is built the same way from [net/](net/): edit `net/dpctrl.c`
and run `python3 net/build.py`, which compiles it with mingw and bakes it back
into `vo-patch.py`. [net/README.md](net/README.md) covers the protocol and
the matchcode server.

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
