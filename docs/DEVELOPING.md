# Developing vo_patch

How to build, what to run before pushing, and what each check is for. For
using the patcher see [README.md](../README.md); for what the patches do see
[NOTES.md](NOTES.md); for the assembly sources see [asm/](../asm/).

## The four layers

```
asm/*.asm    ──nasm──►  hex strings in vo-patch.py  ──PyInstaller──►  vo-patch-X.Y.Z.exe
net/dpctrl.c ──mingw─►  base64 blob in vo-patch.py       CI builds this
  you edit             asm/build.py, net/build.py
                       write these
```

`vo-patch.py` ships as one self-contained file, so it cannot read `asm/` or
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
sudo dnf install nasm gcc-mingw64-i686      # or apt: nasm gcc-mingw-w64-i686
pip install pyflakes
```

`nasm` is needed only to rebuild `asm/`, mingw only to rebuild the netplay
DLL. Neither is needed to run the patcher or to build the exe - both are baked
into `vo-patch.py` as text. pyflakes is the `lint` check.

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
python3 vo-patch.py               # does the window still open?
```

Both build scripts rewrite blobs inside `vo-patch.py`, so `git diff` after one
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
python3 tools/check.py --list                 # what they are
python3 tools/check.py --only asm,net         # just those
```

`VO_GAME` works instead of the argument, and either a folder or any file
inside one will do.

| Name | What it proves |
| --- | --- |
| `tables` | patch tables, blobs and the banner bitmap: lengths, bounds, collisions between patches, the intra-patch overlap the XInput routine relies on |
| `asm` | `asm/` reassembles to the committed blobs, each blob's site agrees with the address its source names, no blob has grown past a cave ceiling in `CEILINGS`, and every call the site table writes that leaves `.text` lands on an assembled label |
| `net` | the baked DLL was built from the current `net/dpctrl.c`, by hash - two mingw versions do not produce identical bytes |
| `lint` | pyflakes |
| `tree` | nothing regenerated was left uncommitted. Skipped outside CI, where it would fail on every edit in progress |
| `credit` | `credittest.py`: the credit line recomposes out of the patched roll files, and both restore byte for byte. The line is spread over three files that have to agree - the block list in the executable, the cells in `scrstfmp.bin`, the tiles in `scrstfcg.bin` - so it patches a copy, walks the block list the way `0x448d39` does, expands the cells back through the tile sheet and compares the pixels against the bitmap the patcher started from |
| `offsets` | `selftest.py`: every `original` column against a real file, no cave write landing on an address the game reads or just past one it points at, hundreds of patch combinations applied, and the fully patched MD5 |
| `banner` | `bannertest.py`: the title prompt decodes back to the bitmap it was written from, and both files restore byte for byte |

Some need a copy of the game, which is not in the repository, so CI skips
them and says so. They are the manual step before tagging.

If a patch legitimately changed and the MD5 moved, update `EXPECTED_ALL` at
the top of `tools/selftest.py`, deliberately.

## The by-hand tools

Neither runs in CI: both need the game's files, which are not in the
repository. Their output is committed.

**`tools/vonbanner.py`** redraws the title screen prompt. It rasterises text
into the banner's 42x3 cells and, with `--write`, writes the game's own
`v_on.exe` and `escrgame.bin`. It does not touch `vo-patch.py`: to ship a new
wording, replace `BANNER_BITS` there with the bitmap it produces.

```bash
python3 tools/vonbanner.py DIR --text 'Press Start'    # preview
python3 tools/vonbanner.py DIR --text 'Press Start' --write
```

**`tools/freespace.py`** answers where a blob of a given length can go.

```bash
python3 tools/freespace.py DIR 91
```

It reads the runs of zeros in `.rdata` and `.rsrc`'s padding, drops what
patch sites already use, and sorts the rest: clean, ones with something
pointing just before them, and ones something reads inside. `.data` is left
out entirely - it is full of arrays the game fills at runtime, and each one
scans as a perfectly good cave.

Both the original and a fully patched copy are read. The original says what
the game points at; the patched copy says what the patches point at, and a
blob's scratch addresses and entry points appear in no original file. Hits
only the patched copy has are marked `from a patch`.

The middle list is the one to read. A run of zeros with a pointer just
before it may be the tail of a structure, and that is how `iniall.asm` came
to sit on the attract loop's scoreboard template: 84 bytes of zeros copied
into a record every time the demo match ended.

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
python3 tools/vocredits.py DIR --write     # into vo-patch.py
```

Edit `LINES` at the top to change what it says. The result goes between the
`CREDITLINE BLOB` markers - not the `CREDITS BLOB` ones, which belong to
`asm/credits.asm` and are a different thing entirely.

## Placing a blob

Nothing downstream checks that a cave is really free, so this is done by
hand, once, per blob:

