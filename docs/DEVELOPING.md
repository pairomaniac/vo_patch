# Developing vo_patch

How to build, what to run before pushing, and what each check is for. For
using the patcher see [README.md](../README.md); for what the patches do see
[NOTES.md](NOTES.md); for the assembly sources see [asm/](../asm/).

## The four layers

```
asm/*.asm    ──nasm──►  hex strings in vo_patch.py  ──PyInstaller──►  vo_patch-X.Y.Z.exe
net/dpctrl.c ──mingw─►  base64 blob in vo_patch.py       CI builds this
  you edit             asm/build.py, net/build.py
                       write these
```

`vo_patch.py` ships as one self-contained file, so it cannot read `asm/` or
`net/` at runtime. The machine code and the netplay DLL are baked in as text
between marker comments. `asm/build.py` and `net/build.py` are the only things
that put them there.

**Never edit a blob by hand.** The next build run silently discards it.

Two more generated things are checked in rather than rebuilt, so a build will
not notice if they go stale: `BANNER_BITS`, the title banner's bitmap, and
the credit line's bitmaps, which `tools/vocredits.py` writes. Change how
either one draws and you have to redo it by hand. See [TEXT.md](TEXT.md).

## Setup, once

```bash
sudo dnf install nasm gcc-mingw64-i686 xorg-x11-server-Xvfb
                                           # or apt: nasm gcc-mingw-w64-i686 xvfb
pip install pyflakes
```

`nasm` is needed only to rebuild `asm/`, mingw only to rebuild the netplay
DLL. Neither is needed to run the patcher or to build the exe - both are baked
into `vo_patch.py` as text. pyflakes is the `lint` check, and xvfb is the
display the `gui` one needs; without it that check skips itself and says so.

A pre-push hook catches a forgotten build before CI does:

```bash
cat > .git/hooks/pre-push <<'EOF'
#!/bin/sh
exec python3 tools/check.py
EOF
chmod +x .git/hooks/pre-push
```

## Daily loop

```bash
python3 asm/build.py              # only if you touched asm/ - regenerates the hex
python3 net/build.py              # only if you touched net/ - recompiles the DLL

python3 tools/check.py            # the checks CI runs, in two seconds
python3 vo_patch.py               # does the window still open?
```

Both build scripts rewrite blobs inside `vo_patch.py`, so `git diff` after one
shows the hex changing and nothing else. If it shows more, something else
moved too.

For anything non-trivial, branch and open a PR - CI runs on both.

## The checks

`tools/check.py` runs all of them and reports one line each. Every check is
still a script of its own; the runner only decides what to run, so CI and you
cannot drift apart.

```bash
python3 tools/check.py                        # the ones CI can run
python3 tools/check.py /path/to/VIRTUAL-ON    # and the ones that need the game
python3 tools/check.py RETAIL/ OEM/ JP/       # those once per build
python3 tools/check.py --list                 # what they are
python3 tools/check.py --only asm,net         # just those
```

`VO_GAME` works instead of one argument, and either a folder or any file
inside one will do. The checks that need the game run once per folder
given and are named by build - `offsets/jp` - because a table can only be
wrong on the build it is for; before tagging, give all three.

