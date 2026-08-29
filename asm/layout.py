"""The .vocd data blob: its layout and string table, shared by vocd.asm."""

import os
import struct

# Layout of the data blob, offsets from its base.
FIELDS = [
    # 0x0C, 0x24 and 0x30 held D_ORIGMCI, D_VPROTECT and D_SCRATCH, which went
    # with the IAT redirect. Left as gaps rather than closed up: the section is
    # page aligned, so tidying them would save nothing.
    ('D_NTRACKS',  0x00), ('D_TRACK',    0x04), ('D_PAUSED',   0x08),
                          ('D_MCISTR',   0x10), ('D_GETMODFN', 0x14),
    ('D_CREATEF',  0x18), ('D_GETFSIZE', 0x1C), ('D_CLOSEH',   0x20),
                          ('D_LSTRCMPI', 0x28), ('D_INIT',     0x2C),
    ('D_TOC',      0x40),          # 100 dwords -> 0x040..0x1D0, exact fit
    ('D_GAMEDIR',  0x1D0),         # 272, of which 264 is ever used
    ('D_PATH',     0x2E0),         # 288, worst case 282
    ('D_CMD',      0x400),         # 448, worst case 318
]
# Worst case is 264 for the gamedir (what GetModuleFileNameA is called with)
# plus music\trackNN.wav, wrapped in the open command: 318 bytes. At the old
# 0x540 that left two bytes, and an overrun would have landed on the string
# table rather than anywhere obvious. The section is page aligned, so the
# extra 128 bytes cost nothing in the file.
STRBASE = 0x5C0

STRINGS = [
    ('S_KERNEL32',   'kernel32.dll'),
    ('S_WINMM',      'winmm.dll'),
    ('S_MCISTR',     'mciSendStringA'),
    ('S_GETMODFN',   'GetModuleFileNameA'),
    ('S_CREATEF',    'CreateFileA'),
    ('S_GETFSIZE',   'GetFileSize'),
    ('S_CLOSEH',     'CloseHandle'),
    ('S_LSTRCMPI',   'lstrcmpiA'),
    ('S_CDAUDIO',    'cdaudio'),
    ('S_MUSICTRACK', 'music\\track'),
    ('S_DOTWAV',     '.wav'),
    ('S_OPENQ',      'open "'),
    ('S_OPENTAIL',   '" type waveaudio alias vocdbgm'),
    ('S_SETFMT',     'set vocdbgm time format milliseconds'),
    ('S_PLAY',       'play vocdbgm'),
    ('S_STOP',       'stop vocdbgm'),
    ('S_PAUSE',      'pause vocdbgm'),
    ('S_RESUME',     'resume vocdbgm'),
    ('S_CLOSE',      'close vocdbgm'),
    ('S_STATUSMODE', 'status vocdbgm mode'),
    ('S_PLAYING',    'playing'),
]


def build():
    inc, blob, off = [], bytearray(), STRBASE
    for name, off_ in FIELDS:
        inc.append('%%define %s 0x%03x' % (name, off_))
    for name, text in STRINGS:
        inc.append('%%define %s 0x%03x' % (name, off))
        blob += text.encode('ascii') + b'\0'
        off += len(text) + 1
    data = bytearray(STRBASE) + blob
    # Track 1 is the data track: 11 min 51 sec 26 frames, never played.
    struct.pack_into('<I', data, 0x40 + 4, 11 | (51 << 8) | (26 << 16))
    return '\n'.join(inc) + '\n', bytes(data)


if __name__ == '__main__':
    inc, data = build()
    open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'strings.inc'), 'w').write(inc)
    open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data.bin'), 'wb').write(data)
    print('data blob: %d bytes, strings from 0x%x' % (len(data), STRBASE))
