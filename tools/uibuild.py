#!/usr/bin/env python3
"""Build the resolution patch's UI_CODE from asm/ui.asm into vo_patch.py.

    python3 tools/uibuild.py

asm/ui.asm is nasm, like the rest of asm/, but position independent and
carrying its own address list, so it is built here rather than by
asm/build.py: label offsets are read out of a dd table appended in a
temporary copy, and the blob and every offset constant derived from it
(UI_CALLS, UI_STUBS, UI_WORLD, UI_SUBMIT, UI_HUD_ENTER, UI_INSERT_A/B,
UI_HANGAR_DRAW, UI_FRAME, UI_FLUSH_A/B, UI_F4) are written into
vo_patch.py. UI_REFS - every game-address dword in the blob, by position,
from its own disassembly - is regenerated with capstone.
tools/hiresport.py must be rerun after this for the non-retail tables.

--check reassembles and compares against the committed blob; nasm is
enough for that (CI has it). Without nasm it falls back to the recorded
fingerprint of the source that built the committed blob.
"""
import os
import re
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
UI = os.path.join(ROOT, 'asm', 'ui.asm')
HIRES = os.path.join(ROOT, 'vo_patch.py')

LABELS = ['world_a', 'world_a2', 'world_b', 'world_b2', 'submit_a',
          'submit_b', 'hud_enter', 'stub1', 'stub2', 'stub3', 'stub4',
          'stub1_call', 'stub2_call', 'stub3_call', 'stub4_call',
          'insert_a', 'insert_b', 'hangar_draw', 'frame_setup', 'flush_a',
          'flush_b', 'f4_toggle', 'dlg_init', 'dlg_ok', 'dlg_done',
          'ini_load', 'ini_save', 'roll_blit', 'rowsafe', 'credits_moon']

# Ends the label table a temporary copy of the source carries; the blob
# is everything before it.
MAGIC = 0x4c42414c


def normalized(src):
    """The source as the fingerprint sees it: comments and blanks
    stripped."""
    out = []
    for ln in src.split('\n'):
        code = ln.split(';')[0].rstrip()
        if code.strip():
            out.append(code)
    return '\n'.join(out)


def have_nasm():
    try:
        subprocess.run(['nasm', '-v'], capture_output=True, check=True)
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


def assemble(src_asm):
    """(blob, {label: offset}) through nasm, in a temporary directory."""
    table = ('\ndd 0x%08x\n' % MAGIC
             + ''.join('dd %s\n' % lb for lb in LABELS))
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, 'ui.asm')
        out = os.path.join(tmp, 'ui.bin')
        with open(path, 'w') as f:
            f.write(src_asm + table)
        subprocess.run(['nasm', '-f', 'bin', '-o', out, path], check=True)
        with open(out, 'rb') as f:
            raw = f.read()
    pos = len(raw) - 4 * (len(LABELS) + 1)
    if struct.unpack_from('<I', raw, pos)[0] != MAGIC:
        raise SystemExit('label table not where expected')
    offs = dict(zip(LABELS, struct.unpack_from('<%dI' % len(LABELS),
                                               raw, pos + 4)))
    return raw[:pos], offs


def fingerprint_check(src_asm):
    import hashlib
    import vo_patch_hires as hires
    digest = hashlib.sha256(normalized(src_asm).encode()).hexdigest()
    if digest == hires.UI_ASM_SHA:
        print('asm/ui.asm matches the committed blob (by fingerprint; '
              'install nasm to reassemble)')
        return
    print('asm/ui.asm changed but the committed UI_CODE was not rebuilt: '
          'run tools/uibuild.py and tools/hiresport.py')
    sys.exit(1)


def data_overlap(src_asm):
    """Two D_ names on one data-block offset: the second silently
    overwrites the first (D_ROUND on D_XO shifted the 2D layer once)."""
    seen = {}
    for m in re.finditer(r'^(D_\w+)\s+equ\s+(0x[0-9a-f]+)', src_asm, re.M):
        off = int(m.group(2), 16)
        if off < 0x1b00 and off in seen:
            sys.exit('asm/ui.asm: %s and %s share data offset %#x'
                     % (seen[off], m.group(1), off))
        seen[off] = m.group(1)