| Name | What it proves |
| --- | --- |
| `tables` | patch tables, blobs and the banner bitmap: lengths, bounds, collisions between patches, the intra-patch overlap the XInput routine relies on |
| `asm` | `asm/` reassembles to the committed blobs, every blob links for every build, and the two placeholders the apply-time sections fill occur exactly once each |
| `net` | the baked DLL was built from the current `net/dpctrl.c`, by hash - two mingw versions do not produce identical bytes |
| `disc` | `disctest.py`: the disc reader, on ISO9660 images the test builds itself - one per sector layout, plus a cue that names the wrong one. Extraction is byte-exact, the `ssp.ini` rules give the retail and OEM file lists, and every refusal names what is wrong. Needs no game and no disc, so CI runs it |
| `gui` | `guitest.py`: the window, opened under xvfb and driven without its loop - which button is offered for which source, that a copy holds both down until it finishes, and that the two columns end level whatever is open. None of these raise on their own, so each asserts the property. Needs a display; with none it skips and prints a note rather than passing quietly |
| `lint` | pyflakes |
| `tree` | nothing regenerated was left uncommitted. Skipped outside CI, where it would fail on every edit in progress |
| `offsets` | `selftest.py`: every `original` column against a real file, hundreds of patch combinations applied, and the fully patched MD5, on whichever build the file is |
| `banner` | `bannertest.py`: the title prompt decodes back to the bitmap it was written from, and both files restore byte for byte, on whichever build the folder holds |
| `credit` | `credittest.py`: the credit line recomposes out of the patched roll files, and both restore byte for byte. The line is spread over three files that have to agree - the block list in the executable, the cells in `scrstfmp.bin`, the tiles in `scrstfcg.bin` - so it patches a copy, walks the block list the way `0x448d39` does, expands the cells back through the tile sheet and compares the pixels against the bitmap the patcher started from |

Some need a copy of the game, which is not in the repository, so CI skips
them and says so. They are the manual step before tagging.

If a patch legitimately changed and the MD5 moved, update `EXPECTED_ALL` at
the top of `tools/selftest.py`, deliberately.

## The by-hand tools

Neither runs in CI: both need the game's files, which are not in the
repository. Their output is committed.

**`tools/vonbanner.py`** redraws the title screen prompt. It rasterises text
into the banner's 42x3 cells and, with `--write`, writes the game's own
`v_on.exe` and `escrgame.bin`. It does not touch `vo_patch.py`: to ship a new
wording, replace `BANNER_BITS` there with the bitmap it produces.

```bash
python3 tools/vonbanner.py DIR --text 'Press Start'    # preview
python3 tools/vonbanner.py DIR --text 'Press Start' --write
```

**`tools/vocredits.py`** builds the credit lines out of the roll's own
letters. The roll is pre-rendered text chopped into cells rather than a font,
so a new line cannot be typed - but the columns of each block separate into
characters at the blank gaps, and matching those runs against a transcript of
all 57 blocks gives a bitmap for each letter. 53 blocks segment exactly,
which covers the 24px face completely. The 11px face has only the letters of
CYBER TROOPERS, so the rest are drawn; `SMALL_DRAWN` holds them as ASCII art
and [TEXT.md](TEXT.md) has the proportions they follow.

```bash
python3 tools/vocredits.py DIR             # preview, as ASCII art
python3 tools/vocredits.py DIR --write     # into vo_patch.py
```

Edit `LINES` at the top to change what it says. The result goes between the
`CREDITLINE BLOB` markers - not the `CREDITS BLOB` ones, which belong to
`asm/credits.asm` and are a different thing entirely.

## Where the blobs live

Every blob but two is in the annex: a section, `.vojp`, appended before
any patch is written, executable, and filled through the site table like
any other site. Where it lands is fixed by the file's own headers, so its
addresses are known at import and nothing is relinked at apply time. The
two exceptions are places the game itself reaches - the F7 device list's
own run in `.data`, and the levers routine written straight after the
XInput routine - and those are the `caves` table of each `Build`.

Nothing lives in a run of zeros in `.rdata`. Such a run is never known to
be free: a pointer just before it means it is the tail of a structure or
the NULL slots of a handler table, and the game reads or calls through it
without any reference the file shows. Growing a blob is a rebuild; moving
one is not a thing that happens.

## Adding a blob or a site

A new blob is a source in `asm/`, a line in `SOURCES` in `build.py`, a
name in `ANNEX_BLOBS`, its symbols in every build's table, and a site in
the table. Then
`python3 asm/build.py`, and for a site by retail offset, every other
build's map regenerated: `buildsites.py NAME retail.exe other.exe map.pkl`
per build, `vomap.py` first if there is no map. Both tools import the
patcher in bootstrap mode (`VO_PATCH_BOOTSTRAP`), where a blob, label or
site that does not exist yet reads as empty rather than failing the
import, so the order of the edits does not matter.

