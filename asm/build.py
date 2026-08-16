#!/usr/bin/env python3
"""Build the blobs in ../vo-patch.py, or check that they still match.

Machine code comes from the .asm files through nasm; the tables and dialog
templates come from the .py modules beside them, which pack them from a
readable description.

    sudo dnf install nasm      # or: sudo apt install nasm
    python3 asm/build.py            # assemble and write
    python3 asm/build.py --check    # verify only, writes nothing

vo-patch.py carries the assembled bytes because it ships as a single file that
has to run from a fresh checkout with nothing installed. So this writes them
in when the assembly changes, and --check catches assembly edited without them
being regenerated. It also reads the patch table back, so each blob's site and
the address its source names for it are checked against each other.

Everything nasm needs is built in a temporary directory, so neither mode
leaves anything behind in the tree.
"""

import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TARGET = os.path.join(ROOT, 'vo-patch.py')

sys.path.insert(0, HERE)
import dialogs                                            # noqa: E402
import layout                                             # noqa: E402
import padtables                                          # noqa: E402

# debugbox.asm is one run assembled at 0x5f4e7c; the dialog procedure inside
# it is pinned at 0x5f4ed8, one byte further on than the hook ends.
DEBUGBOX_SPLIT = 0x5f4ed8 - 0x5f4e7c - 1

# Virtual address minus file offset. Every section this project writes into
# shares one delta except .rsrc, which sits further along in the file.
VA_DELTA = 0x400c00
VA_DELTA_RSRC = 0x305c400

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


def includes(tmp):
    """Write the .inc files the sources include, into nasm's include path.

    Each is emitted by the module that owns the addresses in it, so an
    address cannot be named one thing by the assembly and another by the
    blob it points into."""
    for name, text in (('strings.inc', layout.build()[0]),
                       ('padtables.inc', padtables.build()[0]),
                       ('dialogs.inc', dialogs.build_extras()[0])):
        with open(os.path.join(tmp, name), 'w') as fh:
            fh.write(text)


def assemble(source, tmp):
    """nasm -f bin, against the .inc files written by includes()."""
    if not shutil.which('nasm'):
        raise SystemExit('nasm not found. Install it: dnf install nasm, '
                         'apt install nasm.')
    args = ['nasm', '-f', 'bin', '-I', tmp + os.sep]
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


def blob_sites(names):
    """Blob name -> the offset in vo-patch.py's table that writes it.

    Read out of the patch table rather than repeated here, so each site is
    written down once. A blob is matched by its own hex, which is what the
    `new` column of its site holds."""
    spec = importlib.util.spec_from_file_location('vopatch', TARGET)
    vp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(vp)                 # runs _check_table

    at = {}
    for name in names:
        want = getattr(vp, name).hex()
        hits = [off for _k, _l, _t, sites in vp.FEATURES
                for off, _old, new in sites or () if new == want]
        if len(hits) != 1:
            raise SystemExit('%s is written at %d sites in vo-patch.py, and '
                             'this check needs exactly one' % (name, len(hits)))
        at[name] = hits[0]
    return at


def check_org(at, wanted, padding=()):
    """Sources assembled at a fixed org, where the source and the site that
    writes it have to name the same address. Nothing downstream would
    notice: the code would be written, and every jump in it would land a
    few hundred bytes off.

    `padding` names the sources whose cave is section padding rather than a
    run of zeros cut out of live data, where the four-alignment rule below
    has nothing to protect."""
    for name, (blob, delta) in wanted.items():
        site = at[blob]
        with open(os.path.join(HERE, name), encoding='utf-8') as fh:
            org = int(re.search(r'(?m)^org\s+(0x[0-9a-f]+)', fh.read()).group(1), 16)
        if site % 4 and name not in padding:
            raise SystemExit('%s is written at 0x%08x, which is not a multiple '
                             'of four. A run of zeros starting off a dword '
                             'boundary starts inside the last field before it.'
                             % (name, site))
        if org != site + delta:
            raise SystemExit('%s is assembled at 0x%08x but its site puts it '
                             'at 0x%08x' % (name, org, site + delta))