def main():
    check = '--check' in sys.argv
    src_asm = open(UI).read()
    data_overlap(src_asm)
    if not have_nasm():
        if check:
            fingerprint_check(src_asm)
            return
        sys.exit('building needs nasm: sudo apt install nasm')
    blob, offs = assemble(src_asm)
    if check:
        sys.path.insert(0, HERE)
        import vo_patch_hires as hires
        if hires.UI_CODE == blob:
            print('asm/ui.asm matches the committed blob')
            return
        print('asm/ui.asm and the committed UI_CODE differ: run '
              'tools/uibuild.py and tools/hiresport.py')
        sys.exit(1)
    try:
        import capstone
    except ImportError:
        sys.exit('regenerating UI_REFS needs capstone: '
                 'apt/dnf install python3-capstone')

    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    md.detail = True
    refs = []
    for i in md.disasm(blob, 0):
        if i.mnemonic == 'call' or i.mnemonic.startswith('j'):
            continue                  # rel32s are fixed up per site
        for oa, sa in (('disp_offset', 'disp_size'),
                       ('imm_offset', 'imm_size')):
            o, sz = getattr(i, oa), getattr(i, sa)
            if o and sz == 4:
                v = struct.unpack_from('<I', blob, i.address + o)[0]
                if 0x401000 <= v < 0x3660000:
                    refs.append((i.address + o, v))

    src = open(HIRES).read()

    def setblock(begin, end, body):
        nonlocal src
        i, j = src.index(begin), src.index(end)
        src = src[:i] + begin + '\n' + body + src[j:]

    import hashlib
    sha = hashlib.sha256(normalized(src_asm).encode()).hexdigest()
    hexstr = blob.hex()
    rows = [hexstr[i:i + 72] for i in range(0, len(hexstr), 72)]
    setblock('# UI CODE BEGIN', '# UI CODE END',
             "UI_ASM_SHA = '%s'\n" % sha +
             "UI_CODE = bytes.fromhex(\n" +
             '\n'.join("    '%s'" % r for r in rows) + ')\n')
    setblock('# UI REFS BEGIN', '# UI REFS END',
             'UI_REFS = (\n' +
             '\n'.join('    (0x%04x, 0x%07x),' % rv for rv in refs) + '\n)\n')

    def setconst(name, val):
        nonlocal src
        src = re.sub(r'^%s = .*$' % name, '%s = %s' % (name, val),
                     src, count=1, flags=re.M)
    setconst('UI_HUD_ENTER', hex(offs['hud_enter']))
    setconst('UI_INSERT_A, UI_INSERT_B',
             '%s, %s' % (hex(offs['insert_a']), hex(offs['insert_b'])))
    setconst('UI_HANGAR_DRAW', hex(offs['hangar_draw']))
    setconst('UI_F4', hex(offs['f4_toggle']))
    setconst('UI_DLG_INIT, UI_DLG_OK, UI_DLG_DONE, UI_INI_LOAD, UI_INI_SAVE',
             ', '.join(hex(offs[l]) for l in ('dlg_init', 'dlg_ok',
                                              'dlg_done', 'ini_load',
                                              'ini_save')))
    setconst('UI_ROLLBLIT', hex(offs['roll_blit']))
    setconst('UI_ROWSAFE', hex(offs['rowsafe']))
    setconst('UI_CREDITS_MOON', hex(offs['credits_moon']))
    setconst('UI_FRAME, UI_FLUSH_A, UI_FLUSH_B',
             '%s, %s, %s' % (hex(offs['frame_setup']), hex(offs['flush_a']),
                             hex(offs['flush_b'])))
    calls = [(offs['stub%d_call' % n], t) for n, t in
             ((1, 0x4800d0), (2, 0x4804f0), (3, 0x5670c0), (4, 0x5674f0))]
    src = re.sub(
        r'UI_CALLS = \[.*?\]',
        'UI_CALLS = [(0x%x, 0x%x), (0x%x, 0x%x),   # rel32 at offset+1\n'
        '            (0x%x, 0x%x), (0x%x, 0x%x)]'
        % sum(calls, ()), src, count=1, flags=re.S)
    stubs = re.search(r'UI_STUBS = \[(.*?)\]', src, re.S).group(0)
    sites = re.findall(r'\((0x[0-9a-f]+), (0x[0-9a-f]+), 0x[0-9a-f]+\)',
                       stubs)
    new = 'UI_STUBS = [' + ',\n            '.join(
        '(%s, %s, 0x%x)' % (o, v, offs['stub%d' % (n + 1)])
        for n, (o, v) in enumerate(sites)) + ']'
    src = src.replace(stubs, new, 1)
    for pat, lab in (('UI_WORLD', ('world_a', 'world_a2', 'world_b',
                                   'world_b2')),
                     ('UI_SUBMIT', ('submit_a', 'submit_b'))):
        block = re.search(r'%s = \(\(.*?\)\)' % pat, src, re.S).group(0)
        rows2 = re.findall(r'\((0x[0-9a-f]+), (0x[0-9a-f]+), '
                           r'0x[0-9a-f]+\)', block)
        body = ',\n'.join('(%s, %s, 0x%x)' % (o, v, offs[lab[n]])
                          for n, (o, v) in enumerate(rows2))
        src = src.replace(block, '%s = (%s)' % (pat, body), 1)
    open(HIRES, 'w').write(src)
    print('blob %d bytes; ' % len(blob)
          + ' '.join('%s=%s' % (l, hex(offs[l])) for l in LABELS))


if __name__ == '__main__':
    main()
