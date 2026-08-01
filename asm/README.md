# asm

Source for the machine code the patches install. `vo-patch.py` carries the
assembled bytes, so nobody running the patcher or building the exe needs nasm.

| File | |
| --- | --- |
| `vocd.asm` | CD audio: setup, the `mciSendCommandA` hook, the handlers |
| `levers.asm` | gamepad: the lever cleanup that runs after each input tick |
| `layout.py` | data cave layout and string table, shared by `vocd.asm` and the blob |
| `build.py` | assembles both into `../vo-patch.py` |
| `strings.inc`, `*.bin` | generated, not committed |

```
python3 asm/build.py            # assemble and write
python3 asm/build.py --check    # verify only, exit 1 on a mismatch
```

The bytes are committed rather than assembled on demand because `vo-patch.py`
ships as a single file, both bundled into the exe and downloaded on its own,
and has to run from a fresh checkout with nothing installed.

## Where each one lands

`vocd.asm` is a blob of its own. `build.py` replaces everything between the
`# VOCD BLOB BEGIN` and `# VOCD BLOB END` markers in `vo-patch.py`, so it is
never edited by hand; the markers are load-bearing.

`levers.asm` is a single site inside the XInput patch table, at `0x0020779e`,
so there is nothing for a marker to delimit and the bytes are pasted in by
hand. `--check` prints what it assembled when they disagree. If the routine
changes length, the run of `00` beside it - the bytes it expects to find in
the original executable - has to change with it, or the patch table's length
check rejects it at import.

## vocd.asm

Two blobs go into a new `.vocd` section. The code starts with two thunks: `+0`
is the hook the winmm import is redirected to, `+5` is the setup the entry
point is repointed at. The data blob is the string table plus the space the
code works in - track table, path and command buffers.

Absolute addresses are placeholders (`0xE1E1E1E1` and friends) that
`apply_cdaudio` fills in once it has read the executable: IAT slots, the
previous entry point, and where the blobs landed. Everything else is self
relative, so the section can go anywhere.

Setup resolves what it needs through the game's own `LoadLibraryA` and
`GetProcAddress` imports, walks `music\trackNN.wav` building the table of
contents from file sizes, then `VirtualProtect`s the `mciSendCommandA` IAT
slot and redirects it. With no tracks it changes nothing and every call
reaches the real winmm, which is the fallback to a disc.

## levers.asm

The game's jump and guard are lever gestures, not buttons: both levers spread
outward and both squeezed inward. Each lever word is a bitmask where a clear
bit means pushed - `0x80` left, `0x40` right, `0x20` up, `0x10` down.

The XInput tick ORs every active input into those words, so a held direction
left `up` set alongside the jump bits and the result read as two diagonals
rather than a spread. This runs in place of the tick's epilogue and strips the
contamination back off, but only when a pad was actually read that tick, so
the keyboard path is untouched.
