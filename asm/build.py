#!/usr/bin/env python3
"""Build the blobs in ../vo_patch.py, or check that they still match.

Machine code comes from the .asm files through nasm; the tables and dialog
templates come from the .py modules beside them, which pack them from a
readable description.

    sudo dnf install nasm      # or: sudo apt install nasm
    python3 asm/build.py            # assemble and write
    python3 asm/build.py --check    # verify only, writes nothing

The sources name no addresses. Everything in the game they touch is an
`extern`, and nasm assembles them as ELF objects whose relocations say where
each address goes and how (absolute, or relative to the instruction). What
lands in vo_patch.py is the blob with those slots empty, the fixup list, and
the offsets of its labels; vo_patch.link() fills the slots for a build from
that build's CAVES and SYMBOLS tables. So one set of machine code serves every
build, and the retail addresses live in one table in the patcher rather than
in twenty-eight files here.

Besides matching the blobs, the check pass links every blob for the retail
build and checks blob growth against pinned ceilings (check_ceilings). It
runs vo_patch.py --selfcheck afterwards, which is where the site table is
validated.

vo_patch.py carries the assembled bytes because it ships as a single file that
has to run from a fresh checkout with nothing installed. So this writes them
in when the assembly changes, and --check catches assembly edited without them
being regenerated.

Everything nasm needs is built in a temporary directory, so neither mode
leaves anything behind in the tree.
"""

import importlib.util
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TARGET = os.path.join(ROOT, 'vo_patch.py')

sys.path.insert(0, HERE)
import dialogs                                            # noqa: E402
import layout                                             # noqa: E402
import padtables                                          # noqa: E402

# debugbox.asm is one run; the dialog procedure inside it is pinned 0x5c
# bytes in, one byte further on than the hook ends. vo_patch.py splits the
# linked blob there (DEBUGBOX_SPLIT), so the two halves must be checked as
# one here.
DEBUGBOX_SPLIT = 0x5c - 1

MAGICS = [
    ('MAGIC_ORIGENTRY', 0xE1E1E1E1, 'VA of the entry point we chain to'),
    ('MAGIC_IATMCI',    0xE2E2E2E2, 'VA of the mciSendCommandA IAT slot'),
    ('MAGIC_LOADLIB',   0xE3E3E3E3, 'VA of the LoadLibraryA IAT slot'),
    ('MAGIC_GETPROC',   0xE4E4E4E4, 'VA of the GetProcAddress IAT slot'),
    ('MAGIC_DATA',      0xE5E5E5E5, 'VA the data blob lands at'),
]


def hexblob(name, raw, indent='    '):
    out = ['%s = bytes.fromhex(\n' % name]
    text = raw.hex()
    for i in range(0, len(text), 64):
        out.append("%s'%s'\n" % (indent, text[i:i + 64]))
    out.append(')\n')
    return ''.join(out)


def includes(tmp):
    """Write the .inc files the sources include, into nasm's include path.

    Each is emitted by the module that owns the labels in it, so a table's
    name in the assembly and in the blob it points into cannot drift."""
    for name, text in (('strings.inc', layout.build()[0]),
                       ('padtables.inc', padtables.build()[0]),
                       ('dialogs.inc', dialogs.build_extras()[0])):
        with open(os.path.join(tmp, name), 'w') as fh:
            fh.write(text)


