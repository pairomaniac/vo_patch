#!/usr/bin/env python3
"""The gamepad patch's tables: what each pad input is, and what it is called.

One list drives four blobs that used to be four hex strings, and the pointers
between them are computed rather than counted by hand:

    INPUTS  ->  the condition table the tick reads, at COND
                the bind list the F7 page offers, at BINDS
                the names both of them point at, at NAMES
    NAMES   ->  the device list's three profile names, at DEVLIST

An input's id is 0xe0 plus its position in INPUTS, so the order of that list
is the order of the condition table and cannot be shuffled without moving
everyone's saved binds.
"""

import struct

COND = 0x00624d1b       # condition table, 16 entries of 8 bytes
BINDS = 0x00624843      # bind list, 16 entries of (name, id)
NAMES = 0x00624b9b      # the strings both tables point at
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

# The F7 device list, in slot order. Slot 0 is the game's own keyboard
# handler; the other two are this patch's.
PROFILES = ['Keyboard (Real)', 'Gamepad (XInput)', 'Twin-stick (XInput)']

DEVLIST_LEN = 32        # the run the device list is written into


def build():
    """-> inc text, condition table, bind list, name blob, device list."""
    names, at = bytearray(), {}
    for text in [name for name, _k, _w, _v in INPUTS] + PROFILES:
        at[text] = NAMES + len(names)
        names += text.encode('ascii') + b'\0'

    cond, binds = bytearray(), bytearray()
    for i, (name, kind, where, value) in enumerate(INPUTS):
        cond += struct.pack('<BBhi', KIND[kind], 0, where, value)
        binds += struct.pack('<II', at[name], FIRST_ID + i)

    devlist = b''.join(struct.pack('<I', at[p]) for p in PROFILES)
    devlist += b'\0' * (DEVLIST_LEN - len(devlist))

    inc = '%%define COND 0x%08x\n' % COND
    return inc, bytes(cond), bytes(binds), bytes(names), devlist


if __name__ == '__main__':
    _inc, cond, binds, names, devlist = build()
    print('condition table %d, bind list %d, names %d, device list %d'
          % (len(cond), len(binds), len(names), len(devlist)))
