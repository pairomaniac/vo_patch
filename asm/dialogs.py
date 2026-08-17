#!/usr/bin/env python3
"""The two dialogs the patches build, and the data the Extras box reads.

    build_extras()  the F11 template, and the strings and tables
                    debugbox.asm reads. Both are written into padding.
    build_f5()      the frame rate radio buttons on the F5 page. That is a
                    resource the game already has, so this rewrites part of
                    one rather than building it, and emits both columns of
                    the site: the `original` one from the stock labels, which
                    checks this packing against the resource compiler's.

A control's id is the game's own command id wherever there is one, so a
click can be posted to the main window as the menu item it replaces, and no
lookup table is needed. The ids appear in the template and in the tables
debugbox.asm walks; both come from ITEMS below.
"""

import struct

TEMPLATE = 0x0365fab0   # the F11 template, in the .rsrc cave
TPL_LEN = 460           # the dead menu resource it is written over
DATA = 0x0063e8e8       # its strings and tables, in the .rdata cave

# Window styles. WS_POPUP | WS_CAPTION | WS_SYSMENU | DS_MODALFRAME |
# DS_SETFONT, which is what the game's own dialogs use.
DLGSTYLE = 0x80C800C0
BUTTON = 0x0080         # the button class atom
STATIC = 0x0082
CHECKBOX = 0x50010003   # WS_CHILD|WS_VISIBLE|WS_TABSTOP|BS_AUTOCHECKBOX
PUSH = 0x50010000       # the same, BS_PUSHBUTTON
RADIO = 0x50010009      # BS_AUTORADIOBUTTON
RADIO1 = 0x50030009     # and the first of a group, WS_GROUP

IDCANCEL = 2

# (label, command id, x, y, w, h, style, the flag it shows or None).
#
# Three are check boxes, and the dialog procedure ticks each from the game's
# own flag when the box opens, so what it shows is what is on. The rest are
# one-shot buttons and have nothing to read.
GROUP = 0x50000007      # BS_GROUPBOX, the box the F5 page draws its own with

# Credits has no menu item to post, so it is the one id the dialog acts on
# itself. Small, and nowhere near the game's own 0x9cxx ids.
CMD_CREDITS = 0x0051

# The group box holds everything that acts on the running game, Credits
# included - it jumps to the ending from wherever you are, which is a debug
# button whoever wrote it. Quit and Close are the dialog's own and sit under
# it. The box comes first because a group box drawn after the controls it
# frames paints over them.
#
# The template has no room to spare - it goes over a 460-byte menu resource
# and fills it exactly - so this is a layout, not a place to add a button.
ITEMS = [
    ('Debug',        0xffff,  10,  4, 192, 78, GROUP,   None),
    ('No shot',      0x9c47,  16, 18,  56, 12, CHECKBOX, 0x00652fd8),
    ('SE',           0x9c5b,  80, 18,  36, 12, CHECKBOX, 0x006bcc4c),
    ('CD',           0x9c5c, 124, 18,  36, 12, CHECKBOX, 0x0063f430),
    ('Kill 1P',      0x9c61,  16, 40,  50, 14, PUSH,     None),
    ('Kill 2P',      0x9c62,  70, 40,  50, 14, PUSH,     None),
    ('Credits', CMD_CREDITS, 124, 40,  50, 14, PUSH,     None),
    ('Scorekeeping', 0x9c67,  16, 62,  72, 14, PUSH,     None),
    ('Quit',         0x9c41,  16, 90,  50, 14, PUSH,     None),
    ('Close',      IDCANCEL, 146, 90,  50, 14, PUSH,     None),
]

RATES = ['1/1', '1/2', '1/3', '1/4', '1/5']

# The F5 page's frame rate radios. Sega labelled them for what the setting
# did to the animation; they name the frame rate now, which is what the
# player is choosing. The wider one is the first, and everything after it
# in the resource shifts along, which is the four bytes the size field at
# 0x6035ac gains.
F5_STOCK = [('Fast', 35), ('Smooth', 41)]
F5_NEW = [('30 FPS', 41), ('60 FPS', 41)]
F5_LEN = 504            # the run of the resource the site rewrites


def wstr(text):
    return text.encode('utf-16-le') + b'\0\0'


def align4(blob):
    return blob + b'\0' * (-len(blob) % 4)