def check_follows(at, name, after, length):
    """A blob written straight after another one, with no org of its own.

    levers.asm replaces the tail of the XInput routine, and the jump into it
    is a distance nasm worked out from the routine's own layout. So its site
    is not free: it is the routine's site plus the routine's length, and a
    site a few bytes off would leave that jump pointing into padding."""
    if at[name] != at[after] + length:
        raise SystemExit('%s is written at 0x%08x but %s ends at 0x%08x'
                         % (name, at[name], after, at[after] + length))


def check_addr(at, wanted):
    """The same check for the addresses the .py packers hardcode.

    They name these places as virtual addresses, because the assembly reads
    them or the pointers inside the blobs point at them; the patch table
    names the same places as file offsets. Nothing else compares the two, and
    a blob written a few bytes off is a table every pointer misses."""
    for name, (va, blob, delta) in wanted.items():
        if va != at[blob] + delta:
            raise SystemExit('%s is 0x%08x but %s is written at 0x%08x, which '
                             'is 0x%08x' % (name, va, blob, at[blob],
                                            at[blob] + delta))


def main(check=False):
    with tempfile.TemporaryDirectory() as tmp:
        includes(tmp)
        code = assemble('vocd.asm', tmp)
        timer = assemble('timer.asm', tmp)
        dbgbox = assemble('debugbox.asm', tmp)
        padx = assemble('padxinput.asm', tmp)
        levers = assemble('levers.asm', tmp)
        twin = assemble('twinstick.asm', tmp)
        introwait = assemble('introwait.asm', tmp)
        kbpage = assemble('kbpage.asm', tmp)
        movie = assemble('movie.asm', tmp)
        credits = assemble('credits.asm', tmp)
        nameentry = assemble('nameentry.asm', tmp)
        camskip = assemble('camskip.asm', tmp)
    _inc, data = layout.build()
    _inc, cond, pbinds, pnames, devlist = padtables.build()
    _inc, extras_tpl, extras_data = dialogs.build_extras()

    # One run in the source, two sites in the patcher. The byte between them
    # is padding in front of the dialog procedure that nothing writes.
    if dbgbox[DEBUGBOX_SPLIT]:
        raise SystemExit('debugbox.asm puts %#04x at 0x1f42d7, which the '
                         'patch does not write' % dbgbox[DEBUGBOX_SPLIT])
    dbghook = dbgbox[:DEBUGBOX_SPLIT]
    dbgproc = dbgbox[DEBUGBOX_SPLIT + 1:]

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
    new = replace(new, 'PADX', hexblob('PADX_CODE', padx))
    new = replace(new, 'LEVERS', hexblob('LEVERS_CODE', levers))
    new = replace(new, 'TWIN', hexblob('TWIN_CODE', twin))
    new = replace(new, 'INTROWAIT', hexblob('INTROWAIT_CODE', introwait))
    new = replace(new, 'KBPAGE', hexblob('KBPAGE_CODE', kbpage))
    new = replace(new, 'MOVIE', hexblob('MOVIE_CODE', movie))
    new = replace(new, 'CREDITS', hexblob('CREDITS_CODE', credits))
    new = replace(new, 'NAMEENTRY', hexblob('NAMEENTRY_CODE', nameentry))
    new = replace(new, 'CAMSKIP', hexblob('CAMSKIP_CODE', camskip))
    new = replace(new, 'TIMER', hexblob('TIMER_CODE', timer))
    new = replace(new, 'PADTABLES',
                  hexblob('PAD_COND', cond) + '\n'
                  + hexblob('PAD_BINDS', pbinds) + '\n'
                  + hexblob('PAD_NAMES', pnames) + '\n'
                  + hexblob('PAD_DEVLIST', devlist))
    new = replace(new, 'DIALOGS',
                  hexblob('EXTRAS_TPL', extras_tpl) + '\n'
                  + hexblob('EXTRAS_DATA', extras_data) + '\n'
                  + hexblob('F5_STOCK', dialogs.build_f5(dialogs.F5_STOCK))
                  + '\n'
                  + hexblob('F5_FPS', dialogs.build_f5(dialogs.F5_NEW)))
    new = replace(new, 'DEBUGBOX', hexblob('DEBUGBOX_HOOK', dbghook) + '\n'
                  + hexblob('DEBUGBOX_PROC', dbgproc))
    at = blob_sites(('TIMER_CODE', 'DEBUGBOX_HOOK', 'PADX_CODE', 'LEVERS_CODE',
                     'TWIN_CODE', 'INTROWAIT_CODE', 'KBPAGE_CODE',
                     'MOVIE_CODE', 'CREDITS_CODE', 'NAMEENTRY_CODE',
                     'CAMSKIP_CODE',
                     'PAD_COND', 'PAD_BINDS', 'PAD_NAMES', 'EXTRAS_TPL',
                     'EXTRAS_DATA'))
    check_org(at, {'timer.asm': ('TIMER_CODE', VA_DELTA),
                   'debugbox.asm': ('DEBUGBOX_HOOK', VA_DELTA),
                   'padxinput.asm': ('PADX_CODE', VA_DELTA),
                   'twinstick.asm': ('TWIN_CODE', VA_DELTA),
                   'introwait.asm': ('INTROWAIT_CODE', VA_DELTA),
                   'kbpage.asm': ('KBPAGE_CODE', VA_DELTA),
                   'movie.asm': ('MOVIE_CODE', VA_DELTA_RSRC),
                   'credits.asm': ('CREDITS_CODE', VA_DELTA),
                   'nameentry.asm': ('NAMEENTRY_CODE', VA_DELTA),
                   'camskip.asm': ('CAMSKIP_CODE', VA_DELTA)},
              # The .text and .rsrc caves are padding past VirtualSize, so
              # there is no field in front of them for an unaligned start
              # to land in.
              padding=('timer.asm', 'debugbox.asm', 'movie.asm',
                 'credits.asm'))
    check_follows(at, 'LEVERS_CODE', 'PADX_CODE', len(padx))
    check_addr(at, {
        'padtables.COND': (padtables.COND, 'PAD_COND', VA_DELTA),
        'padtables.BINDS': (padtables.BINDS, 'PAD_BINDS', VA_DELTA),
        'padtables.NAMES': (padtables.NAMES, 'PAD_NAMES', VA_DELTA),
        'dialogs.DATA': (dialogs.DATA, 'EXTRAS_DATA', VA_DELTA),
        'dialogs.TEMPLATE': (dialogs.TEMPLATE, 'EXTRAS_TPL', VA_DELTA_RSRC),
    })

    sizes = ('vocd %d + %d bytes, timer %d, debugbox %d, padxinput %d, '
             'levers %d, twinstick %d, introwait %d, kbpage %d, movie %d, '
             'credits %d, nameentry %d, camskip %d, '
             'tables %d, dialogs %d'
             % (len(code), len(data), len(timer), len(dbgbox), len(padx),
                len(levers), len(twin), len(introwait), len(kbpage),
                len(movie), len(credits), len(nameentry), len(camskip),
                len(cond) + len(pbinds) + len(pnames) + len(devlist),
                len(extras_tpl) + len(extras_data) + 2 * dialogs.F5_LEN))
    if check:
        if new != src:
            raise SystemExit('asm/ does not match the blobs in '
                             'vo-patch.py.\nRun: python3 asm/build.py')
        print('asm/ matches vo-patch.py (%s)' % sizes)
    else:
        with open(TARGET, 'w', encoding='utf-8') as fh:
            fh.write(new)
        print('written to vo-patch.py (%s)' % sizes)

    # The tables are what actually gets written to somebody's executable, so
    # never report success without running their checks too.
    subprocess.check_call([sys.executable, TARGET, '--selfcheck'])


if __name__ == '__main__':
    main('--check' in sys.argv)
