#!/usr/bin/env python3
"""Assemble the sources into ../vo-patch.py, or check that they still match.

    sudo dnf install nasm      # or: sudo apt install nasm
    python3 asm/build.py            # assemble and write
    python3 asm/build.py --check    # verify only, exit 1 on a mismatch

vo-patch.py carries the assembled bytes because it ships as a single file that
has to run from a fresh checkout with nothing installed. So this writes them
in when the assembly changes, and --check catches assembly edited without them
being regenerated.
"""

import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

sys.path.insert(0, HERE)
import layout                                             # noqa: E402

MAGICS = [
    ('MAGIC_ORIGENTRY', 0xE1E1E1E1, 'VA of the entry point we chain to'),
    ('MAGIC_IATMCI',    0xE2E2E2E2, 'VA of the mciSendCommandA IAT slot'),
    ('MAGIC_LOADLIB',   0xE3E3E3E3, 'VA of the LoadLibraryA IAT slot'),
    ('MAGIC_GETPROC',   0xE4E4E4E4, 'VA of the GetProcAddress IAT slot'),
    ('MAGIC_DATA',      0xE5E5E5E5, 'VA the data blob lands at'),
    ('MAGIC_HOOK',      0xE6E6E6E6, 'VA of the hook thunk'),
]


def hexblob(name, raw):
    out = ['%s = bytes.fromhex(\n' % name]
    text = raw.hex()
    for i in range(0, len(text), 68):
        out.append("    '%s'\n" % text[i:i + 68])
    out.append(')\n')
    return ''.join(out)


def main(check=False):
    inc, data = layout.build()
    open(os.path.join(HERE, 'strings.inc'), 'w').write(inc)

    binpath = os.path.join(HERE, 'vocd.bin')
    subprocess.check_call(['nasm', '-f', 'bin', '-I', HERE + os.sep,
                           '-o', binpath, os.path.join(HERE, 'vocd.asm')])
    code = open(binpath, 'rb').read()

    body = ['VOCD_MAGICS = {\n']
    for name, value, note in MAGICS:
        body.append("    '%s': 0x%08X,%s# %s\n"
                    % (name, value, ' ' * (13 - len(name)), note))
    body.append('}\n\n')
    body.append(hexblob('VOCD_CODE', code))
    body.append('\n')
    body.append(hexblob('VOCD_DATA', data))

    path = os.path.join(ROOT, 'vo-patch.py')
    src = open(path).read()
    new, n = re.subn(r'(# VOCD BLOB BEGIN\n).*?(# VOCD BLOB END)',
                     lambda m: m.group(1) + ''.join(body) + m.group(2),
                     src, flags=re.S)
    if n != 1:
        raise SystemExit('markers not found in vo-patch.py')

    if check:
        if new != src:
            raise SystemExit(
                'vocd.asm does not match the blob in vo-patch.py.\n'
                'Run: python3 asm/build.py')
        print('vocd.asm matches vo-patch.py (%d + %d bytes)'
              % (len(code), len(data)))
    else:
        open(path, 'w').write(new)
        print('code %d bytes, data %d bytes, written to vo-patch.py'
              % (len(code), len(data)))
    check_levers(new)


def check_levers(source):
    """levers.asm is one site inside the XInput patch rather than a blob of
    its own, so it is pasted in by hand. Assemble it and make sure the bytes
    in vo-patch.py still say the same thing."""
    binpath = os.path.join(HERE, 'levers.bin')
    subprocess.check_call(['nasm', '-f', 'bin', '-o', binpath,
                           os.path.join(HERE, 'levers.asm')])
    want = open(binpath, 'rb').read().hex()
    if want in source:
        print('levers.asm matches vo-patch.py (%d bytes)' % (len(want) // 2))
    else:
        raise SystemExit('levers.asm assembles to bytes vo-patch.py does not '
                         'contain:\n  ' + want)


if __name__ == '__main__':
    main('--check' in sys.argv)
