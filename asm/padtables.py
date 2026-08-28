#!/usr/bin/env python3
"""The gamepad patch's tables: what each pad input is, and what it is called.

One list drives four blobs that used to be four hex strings, and the pointers
between them are computed rather than counted by hand:

    INPUTS  ->  the condition table the tick reads, at COND
                the bind list the F7 page offers, at BINDS
                the names both of them point at, at NAMES
    NAMES   ->  the device list's four profile names, at DEVLIST

An input's id is 0xe0 plus its position in INPUTS, so the order of that list
is the order of the condition table and cannot be shuffled without moving
everyone's saved binds.
"""

import struct

# Where the tables land is the build's business (the CAVES table in
# vo_patch.py). The pointers between them are fixups on PAD_NAMES.
FIRST_ID = 0xe0         # a bind byte this or over is a pad input

# How the tick decides an input is active. `where` is a byte offset from
# wButtons in the XINPUT_GAMEPAD it polled.
MASK = 0                # wButtons & value
ABOVE = 1               # signed word at `where` >  value
BELOW = 2               # signed word at `where` <  value
TRIGGER = 3             # unsigned byte at `where` >  value

# The kind byte the table actually carries. The tick tests 0, 1 and 2 in
# that order and treats anything else as a trigger, so these are its numbers
# and not the ones above.
KIND = {BELOW: 0, ABOVE: 1, MASK: 2, TRIGGER: 3}

WBUTTONS = 0
LTRIGGER, RTRIGGER = 2, 3
LX, LY, RX, RY = 4, 6, 8, 10

DEADZONE = 13000        # what a stick has to pass to count as pushed. Per
                        # axis, not radial: a 45 degree push puts 23170 on
                        # each, so diagonals stay comfortable
PULL = 0x40             # and a trigger

# (name, kind, where, value). Sixteen entries, ids 0xe0 to 0xef.
INPUTS = [
    ('A',         MASK,    WBUTTONS, 0x1000),
    ('B',         MASK,    WBUTTONS, 0x2000),
    ('X',         MASK,    WBUTTONS, 0x4000),
    ('Y',         MASK,    WBUTTONS, 0x8000),
    ('LB',        MASK,    WBUTTONS, 0x0100),
    ('RB',        MASK,    WBUTTONS, 0x0200),
    ('LT',        TRIGGER, LTRIGGER, PULL),
    ('RT',        TRIGGER, RTRIGGER, PULL),
    ('LS Up',     ABOVE,   LY,  DEADZONE),
    ('LS Down',   BELOW,   LY, -DEADZONE),
    ('LS Left',   BELOW,   LX, -DEADZONE),
    ('LS Right',  ABOVE,   LX,  DEADZONE),
    ('RS Up',     ABOVE,   RY,  DEADZONE),
    ('RS Down',   BELOW,   RY, -DEADZONE),
    ('RS Left',   BELOW,   RX, -DEADZONE),
    ('RS Right',  ABOVE,   RX,  DEADZONE),
]

# The F7 device list, in display order; asm/devorder.asm maps positions
# to the fixed device numbers (0 Real, 1 gamepad, 2 twin-stick, 3 Simple).
PROFILES = ['Gamepad (XInput)', 'Twin-stick (XInput)', 'Keyboard (Simple)',
            'Keyboard (Real)']

DEVLIST_LEN = 32        # the run the device list is written into

# Keyboard (Simple)'s shipped binds, 1P then 2P: the two runs the executable
# holds for the block it originally shared with Keyboard (Real). The gamepad
# defaults replaced them in place, so its returning profile brings its own
# copy, written to the +0x38 block by the startup call kbpage.asm redirects.
SIMPLEDEF = bytes.fromhex(
    '11001f001e002000100012002e0022002d0013002f002100'    # 1P
    'c700cf00d300d100d200c900520051004f004c0053005000')   # 2P


def build():
    """-> inc text, then (blob, fixups, labels) for the condition table,
    the bind list, the names, the device list, the Simple defaults and the
    ini keys. A fixup is (offset, kind, symbol, addend) as in vo_patch.link;
    the symbol is ('PAD_NAMES', offset) for a pointer into the names."""
    names, at = bytearray(), {}
    for text in ([name for name, _k, _w, _v in INPUTS] + PROFILES
                 + ['1P Deadzone', '2P Deadzone']):
        at[text] = len(names)
        names += text.encode('ascii') + b'\0'

    cond, binds, bfix = bytearray(), bytearray(), []
    for i, (name, kind, where, value) in enumerate(INPUTS):
        cond += struct.pack('<BBhi', KIND[kind], 0, where, value)
        bfix.append((len(binds), 'abs', ('PAD_NAMES', at[name]), 0))
        binds += struct.pack('<II', 0, FIRST_ID + i)

    devlist, dfix = bytearray(), []
    for p in PROFILES:
        dfix.append((len(devlist), 'abs', ('PAD_NAMES', at[p]), 0))
        devlist += struct.pack('<I', 0)
    devlist += b'\0' * (DEVLIST_LEN - len(devlist))

    inikeys = (b'1P Simple Assign\0' + b'2P Simple Assign\0'
               + b'1P Keyboard Assign\0' + b'2P Keyboard Assign\0')

    # The deadzone keys ride in the names blob: the INIKEYS run turned out
    # to end within nine bytes of the four Assign keys - the real file has
    # live data past it, which the offsets check caught - and the names
    # cave has room to spare.

    inc = 'extern COND, SIMPLEDEF, INIKEYS, DZKEYS\n'
    return (inc,
            (bytes(cond), [], {}),
            (bytes(binds), bfix, {}),
            (bytes(names), [], {'dzkeys': at['1P Deadzone']}),
            (bytes(devlist), dfix, {}),
            (SIMPLEDEF, [], {}),
            (inikeys, [], {}))


if __name__ == '__main__':
    _inc, cond, binds, names, devlist, sdef, keys = build()
    print('condition table %d, bind list %d, names %d, device list %d, '
          'simple defaults %d, ini keys %d'
          % (len(cond[0]), len(binds[0]), len(names[0]), len(devlist[0]),
             len(sdef[0]), len(keys[0])))