## Adding a build

A build is a `Build` in `vo_patch.py`: its sections, where each blob goes,
what every symbol the blobs name resolves to, its title artwork, and for
any build but retail a site map and an annex. The tables come from the
retail executable and the new one side by side, none of which is
committed:

1. `python3 tools/vomap.py retail.exe other.exe map.pkl` matches the two
   function by function and votes on where every address went.
2. `python3 tools/votrans.py symbols map.pkl` prints `RETAIL.symbols` as
   the other build has them, with the unresolved ones commented out. Frame
   offsets, the scratch past `.data` and the two spare bytes are always by
   hand: read the disassembly at each hook (`votrans.py one VA`) and pick
   free memory.
3. Add the `Build` with `sites=None` and `annex=ANNEX_BLOBS`; the two
   caves are the game's own device list, translated like any symbol, and
   the levers tail, as in every build. `SYNC_SITES` follows from the site
   map.
4. Put `# SITES NAME BEGIN` / `END` markers after the JAPAN ones and run
   `python3 tools/buildsites.py NAME retail.exe other.exe map.pkl`. What
   it cannot place is listed; those go in `HAND` in `votrans.py`, keyed by
   the build's MD5, with a reason each.
5. A row in `fp_builds` in `net/dpctrl.c` with the PE timestamp and the
   two sync sites as virtual addresses, then `net/build.py`.
6. `python3 asm/build.py --check`, then `selftest.py` on the new file and
   pin its all-patches MD5 in `EXPECTED_ALL`; `bannertest.py` and
   `credittest.py` on an install of it once its artwork MD5 is in `art`.
   Then someone has to play it.

Two things to check by eye before anyone runs it. A site the matcher has
placed by its bytes may be in the wrong function - ten bytes of
`cmp [ebp-8], 0x1a; jge` occur in more than one place, and only the
disassembly says which copy has the recompiled frame. And anything the
apply code writes outside the site table - the annex code in `.voxt` -
must be linked for the build being patched, not taken from a module-level
constant, which is retail's. `tools/whereis.py` turns a crash address into
a blob and label.

## Netplay

`net/dpctrl.c` is the only part with no automated coverage at all. CI compiles
it and checks the exports are still there; nothing runs it. The server side,
`net/rendezvous.py`, is plain Python and at least gets pyflakes.

The match runs in lockstep: each machine simulates from both players' input
frames, so the two must step the simulation identically. The handshake tag's
last byte is a fingerprint of the patches that change how the game plays,
read straight from the running exe by `sync_fingerprint()`, and a mismatch is
refused at connect. If you add a patch that changes the simulation - a rule,
a timing, a physics value, anything that alters what the game computes from a
given input - add its site beside `FP_DIVISOR_VA` and `FP_CONTINUE_VA`
there, and the same site to `SYNC_SITES` in `vo_patch.py`, which is what
warns when the netplay add-on is installed beside an executable an older
release patched without it. Miss one and two builds with and without it
desync instead of refusing to link. A
patch that only changes what a machine shows or how it reads its own controls
stays out of the fingerprint: those are each player's own business.

Two scripts under `tools/` build the DLL and put it in a game folder. Both
read `~/.vo-test`, which is yours and is not in the repository:

```bash
# ~/.vo-test
VO_GAME=/path/to/VIRTUAL-ON           # vo-dll.sh
VO_GAME_A=/path/to/VIRTUAL-ON         # vo-loopback.sh
VO_GAME_B=/path/to/VIRTUAL-ON-P2
VO_PFX_A=$HOME/prefixes/virtual-on
VO_PFX_B=$HOME/prefixes/virtual-on-p2
```

`tools/vo-dll.sh` is for testing against another machine:

```bash
tools/vo-dll.sh build
tools/vo-dll.sh install               # or install /other/folder
tools/vo-dll.sh restore
```

