#!/usr/bin/env python3
"""Build the blobs in ../vo-patch.py, or check that they still match.

Machine code comes from the .asm files through nasm; the tables and dialog
templates come from the .py modules beside them, which pack them from a
readable description.

    sudo dnf install nasm      # or: sudo apt install nasm
    python3 asm/build.py            # assemble and write
    python3 asm/build.py --check    # verify only, writes nothing

Besides matching the blobs, the check pass validates the hand-computed
parts of the site table: each source's org against the site that writes it
(check_org), blob growth against pinned ceilings (check_ceilings), and
every call or jump a site writes whose target lands inside a cave against
the assembled labels (check_calls) - a slipped rel32 is caught here rather
than in the game.

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
    """nasm -f bin, against the .inc files written by includes(). Label
    offsets from the listing are collected into LABELS for check_calls."""
    if not shutil.which('nasm'):
        raise SystemExit('nasm not found. Install it: dnf install nasm, '
                         'apt install nasm.')
    args = ['nasm', '-f', 'bin', '-I', tmp + os.sep]
    out = os.path.join(tmp, os.path.basename(source) + '.bin')
    lst = out + '.lst'
    args += ['-o', out, '-l', lst, os.path.join(HERE, source)]
    subprocess.check_call(args)
    org = None
    with open(os.path.join(HERE, source), encoding='utf-8') as fh:
        m = re.search(r'(?m)^org\s+(0x[0-9a-f]+)', fh.read())
        org = int(m.group(1), 16) if m else None
    if org is not None:
        offset = None
        with open(lst, encoding='utf-8') as fh:
            for line in fh:
                m = re.match(r'\s*\d+ ([0-9A-F]{8}) ', line)
                if m:
                    offset = int(m.group(1), 16)
                m = re.match(r'\s*\d+\s+([a-z_][a-z0-9_]*):\s*(?:;.*)?$', line)
                if m:
                    # a label line carries no address; the next coded line
                    # does, so remember the name until one arrives
                    LABELS.setdefault(source, {})[m.group(1)] = None
                elif offset is not None:
                    for name, at in LABELS.get(source, {}).items():
                        if at is None:
                            LABELS[source][name] = org + offset
        LABELS.setdefault(source, {})
        ORGS[source] = org
    with open(out, 'rb') as fh:
        return fh.read()


LABELS = {}     # source -> {label: VA}, filled by assemble()
ORGS = {}       # source -> org VA


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


# A cave's last usable byte, for the ones where something live sits close
# enough behind them to matter. The zeros run past these; that is the point.
# `selftest.py` finds them against a real v_on.exe, this pins what it found
# so a blob cannot grow into one without CI saying so.
CEILINGS = {
    'DEBUGBOX_PROC': (0x005f5000, 'a qword 0.0 that 0x401ce4 compares '
                                  'depth against'),
    'OVERLAY_CODE': (0x005f8188, 'a live address 18 sites point at'),
    'TITLEVER_CODE': (0x00623e40, 'a qword 480.0 that 18 sites load'),
}


def check_ceilings(at, blobs):
    """Blobs whose cave ends before its run of zeros does.

    A blob that outgrows its cave writes onto whatever follows it, and if
    that is zeroed data every other check in the project passes. The F11
    dialog procedure did this in v0.8.5: two bytes over the end, onto a
    constant the game reads."""
    for name, (limit, what) in CEILINGS.items():
        end = at[name] + len(blobs[name]) + VA_DELTA
        if end > limit:
            raise SystemExit('%s ends at 0x%08x, %d bytes past 0x%08x, where '
                             'the cave stops: %s'
                             % (name, end, end - limit, limit, what))


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


def check_calls(blobs):
    """Every call or jump a site writes whose target lands inside one of
    the caves must land exactly on an assembled label. The rel32s in the
    site table are computed by hand, and a slip lands mid-cave or in
    unrelated data with nothing else to notice - the class of mistake this
    exists to catch. Targets outside every cave are the executable's own
    code and not judged here."""
    ranges = [(ORGS[src], ORGS[src] + len(blob), src)
              for src, blob in blobs.items() if src in ORGS]
    labels = set(ORGS.values())         # the org is always an entry point
    for per in LABELS.values():
        labels.update(va for va in per.values() if va is not None)
    with open(TARGET, encoding='utf-8') as fh:
        text = fh.read()
    for m in re.finditer(r"\(0x([0-9a-f]{8}),\s*'[0-9a-f]*',\s*"
                         r"'((?:e8|e9)[0-9a-f]{8})[0-9a-f]*'\)", text):
        off = int(m.group(1), 16)
        va = off + (0x400c00 if off < 0x23de00 else 0x401200)
        rel = int.from_bytes(bytes.fromhex(m.group(2)[2:]), 'little', signed=True)
        target = (va + 5 + rel) & 0xffffffff
        for lo, hi, src in ranges:
            if lo <= target < hi and target not in labels:
                raise SystemExit(
                    'the site at 0x%08x calls 0x%08x, inside %s but on no '
                    'label - a hand-computed rel32 gone wrong'
                    % (off, target, src))


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
        bindlist = assemble('bindlist.asm', tmp)
        bindmap = assemble('bindmap.asm', tmp)
        bindblock = assemble('bindblock.asm', tmp)
        inisave = assemble('inisave.asm', tmp)
        iniload = assemble('iniload.asm', tmp)
        blockcur = assemble('blockcur.asm', tmp)
        iniparse = assemble('iniparse.asm', tmp)
        pagesec = assemble('pagesec.asm', tmp)
        pagesel = assemble('pagesel.asm', tmp)
        commitdev = assemble('commitdev.asm', tmp)
        iniall = assemble('iniall.asm', tmp)
        devorder = assemble('devorder.asm', tmp)
        f11pause = assemble('f11pause.asm', tmp)
        movie = assemble('movie.asm', tmp)
        credits = assemble('credits.asm', tmp)
        nameentry = assemble('nameentry.asm', tmp)
        camskip = assemble('camskip.asm', tmp)
        overlay = assemble('overlay.asm', tmp)
        titlever = assemble('titlever.asm', tmp)
    _inc, data = layout.build()
    (_inc, cond, pbinds, pnames, devlist, sdef,
     inikeys) = padtables.build()
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
    new = replace(new, 'BINDLIST', hexblob('BINDLIST_CODE', bindlist))
    new = replace(new, 'BINDMAP', hexblob('BINDMAP_CODE', bindmap))
    new = replace(new, 'BINDBLOCK', hexblob('BINDBLOCK_CODE', bindblock))
    new = replace(new, 'INISAVE', hexblob('INISAVE_CODE', inisave))
    new = replace(new, 'INILOAD', hexblob('INILOAD_CODE', iniload))
    new = replace(new, 'BLOCKCUR', hexblob('BLOCKCUR_CODE', blockcur))
    new = replace(new, 'INIPARSE', hexblob('INIPARSE_CODE', iniparse))
    new = replace(new, 'PAGESEC', hexblob('PAGESEC_CODE', pagesec))
    new = replace(new, 'PAGESEL', hexblob('PAGESEL_CODE', pagesel))
    new = replace(new, 'COMMITDEV', hexblob('COMMITDEV_CODE', commitdev))
    new = replace(new, 'INIALL', hexblob('INIALL_CODE', iniall))
    new = replace(new, 'DEVORDER', hexblob('DEVORDER_CODE', devorder))
    new = replace(new, 'F11PAUSE', hexblob('F11PAUSE_CODE', f11pause))
    new = replace(new, 'MOVIE', hexblob('MOVIE_CODE', movie))
    new = replace(new, 'CREDITS', hexblob('CREDITS_CODE', credits))
    new = replace(new, 'NAMEENTRY', hexblob('NAMEENTRY_CODE', nameentry))
    new = replace(new, 'CAMSKIP', hexblob('CAMSKIP_CODE', camskip))
    new = replace(new, 'OVERLAY', hexblob('OVERLAY_CODE', overlay))
    new = replace(new, 'TITLEVER', hexblob('TITLEVER_CODE', titlever))
    new = replace(new, 'TIMER', hexblob('TIMER_CODE', timer))
    new = replace(new, 'PADTABLES',
                  hexblob('PAD_COND', cond) + '\n'
                  + hexblob('PAD_BINDS', pbinds) + '\n'
                  + hexblob('PAD_NAMES', pnames) + '\n'
                  + hexblob('PAD_DEVLIST', devlist) + '\n'
                  + hexblob('PAD_SIMPLEDEF', sdef) + '\n'
                  + hexblob('PAD_INIKEYS', inikeys))
    new = replace(new, 'DIALOGS',
                  hexblob('EXTRAS_TPL', extras_tpl) + '\n'
                  + hexblob('EXTRAS_DATA', extras_data) + '\n'
                  + hexblob('F5_STOCK', dialogs.build_f5(dialogs.F5_STOCK))
                  + '\n'
                  + hexblob('F5_FPS', dialogs.build_f5(dialogs.F5_NEW)))
    new = replace(new, 'DEBUGBOX', hexblob('DEBUGBOX_HOOK', dbghook) + '\n'
                  + hexblob('DEBUGBOX_PROC', dbgproc))
    at = blob_sites(('TIMER_CODE', 'DEBUGBOX_HOOK', 'DEBUGBOX_PROC',
                     'PADX_CODE', 'LEVERS_CODE',
                     'TWIN_CODE', 'INTROWAIT_CODE', 'KBPAGE_CODE',
                     'MOVIE_CODE', 'CREDITS_CODE', 'NAMEENTRY_CODE',
                     'CAMSKIP_CODE', 'OVERLAY_CODE', 'TITLEVER_CODE',
                     'BINDLIST_CODE', 'BINDMAP_CODE', 'BINDBLOCK_CODE',
                     'INISAVE_CODE', 'INILOAD_CODE', 'BLOCKCUR_CODE', 'INIPARSE_CODE', 'PAGESEC_CODE', 'PAGESEL_CODE', 'COMMITDEV_CODE', 'INIALL_CODE', 'DEVORDER_CODE', 'F11PAUSE_CODE', 'PAD_INIKEYS',
                     'PAD_COND', 'PAD_BINDS', 'PAD_NAMES', 'PAD_SIMPLEDEF',
                     'EXTRAS_TPL',
                     'EXTRAS_DATA'))
    check_ceilings(at, {'DEBUGBOX_PROC': dbgproc, 'OVERLAY_CODE': overlay,
                        'TITLEVER_CODE': titlever})
    check_org(at, {'timer.asm': ('TIMER_CODE', VA_DELTA),
                   'debugbox.asm': ('DEBUGBOX_HOOK', VA_DELTA),
                   'padxinput.asm': ('PADX_CODE', VA_DELTA),
                   'twinstick.asm': ('TWIN_CODE', VA_DELTA),
                   'introwait.asm': ('INTROWAIT_CODE', VA_DELTA),
                   'kbpage.asm': ('KBPAGE_CODE', VA_DELTA),
                   'bindlist.asm': ('BINDLIST_CODE', VA_DELTA),
                   'bindmap.asm': ('BINDMAP_CODE', VA_DELTA),
                   'bindblock.asm': ('BINDBLOCK_CODE', VA_DELTA),
                   'inisave.asm': ('INISAVE_CODE', VA_DELTA),
                   'iniload.asm': ('INILOAD_CODE', VA_DELTA),
                   'blockcur.asm': ('BLOCKCUR_CODE', VA_DELTA),
                   'iniparse.asm': ('INIPARSE_CODE', VA_DELTA),
                   'pagesec.asm': ('PAGESEC_CODE', VA_DELTA),
                   'pagesel.asm': ('PAGESEL_CODE', VA_DELTA),
                   'commitdev.asm': ('COMMITDEV_CODE', VA_DELTA),
                   'iniall.asm': ('INIALL_CODE', VA_DELTA),
                   'devorder.asm': ('DEVORDER_CODE', VA_DELTA),
                   'f11pause.asm': ('F11PAUSE_CODE', VA_DELTA),
                   'movie.asm': ('MOVIE_CODE', VA_DELTA_RSRC),
                   'credits.asm': ('CREDITS_CODE', VA_DELTA),
                   'nameentry.asm': ('NAMEENTRY_CODE', VA_DELTA),
                   'camskip.asm': ('CAMSKIP_CODE', VA_DELTA),
                   'overlay.asm': ('OVERLAY_CODE', VA_DELTA),
                   'titlever.asm': ('TITLEVER_CODE', VA_DELTA)},
              # The .text and .rsrc caves are padding past VirtualSize, so
              # there is no field in front of them for an unaligned start
              # to land in.
              padding=('timer.asm', 'debugbox.asm', 'movie.asm'))
    check_follows(at, 'LEVERS_CODE', 'PADX_CODE', len(padx))
    check_addr(at, {
        'padtables.COND': (padtables.COND, 'PAD_COND', VA_DELTA),
        'padtables.BINDS': (padtables.BINDS, 'PAD_BINDS', VA_DELTA),
        'padtables.NAMES': (padtables.NAMES, 'PAD_NAMES', VA_DELTA),
        'padtables.SIMPLEDEF_AT':
            (padtables.SIMPLEDEF_AT, 'PAD_SIMPLEDEF', VA_DELTA),
        'padtables.INIKEYS_AT':
            (padtables.INIKEYS_AT, 'PAD_INIKEYS', VA_DELTA),
        'dialogs.DATA': (dialogs.DATA, 'EXTRAS_DATA', VA_DELTA),
        'dialogs.TEMPLATE': (dialogs.TEMPLATE, 'EXTRAS_TPL', VA_DELTA_RSRC),
    })
    check_calls({'debugbox.asm': dbgbox, 'padxinput.asm': padx,
                 'levers.asm': levers, 'twinstick.asm': twin,
                 'introwait.asm': introwait, 'kbpage.asm': kbpage,
                 'bindlist.asm': bindlist, 'bindmap.asm': bindmap,
                 'bindblock.asm': bindblock, 'inisave.asm': inisave,
                 'iniload.asm': iniload, 'blockcur.asm': blockcur,
                 'iniparse.asm': iniparse, 'pagesec.asm': pagesec,
                 'pagesel.asm': pagesel, 'commitdev.asm': commitdev,
                 'iniall.asm': iniall, 'devorder.asm': devorder,
                 'f11pause.asm': f11pause, 'movie.asm': movie,
                 'credits.asm': credits, 'nameentry.asm': nameentry,
                 'camskip.asm': camskip, 'overlay.asm': overlay,
                 'titlever.asm': titlever, 'timer.asm': timer,
                 'vocd.asm': code})

    sizes = ('vocd %d + %d bytes, timer %d, debugbox %d, padxinput %d, '
             'levers %d, twinstick %d, introwait %d, kbpage %d, movie %d, '
             'credits %d, nameentry %d, camskip %d, overlay %d, '
             'titlever %d, tables %d, dialogs %d'
             % (len(code), len(data), len(timer), len(dbgbox), len(padx),
                len(levers), len(twin), len(introwait), len(kbpage),
                len(movie), len(credits), len(nameentry), len(camskip),
                len(overlay), len(titlever),
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
