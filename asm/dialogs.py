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

TEMPLATE = 0xE7E7E7E7   # a placeholder: the template lives in its own
                        # appended section, whose address only exists at
                        # apply time - vo-patch.py fills it in the way it
                        # fills .vocd's, at apply_extras_template()
DATA = 0x0063e8e8       # its strings and tables, in the .rdata cave

# Window styles. WS_POPUP | WS_CAPTION | WS_SYSMENU | DS_MODALFRAME |
# DS_SETFONT, which is what the game's own dialogs use. The font block is
# back: the template no longer squeezes into the 460-byte menu resource,
# so nothing has to pay for anything.
DLGSTYLE = 0x80C800C0
BUTTON = 0x0080         # the button class atom
STATIC = 0x0082
EDIT = 0x0081
CHECKBOX = 0x50010003   # WS_CHILD|WS_VISIBLE|WS_TABSTOP|BS_AUTOCHECKBOX
PUSH = 0x50010000       # the same, BS_PUSHBUTTON
RADIO = 0x50010009      # BS_AUTORADIOBUTTON
RADIO1 = 0x50030009     # and the first of a group, WS_GROUP
LABEL = 0x50000000      # WS_CHILD|WS_VISIBLE, a static
NUMBOX = 0x50812000     # the same + TABSTOP, WS_BORDER and ES_NUMBER,
                        # so the edit takes digits and nothing else

IDCANCEL = 2
ID_DZ1 = 0x52           # the deadzone boxes, 1P then 2P - adjacent so the
ID_DZ2 = 0x53           # dialog procedure can loop; like CMD_CREDITS,
                        # nowhere near the game's own 0x9cxx ids
ID_DZDEF = 0x54         # the Defaults button, handled in asm/voxt.asm

# (label, command id, x, y, w, h, style, class, the flag it shows or None).
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
# button whoever wrote it. Quit and the Deadzone row are under it, the
# dialog's own. The box comes first because a group box drawn after the
# controls it frames paints over them.
#
# Everything at full size, Quit included: the template lives in its own
# section now, and the 460-byte menu resource it used to squeeze into no
# longer prices the labels. The min/max hint is the dialog's one font -
# templates carry a single font block, so there is no smaller one to use.
ITEMS = [
    ('Debug',        0xffff,  10,  4, 192, 78, GROUP,    BUTTON, None),
    ('No shot',      0x9c47,  16, 18,  56, 12, CHECKBOX, BUTTON, 0x00652fd8),
    ('SE',           0x9c5b,  80, 18,  36, 12, CHECKBOX, BUTTON, 0x006bcc4c),
    ('CD',           0x9c5c, 124, 18,  36, 12, CHECKBOX, BUTTON, 0x0063f430),
    ('Kill 1P',      0x9c61,  16, 40,  50, 14, PUSH,     BUTTON, None),
    ('Kill 2P',      0x9c62,  70, 40,  50, 14, PUSH,     BUTTON, None),
    ('Credits', CMD_CREDITS, 124, 40,  50, 14, PUSH,     BUTTON, None),
    ('Scorekeeping', 0x9c67,  16, 62,  72, 14, PUSH,     BUTTON, None),
    ('Stick Deadzone % [ XInput ]', 0xffff,
                              10, 86, 192, 46, GROUP,    BUTTON, None),
    ('1P',           0xffff,  18, 100, 12, 10, LABEL,    STATIC, None),
    ('',             ID_DZ1,  32, 98,  22, 12, NUMBOX,   EDIT,   None),
    ('%',            0xffff,  57, 100,  8, 10, LABEL,    STATIC, None),
    ('2P',           0xffff,  74, 100, 12, 10, LABEL,    STATIC, None),
    ('',             ID_DZ2,  88, 98,  22, 12, NUMBOX,   EDIT,   None),
    ('%',            0xffff, 113, 100,  8, 10, LABEL,    STATIC, None),
    ('Defaults',   ID_DZDEF, 140, 97,  54, 14, PUSH,     BUTTON, None),
    ('min 05, max 95', 0xffff,
                              18, 116, 100, 10, LABEL,   STATIC, None),
    ('Quit Game',    0x9c41,  10, 140, 60, 14, PUSH,     BUTTON, None),
    ('Close',      IDCANCEL, 152, 140, 50, 14, PUSH,     BUTTON, None),
]

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
    tpl += struct.pack('<5H', len(ITEMS), 0, 0, 212, 160)
    tpl += struct.pack('<HH', 0, 0)             # no menu, default class
    tpl += wstr('Extras') + struct.pack('<H', 8) + wstr('MS Sans Serif')
    for label, iid, x, y, cx, cy, style, cls, _flag in ITEMS:
        tpl = align4(tpl) + item(style, x, y, cx, cy, iid, cls, label)

    names = {}
    data = bytearray()
    for text in ('USER32.DLL', 'DialogBoxIndirectParamA'):
        names[text] = DATA + len(data)
        data += text.encode('ascii') + b'\0'
    data += b'\0' * (-len(data) % 4)

    checks = DATA + len(data)
    for _label, iid, _x, _y, _cx, _cy, _style, _cls, flag in ITEMS:
        if flag is not None:
            data += struct.pack('<II', flag, iid)
    inc = ''.join('%%define %-11s 0x%08x\n' % (name, value) for name, value in (
        ('USER32', names['USER32.DLL']),
        ('DLGBOXPROC', names['DialogBoxIndirectParamA']),
        ('CHECKS', checks),
        ('TEMPLATE', TEMPLATE),
        ('CMD_QUIT', dict((name, i) for name, i, *_r in ITEMS)['Quit Game']),
        ('CMD_CREDITS', CMD_CREDITS),
        ('IDCANCEL', IDCANCEL),
        ('ID_DZ1', ID_DZ1),
        ('ID_DZDEF', ID_DZDEF),
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