`tools/vo-loopback.sh` puts two local instances against each other. It needs
two installs with two prefixes - both write `v_on.ini` and save state, and
sharing either produces failures that look like netcode bugs and are not.

```bash
tools/vo-loopback.sh build install status
tools/vo-loopback.sh a            # hosts on 127.0.0.1
tools/vo-loopback.sh b            # joins
tools/vo-loopback.sh restore
```

What it proves is the ABI, the ring handling and the copy-out. What it does
not: at 0 ms round trip the delay negotiation computes 1 every time, so that
path never runs at anything but its minimum. Shape the loopback with `tc` for
that, and read [net/README.md](../net/README.md) first.

Loopback cannot test matchcodes: both instances sit behind the same address.
That needs two machines on two networks - a laptop on a phone hotspot against
a desktop on ethernet is the cheap rig. Create an empty `vo-net.log` beside
each `v_on.exe` first and look for `linked directly` or `linked through the
relay` afterwards. `relay=1` under `[net]` in `vo-net.ini` on one side forces
the relay. The server logs the same outcome per code;
`tools/rendezvous-install.sh status` tallies them.

`tools/rendezvous-install.sh` installs the server from `net/rendezvous.service`
on a machine that should keep one up.

`tools/rvload.py` probes a running server, or two side by side, so a box
that has drifted from the source shows up:

```bash
python3 tools/rvload.py segaonline.net us.segaonline.net jp.segaonline.net
```

Each probe targets one thing the server does: the create round trip, a full
handshake, a 512 vs 513-byte relay, a non-alphabet code, and a burst of
creates from one address against the per-IP cap. `--flood` adds an
unknown-code storm to watch the guess-ban engage - own servers only, and not
during a match. It creates only codes it lets expire, so it is safe to point
at a live server, the flood aside.

## Releasing

The version comes from the tag and nowhere else. `VERSION = 'dev'` stays as it
is in the source; the workflow rewrites that line during the build, and the
spec, the exe name, the file properties, the window title, `--version` and the
line on the title screen all read it from there. So there is nothing in a file
to bump.

The title-screen line is the one thing the patcher writes that is not the same
for everyone, so it is written after the patch table rather than from it and
`EXPECTED_ALL` in `selftest.py` stays one digest.

```bash
git pull
python3 tools/check.py /path/to/VIRTUAL-ON     # everything, nothing skipped

git tag v0.8.4
git push && git push --tags

gh run watch               # follow CI
```

CI runs `verify` (ubuntu) and, only if it passes, `windows`, which stamps the
version, builds the exe, checks the bundle, runs `--selfcheck` on it, stages
the LF-normalised script, and attaches both to the release.

`--generate-notes` writes the release body from commit subjects, which for a
squashed history is close to useless. Replace it once CI is green:

```bash
gh release edit v0.8.4 --notes-file notes.md
```

Keep the notes to what a player would notice. Internal changes go in the diff.

### Play it first

The checks prove the files are consistent with each other. They cannot
prove the game still runs, and both v0.10.2 bugs passed everything green.
Patch a clean copy and walk these once:

- **the attract loop**: skip the intro movie, then leave it alone through
  the demo match and the scoreboards until the title screen comes back.
  This is the path nobody plays and the one the v0.10.2 crash lived on
- a match to the win screen, and the replay after it
- the F5, F7 and F11 dialogs, and a deadzone edit that survives a restart
- the ending credits and the initials screen after them
- one matchcode match against a second machine, direct and forced through
  the relay, since the netplay DLL ships in the same build

Ten minutes, and it covers the state machines no check reaches.

### Bad tag

```bash
git tag -d v0.8.4
git push --delete origin v0.8.4
gh release delete v0.8.4 --yes     # if a release was created
# fix, re-tag
```

## What catches what