def read_obj(path):
    """An ELF32 object from nasm -> (code, fixups, labels).

    fixups are (offset, kind, symbol, addend): kind 'abs' for a 32-bit
    absolute address, 'rel' for one relative to the end of the slot, 'abs8'
    for a byte - a frame offset written as [byte ebp + SYM], so a build can
    say where its caller keeps the local; symbol
    is the extern's name, or '.' for the blob's own base. labels are the
    source's labels and their offsets, which is how another blob or the
    site table names a place inside this one."""
    d = open(path, 'rb').read()
    shoff = struct.unpack_from('<I', d, 32)[0]
    shentsize, shnum, shstrndx = struct.unpack_from('<HHH', d, 46)
    secs = []
    for i in range(shnum):
        name, _typ, _flags, _addr, off, size, link, _info, _al, _es = \
            struct.unpack_from('<10I', d, shoff + i * shentsize)
        secs.append((name, off, size, link))

    def cstr(at):
        return d[at:d.index(b'\0', at)].decode()

    names = {cstr(secs[shstrndx][1] + n): i for i, (n, *_r) in enumerate(secs)}
    text = names['.text']
    _n, off, size, _l = secs[text]
    code = d[off:off + size]
    _n, soff, ssize, slink = secs[names['.symtab']]
    stroff = secs[slink][1]
    syms = []
    for i in range(ssize // 16):
        n, value, _sz, info, _other, shndx = struct.unpack_from(
            '<IIIBBH', d, soff + 16 * i)
        syms.append((cstr(stroff + n), value, info & 0xf, shndx))
    fixups = []
    if '.rel.text' in names:
        _n, roff, rsize, _l = secs[names['.rel.text']]
        for i in range(rsize // 8):
            at, info = struct.unpack_from('<II', d, roff + 8 * i)
            name, _v, typ, shndx = syms[info >> 8]
            kind = {1: 'abs', 2: 'rel', 22: 'abs8'}[info & 0xff]
            if typ == 3:                # STT_SECTION: the blob itself
                name = '.'
            if kind == 'abs8':
                addend = struct.unpack_from('<b', code, at)[0]
            else:
                addend = struct.unpack_from('<i', code, at)[0]
            fixups.append((at, kind, name, addend))
    labels = {name: value for name, value, typ, shndx in syms
              if name and shndx == text and typ != 3 and '.' not in name}
    return code, fixups, labels


def assemble(source, tmp):
    """nasm -f elf32, against the .inc files written by includes()."""
    if not shutil.which('nasm'):
        raise SystemExit('nasm not found. Install it: dnf install nasm, '
                         'apt install nasm.')
    out = os.path.join(tmp, os.path.basename(source) + '.o')
    subprocess.check_call(['nasm', '-f', 'elf32', '-I', tmp + os.sep,
                           '-o', out, os.path.join(HERE, source)])
    return read_obj(out)


def replace(text, name, body):
    """Swap the contents of one # NAME BLOB BEGIN/END pair."""
    new, n = re.subn(r'(# %s BLOB BEGIN\n).*?(# %s BLOB END)' % (name, name),
                     lambda m: m.group(1) + body + m.group(2),
                     text, flags=re.S)
    if n != 1:
        raise SystemExit('%s BLOB markers not found in vo_patch.py' % name)
    return new


def emit_blobs(blobs):
    """The BLOBS table: name -> (code, fixups, labels)."""
    out = ['BLOBS = {\n']
    for name, (code, fixups, labels) in blobs.items():
        out.append("    '%s': (bytes.fromhex(\n" % name)
        text = code.hex()
        for i in range(0, len(text), 64):
            out.append("        '%s'\n" % text[i:i + 64])
        out.append('    ), (\n')
        for at, kind, sym, addend in fixups:
            out.append('        (0x%x, %r, %r, %d),\n' % (at, kind, sym, addend))
        out.append('    ), {\n')
        for label, at in sorted(labels.items(), key=lambda kv: kv[1]):
            out.append("        '%s': 0x%x,\n" % (label, at))
        out.append('    }),\n')
    out.append('}\n')
    return ''.join(out)


# A cave's last usable byte, for the ones where something live sits close
# enough behind them to matter. The zeros run past these; that is the point.
# `selftest.py` finds them against a real v_on.exe, this pins what it found
# so a blob cannot grow into one without CI saying so.
CEILINGS = {
    'DEBUGBOX': (0x005f5000, 'a qword 0.0 that 0x401ce4 compares '
                             'depth against'),
    'INISAVE': (0x00601c98, 'a live address 0x4cf61b reads, inside '
                            'what scans as a longer run'),
    'INIALL': (0x0063c64c, 'the end of its run; the deadzone seed '
                           'took most of the slack'),
    'F11PAUSE': (0x0063bf7c, 'the end of its run; the check-box loop '
                             'took nearly all of it'),
    'INIPARSE': (0x00601b6c, 'the end of its run; the deadzone '
                             'write-back sits in its tail'),
    'OVERLAY': (0x005f8188, 'a live address 18 sites point at'),
    'TITLEVER': (0x00623e40, 'a qword 480.0 that 18 sites load'),
}


def check_ceilings(vp, blobs):
    """Blobs whose cave ends before its run of zeros does.

    A blob that outgrows its cave writes onto whatever follows it, and if
    that is zeroed data every other check in the project passes. The F11
    dialog procedure did this in v0.8.5: two bytes over the end, onto a
    constant the game reads."""
    for name, (limit, what) in CEILINGS.items():
        end = vp.cave_va(name, vp.RETAIL) + len(blobs[name][0])
        if end > limit:
            raise SystemExit('%s ends at 0x%08x, %d bytes past 0x%08x, where '
                             'the cave stops: %s'
                             % (name, end, end - limit, limit, what))


def check_link(vp, blobs):
    """Every blob links for every build, and every pin the site table
    relies on is where the assembly put it. A blob whose cave only exists
    at apply time is linked there instead, so a KeyError on a missing cave
    is expected; a missing symbol is not."""
    for build in vp.BUILDS.values():
        for name in blobs:
            try:
                vp.link(name, build, blobs=blobs)
            except ValueError:
                pass                    # its own cave is an apply-time one
            except KeyError as exc:
                if exc.args[0] not in vp.BLOBS:
                    raise SystemExit('%s does not resolve %s for build %s'
                                     % (name, exc, build.md5))
    padx = blobs['PADX'][0]
    if len(padx) != vp.PADX_LEN:
        raise SystemExit('padxinput.asm assembles to %d bytes, not %d'
                         % (len(padx), vp.PADX_LEN))
    dbg = blobs['DEBUGBOX'][0]
    if dbg[DEBUGBOX_SPLIT]:
        raise SystemExit('debugbox.asm puts %#04x at the byte between the '
                         'hook and the dialog procedure, which the patch '
                         'does not write' % dbg[DEBUGBOX_SPLIT])
    if blobs['DEBUGBOX'][2]['dlgproc'] != DEBUGBOX_SPLIT + 1:
        raise SystemExit('debugbox.asm has the dialog procedure at 0x%x, '
                         'not 0x%x' % (blobs['DEBUGBOX'][2]['dlgproc'],
                                       DEBUGBOX_SPLIT + 1))
    for blob, magic in (('F11PAUSE', dialogs.TEMPLATE),
                        ('DEBUGBOX', vp.MAGIC_ANNEXREL)):
        pattern = struct.pack('<I', magic)
        if blobs[blob][0].count(pattern) != 1:
            raise SystemExit('the 0x%08x placeholder should appear exactly '
                             'once in %s' % (magic, blob))
        for other, (code, _f, _l) in blobs.items():
            if other != blob and pattern in code:
                raise SystemExit('the 0x%08x placeholder occurs in %s'
                                 % (magic, other))


SOURCES = [
    ('TIMER', 'timer.asm'), ('DEBUGBOX', 'debugbox.asm'),
    ('PADX', 'padxinput.asm'), ('LEVERS', 'levers.asm'),
    ('TWIN', 'twinstick.asm'), ('INTROWAIT', 'introwait.asm'),
    ('KBPAGE', 'kbpage.asm'), ('BINDLIST', 'bindlist.asm'),
    ('BINDMAP', 'bindmap.asm'), ('BINDBLOCK', 'bindblock.asm'),
    ('INISAVE', 'inisave.asm'), ('INILOAD', 'iniload.asm'),
    ('BLOCKCUR', 'blockcur.asm'), ('INIPARSE', 'iniparse.asm'),
    ('PAGESEC', 'pagesec.asm'), ('PAGESEL', 'pagesel.asm'),
    ('COMMITDEV', 'commitdev.asm'), ('INIALL', 'iniall.asm'),
    ('DEVORDER', 'devorder.asm'), ('F11PAUSE', 'f11pause.asm'),
    ('VOXT', 'voxt.asm'), ('MOVIE', 'movie.asm'),
    ('CREDITS', 'credits.asm'), ('NAMEENTRY', 'nameentry.asm'),
    ('CAMSKIP', 'camskip.asm'), ('OVERLAY', 'overlay.asm'),
    ('TITLEVER', 'titlever.asm'),
]


def main(check=False):
    blobs = {}
    with tempfile.TemporaryDirectory() as tmp:
        includes(tmp)
        # vocd.asm keeps its magic placeholders: its section is appended at
        # apply time, so it has no cave to link against.
        vocd = assemble('vocd.asm', tmp)
        if vocd[1]:
            raise SystemExit('vocd.asm should have no relocations; it '
                             'names its addresses through MAGIC_ placeholders')
        for name, source in SOURCES:
            blobs[name] = assemble(source, tmp)
    _inc, data = layout.build()
    (_inc, blobs['PAD_COND'], blobs['PAD_BINDS'], blobs['PAD_NAMES'],
     blobs['PAD_DEVLIST'], blobs['PAD_SIMPLEDEF'],
     blobs['PAD_INIKEYS'], blobs['PAD_PROFILES']) = padtables.build()
    _inc, extras_tpl, blobs['EXTRAS_DATA'] = dialogs.build_extras()

    vocd_out = ['VOCD_MAGICS = {\n']
    for name, value, note in MAGICS:
        # Pad to a fixed column: the generated file is linted like any other.
        vocd_out.append('%-38s # %s\n'
                        % ("    '%s': 0x%08X," % (name, value), note))
    vocd_out.append('}\n\n')
    vocd_out.append(hexblob('VOCD_CODE', vocd[0]))
    vocd_out.append('\n')
    vocd_out.append(hexblob('VOCD_DATA', data))

    with open(TARGET, encoding='utf-8') as fh:
        src = fh.read()
    new = replace(src, 'VOCD', ''.join(vocd_out))
    new = replace(new, 'BLOBS', emit_blobs(blobs))
    new = replace(new, 'DIALOGS',
                  hexblob('EXTRAS_TPL', extras_tpl) + '\n'
                  + hexblob('F5_STOCK', dialogs.build_f5(dialogs.F5_STOCK))
                  + '\n'
                  + hexblob('F5_FPS', dialogs.build_f5(dialogs.F5_NEW)))

    # Import the patcher as it will be written, which links every blob for
    # the retail build on the way in.
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, 'vo_patch.py')
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write(new)
        spec = importlib.util.spec_from_file_location('vopatch', path)
        vp = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(vp)
    check_link(vp, blobs)
    check_ceilings(vp, blobs)

    sizes = ', '.join('%s %d' % (name.lower(), len(code))
                      for name, (code, _f, _l) in blobs.items()
                      if not name.startswith('PAD_') and name != 'EXTRAS_DATA')
    sizes = 'vocd %d + %d bytes, %s, tables %d, dialogs %d' % (
        len(vocd[0]), len(data), sizes,
        sum(len(blobs[n][0]) for n in ('PAD_COND', 'PAD_BINDS', 'PAD_NAMES',
                                       'PAD_DEVLIST')),
        len(extras_tpl) + len(blobs['EXTRAS_DATA'][0]) + 2 * dialogs.F5_LEN)
    if check:
        if new != src:
            raise SystemExit('asm/ does not match the blobs in '
                             'vo_patch.py.\nRun: python3 asm/build.py')
        print('asm/ matches vo_patch.py (%s)' % sizes)
    else:
        with open(TARGET, 'w', encoding='utf-8') as fh:
            fh.write(new)
        print('written to vo_patch.py (%s)' % sizes)

    # The tables are what actually gets written to somebody's executable, so
    # never report success without running their checks too.
    subprocess.check_call([sys.executable, TARGET, '--selfcheck'])


if __name__ == '__main__':
    main('--check' in sys.argv)
