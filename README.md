# vo_patch

Gets *Cyber Troopers Virtual-On* (PC, 1997) running properly on a modern
system. It installs the game straight from a disc image, fixes the crashes,
the frame rate and the keyboard, renders the game at 1920x1080, adds XInput
gamepad support for both players, plays the soundtrack from files instead of
the disc, and puts two-player versus on the internet with a code to share -
no port forwarding.

<img height="220" alt="Widescreen match" src="https://github.com/user-attachments/assets/464143ee-a63b-4004-83f5-16cf28c146dd" />
&nbsp;
<img height="220" alt="Widescreen title screen" src="https://github.com/user-attachments/assets/eaa35047-e49a-4ca9-8ff1-9cb7e1d8a07f" />
<img height="448" alt="Widescreen ending cutscene: Temjin over the Earth and the Moon" src="https://github.com/user-attachments/assets/3f1e19b6-406d-4a80-8a0c-e2fd9f0ffdea" />
<br />
...in a nutshell - the patch makes the game <i>just work ™️</i>
<br /><br />
<img height="700" alt="The patcher window" src="https://github.com/user-attachments/assets/a3344ff2-fa48-4961-b983-7019ab8dffa8" />

<h4 align="center">
  <a href="#quick-start">Quick start</a> &nbsp;·&nbsp;
  <a href="#virus-warnings">Virus warnings</a> &nbsp;·&nbsp;
  <a href="#what-the-patches-do">Patches</a> &nbsp;·&nbsp;
  <a href="#native-widescreen">Widescreen</a> &nbsp;·&nbsp;
  <a href="#gamepad">Gamepad</a>
  <br /><br />
  <a href="#internet-play">Internet play</a> &nbsp;·&nbsp;
  <a href="#music">Music</a> &nbsp;·&nbsp;
  <a href="#windowing-and-scaling-cnc-ddraw">Windowing</a> &nbsp;·&nbsp;
  <a href="#builds">Builds</a>
</h4>

## Quick start

