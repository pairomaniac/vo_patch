#!/usr/bin/env python3
"""Read a disc image the way the patcher does, on discs built here.

    python3 tools/disctest.py

Needs no game and no disc: it writes small ISO9660 images itself, one per
sector layout, wraps them in a cue sheet and checks that what comes back out
is what went in. That covers the part of the installer that would otherwise
only be exercised by owning four pressings - the sector probe, the directory
walk, the ssp.ini rules, and the refusals.
"""

import contextlib
import hashlib
import importlib.util
import io
import os
import shutil
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LOGICAL = 2048
RAW = 2352

# The manifests that matter, cut down to the keys the patcher reads.
# Retail copies a language directory; the OEM and Japanese pressings have
# none and keep the help files in v_on/ instead.
RETAIL_SSP = b"""[option]
SourcePath1     = V_ON
IniFileName     = V_ON.INI
DefaultSection  = English
Select1         = SourceCopy, LangExeclusive
[ENGLISH]
LangExeclusive  = ENGLISH
[FRENCH]
LangExeclusive  = FRENCH
[RunTime]
DirectX         = Yes
"""

OEM_SSP = b"""[option]
SourcePath1     = V_ON
IniFileName     = V_ON.INI
DefaultSection  = English
Select1         = SourceCopy
[JAPANESE]
LangExeclusive  =
[ENGLISH]
LangExeclusive  =
"""

# The Ultra 2000 pressing: OEM shape, Japanese section first.
# The Japanese rerelease, trimmed from the disc's own ssp.ini. Both language
# sections are empty, so no language directory is copied and none is offered.
JP_SSP = b"""[option]
SourcePath1     = V_ON
IniFileName     = V_ON.INI
DefaultSection  = English
Select1         = SourceCopy
[JAPANESE]
LangExeclusive  =
[ENGLISH]
LangExeclusive  =
[RunTime]
DirectX         = Yes
"""


# ------------------------------------------------------- a disc, from scratch

def _both16(n):
    return struct.pack('<H', n) + struct.pack('>H', n)


def _both32(n):
    return struct.pack('<I', n) + struct.pack('>I', n)


def _record(name, lba, size, is_dir):
    raw = (name.encode('latin-1') if name in ('\x00', '\x01')
           else (name if is_dir else name + ';1').encode('latin-1'))
    length = 33 + len(raw)
    length += length % 2
    rec = bytearray(length)
    rec[0] = length
    rec[2:10] = _both32(lba)
    rec[10:18] = _both32(size)
    rec[25] = 0x02 if is_dir else 0
    rec[28:32] = _both16(1)
    rec[32] = len(raw)
    rec[33:33 + len(raw)] = raw
    return bytes(rec)


def _pack_dir(entries, self_lba, parent_lba):
    out = bytearray()
    out += _record('\x00', self_lba, LOGICAL, True)
    out += _record('\x01', parent_lba, LOGICAL, True)
    for name, lba, size, is_dir in entries:
        rec = _record(name, lba, size, is_dir)
        if len(out) % LOGICAL + len(rec) > LOGICAL:
            out += bytes(LOGICAL - len(out) % LOGICAL)
        out += rec
    if len(out) % LOGICAL:
        out += bytes(LOGICAL - len(out) % LOGICAL)
    return bytes(out)