| Mistake | Caught by | When |
| --- | --- | --- |
| edited asm, forgot `build.py` | `asm` | CI, every push |
| edited dpctrl.c, forgot `net/build.py` | `net` | CI, every push |
| moved a blob's site, left its address behind | `asm` | CI, every push |
| ran a build, forgot to commit | `tree` | CI, every push |
| dpctrl.c stopped exporting something | mingw build step | CI, every push |
| reordered a site list | `tables` | CI, every push |
| two patches on one byte | `tables` | CI, every push |
| typo'd an offset | `offsets` | **only if you run it** |
| wrong offset in the banner or its artwork | `banner` | **only if you run it** |
| hand-edited a generated blob | nothing | next build eats it |
| a button armed when it should not be | `gui` | CI, every push |
| a card or column that lays out wrong | `gui` | CI, every push |
| a state machine left in a state nothing handles | nothing | only playing the game |
| a blob moved without its hook | `asm` | CI, every push |
| a table that is well-formed and wrong | nothing | only playing the game |
| the netplay DLL misbehaving on a real link | nothing | only two machines |
| the matchcode punch or relay failing | nothing | two machines on two networks |

## Troubleshooting

**"asm/ does not match the blobs"** - run `python3 asm/build.py`, commit,
push.

**"net/dpctrl.c and the baked DLL disagree"** - run `python3 net/build.py`,
commit, push. It needs mingw; `--check` does not, it only compares hashes.

**`--selfcheck` AssertionError** - it names the patch and offset. "expects XX
where an earlier site in the same patch wrote YY" means a site list got
reordered; put it back.

**`offsets` reports mismatches** - an offset is wrong for the real file.
Nothing has been written to anyone's game. Fix the table.

**The credit is skipped at apply time** - one of the two roll files is
missing or is not the one the patch was built against. Both are checked
before the executable is written, so the run goes ahead without it rather
than leaving a block list the map cannot satisfy. The title-screen version
goes with it: it needs no files of its own, but it is the same tick box.

**`credit` fails** - the ending roll does not read back as written. The line
is spread over the block list in the executable, the cells in `scrstfmp.bin`
and the tiles in `scrstfcg.bin`, and all three have to agree. A block list
naming cells the map does not have sends the renderer off the end of it, so
the patcher checks both files before it writes either half.

**`offsets` says "is 6653952 bytes, expected 6650880"** - that is the patched
file. Point it at `v_on.exe.bak`.

**`banner` fails** - the tile indices in `v_on.exe` and the artwork in
`escrgame.bin` have come apart. Both offsets are in `tools/vonbanner.py`.

**`nasm not found`** - the `asm` check needs it. Everything else runs without.

**`gui` says it found no display** - it needs one, and xvfb is what gives it
one. Install xvfb, or run `xvfb-run -a python3 tools/guitest.py` by hand. A
skipped `gui` still counts as passing, which is why it prints the note.

**Push rejected** - someone (probably you, via the web UI) committed on
GitHub. `git pull`, then push. Pick local *or* web and stick with it.

**Broke your own game** - the patcher wrote `v_on.exe.bak` first. Hit
**Restore original**, or copy the .bak back by hand.

**It crashes in the game** - nothing here can catch that; every check reasons
about where bytes go, not whether the code runs. Get a log with
`PROTON_LOG=1`, then the forty lines around `dispatch_exception
code=c0000005`. `python3 tools/whereis.py v_on.exe <address>` says which
blob and label it is in, or which part of the game. A fault *at* an address
that is the first bytes of a blob means the game called through a pointer
that our bytes overwrote; a fault inside a blob is the blob's.

## Useful commands

```bash
python3 vo_patch.py --help
python3 vo_patch.py --install CUE DIR  # install the game from a disc image
python3 vo_patch.py --rip          # list CD drives (Linux)
python3 vo_patch.py --netplay DIR  # install the UDP netplay DLL
python3 tools/check.py --list      # what the checks are
python3 tools/check.py --only asm  # run one of them

python3 tools/vonbanner.py DIR     # redraw the title prompt
python3 tools/vocredits.py DIR     # redraw the credit line
```

`DIR` is the game folder. Without `--write` both only preview; with it
`vocredits.py` writes into `vo_patch.py` and `vonbanner.py` writes the game's
files.
