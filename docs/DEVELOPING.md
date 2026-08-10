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

The title banner is a third generated thing, but it is checked in rather than
rebuilt: `tools/vonbanner.py` writes the bitmap, and nothing regenerates it
during a build. See [TEXT.md](TEXT.md).

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

python3 tools/check.py            # the five checks CI runs, in two seconds
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
python3 tools/check.py                        # the five CI can run
python3 tools/check.py /path/to/VIRTUAL-ON    # and the two that need the game
python3 tools/check.py --list                 # what they are
python3 tools/check.py --only asm,net         # just those
```

`VO_GAME` works instead of the argument, and either a folder or any file
inside one will do.

| Name | What it proves |
| --- | --- |
| `tables` | patch tables, blobs and the banner bitmap: lengths, bounds, collisions between patches, the intra-patch overlap the XInput routine relies on |
| `asm` | `asm/` reassembles to the committed blobs, and each blob's site agrees with the address its source names |
| `net` | the baked DLL was built from the current `net/dpctrl.c`, by hash - two mingw versions do not produce identical bytes |
| `lint` | pyflakes |
| `tree` | nothing regenerated was left uncommitted. Skipped outside CI, where it would fail on every edit in progress |
| `offsets` | `selftest.py`: every `original` column against a real file, 350-odd combinations applied, and the fully patched MD5 |
| `banner` | `bannertest.py`: the title prompt decodes back to the bitmap it was written from, and both files restore byte for byte |

The last two need a copy of the game, which is not in the repository, so CI
skips them and says so. They are the manual step before tagging.

If a patch legitimately changed and the MD5 moved, update `EXPECTED_ALL` at
the top of `tools/selftest.py`, deliberately.

## Netplay

`net/dpctrl.c` is the only part with no automated coverage at all. CI compiles
it and checks the exports are still there; nothing runs it.

`tools/vo-loopback.sh` puts two local instances against each other. It needs
two installs with two prefixes - both write `v_on.ini` and save state, and
sharing either produces failures that look like netcode bugs and are not.
Configure it in `~/.vo-loopback`, which is yours and is not in the repository:

```bash
# ~/.vo-loopback
VO_GAME_A=/path/to/VIRTUAL-ON
VO_GAME_B=/path/to/VIRTUAL-ON-P2
VO_PFX_A=$HOME/prefixes/virtual-on
VO_PFX_B=$HOME/prefixes/virtual-on-p2
```

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

## Releasing

The version comes from the tag and nowhere else. `VERSION = 'dev'` stays as it
is in the source; the workflow rewrites that line during the build, and the
spec, the exe name, the file properties, the window title and `--version` all
read it from there. So there is nothing in a file to bump.

```bash
git pull
python3 tools/check.py /path/to/VIRTUAL-ON     # all seven, nothing skipped

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
| a table that is well-formed and wrong | nothing | only playing the game |
| the netplay DLL misbehaving on a real link | nothing | only two machines |

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
zeros that is not free, and writing to `.rdata`, which the gamepad patch marks
executable but not writable - mutable state goes in the `.data` scratch.

## Useful commands

```bash
python3 vo-patch.py --help
python3 vo-patch.py --rip          # list CD drives
python3 vo-patch.py --netplay DIR  # install the UDP netplay DLL
python3 tools/check.py --list      # what the checks are
python3 tools/check.py --only asm  # run one of them
```