1. `python3 tools/freespace.py DIR <length>` and take a clean candidate.
   The address has to be a multiple of four - `build.py` refuses otherwise,
   because a run starting off a boundary starts inside the field before it.
2. If nothing clean is long enough, read the game at the address the
   middle list names before using one of those. What lives there has to be
   shorter than the gap.
3. Put the address in the source's `org`, the file offset in the site that
   writes it, and the cave's last usable byte in `CEILINGS` in
   `asm/build.py`.

Moving one is the same work plus its hooks: `grep` the old address across
`vo-patch.py` and `asm/`, because every rel32 in the site table is computed
by hand. `check_calls` catches a call that lands outside `.text` on no
label, which is what a stale hook looks like, but only for `e8`/`e9` sites.

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
there, and the same site and its key to `SYNC_SITES` and `SYNC_KEYS` in
`vo-patch.py`, which is what warns at Apply and at netplay install. Miss one
and two builds with and without it desync instead of refusing to link. A
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
tools/vo-loopback.sh build install
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
python3 tools/rvload.py segaonline.net us.segaonline.net
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
| a blob grown past a pinned cave | `asm` | CI, every push |
| a blob grown into live data | `offsets` | **only if you run it** |
| typo'd an offset | `offsets` | **only if you run it** |
| wrong offset in the banner or its artwork | `banner` | **only if you run it** |
| hand-edited a generated blob | nothing | next build eats it |
| a cave whose end nobody has pinned | nothing until `offsets` runs | before tagging |
| a cave that was live data to begin with | `offsets`, as a note to read | before tagging |
| picked a cave that was never free | `freespace.py` | only when placing a blob |
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

**`offsets` reports OVERRUN** - a blob has outgrown its cave and is writing
onto something the game reads. The run of zeros carried on past the end of
the cave, so nothing else noticed. It names the address and the instruction
that reads it; shorten the blob or move it, then add the cave's real ceiling
to `CEILINGS` in `asm/build.py` so CI catches the next one without a game
file. Bare dwords in range are counted and ignored - four bytes of tile data
can equal an address, and only an operand means the game reads it.

**`offsets` prints a note about a cave** - something points at an address
shortly before it, and a table there could run into the cave. Most are
bytes that happen to look like an address; the rest are short structures
that stop where the cave starts. Read the game at that address once, and
if what lives there is shorter than the gap the note is noise. A cave
that is zeros in the file can still be a template the game copies, and
only this catches it.

**`offsets` says "is 6653952 bytes, expected 6650880"** - that is the patched
file. Point it at `v_on.exe.bak`.

**`banner` fails** - the tile indices in `v_on.exe` and the artwork in
`escrgame.bin` have come apart. Both offsets are in `tools/vonbanner.py`.

**`nasm not found`** - the `asm` check needs it. Everything else runs without.

**Push rejected** - someone (probably you, via the web UI) committed on
GitHub. `git pull`, then push. Pick local *or* web and stick with it.

**Broke your own game** - the patcher wrote `v_on.exe.bak` first. Hit
**Restore original**, or copy the .bak back by hand.

**It crashes in the game** - nothing here can catch that; every check reasons
about where bytes go, not whether the code runs. Get a log with
`PROTON_LOG=1`, then the forty lines around `dispatch_exception
code=c0000005`. Subtract the blob's org from the fault address to find it in
the source. Two ways to earn one, both already paid for: writing into a run of
zeros that is not free, and writing to `.rdata`, which is marked executable
but not writable - mutable state goes in the `.data` scratch.

A run of zeros that is not free will not always crash, which is the awkward
part. The F11 dialog outgrew its cave by two bytes in v0.8.5 and landed on a
`qword` 0.0 the projection routine compares depth against; nothing faulted
and nothing looked wrong. That is what the cave check in `selftest.py` and
`CEILINGS` in `asm/build.py` are between them for.

A stub in `.rdata` needs that mark, and the site that sets it is `RDATA_EXEC`
in `vo-patch.py` rather than any one patch's site list, because a site written
twice fails its own original check. Add the patch's key to `RDATA_EXEC_KEYS`
and it is applied once for whichever of them is ticked. `_check_table` seeds
its collision map with those four bytes, so putting them in a patch's list as
well is caught at import rather than in someone's game.

## Useful commands

```bash
python3 vo-patch.py --help
python3 vo-patch.py --rip          # list CD drives
python3 vo-patch.py --netplay DIR  # install the UDP netplay DLL
python3 tools/check.py --list      # what the checks are
python3 tools/check.py --only asm  # run one of them

python3 tools/vonbanner.py DIR     # redraw the title prompt
python3 tools/vocredits.py DIR     # redraw the credit line
```

`DIR` is the game folder. Without `--write` both only preview; with it
`vocredits.py` writes into `vo-patch.py` and `vonbanner.py` writes the game's
files.