def item(style, x, y, cx, cy, iid, cls, text):
    out = struct.pack('<IIHHHHH', style, 0, x, y, cx, cy, iid)
    return out + struct.pack('<HH', 0xffff, cls) + wstr(text) + b'\0\0'


def build_extras():
    """-> inc text, the dialog template, the strings and tables."""
    tpl = struct.pack('<II', DLGSTYLE, 0)
    tpl += struct.pack('<5H', len(ITEMS), 0, 0, 212, 112)
    tpl += struct.pack('<HH', 0, 0)             # no menu, default class
    tpl += wstr('Extras') + struct.pack('<H', 8) + wstr('MS Sans Serif')
    for label, iid, x, y, cx, cy, style, _flag in ITEMS:
        tpl = align4(tpl) + item(style, x, y, cx, cy, iid, BUTTON, label)
    if len(tpl) > TPL_LEN:
        raise SystemExit('the Extras template is %d bytes, and the resource '
                         'it is written over is %d' % (len(tpl), TPL_LEN))
    tpl += b'\0' * (TPL_LEN - len(tpl))

    names = {}
    data = bytearray()
    for text in ('USER32.DLL', 'DialogBoxIndirectParamA'):
        names[text] = DATA + len(data)
        data += text.encode('ascii') + b'\0'
    data += b'\0' * (-len(data) % 4)

    checks = DATA + len(data)
    for _label, iid, _x, _y, _cx, _cy, _style, flag in ITEMS:
        if flag is not None:
            data += struct.pack('<II', flag, iid)
    rates = DATA + len(data)
    for text in RATES:
        data += text.encode('ascii') + b'\0'

    inc = ''.join('%%define %-11s 0x%08x\n' % (name, value) for name, value in (
        ('USER32', names['USER32.DLL']),
        ('DLGBOXPROC', names['DialogBoxIndirectParamA']),
        ('CHECKS', checks),
        ('RATES', rates),
        ('TEMPLATE', TEMPLATE),
        ('CMD_QUIT', dict((name, i) for name, i, *_r in ITEMS)['Quit']),
        ('CMD_CREDITS', CMD_CREDITS),
        ('IDCANCEL', IDCANCEL),
    ))
    return inc, bytes(tpl), bytes(data)


def build_f5(labels):
    """The tail of the F5 dialog resource, from the first radio's width on.

    The site starts inside that control, because its width is the first
    thing that changes; everything from there to the end of the resource is
    rewritten, since a longer label moves all of it."""
    (first, firstw), (second, secondw) = labels
    out = struct.pack('<3H', firstw, 8, 0x042c)
    out += struct.pack('<HH', 0xffff, BUTTON) + wstr(first) + b'\0\0'
    rest = [
        (RADIO,  133,  84, secondw,  8, 0x042d, BUTTON, second),
        (RADIO1,  68,  98, 36,  8, 0x042e, BUTTON, 'Type1'),
        (RADIO,  110,  98, 36,  8, 0x042f, BUTTON, 'Type2'),
        (RADIO,  152,  98, 36,  8, 0x0431, BUTTON, 'Type3'),
        (0x50020000, 15,   5, 34,  8, 0xffff, STATIC, 'Screen'),
        (0x50020000, 15,  84, 50,  8, 0xffff, STATIC, 'Motion Type'),
        (0x50020000, 15,  96, 50, 10, 0xffff, STATIC, 'Screen Split'),
        (0x50000007, 16,  25, 175, 25, 0xffff, BUTTON, 'Texture'),
        (0x50000007, 16,  55, 175, 24, 0xffff, BUTTON, 'Display Objects'),
        (0x50020000, 15,  15, 43,  9, 0xffff, STATIC, 'Field Graphic'),
        (0x50020000, 22, 105, 26,  8, 0xffff, STATIC, '(2P VS)'),
    ]
    for style, x, y, cx, cy, iid, cls, text in rest:
        out = align4(out) + item(style, x, y, cx, cy, iid, cls, text)
    if len(out) > F5_LEN:
        raise SystemExit('the F5 labels need %d bytes and the resource has '
                         '%d; the size field at 0x6035ac would have to move '
                         'by more than the four bytes the patch gives it'
                         % (len(out), F5_LEN))
    return out + b'\0' * (F5_LEN - len(out))


if __name__ == '__main__':
    _inc, tpl, data = build_extras()
    print('Extras template %d, data %d, F5 %d'
          % (len(tpl), len(data), len(build_f5(F5_NEW))))
