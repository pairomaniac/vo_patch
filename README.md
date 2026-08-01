# Virtual-On (PC, 1997) - v_on.exe patcher

New patcher for *Cyber Troopers Virtual-On* on Windows: four of original
VO_Patch's byte edits, fixes of my own, XInput gamepad support for both
players, and CD music played from files so the soundtrack works with no disc
and nothing mounted.

VO_Patch 0.43 (2008) is by [UE2A-GEL](https://jaguarandi.xxxxxxxx.jp/). Rights
to the game belong to SEGA. The CC0 dedication in `LICENSE` covers this
repository's own code and nothing else - not the game, and not the byte
sequences quoted from it.

<img width="480" height="624" alt="The patcher, with the patch lists and the CD music section" src="https://github.com/user-attachments/assets/116e05e0-2351-4268-b12d-d7c7d1dc0104" />


## Download

**Windows:** Get `vo-patch-*.exe` from the
[latest release](https://github.com/pairomaniac/vo_patch/releases/latest).

It is unsigned, so SmartScreen calls it an unknown publisher on the first run.
If you would rather see how it was built than take that on trust, the log is
under this repository's Actions.

**Linux:** check [Running from source](#running-from-source).

## What the patches do

**Essential** fix things that are broken on modern systems; **Extra** are up
to taste. Everything starts ticked, so untick what you do not want before
patching. How each one works is under [Notes](#notes).

### Essential

- **Skip processor check** - lets the game start on a modern CPU, without you
having to set `ProcessorCheck=Off` in `v_on.ini` first.
- **Fix the frame rate (60 FPS)** - three fixes for the same complaint:
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

- **XInput gamepad support** - adds a *Gamepad (XInput)* device profile and
binds every action to a modern controller, for both players. The Windows 95
joystick API the game uses leaves one trigger unreachable. See
[Gamepad](#gamepad).
- **CD music from files, no disc needed** - the soundtrack comes from
`music\trackNN.wav` beside the game and the disc check goes with it, so the
game runs and the music plays with nothing mounted. See [Music](#music).
- **Disable menu bar (Extras menu on F11)** - drops the strip across the top
and puts the Debug options on F11 instead: No shot, SE, CD, Kill 1P, Kill 2P,
Scorekeeping and **Quit Program**. Motion is not among them, the F5 page
having taken that over. Every other menu was always on a key:

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
- **Miscellaneous sound fixes** - three small ones together: sound effects
fire without their built-in delay so rapid-fire weapons sound like the arcade,
output goes from 22050 to 44100 Hz, and an enemy Fei-Yen gets back the
hypermode sound a bug left silent.
- **Hide loading screen text** - removes "Now Loading . . .".

## Using the patcher

Select `v_on.exe` and press **Apply patches**. Open a list and press the (i)
next to a patch to read what it does, and untick anything you do not want.

Only the unmodified disc file is accepted - 6,650,880 bytes, MD5
`a464b0ff32d5bab499f265e45658504e`. The original is copied to `v_on.exe.bak`
before anything is written, and **Restore original** puts it back so you can
change your selection. Nothing is written unless every selected patch applied,
so a failure leaves the game exactly as it was.

If **XInput gamepad support** was among them, `v_on.ini` is moved to
`v_on.ini.bak` at the same time and the game writes a fresh one. **Restore
original** puts that back too, keeping whatever the patched game wrote as
`v_on.ini.patched`.

## Running from source

Windows, with Python from python.org - Tk ships with it, nothing else needed:

```
py vo-patch.py
```

On Linux it uses GTK4 if it can and Tk if it cannot. GTK4 has to be 4.10 or
newer; anything older falls back to Tk, which looks slightly plainer and works
the same.

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

To change the machine code the patches install, see [asm/](asm/). The assembly
lives there and `asm/build.py` copies the assembled bytes into `vo-patch.py` -
the hex in the script is generated and should never be edited by hand. CI
checks the two still agree on every push. `python3 vo-patch.py --selfcheck`
validates the patch tables without needing a copy of the game;
`python3 tools/selftest.py path/to/v_on.exe` applies them to a real one.

## Gamepad

<img width="382" height="267" alt="xinput1" src="https://github.com/user-attachments/assets/457643b7-f42b-49b2-993f-50b56733c59d" />
<img width="382" height="267" alt="xinput2" src="https://github.com/user-attachments/assets/9fac5899-0465-4fbe-990f-65f978352632" />

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
calls against the `cdaudio` device. Left alone it needs a disc, or a virtual
drive presenting the audio tracks - a data-only ISO has nothing to play, so
the game runs silent but otherwise fine.

The patch removes that requirement: rip the soundtrack to WAV, drop it beside
the game, and the music plays with no drive and no extra DLL.

### Ripping the tracks

The **CD MUSIC** section of the patcher rips them, from a `bin`/`cue` pair or
a CD drive. Pick `v_on.exe` first so it knows where they go, put a cue sheet
or a drive in the box, press **Rip tracks**.

Via terminal:

```bash
python3 vo-patch.py --rip VIRTUAL-ON.cue /path/to/VIRTUAL-ON
python3 vo-patch.py --rip /dev/sr0       /path/to/VIRTUAL-ON
python3 vo-patch.py --rip                # list drives it can see
```

The `bin`/`cue` route is the one to prefer: exact, no drive needed, sector
offsets read straight from the sheet. A drive is read with `CDROMREADAUDIO` on
Linux and `IOCTL_CDROM_RAW_READ` on Windows, and a cdemu device works the same
as a physical one.

The result is about 320 MB: 26 audio tracks, roughly 30 minutes, uncompressed.

```
VIRTUAL-ON\
    v_on.exe
    music\
        track02.wav ... track27.wav
```

Track 1 is the data track and has no file. Numbering has to match the disc,
because the game asks for tracks by number.

### At runtime

If `music\` is missing or empty the patch stands aside and the game talks to
the drive as it always did. If the tracks are there they win, disc or no disc.
Under Wine they play through `mciwave`, so no `dosdevices` entry, raw device
link or cdemu instance is needed.

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
| **Let v_on.ini set Motion** | `0x10afbe`, `0x10afeb`, `0x10b002`, `0x1c6941`–`0x1c8bd3` | three fallbacks `3` → `1`, eight stores in four routines → `nop` |
| **Raise timer resolution** | `0x1f423e`, `0xa8` | stub in `.text` padding, entry point redirected |
| **Better ini defaults** | `0x10acd7`, `0x10b088`, `0x10b131`, `0x10b1b0`–`0x10b1c4` | fallback immediates changed, Field Graphic branched into the Rich path |
| **Motion Type 30 / 60 FPS** | `0x273c1`, `0x275d3`, `0x275e2`, `0x6035ac`, `0x60c064` | the radios write 2 and 1 instead of 3 and 2, dialog rebuilt with the new labels |
| **Fix the lose-a-round crash** | ten sites, `0x077f5a`–`0x0c0ada` | 42-byte blocks → `nop` |
| **Keep input after alt-tab** | signature | `push 6` → `push 0xA` at `SetCooperativeLevel` |
| **XInput gamepad support** | `0x0001c4`, `0x0422a8`, `0x1bc13b`, `0x1c530e`, `0x0971bd`, `0x207702`, `0x20779e`, F7 page constants, four `.rdata` caves | routine, tables and lever cleanup in section padding, both players' profile dispatch repointed |
| **CD music from files** | new `.vocd` section, entry point, winmm IAT slot | `mciSendCommandA` redirected to a routine that answers from WAV files |
| **Menu bar off, F11 dialog** | `0x1c4d42`, `0x1c4d4b`, `0x1c4d7e`, `0x1f427c`, `0x1f42d8`, `0x1f43cf`, `0x23dce8`, `0x6036b0` | dialog built in unused section padding and over the dead menu |

Bold entries are not part of original VO_Patch.

## Notes

Not every row needs one - the four inherited byte edits do what they say on
the tin. These are the rest, in the order of the table above.

**Sample rate.** This is the DirectSound buffer format, not the samples, which
are 8-bit at 7500 or 11025 Hz either way. VO_Patch set only `nSamplesPerSec`,
leaving `nAvgBytesPerSec` inconsistent; both are set here.

**No disc, CD music.** A helper returns -1 when no disc is found and the
caller loops on a message box; removing the branch into that loop falls
through to the success path. The scan itself is untouched, so a mounted image
is still found.

The music logic is small: open `cdaudio`, set TMSF, read the track count and
every length once, then only whole tracks, stops and the occasional pause. No
notifications, no position polling, so a finished track just goes quiet -
which is what the disc did too. Little enough to answer without one. A routine
in a new `.vocd` section takes over the `mciSendCommandA` import, builds the
table of contents from the WAV file sizes and turns each play request into
`mciSendStringA` against `waveaudio`. Lengths come from the file size rather
than the header, the ripper always writing 44100 Hz 16-bit stereo.

This is the one patch that cannot be a byte edit in place: the padding at the
end of `.text` has 24 bytes left after the timer stub and the F11 dialog, and
the zero runs in `.data` are globals the game writes at runtime. The
executable gets a section of its own and grows by about 3 KB, and the entry
point is repointed at the setup thunk, which chains to whatever it was before
- hence this patch running after all the others.

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
those are the least attractive option going - Sky off, every texture off,
Field Graphic Normal. A four-byte edit each.

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

It hangs off the *Keyboard only(Simple)* profile, the only F7 page that binds
all twelve actions, with its input list swapped for pad inputs. Bindings are
one byte per action, so pad entries occupy `0xE0`-`0xEF` in the scancode
space, which the game does not otherwise use. Player 2 is a full mirror, so
both sides are the same routine with a different parameter block. Start and A
are also posted as key messages from the message pump, because the input tick
does not run on the intro or while paused.

Jump and guard are lever gestures rather than buttons - both levers spread
outward, both squeezed inward - and each lever word is a bitmask where a clear
bit means pushed: `0x80` left, `0x40` right, `0x20` up, `0x10` down. Forward
is up on both, turning is opposed vertical. The tick ORs every active input
into those words, so holding a direction left `up` set alongside the jump bits
and the result read as two diagonals rather than a spread. A short routine
after each tick strips that back off, and only when a pad was read, so the
keyboard path is untouched.

**F11 dialog.** No dialog resource ever existed, so one is built at runtime
from a template written into unused space - over the old menu, which this same
patch unhooks. Every control carries the game's own command ID, so clicks go
straight to the main window and **Quit Program** is just the *Exit Game*
command; the check boxes read the game's own flags. F11 because F9 disconnects
a network game and F10 is a Windows system key.

Motion is not among them any more, the F5 page having taken it over. The
handler that filled the box stays and does nothing, `SendDlgItemMessage`
against a missing control being a no-op.

---

Written with AI assistance. Every offset and byte sequence is checked against
the original executable before it is written, and the patcher refuses anything
that is not the unmodified disc file - but this is a hobby project poking at a
nearly 30-year-old binary so expect bugs.
