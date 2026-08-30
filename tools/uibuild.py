#!/usr/bin/env python3
"""Build hires.py's UI_CODE from ui.asm and splice it in.

    python3 tools/uibuild.py

Preprocessing: NAME = value equates are substituted, comments stripped,
and each .long becomes a unique 4-byte marker instruction pair that is
overwritten with the value after assembly (keystone has no data
directives). Label offsets are read back by assembling a second copy
with a jmp to every wanted label appended at the end - appending shifts
nothing - and decoding the rel32s. The blob and every offset constant
derived from it (UI_CALLS, UI_STUBS, UI_WORLD, UI_SUBMIT, UI_HUD_ENTER,
UI_INSERT_A/B, UI_HANGAR_DRAW) are written into hires.py. UI_REFS is
regenerated from the new blob. tools/hiresport.py must be rerun after
this for the non-retail tables.
"""
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
UI = os.path.join(ROOT, 'ui.asm')
HIRES = os.path.join(ROOT, 'vo_patch.py')

LABELS = ['world_a', 'world_a2', 'world_b', 'world_b2', 'submit_a',
          'submit_b', 'hud_enter', 'stub1', 'stub2', 'stub3', 'stub4',
          'insert_a', 'insert_b', 'hangar_draw']


def normalized(src):
    """The source as the hash sees it: comments and blanks stripped."""
    out = []
    for ln in src.split('\n'):
        code = ln.split(';')[0].rstrip()
        if code.strip():
            out.append(code)
    return '\n'.join(out)


def preprocess(src):
    consts, lines, longs = {}, [], []
    for ln in src.split('\n'):
        code = ln.split(';')[0].rstrip()
        m = re.match(r'^([A-Za-z_][A-Za-z_0-9]*)\s*=\s*(.+)$', code)
        if m:
            consts[m.group(1)] = eval(m.group(2), {}, dict(consts))
            continue
        if not code.strip():
            continue
        m = re.match(r'^\s*\.long\s+(0x[0-9a-fA-F]+)', code)
        if m:
            lines.append('    test al, 0x5a')
            lines.append('    test al, %d' % len(longs))
            longs.append(int(m.group(1), 16))
            continue
        lines.append(code)

    def subst(l):
        return re.sub(r'\b([A-Za-z_][A-Za-z_0-9]*)\b',
                      lambda m: hex(consts[m.group(1)])
                      if m.group(1) in consts else m.group(1), l)
    return [subst(l) for l in lines], longs


def assemble(ks_mod, lines, longs):
    ks = ks_mod.Ks(ks_mod.KS_ARCH_X86, ks_mod.KS_MODE_32)
    enc, _ = ks.asm('\n'.join(lines), 0)
    blob = bytearray(enc)
    for k, v in enumerate(longs):
        pat = bytes([0xa8, 0x5a, 0xa8, k])
        i = blob.find(pat)
        assert i >= 0 and blob.find(pat, i + 1) < 0, '.long marker %d' % k
        struct.pack_into('<I', blob, i, v)
    return bytes(blob)


def main():
    check = '--check' in sys.argv
    src_asm = open(UI).read()
    try:
        import capstone
        import keystone
    except ImportError:
        if not check:
            sys.exit('building needs keystone and capstone: '
                     'pip install keystone-engine capstone')
        # a fresh checkout with nothing installed still gets a real
        # answer: the recorded fingerprint of the source that built the
        # committed blob
        import hashlib
        import vo_patch_hires as hires
        digest = hashlib.sha256(normalized(src_asm).encode()).hexdigest()
        if digest == hires.UI_ASM_SHA:
            print('ui.asm matches the committed blob (by fingerprint; '
                  'install keystone-engine and capstone to reassemble)')
            return
        print('ui.asm changed but the committed UI_CODE was not rebuilt: '
              'run tools/uibuild.py and tools/hiresport.py')
        sys.exit(1)
    lines, longs = preprocess(src_asm)
    blob = assemble(keystone, lines, longs)
    probed = assemble(keystone, lines + ['    jmp %s' % l for l in LABELS],
                      longs)
    assert probed[:len(blob)] == blob
    offs = {}
    p = len(blob)
    for label in LABELS:
        assert probed[p] == 0xe9
        offs[label] = p + 5 + struct.unpack_from('<i', probed, p + 1)[0]
        p += 5

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
    if check:
        import vo_patch_hires as hires
        if hires.UI_CODE == blob:
            print('ui.asm matches the committed blob')
            return
        print('ui.asm and the committed UI_CODE differ: run '
              'tools/uibuild.py and tools/hiresport.py')
        sys.exit(1)

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
    for tag, names in (('UI_CALLS', None),):
        pass
    calls = [(offs['stub%d' % n] + 10, t) for n, t in
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
