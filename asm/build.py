#!/usr/bin/env python3
"""Assemble the sources into ../vo-patch.py, or check that they still match.

    sudo dnf install nasm      # or: sudo apt install nasm
    python3 asm/build.py            # assemble and write
    python3 asm/build.py --check    # verify only, writes nothing

vo-patch.py carries the assembled bytes because it ships as a single file that
has to run from a fresh checkout with nothing installed. So this writes them
in when the assembly changes, and --check catches assembly edited without them
being regenerated.

Everything nasm needs is built in a temporary directory, so neither mode
leaves anything behind in the tree.
"""

import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TARGET = os.path.join(ROOT, 'vo-patch.py')

sys.path.insert(0, HERE)
import layout                                             # noqa: E402

MAGICS = [
    ('MAGIC_ORIGENTRY', 0xE1E1E1E1, 'VA of the entry point we chain to'),
    ('MAGIC_IATMCI',    0xE2E2E2E2, 'VA of the mciSendCommandA IAT slot'),
    ('MAGIC_LOADLIB',   0xE3E3E3E3, 'VA of the LoadLibraryA IAT slot'),
    ('MAGIC_GETPROC',   0xE4E4E4E4, 'VA of the GetProcAddress IAT slot'),
    ('MAGIC_DATA',      0xE5E5E5E5, 'VA the data blob lands at'),
]


def hexblob(name, raw):
    out = ['%s = bytes.fromhex(\n' % name]
    text = raw.hex()
    for i in range(0, len(text), 64):
        out.append("    '%s'\n" % text[i:i + 64])
    out.append(')\n')
    return ''.join(out)


def assemble(source, tmp, includes=False):
    """nasm -f bin, with strings.inc generated into tmp when asked."""
    args = ['nasm', '-f', 'bin']
    if includes:
        inc, _data = layout.build()
        with open(os.path.join(tmp, 'strings.inc'), 'w') as fh:
            fh.write(inc)
        args += ['-I', tmp + os.sep]
    out = os.path.join(tmp, os.path.basename(source) + '.bin')
    args += ['-o', out, os.path.join(HERE, source)]
    subprocess.check_call(args)
    with open(out, 'rb') as fh:
        return fh.read()


def replace(text, name, body):
    """Swap the contents of one # NAME BLOB BEGIN/END pair."""
    new, n = re.subn(r'(# %s BLOB BEGIN\n).*?(# %s BLOB END)' % (name, name),
                     lambda m: m.group(1) + body + m.group(2),
                     text, flags=re.S)
    if n != 1:
        raise SystemExit('%s BLOB markers not found in vo-patch.py' % name)
    return new


def check_org(src, wanted):
    """Sources assembled at a fixed org, where the source and the site that
    writes it have to name the same address. Nothing downstream would
    notice: the code would be written, and every jump in it would land a
    few hundred bytes off."""
    for name, site in wanted.items():
        with open(os.path.join(HERE, name), encoding='utf-8') as fh:
            org = int(re.search(r'(?m)^org\s+(0x[0-9a-f]+)', fh.read()).group(1), 16)
        if site % 4:
            raise SystemExit('%s is written at 0x%08x, which is not a multiple '
                             'of four. A run of zeros that starts off a dword '
                             'boundary starts inside the last field before it, '
                             'and the first byte written lands in that field.'
                             % (name, site))
        if org != site + 0x400c00:
            raise SystemExit('%s is assembled at 0x%08x but its site puts it '
                             'at 0x%08x' % (name, org, site + 0x400c00))


def main(check=False):
    with tempfile.TemporaryDirectory() as tmp:
        code = assemble('vocd.asm', tmp, includes=True)
        levers = assemble('levers.asm', tmp)
        twin = assemble('twinstick.asm', tmp)
        kbpage = assemble('kbpage.asm', tmp)
    _inc, data = layout.build()

    vocd = ['VOCD_MAGICS = {\n']
    for name, value, note in MAGICS:
        # Pad to a fixed column: the generated file is linted like any other.
        vocd.append('%-38s # %s\n'
                    % ("    '%s': 0x%08X," % (name, value), note))
    vocd.append('}\n\n')
    vocd.append(hexblob('VOCD_CODE', code))
    vocd.append('\n')
    vocd.append(hexblob('VOCD_DATA', data))

    with open(TARGET, encoding='utf-8') as fh:
        src = fh.read()
    new = replace(src, 'VOCD', ''.join(vocd))
    new = replace(new, 'LEVERS', hexblob('LEVERS_CODE', levers))
    new = replace(new, 'TWIN', hexblob('TWIN_CODE', twin))
    new = replace(new, 'KBPAGE', hexblob('KBPAGE_CODE', kbpage))
    check_org(src, {'twinstick.asm': 0x00223dc4,
                    'kbpage.asm': 0x0023dd38})

    sizes = ('vocd %d + %d bytes, levers %d, twinstick %d, kbpage %d'
             % (len(code), len(data), len(levers), len(twin), len(kbpage)))
    if check:
        if new != src:
            raise SystemExit('the assembly does not match the blobs in '
                             'vo-patch.py.\nRun: python3 asm/build.py')
        print('assembly matches vo-patch.py (%s)' % sizes)
    else:
        with open(TARGET, 'w', encoding='utf-8') as fh:
            fh.write(new)
        print('written to vo-patch.py (%s)' % sizes)

    # The tables are what actually gets written to somebody's executable, so
    # never report success without running their checks too.
    subprocess.check_call([sys.executable, TARGET, '--selfcheck'])


if __name__ == '__main__':
    main('--check' in sys.argv)