def build_iso(tree):
    """{'name': bytes} or {'dir': {...}} -> a 2048-byte-sector image."""
    lba = 18                            # 0-15 blank, 16 PVD, 17 terminator
    root_lba = lba
    lba += 1
    dirs = {}
    for name, value in sorted(tree.items()):
        if isinstance(value, dict):
            dirs[name] = lba
            lba += 1

    files = []                          # (name, lba, data, parent or None)
    for name, value in sorted(tree.items()):
        children = sorted(value.items()) if isinstance(value, dict) \
            else [(name, value)]
        parent = name if isinstance(value, dict) else None
        for child, data in children:
            files.append((child, lba, data, parent))
            lba += max(1, (len(data) + LOGICAL - 1) // LOGICAL)

    image = bytearray(lba * LOGICAL)

    pvd = bytearray(LOGICAL)
    pvd[0] = 1
    pvd[1:6] = b'CD001'
    pvd[6] = 1
    pvd[80:88] = _both32(lba)
    pvd[128:132] = _both16(LOGICAL)
    pvd[156:190] = _record('\x00', root_lba, LOGICAL, True)
    image[16 * LOGICAL:17 * LOGICAL] = pvd

    term = bytearray(LOGICAL)
    term[0] = 255
    term[1:6] = b'CD001'
    term[6] = 1
    image[17 * LOGICAL:18 * LOGICAL] = term

    root = [(name.upper(), dirs[name], LOGICAL, True)
            for name, value in sorted(tree.items())
            if isinstance(value, dict)]
    root += [(name.upper(), at, len(data), False)
             for name, at, data, parent in files if parent is None]
    block = _pack_dir(root, root_lba, root_lba)
    image[root_lba * LOGICAL:root_lba * LOGICAL + len(block)] = block

    for name, at in dirs.items():
        block = _pack_dir([(child.upper(), where, len(data), False)
                           for child, where, data, parent in files
                           if parent == name], at, root_lba)
        image[at * LOGICAL:at * LOGICAL + len(block)] = block

    for _name, at, data, _parent in files:
        image[at * LOGICAL:at * LOGICAL + len(data)] = data
    return bytes(image)


def _msf(sectors):
    return '%02d:%02d:%02d' % (sectors // (75 * 60), sectors // 75 % 60,
                               sectors % 75)


def write_one_bin(directory, name, image, form, audio):
    """One bin holding every track, indices absolute. Returns cue and spans.

    What a whole-disc rip looks like: each audio track after the first sits
    behind a 150 sector pregap inside the same file, so a track ends at the
    next one's INDEX 00 rather than at EOF.
    """
    offset = {'MODE1/2352': 16, 'MODE2/2352': 24}[form]
    raw = bytearray()
    for at in range(0, len(image), LOGICAL):
        sector = bytearray(RAW)
        sector[0:12] = b'\x00' + b'\xff' * 10 + b'\x00'
        sector[offset:offset + LOGICAL] = image[at:at + LOGICAL]
        raw += sector

    track = '%s.bin' % name
    lines = ['FILE "%s" BINARY' % track, '  TRACK 01 %s' % form,
             '    INDEX 01 00:00:00']
    spans = []
    for number in range(2, 2 + audio):
        pregap = None
        if number > 2:                       # first audio track has none
            pregap = len(raw) // RAW
            raw += bytes(RAW * 150)
        start = len(raw) // RAW
        lines += ['FILE "%s" BINARY' % track, '  TRACK %02d AUDIO' % number]
        if pregap is not None:
            lines.append('    INDEX 00 %s' % _msf(pregap))
        lines.append('    INDEX 01 %s' % _msf(start))
        raw += bytes(RAW * 200)
        spans.append((number, start, start + 200))

    with open(os.path.join(directory, track), 'wb') as fh:
        fh.write(bytes(raw))
    cue = os.path.join(directory, '%s.cue' % name)
    with open(cue, 'w') as fh:
        fh.write('\n'.join(lines) + '\n')
    return cue, spans


def write_disc(directory, name, image, form, audio=0):
    """bin + cue on disk in one of the raw sector layouts. Returns the cue."""
    stride, offset = {'MODE1/2048': (2048, 0), 'MODE1/2352': (2352, 16),
                      'MODE2/2352': (2352, 24), 'MODE2/2336': (2336, 8)}[form]
    raw = bytearray()
    for at in range(0, len(image), LOGICAL):
        sector = bytearray(stride)
        if stride == 2352:
            # A real sync pattern, so a wrong offset finds these bytes
            # rather than accidentally landing on the descriptor.
            sector[0:12] = b'\x00' + b'\xff' * 10 + b'\x00'
        sector[offset:offset + LOGICAL] = image[at:at + LOGICAL]
        raw += sector

    track = '%s (Track 01).bin' % name
    with open(os.path.join(directory, track), 'wb') as fh:
        fh.write(bytes(raw))
    lines = ['FILE "%s" BINARY' % track, '  TRACK 01 %s' % form,
             '    INDEX 01 00:00:00']
    for number in range(2, 2 + audio):
        audio_bin = '%s (Track %02d).bin' % (name, number)
        with open(os.path.join(directory, audio_bin), 'wb') as fh:
            fh.write(bytes(2352 * 200))
        lines += ['FILE "%s" BINARY' % audio_bin,
                  '  TRACK %02d AUDIO' % number,
                  '    INDEX 00 00:00:00', '    INDEX 01 00:02:00']
    cue = os.path.join(directory, '%s.cue' % name)
    with open(cue, 'w') as fh:
        fh.write('\n'.join(lines) + '\n')
    return cue


# ------------------------------------------------------------------ the tests

FAILS = []


def check(label, condition, detail=''):
    print('%-4s %s%s' % ('ok' if condition else 'FAIL', label,
                         '' if condition else '   <- %s' % (detail,)))
    if not condition:
        FAILS.append(label)


def load_patcher():
    spec = importlib.util.spec_from_file_location(
        'vo_patch', os.path.join(ROOT, 'vo_patch.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def retail_tree(exe):
    return {
        'ssp.ini': RETAIL_SSP,
        'v_on': {'v_on.exe': exe,
                 'dpctrl.dll': b'D' * 300,
                 'v_on_a.ini': b'[Option]\nMotion=3\n',
                 'v_on_b.ini': b'[Option]\nMotion=2\n'},
        'english': {'von.hlp': b'H' * 4100, 'readme.txt': b'R' * 10},
        'french': {'von.hlp': b'F' * 4100, 'readme.txt': b'r' * 10},
        # Not part of the game, and the installer never copies it.
        'directx': {'ddraw.dll': b'X' * 100},
    }


def main():
    vp = load_patcher()
    # Stands in for the game: the right size, and its checksum is registered
    # as the supported one for the length of this run so the build test has
    # something to agree with.
    exe = bytes(vp.EXE_SIZE)
    original = vp.ORIGINAL_MD5
    vp.ORIGINAL_MD5 = hashlib.md5(exe).hexdigest()

    tmp = tempfile.mkdtemp(prefix='vo-disctest-')
    try:
        for form in ('MODE1/2352', 'MODE2/2352', 'MODE1/2048', 'MODE2/2336'):
            here = os.path.join(tmp, form.replace('/', '_'))
            os.makedirs(here)
            cue = write_disc(here, 'RETAIL', build_iso(retail_tree(exe)),
                             form, audio=3)
            info = vp.probe_disc(cue)
            check('%-10s sector layout found' % form, info['form'] == form,
                  info['form'])
            check('%-10s build recognised' % form, info['build']['supported'],
                  info['build'])
            check('%-10s languages listed' % form,
                  info['languages'] == ['ENGLISH', 'FRENCH'],
                  info['languages'])

        # A cue that names the wrong mode: the probe looks rather than trusts.
        here = os.path.join(tmp, 'mislabelled')
        os.makedirs(here)
        cue = write_disc(here, 'LIAR', build_iso(retail_tree(exe)),
                         'MODE2/2352')
        with open(cue) as fh:
            text = fh.read()
        with open(cue, 'w') as fh:
            fh.write(text.replace('MODE2/2352', 'MODE1/2352'))
        check('mislabelled cue read anyway',
              vp.probe_disc(cue)['form'] == 'MODE2/2352')

        # A full extraction, byte for byte.
        here = os.path.join(tmp, 'install')
        os.makedirs(here)
        cue = write_disc(here, 'RETAIL', build_iso(retail_tree(exe)),
                         'MODE1/2352', audio=2)
        dest = os.path.join(tmp, 'game')
        seen = []
        vp.install_disc(cue, dest, 'ENGLISH',
                        progress=lambda d, t: seen.append((d, t)))
        want = ['dpctrl.dll', 'readme.txt', 'v_on.exe', 'v_on_a.ini',
                'v_on_b.ini', 'von.hlp']
        check('installed file list', sorted(os.listdir(dest)) == want,
              sorted(os.listdir(dest)))
        with open(os.path.join(dest, 'v_on.exe'), 'rb') as fh:
            check('v_on.exe byte-exact', fh.read() == exe)
        with open(os.path.join(dest, 'von.hlp'), 'rb') as fh:
            check('language file byte-exact', fh.read() == b'H' * 4100)
        check('no v_on.ini is written',
              not os.path.exists(os.path.join(dest, 'v_on.ini')))
        check('directx left on the disc',
              'ddraw.dll' not in os.listdir(dest))
        check('progress ran to the end', seen and seen[-1][0] == seen[-1][1],
              seen[-1:])

        other = os.path.join(tmp, 'game-fr')
        vp.install_disc(cue, other, 'FRENCH')
        with open(os.path.join(other, 'von.hlp'), 'rb') as fh:
            check('a second language picks its own directory',
                  fh.read() == b'F' * 4100)

        # The OEM pressing: no language directories, help files in v_on/.
        here = os.path.join(tmp, 'oem')
        os.makedirs(here)
        oem = write_disc(here, 'OEM', build_iso({
            'ssp.ini': OEM_SSP,
            'v_on': {'v_on.exe': bytes(6649344), 'von.hlp': b'H' * 99,
                     'cpuid32.dll': b'C' * 50,
                     'v_on_a.ini': b'[Option]\n', 'v_on_b.ini': b'[Option]\n'},
        }), 'MODE1/2352')
        info = vp.probe_disc(oem)
        check('oem build refused', not info['build']['supported'])
        check('oem copies no language directory', not info['wants_language'])
        check('and offers no manual to choose', info['languages'] == [],
              info['languages'])
        dest = os.path.join(tmp, 'game-oem')
        vp.install_disc(oem, dest)
        check('oem file list',
              sorted(os.listdir(dest)) == ['cpuid32.dll', 'v_on.exe',
                                           'v_on_a.ini', 'v_on_b.ini',
                                           'von.hlp'],
              sorted(os.listdir(dest)))

        # The Ultra 2000 pressing: OEM shape with a Japanese section, and a
        # whole-disc rip rather than one bin per track.
        here = os.path.join(tmp, 'jp')
        os.makedirs(here)
        jp_exe = bytes(6621696)
        jp, spans = write_one_bin(here, 'JP', build_iso({
            'ssp.ini': JP_SSP,
            'v_on': {'v_on.exe': jp_exe, 'von.hlp': b'H' * 99,
                     'cpuid32.dll': b'C' * 50, 'jscrgame.bin': b'J' * 40,
                     'jscradv.bin': b'A' * 40, 'scrstfcg.bin': b'S' * 40,
                     'v_on_a.ini': b'[Option]\n', 'v_on_b.ini': b'[Option]\n'},
        }), 'MODE1/2352', audio=3)
        vp.OTHER_BUILDS[hashlib.md5(jp_exe).hexdigest()] = (
            len(jp_exe), 'Japanese rerelease', 'test')
        info = vp.probe_disc(jp)
        check('jp build not patchable', not info['build']['supported'])
        check('jp build named', info['build']['name'] == 'Japanese rerelease',
              info['build']['name'])
        check('jp copies no language directory', not info['wants_language'])
        dest = os.path.join(tmp, 'game-jp')
        vp.install_disc(jp, dest)
        check('jp file list',
              sorted(os.listdir(dest)) == ['cpuid32.dll', 'jscradv.bin',
                                           'jscrgame.bin', 'scrstfcg.bin',
                                           'v_on.exe', 'v_on_a.ini',
                                           'v_on_b.ini', 'von.hlp'],
              sorted(os.listdir(dest)))

        # --install says which build it is and copies it anyway. The copy is
        # the same work whichever build is on the disc; only the patches are
        # English-retail-only.
        cli_dest = os.path.join(tmp, 'game-jp-cli')
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            err = vp.install_cli([jp, cli_dest])
        check('--install copies another build', err in (None, 0), err)
        check('and names it first',
              'Japanese rerelease' in out.getvalue(), out.getvalue()[:120])
        check('--install file list',
              os.path.isdir(cli_dest) and len(os.listdir(cli_dest)) == 8,
              sorted(os.listdir(cli_dest)) if os.path.isdir(cli_dest) else '-')

        # One bin for the lot: a track ends at the next track's pregap, not
        # at the end of the file.
        got = [(t['no'], start, end)
               for t, start, end in vp._audio_spans(vp.parse_cue(jp))]
        check('one-bin cue rips to track bounds', got == spans, got)

        # Refusals, each naming what is wrong rather than failing bare.
        here = os.path.join(tmp, 'no_ssp')
        os.makedirs(here)
        bad = write_disc(here, 'X', build_iso({'readme.txt': b'hi'}),
                         'MODE1/2352')
        try:
            vp.probe_disc(bad)
            check('a disc with no ssp.ini refused', False, 'it was accepted')
        except vp.DiscError as exc:
            check('a disc with no ssp.ini refused', 'ssp.ini' in str(exc), exc)

        here = os.path.join(tmp, 'blank')
        os.makedirs(here)
        with open(os.path.join(here, 'B (Track 01).bin'), 'wb') as fh:
            fh.write(bytes(1 << 20))
        with open(os.path.join(here, 'B.cue'), 'w') as fh:
            fh.write('FILE "B (Track 01).bin" BINARY\n'
                     '  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n')
        try:
            vp.probe_disc(os.path.join(here, 'B.cue'))
            check('an image with no filesystem refused', False)
        except vp.DiscError as exc:
            check('an image with no filesystem refused',
                  'No filesystem' in str(exc), exc)

        here = os.path.join(tmp, 'audio')
        os.makedirs(here)
        with open(os.path.join(here, 'A (Track 01).bin'), 'wb') as fh:
            fh.write(bytes(2352 * 100))
        with open(os.path.join(here, 'A.cue'), 'w') as fh:
            fh.write('FILE "A (Track 01).bin" BINARY\n  TRACK 01 AUDIO\n'
                     '    INDEX 01 00:00:00\n')
        try:
            vp.probe_disc(os.path.join(here, 'A.cue'))
            check('an audio-only cue refused', False)
        except vp.DiscError as exc:
            check('an audio-only cue refused', 'only audio tracks' in str(exc),
                  exc)

        # What the ripper will and will not accept, told apart from what
        # the installer will: a cue for another game rips but does not
        # install, and a data-only image does neither.
        check('a device node is recognised, a cue sheet is not',
              vp.looks_like_drive('/dev/sr0')
              and not vp.looks_like_drive('game.cue')
              and not vp.looks_like_drive('D:'))
        here = os.path.join(tmp, 'othergame')
        os.makedirs(here)
        other = write_disc(here, 'OTHER',
                           build_iso({'readme.txt': b'not virtual-on'}),
                           'MODE1/2352', audio=9)
        check('another game has audio to rip',
              vp.audio_tracks(other) == list(range(2, 11)),
              vp.audio_tracks(other))
        try:
            vp.probe_disc(other)
            check('another game refused by the installer', False)
        except vp.DiscError:
            check('another game refused by the installer', True)
        check('its layout is not Virtual-On\'s',
              tuple(vp.audio_tracks(other)) != vp.VO_AUDIO)
        data_only = write_disc(os.path.join(tmp, 'install'), 'DATAONLY',
                               build_iso(retail_tree(exe)), 'MODE1/2352')
        check('a data-only image has nothing to rip',
              vp.audio_tracks(data_only) == [], vp.audio_tracks(data_only))
        check('a data-only image needs no room either',
              vp.rip_bytes(data_only) == 0, vp.rip_bytes(data_only))

        # The room the tracks will take, read off the sheet before anything
        # is written. write_disc gives each audio track its own 200-sector
        # file with INDEX 01 at 00:02:00, so 150 sectors of that is pregap
        # and only the rest is written out.
        sized = write_disc(os.path.join(tmp, 'othergame'), 'SIZED',
                           build_iso({'readme.txt': b'x'}), 'MODE1/2352',
                           audio=4)
        want = 4 * ((200 - 150) * 2352 + 44)
        check('the rip size comes off the cue sheet',
              vp.rip_bytes(sized) == want,
              '%d, wanted %d' % (vp.rip_bytes(sized), want))
        check('and is what the room check is asked about',
              vp.room_for(tmp, 1 << 60, 'the soundtrack').startswith(
                  'Not enough room for the soundtrack')
              and vp.room_for(tmp, 1) == '',
              vp.room_for(tmp, 1 << 60, 'the soundtrack'))
        check('a folder that is not there yet asks its parent',
              vp.room_for(os.path.join(tmp, 'not', 'yet'), 1 << 60) != '')

        # A language the ssp.ini names but the disc does not carry is not
        # offered: it would appear in the box and then fail on the copy.
        here = os.path.join(tmp, 'onemanual')
        os.makedirs(here)
        tree = retail_tree(exe)
        del tree['french']
        short = write_disc(here, 'SHORT', build_iso(tree), 'MODE1/2352')
        check('a language with no directory is not offered',
              vp.probe_disc(short)['languages'] == ['ENGLISH'],
              vp.probe_disc(short)['languages'])
        # And is refused rather than quietly copying no manual at all.
        try:
            vp.install_disc(short, os.path.join(tmp, 'game-none'), 'GERMAN')
            check('a language the disc lacks is refused', False,
                  'it installed without one')
        except vp.DiscError as exc:
            check('a language the disc lacks is refused',
                  'no GERMAN manual' in str(exc), exc)

        # Cue sheets as the tools that write them actually write them: the
        # sheet travels with the image and names it the way another machine
        # saw it.
        variants = os.path.join(tmp, 'cues')
        os.makedirs(variants)
        plain = write_disc(variants, 'VARIANT', build_iso(retail_tree(exe)),
                           'MODE1/2352')
        base = os.path.dirname(plain)
        sheet = open(plain).read()
        for label, text in (
                ('a CRLF sheet reads', sheet.replace('\n', '\r\n')),
                ('a sheet with a BOM reads', '\ufeff' + sheet),
                ('an unquoted FILE name reads',
                 '\n'.join(l.replace('"', '') if l.startswith('FILE') else l
                           for l in sheet.splitlines())),
                ('a wrong-case FILE name reads',
                 sheet.replace('.bin"', '.BIN"')),
                ('a stale absolute path reads',
                 sheet.replace('FILE "', 'FILE "D:\\rips\\gone\\'))):
            path = os.path.join(base, label.split()[1] + '.cue')
            with open(path, 'w', newline='') as fh:
                fh.write(text)
            try:
                check(label, vp.probe_disc(path)['count'] > 0)
            except Exception as exc:                    # noqa: BLE001
                check(label, False, '%s: %s' % (type(exc).__name__, exc))

        # Destination checks, which run before anything is written.
        _why, level = vp.dest_problem(os.path.join(tmp, 'game'), 1000)
        check('a folder with files in it warns', level == 'warn', level)
        _why, level = vp.dest_problem(os.path.join(tmp, 'fresh'), 1000)
        check('an empty destination is clean', level is None, level)
        _why, level = vp.dest_problem(os.path.join(tmp, 'no', 'such'), 1000)
        check('a missing parent is refused', level == 'bad', level)
        locked = os.path.join(tmp, 'locked')
        os.makedirs(locked)
        os.chmod(locked, 0o500)
        try:
            why, level = vp.dest_problem(locked, 1000)
            # os.access(W_OK) would pass this as root, and on Windows it
            # passes for any folder without the read-only attribute. The
            # probe write is what actually answers the question.
            root = getattr(os, 'geteuid', lambda: 1)() == 0
            check('an unwritable folder is refused', level == 'bad' or root,
                  '%s / %s' % (level, why))
        finally:
            os.chmod(locked, 0o700)
        _why, level = vp.dest_problem(tmp, 1 << 60)
        check('too little room is refused', level == 'bad', level)
    finally:
        vp.ORIGINAL_MD5 = original
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if FAILS:
        print('%d failed' % len(FAILS))
        return 1
    print('disc reader OK')
    return 0


if __name__ == '__main__':
    sys.exit(main())