**Download** `vo_patch-*-win.zip` from the
[latest release](https://github.com/pairomaniac/vo_patch/releases/latest),
unzip it anywhere and run `vo_patch-*.exe`; the `_internal` folder beside it
has to stay. It is unsigned, so SmartScreen calls it an unknown publisher the
first time you run it. If a virus scanner objects, see
[Virus warnings](#virus-warnings). On Linux, see
[Running from source](#running-from-source).

The window is split into numbered sections. Work through them in order:

1. **INSTALL** - put your `.cue` sheet in **Source**, choose a folder in
   **Install to**, and press **Install game**. Then press **Rip soundtrack**,
   unless you plan to keep a disc in the drive.
   Already have the game installed? Skip this and start at 2 - the tracks
   are ripped beside whichever `v_on.exe` you pick there.
   See [Installing from a disc image](#installing-from-a-disc-image) and
   [Music](#music).
2. **GAME FILE** - installing fills this in for you. Otherwise browse to
   your `v_on.exe`. Only an unmodified disc copy is accepted; if yours is
   refused, see [Builds](#builds).
3. **ESSENTIAL PATCHES** - always applied, no tick boxes.
   See [What the patches do](#what-the-patches-do).
4. **EXTRA PATCHES** - all ticked to start with, and yours to change.
   Click the ⓘ beside a patch to read what it does. Then press
   **Apply patches**.
5. **ADD-ONS** - starts collapsed. Press **Install** on the rows you want:
    - **Internet play** - two-player versus over the internet. Both players
      need it. See [Internet play](#internet-play).
    - **Windowing and scaling** - installs cnc-ddraw for windowed and
      borderless play. See
      [Windowing and scaling](#windowing-and-scaling-cnc-ddraw).

Then play. **Restore original** puts the game back if you change your mind.
Add-ons put files beside the game rather than editing it, so Apply and
Restore leave them alone.

## Virus warnings

Defender and other scanners sometimes flag the download. It is a false
positive: an unsigned program that edits another program is the sort of
thing they warn about. To allow it in Defender: Windows Security → Virus &
threat protection → Protection history → the entry for the file → Allow,
then run it again.

If you would rather not run it, `vo_patch.py` does everything the download
does - see [Running from source](#running-from-source). Each release is
built on GitHub from this repository, and the build log lists the file's
checksum if you want to check that yours matches.

## Installing from a disc image

The patcher reads the image itself, so there is nothing to mount and no
virtual drive to set up.

<img height="280" alt="INSTALL section" src="https://github.com/user-attachments/assets/d03430cf-4ef4-4ff8-bf8d-e608d5049be5" />
<br /><br />

Put the **`.cue`** sheet in **Source** - the small file beside the `.bin`
files, not the `.bin` itself. Choose a folder in **Install to** and press
**Install game**. The game is about 95 MB.

The **Manual** box picks which `readme.txt`, `von.hlp` and `von.cnt` are
copied. It does not change the language of the game: every pressing carries
one `v_on.exe`, whichever manuals it ships with. `von.hlp` is 1997 WinHelp,
which Windows has not been able to open since Windows 10; the readme is
plain text.

Or from a terminal:

```bash
python3 vo_patch.py --install VIRTUAL-ON.cue ~/games/VIRTUAL-ON
python3 vo_patch.py --install VIRTUAL-ON.cue ~/games/VIRTUAL-ON --language GERMAN
```

### If you have the disc, not an image

The patcher needs a `bin`/`cue` pair. It does not read a drive directly, and
a plain ISO will not do because it drops the audio tracks. Image the disc
once:

- **Windows** - [ImgBurn](https://www.imgburn.com) in *Read* mode, with the
  output set to **BIN/CUE** rather than ISO.
- **Linux** - `cdrdao`, then its own `toc2cue`:

  ```bash
  cdrdao read-cd --driver generic-mmc-raw --datafile VIRTUAL-ON.bin \
      VIRTUAL-ON.toc /dev/sr0
  toc2cue VIRTUAL-ON.toc VIRTUAL-ON.cue
  ```

Then put the `.cue` in **Source**. On Linux a drive can also go straight in
**Source** for the soundtrack rip, though not for the install - see
[Music](#music). On Windows the image is the only way in.

### What it copies

The game directory and the chosen manual. Nothing else: `directx\` is a
1997 redistributable and the rest is the installer's own furniture.

No `v_on.ini` is written. The game makes its own on first run, and with
**Better defaults** applied it is a good one. The disc's `v_on_a.ini` and
`v_on_b.ini` are copied as they are.

For how the copy rules are read off the disc, see
[docs/NOTES.md](docs/NOTES.md#installing-from-a-disc-image).

## What the patches do

<img height="160" alt="Patched game" src="https://github.com/user-attachments/assets/15fdc7a1-c52e-4565-8977-6ac024229f4f" />
<br /><br />

**Essential** fixes what is broken on modern systems and is always applied.
Without it the game does not start, crashes when you lose a round, runs at a
third of the frame rate, or loses the keyboard after ALT+TAB.

**Extra** is down to taste. Every patch starts ticked; untick what you do
not want.

The offsets and internals of every patch are in [NOTES.md](docs/NOTES.md).

### Essential

- **Skip processor check** - the game refuses to start on a modern CPU.
  Removes the check, so `ProcessorCheck=Off` in `v_on.ini` is not needed.
- **Fix frame rate (60 FPS)** - three fixes for the same complaint:
    - raises the multimedia timer resolution. Without it the game runs at
      about 70% speed on Windows 2000 and later (not needed on Wine).
    - makes `Motion=` in `v_on.ini` stick. The game read it, then overwrote
      it.
    - relabels the *Motion Type* radios on F5, which only ever offered 1/3
      and 1/2 speed. They now read **30 FPS** and **60 FPS**.
- **Fix crash on round loss** - the game crashes when you lose as Temjin,
  Viper II, Apharmd or Raiden.
- **Fix the lock-on line** - the line from the enemy to the distance
  readout flashed across the screen as a grey band whenever the enemy was
  far off to one side. It is drawn right now.
- **Fix keyboard input after ALT+TAB** - alt-tabbing away, or opening an
  F-key dialog, kills the keyboard for the rest of the session. The fix
  reads the keyboard in the background, so keys pressed in another window
  still reach the game while it is running.
- **Fix crash on ALT+TAB** - switching away during the intro movie crashes
  the game on the way back. The game took the interruption for the movie
  ending and never rebuilt the screen it had handed to the movie player. It
  is rebuilt now, and the game waits until that has worked before carrying
  on.

### Extra

- **Native widescreen** - the game renders at 1920x1080 instead of 640x480,
  with menus and text redrawn to match and more of the arena at the sides.
  See [Native widescreen](#native-widescreen).
- **XInput gamepad support** - a modern controller for both players: twelve
  bindable actions, plus the arcade twin-stick scheme. See
  [Gamepad](#gamepad).
- **No disc required** - removes the disc check and plays the soundtrack from
  `music\trackNN.wav` beside the game. See [Music](#music).
- **Disable menu bar (Extras menu on F11)** - hides the menu bar and moves
  the Debug options to a new F11 dialog: No shot, SE, CD, Kill 1P, Kill 2P,
  Scorekeeping and Quit Game. **Credits** is new - it jumps to the credit
  roll from any match, so you can see it without finishing the game. Motion
  has moved to F5. With the gamepad patch on, F11 also sets each player's
  [stick deadzone](#stick-deadzone). Like the other F keys, it does nothing
  during an internet match. Every other menu was already on a key:

    | Key | Opens |
    | --- | --- |
    | **F1** | Help |
    | **F3** | Pause |
    | **F4** | High / low resolution (1080p / 720p with Native widescreen on) |
    | **F5** | Graphic Settings |
    | **F6** | Mode Settings |
    | **F7** | Device Settings |
    | **F8** | Sound Test |
    | **F11** | Extras, the new dialog |

- **Better defaults with no v_on.ini** - what the game falls back on for any
  setting `v_on.ini` does not have, which on a first run is all of them: Sky
  on, all three Texture boxes on, Field Graphic Rich, Screen Large.
- **Sound fixes** - removes the built-in delay before each sound effect,
  raises the output from 22050 to 44100 Hz, and gives an enemy Fei-Yen back
  the hypermode sound a bug left silent.
- **Intro, loading and ending screens** - four fixes to the screens either
  side of the fighting:
    - the intro movie is fitted to the window instead of sitting in a
      corner, and the black bars inside its frames are cropped off, so on a
      widescreen display it fills the height
    - "Now Loading . . ." is hidden
    - the ending credits can be skipped - hold **A**, **Select** or Space
      for a second. Stock has no way past them
    - the initials screen after them takes those buttons too, and either
      weapon trigger. A and Select are 1P's, so 2P skips with **RT**

### About

**Version and credit in the game** sits under ABOUT rather than with the
patches, and is on by default. It prints the patcher's version in the bottom
right of the title screen and adds two lines under the title of the ending
credits.

The credit lines rewrite `scrstfcg.bin` and `scrstfmp.bin`, backing both up;
**Restore original** puts them back. If either file is missing the whole box
is skipped, version included, and everything else still applies.

### Add-ons

Open the collapsed **ADD-ONS** header and press **Install** on a row. The
same button reads **Remove** once installed.

<img height="360" alt="ADD-ONS section" src="https://github.com/user-attachments/assets/314b5279-9e61-4899-8cef-d5f40e0a65d7" />
<br /><br />

| Row | What it is |
| --- | --- |
| **Internet play** | two-player versus over the internet. Both players need it. See [Internet play](#internet-play) |
| **Windowing and scaling** | downloads and installs cnc-ddraw beside the game. See [Windowing and scaling](#windowing-and-scaling-cnc-ddraw) |

The soundtrack rip lives in **INSTALL** at the top of the window, beside the
game copy. See [Music](#music).

## Native widescreen

The game draws everything at 640x480 and assumes that size everywhere, so
scaling the picture up only makes it bigger. This patch makes the game
render at 1920x1080 itself. The 3D view, the menus, the HUD and the text are
all drawn at that size, and the wider view shows more of the arena at the
sides rather than stretching the middle.

<img height="220" alt="Widescreen match, 1P" src="https://github.com/user-attachments/assets/b4026e8a-39d6-4fe2-9f6f-275e5c2c4545" />
&nbsp;
<img height="220" alt="Widescreen machine select" src="https://github.com/user-attachments/assets/2118e335-366e-4f2a-b332-f9fca25ce4ac" />
<br />
<img height="220" alt="Widescreen split screen" src="https://github.com/user-attachments/assets/4fa56492-db68-4a9b-92b7-a2724adf0bef" />
&nbsp;
<img height="220" alt="Widescreen menu" src="https://github.com/user-attachments/assets/dc17fcbd-5898-4ca5-907e-865024d2d509" />
<br />
<img height="220" alt="Widescreen NEXT ENEMY screen" src="https://github.com/user-attachments/assets/f5ccbf1a-fca8-47c0-9c43-cd41c13f6f6c" />
&nbsp;
<img height="220" alt="Widescreen two-player machine select" src="https://github.com/user-attachments/assets/690d6e7c-e480-4144-bc3d-f4a8be750c62" />
<br /><br />

It is **on by default**, like the other Extra patches. Untick it for the
original 640x480.

### What changes

- **Resolution** - **F4** switches between 1920x1080 and 1280x720, in place
  of the stock 640x480 / 320x240. The **Screen** row on F5 offers the same
  choice as **1080p** / **720p**; the stock *Normal* option, a small window
  centred in the picture, is gone. The choice is saved to `v_on.ini`, so the
  game starts at the size it was left in. The menu entry that picked 320x240
  outright is turned off.
- **HUD** - in a match the timer stays at the top left, as close to the
  edge as at 4:3, instead of drifting towards the middle of the wider
  picture, and the health bars with their labels are centred. The machine
  select and waiting screens of a two-player game, whose photo backdrops
  sat in a black frame, fill the screen.
- **Split screen** - the **Screen Split** row on F5 offers **Ver** (side by
  side) and **Hor** (top and bottom). The third stock option, which
  duplicated the first, is gone. In side-by-side mode the timer and health
  bars sit at the top of each half, with the rest of the HUD centred below;
  top and bottom mode draws the HUD at the same size.
  The machine select, which used to show the same grid in both halves, is
  drawn once at full size.
- **Text** - the pause, loading and credits text scales with the picture.
  The ending credits lose their black bands and roll across the whole
  screen, with the scenery clean behind them.

### Works with everything else

It works with every other patch and with cnc-ddraw, which is what gives you
windowed or borderless play at that size - see
[Windowing and scaling](#windowing-and-scaling-cnc-ddraw). The game itself
still asks the display for exclusive fullscreen at 1920x1080, as it always
asked for 640x480, and cnc-ddraw fits that to your monitor without
stretching.

It applies to all three [builds](#builds). On the USA OEM and Japanese
builds it is newer and has had less play; if something looks wrong there,
say which build in the report - the window names it.

## Gamepad

**XInput gamepad support** rebuilds the F7 device list. The legacy joystick
profiles are hidden and four remain, available to both players.

Players on a pad profile take the connected pads in order, 1P first. With
two pads, the first drives 1P and the second 2P. With one pad and 1P on the
keyboard, that pad drives 2P.

<img width="350" alt="F7 device list with the four profiles" src="https://github.com/user-attachments/assets/457643b7-f42b-49b2-993f-50b56733c59d" />
&nbsp;
<img width="350" alt="F7 bind page for Gamepad (XInput)" src="https://github.com/user-attachments/assets/9fac5899-0465-4fbe-990f-65f978352632" />
<br /><br />

| Profile | What it is |
| --- | --- |
| **Gamepad (XInput)** | twelve named actions, bound from the F7 screen |
| **Twin-stick (XInput)** | the arcade levers, nothing to bind |
| **Keyboard (Simple)** | every action on a bindable key |
| **Keyboard (Real)** | the game's own two-lever keyboard scheme, bindable |

### Buttons that work everywhere

Four buttons do the same thing on every profile, including Start on the
pause screen:

| Button | Does |
| --- | --- |
| **A** | Accept - confirms menus, and skips the win and lose screens |
| **Select** | Camera, and skips those screens too |
| **Start** | Pause |
| **D-pad** | Moves, so it also drives the menus |

The D-pad is not bindable: it is wired to the same four directions as the
movement binds, which is what the menus read.

**A** also skips the intro movie, the same as Space. **Start** does not -
the game ignores F3 while the movie plays.

<img height="220" alt="Pause screen prompt" src="https://github.com/user-attachments/assets/6f6443ba-1ea2-45f3-a012-88df008d7e39" />
&nbsp;
<img height="220" alt="Title screen prompt" src="https://github.com/user-attachments/assets/dd50a0dd-a98e-4054-8ec1-96d6160b4f07" />
<br /><br />

The on-screen prompts follow the pad: the pause screen reads **PRESS START
TO UNPAUSE**, and the title and scoreboard screens read **Press A Button**.
That last one is artwork rather than text, so the title artwork is rewritten
too - see [TEXT.md](docs/TEXT.md).

Applying the patch also moves `v_on.ini` aside, because binds saved by the
unpatched game do not fit the new device list. See
[What gets written](#what-gets-written).

### Gamepad (XInput)

Sticks, triggers, bumpers and face buttons are all in the bind list, and the
sticks are read as eight directions.

Defaults for both players: left stick moves, right stick turns, LT and RB
fire left and right, RT fires both, LB dashes, A jumps, X guards.
**Default** on the F7 page puts them back.

**Soft reset:** hold **LB + RB + LT + RT + Start** together and the game
returns to the title screen from wherever it is, on either player's pad.
Like the F keys, it does nothing during an internet match.

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
the keyboard's letters, digits and named keys on the other. Each keeps its
own saved set in `v_on.ini`, so switching between them costs nothing.

### Keyboard (Real)

Keeps its own bind page. Two keyboard players cannot share a key: if 2P
wants one 1P already has, rebind 1P first. If 1P is on a pad, 2P can take
1P's keys, since nothing is using them. **Default** resets whichever side
you are editing.

### Stick deadzone

<img height="270" alt="F11 Extras dialog" src="https://github.com/user-attachments/assets/a2482765-37bc-46d3-8763-4923c8e5449b" />
<br /><br />

How far a stick has to move before it counts. It is 40% out of the box, set
per player in the *Stick Deadzone % [ XInput ]* box of the F11 Extras dialog
(with **Disable menu bar** on). That box's **Defaults** button puts both
back to 40.

Closing the dialog saves each to its own `v_on.ini` line, which you can
also edit by hand:

```ini
1P Deadzone=25
2P Deadzone=40
```

Two digits, `05` to `95`. Lower is more sensitive; higher rides out a worn
stick's drift.

## Internet play

Link mode is two-player versus over a network, but stock it never leaves the
LAN: the game finds opponents by broadcasting, and no router forwards a
broadcast. Hence the usual advice to run a VPN and pretend everyone is on
one LAN.

**Internet play**, under ADD-ONS, replaces that layer with plain UDP. One
player hosts and gets a short code, the other types it in. No port
forwarding, no VPN. Direct IP is still there for LAN play.

<img height="360" alt="Internet play dialog" src="https://github.com/user-attachments/assets/aab7d268-e9f5-47bb-810c-83b183f253e5" />
<br /><br />

### Before you start

- **Both players install the add-on.** Select `v_on.exe`, open **ADD-ONS**
  and press **Install** on the *Internet play* row. Nothing downloads - the
  DLL is inside the patcher - and the row then reads **Remove**. If it says
  an older netplay DLL is installed, press **Install** to update it.

  Or from a terminal:

    ```bash
    python3 vo_patch.py --netplay path/to/game            # install
    python3 vo_patch.py --netplay path/to/game --remove   # put the stock one back
    ```

- **Both players need Fix frame rate and Fix crash on round loss.** Each
  machine runs its own copy of the game on both players' inputs, so those
  two have to agree. Both are Essential, so a game patched with this
  release always has them; a copy from an older patcher may not. A mismatch
  is refused with a note saying so, so there is nothing to check by hand.
  Nothing else matters - sound, video and controls are each machine's own
  business.
- The stock `dpctrl.dll` is kept as `dpctrl.dll.stock`, so **Remove** puts
  you back on LAN-and-VPN play.

### Playing with a code

This is the default, and nobody forwards anything.

1. **Host:** leave the connection on **Matchcode**, pick the **Region**
   nearest to you - Europe, America or Asia; the line under the buttons
   says where each server is - then choose **Host a game** and press
   **OK**.
2. The dialog shows a code like `EU-ABCDE`, with a **Copy** button. Send it
   to the other player.
3. **Guest:** choose **Join a game**, type or paste the code in, and press
   **OK**.

The `EU`, `US` or `JP` in front says which server the code lives on, so the
code is all the guest needs. Hyphens, spaces and case do not matter. The two
machines talk directly where the routers allow it and through the server
where they do not, so it works from anywhere with UDP.

| Region | Code | Server | Where |
| --- | --- | --- | --- |
| **Europe** | `EU-` | `segaonline.net` | Helsinki |
| **America** | `US-` | `us.segaonline.net` | New York |
| **Asia** | `JP-` | `jp.segaonline.net` | Tokyo |

All three listen on UDP 47625.

The guest keeps trying until you cancel, so there is no rush to press things
at the same moment. Once a match is running, a player who quits or crashes
is noticed within a few seconds.

**Custom** points both players at a server that is not one of ours. Both
enter the same address, and codes from it read `XX-ABCDE`.

### Playing by IP

For a LAN, or when you would rather not depend on the server. The host
forwards **UDP 47624**, picks *Host a game* and reads out their address -
the dialog shows the local one and looks up the public one on request. The
guest types it in and needs nothing forwarded.

### If a match freezes or plays badly

Force the relay: the match then goes through the server instead of
connecting directly, which is often steadier over a long distance.

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

Create an empty file called `vo-net.log` beside `v_on.exe` and try again.
The DLL writes what it did into that file, and it is what to send with a
bug report.

## Music

The BGM is Redbook CD audio, so unpatched the game needs a disc or a virtual
drive with the audio tracks - a data-only ISO plays nothing. **No disc
required** reads the music from WAV files beside the game instead. No drive,
no extra DLL.

### Ripping the tracks

Use the same **Source** box as the install - one cue sheet holds the game
and the soundtrack both. Press **Rip soundtrack**; the note under the
buttons names the folder they go to. Closing the window mid-rip discards the
part-written track.

On Linux a device node works in **Source** too, a cdemu one like a physical
drive. Windows drives are not read at all, in the window or from a terminal;
image the disc first, as in
[If you have the disc, not an image](#if-you-have-the-disc-not-an-image).

Or from a terminal:

```bash
python3 vo_patch.py --rip VIRTUAL-ON.cue /path/to/VIRTUAL-ON
python3 vo_patch.py --rip /dev/sr0       /path/to/VIRTUAL-ON   # Linux
python3 vo_patch.py --rip                # list drives (Linux)
```

The directory is the one holding `v_on.exe`; `music\` is created inside it.
About 320 MB: 26 tracks, roughly 30 minutes, uncompressed.

```
VIRTUAL-ON\
    v_on.exe
    music\
        track02.wav ... track27.wav
```

Track 1 is the data track and has no file. The numbering must match the
disc, because the game asks for tracks by number.

### At runtime

The tracks are read by the **No disc required** patch, so with it unticked
the folder is ignored however full it is. With the patch on and `music\`
missing or empty, the game reads the drive as before; with tracks there,
they are used, disc or no disc. Under Wine they play through `mciwave` - no
`dosdevices` entry, raw device link or cdemu instance needed.

## Windowing and scaling (cnc-ddraw)

The game asks for exclusive fullscreen at its render size - 640x480 stock,
1920x1080 with **Native widescreen** on - and leaves the rest to the display.
On a modern panel that can mean a stretched picture or no windowed mode.
That part is between the game and the graphics driver, and it is what
cnc-ddraw is for.

<img height="360" alt="cnc-ddraw row under ADD-ONS" src="https://github.com/user-attachments/assets/ee0e5c12-2db3-4a85-bc23-8ba4d859c6ce" />
<br /><br />

[cnc-ddraw](https://github.com/FunkyFr3sh/cnc-ddraw) replaces the DirectDraw
the game renders through, adding windowed and borderless modes, correct
aspect ratio and upscaling. Every patch here works with it.

### Installing

**Install** under ADD-ONS downloads the current release and unpacks it
beside `v_on.exe` - `ddraw.dll`, `ddraw.ini`, `cnc-ddraw config.exe` and
the shaders. The same button then reads **Remove**, which deletes them again
but keeps `ddraw.ini`. From a terminal:

```bash
python3 vo_patch.py --ddraw path/to/game
```

It comes straight from
[the releases page](https://github.com/FunkyFr3sh/cnc-ddraw/releases), so it
is always current. An existing `ddraw.ini` is kept, so re-running to update
leaves your settings alone. Close the game first: Windows will not replace a
DLL that is loaded.

**On Linux there is a second step.** Set `ddraw` to native in `winecfg` for
that prefix, or run `cnc-ddraw config.exe` once. Without it Wine keeps using
its own DirectDraw and nothing changes.

### The settings it ships with

A fresh `ddraw.ini` is cnc-ddraw's own file with a few settings changed:
`fullscreen`, `windowed`, `maintas`, `noactivateapp`, `toggle_borderless`,
`devmode` and `game_handles_close` are all `true`. That gives a borderless
window at the right aspect ratio that does not trap the cursor. Everything
else, comments and per-game sections included, is left as it comes. Change
any of it with `cnc-ddraw config.exe`.

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

### gamescope

gamescope handles the scaling without a DLL, if you would rather:

```bash
gamescope -W 1920 -H 1080 -w 640 -h 480 -f -S integer -- %command%
```

`-S fit` fills more of the screen without whole-number scaling.

## Builds

Four builds of the game exist. Three of them patch:

| Build | Size | MD5 | Patcher |
| --- | --- | --- | --- |
| English retail | 6,650,880 | `a464b0ff32d5bab499f265e45658504e` | patches |
| USA OEM | 6,649,344 | `4c70f780a7f0d98d74be62304fb99021` | patches |
| Japanese rerelease | 6,621,696 | `d19320bdc3381a48228990907910a391` | patches |
| Japanese original | not sourced | not sourced | not supported |

The USA, USA Alt and European discs all carry the same English retail
`v_on.exe`, so any of them will do. Every patch works on all three builds
above, and the window names the build it is looking at.

**[The Japanese original](https://redump.info/disc/133978)** has not been
sourced, so there is nothing to write tables against and the patcher treats
it as an unknown file. If you have a disc image of it, please get in touch -
see [Credits and licence](#credits-and-licence).

A repack, a bad rip or a copy already modified is refused, with its size and
MD5 shown beside a supported build's - in **GAME FILE** for a file you
picked, in **INSTALL** for a disc image. Installing and ripping work
whichever build the disc holds; only patching needs one from the table.

### What gets written

The original is copied to `v_on.exe.bak` first, and nothing is written
unless every selected patch applied, so a failure leaves the game as it was.

**XInput gamepad support** touches two more files. `v_on.ini` is moved to
`v_on.ini.bak` and the game writes a fresh one, because binds saved by the
unpatched game do not fit the new device list. The title artwork -
`escrgame.bin`, or `jscrgame.bin` on the Japanese rerelease - is rewritten
with the new title prompt, after a copy is kept as `.bak`.

**Native widescreen** adds a section to `v_on.exe` for its own code and its
off-screen canvas; nothing else on disk changes.

**Restore original** puts every backed-up file back - those three and the
two roll files the credit line rewrites - keeping whatever the patched game
wrote as `v_on.ini.patched`. Use it rather than copying a `.bak` over by
hand: the artwork and `v_on.exe` have to match, and restoring one alone
draws the title prompt as scrambled letters.

## Running from source

Working on the patcher rather than running it? [docs/README.md](docs/README.md)
maps the developer documentation.

Windows, with Python from python.org - Tk ships with it, nothing else needed:

```
py vo_patch.py
```

On Linux, Tk usually needs installing:

```bash
sudo apt install python3-tk        # Debian, Ubuntu, Mint
sudo dnf install python3-tkinter   # Fedora, RHEL
sudo pacman -S tk                  # Arch, EndeavourOS
python3 vo_patch.py
```

The release's `vo_patch-*.py` is the same file. For netplay it needs the
release's `dpctrl.dll` beside it; a checkout has it in `net/`.

Everything the patcher does is also available without a window:

```bash
python3 vo_patch.py --install CUE DIR  # the game, out of a disc image
python3 vo_patch.py --rip SOURCE DIR   # soundtrack, from a cue sheet or drive
python3 vo_patch.py --ddraw DIR        # fetch and install cnc-ddraw
python3 vo_patch.py --netplay DIR      # install the UDP netplay DLL
python3 vo_patch.py --selfcheck        # validate the patch tables
```

To build the Windows binary yourself, `pip install pyinstaller` and run
`pyinstaller vo_patch.spec`. It builds `dist/vo_patch/`, the exe with its
`_internal` folder, as `vo_patch-dev.exe` - releases take their version from
the git tag, and a source tree has none.

To change the machine code the patches install, see [asm/](asm/);
`asm/build.py` builds it into the hex strings in `vo_patch.py`. Never edit
those by hand.

The netplay DLL is built from [net/](net/): edit `net/dpctrl.c` and run
`python3 net/build.py`, which compiles it with mingw to `net/dpctrl.dll` and
records its hash in `vo_patch.py`. [net/README.md](net/README.md) covers the
protocol and the matchcode server.

`python3 tools/check.py` runs every check in the project. Give it a game
folder and it runs the ones that need one; give it a folder per build and
each of those runs once per build.
[docs/DEVELOPING.md](docs/DEVELOPING.md) covers the rest of the workflow.

## AI Disclaimer

LLMs are part of the toolchain here, alongside Ghidra, gdb and winedbg on
the running game, Cheat Engine and Unicorn. The scope, the disc dumps, the
testing and the debugging are human: every change is read before it goes
in and played on the real game, across all three builds, before it ships.
Offsets and byte sequences are verified against the original executable
before anything is written, and the patcher refuses any file that is not
an unmodified build it has tables for. It is still a hobby project poking
at a nearly 30-year-old binary, so expect bugs.

## Credits and licence

Some of the byte edits come from the original VO_Patch 0.43 (2008) by
[UE2A-GEL](https://jaguarandi.xxxxxxxx.jp/). Rights to the game belong to
SEGA. `LICENSE` (MIT) covers the patcher, its tools and its documentation -
not the game, not the bytes quoted from it, and not the letterforms traced
from its artwork. Special thanks to SirRockEmSockEm for the in-game shots.

Bug reports and patches are welcome as issues and pull requests. For
anything else - a disc image of a build the patcher does not know, or a
question that does not fit an issue - write to pairo@segaonline.net.
