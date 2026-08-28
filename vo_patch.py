#!/usr/bin/env python3
"""Virtual-On (PC, 1997) patcher. See README.md.

    python3 vo_patch.py                 patch a copy of v_on.exe
    python3 vo_patch.py --install CUE DIR   install from a disc image
    python3 vo_patch.py --rip SRC DIR   rip the soundtrack, no window needed
    python3 vo_patch.py --ddraw DIR     fetch cnc-ddraw into the game folder
    python3 vo_patch.py --netplay DIR   install the UDP netplay DLL
    python3 vo_patch.py --selfcheck     validate the patch tables and exit
    python3 vo_patch.py --version

The version is the VERSION line below and nowhere else, so there is nothing
to keep in step with it.

https://github.com/pairomaniac/vo_patch
"""

import base64
import ctypes
import errno
import hashlib
import io
import os
import queue
import re
import shutil
import ssl
import struct
import sys
import time
import webbrowser
import threading
import urllib.request
import zipfile
import zlib
import urllib.error

# Stamped by the build from the tag; see .github/workflows/build.yml. A
# source checkout has no version of its own, and saying so is more use in a
# bug report than a number nobody bumped.
VERSION = 'dev'
# One name for the tool, everywhere it is shown: the window, the About card,
# the version resource, the line the patched game prints on its own title
# screen, and the docs. It was three before - "Virtual-On patcher" on the
# window, vo-patch on the executable, vo_patch in the version resource - and
# a bug report could name any of them.
NAME = 'vo_patch'
REPO_URL = 'https://github.com/pairomaniac/vo_patch'

EXE_SIZE = 6650880

ORIGINAL_MD5 = 'a464b0ff32d5bab499f265e45658504e'

# Other builds that turn up. None of them can be patched - they are listed
# only so the patcher can name what it was handed instead of saying "not the
# original". md5 -> (size, name, why).
OTHER_BUILDS = {
    '4c70f780a7f0d98d74be62304fb99021': (
        6649344, 'USA OEM',
        'A different release of the same game. Everything here is written '
        'for the English retail build.'),
    'd19320bdc3381a48228990907910a391': (
        6621696, 'Japanese rerelease',
        'A separate compile of the same game, so the offsets here do not '
        'line up with it. Everything is written for the English retail '
        'build.'),
}

RETAIL_HINT = ('Install from an English retail disc image above, or pick a '
               'copy installed from one. Its v_on.exe alone will not do: the '
               'patcher writes to files beside it.')

# Patcher.load has no idea how many boxes are ticked, so it returns this and
# the window swaps in a count. Anything else using load gets a plain word.
READY_TAG = 'READY'

# Handed to this window instead of the executable often enough to be worth
# naming. The DISC section wants the cue sheet; this one wants the game.
DISC_IMAGES = {
    '.cue': 'cue sheet', '.bin': 'disc image', '.iso': 'disc image',
    '.img': 'disc image', '.mds': 'disc image', '.mdf': 'disc image',
    '.nrg': 'disc image', '.ccd': 'disc image', '.toc': 'cue sheet',
}

# Where each blob goes and what it names in the game, per build.
#
# The machine code in BLOBS below names no addresses: every place in the
# game it touches is a symbol, and link() fills the symbol in from these
# tables when the module loads. A second build is a second Build with its
# own caves and symbols and the same BLOBS.
#
# A cave is the virtual address a blob is written at, or (blob, offset) for
# one that rides inside another. A symbol is a virtual address in the game,
# or (blob, label) for a place inside one of ours.

class Build(object):
    def __init__(self, md5, size, sections, caves, symbols):
        self.md5, self.size = md5, size
        # (file offset, virtual address) of each section, in file order
        self.sections = sections
        self.caves, self.symbols = caves, symbols

    def va(self, off):
        """File offset -> virtual address."""
        for raw, va in reversed(self.sections):
            if off >= raw:
                return off - raw + va
        raise ValueError('0x%x is in the headers' % off)

    def off(self, va):
        """Virtual address -> file offset."""
        for raw, vaddr in reversed(self.sections):
            if va >= vaddr:
                return va - vaddr + raw
        raise ValueError('0x%x is below the image' % va)


RETAIL = Build(ORIGINAL_MD5, EXE_SIZE, sections=(
    (0x00000400, 0x00401000),       # .text
    (0x001f4400, 0x005f5000),       # .rdata
    (0x0023de00, 0x0063f000),       # .data
    (0x00601a00, 0x0365d000),       # .idata
    (0x00602c00, 0x0365f000),       # .rsrc
    (0x0060c400, 0x03669000),       # .reloc
), caves={
    'TIMER': 0x005f4e3e,            # .text padding past the code
    'DEBUGBOX': 0x005f4e7c,         # the rest of it; hook, then procedure
    'OVERLAY': 0x005f80e0,
    'COMMITDEV': 0x005fd74c,        # runs of zeros in .rdata from here on
    'BINDLIST': 0x005fd7e4,
    'BLOCKCUR': 0x005fd864,
    'BINDMAP': 0x005fd904,
    'BINDBLOCK': 0x005ff24c,
    'INIPARSE': 0x00601b0c,
    'PAGESEC': 0x00601b70,
    'PAGESEL': 0x00601bd4,
    'INISAVE': 0x00601c38,
    'DEVORDER': 0x00604734,
    'INILOAD': 0x0060702c,
    'PADX': 0x00608060,
    'LEVERS': ('PADX', 'end'),      # written straight after the routine
    'TITLEVER': 0x00623d98,
    'PAD_SIMPLEDEF': 0x00624788,
    'PAD_BINDS': 0x00624843,
    'TWIN': 0x006249c4,
    'PAD_INIKEYS': 0x00624ae0,
    'PAD_NAMES': 0x00624b9b,
    'PAD_COND': 0x00624d1b,
    'F11PAUSE': 0x0063bf24,
    'INIALL': 0x0063c5f4,
    'CREDITS': 0x0063d6d0,
    'CAMSKIP': 0x0063dda0,
    'NAMEENTRY': 0x0063ddc4,
    'EXTRAS_DATA': 0x0063e8e8,
    'KBPAGE': 0x0063e938,
    'INTROWAIT': 0x0063e970,        # .rdata raw padding after it
    'PAD_DEVLIST': 0x0066d418,      # the F7 device list's own run, in .data
    'MOVIE': 0x0366865c,            # .rsrc padding
    # VOXT rides in the appended .voxt section and names nothing relative
    # to itself, so it has no cave: link() is given None.
}, symbols={
    # Places inside our own blobs, by label
    'DEVCUR': ('BINDLIST', 'devcur'),
    'PADLIST': ('PAD_BINDS', 0),
    'DZKEYS': ('PAD_NAMES', 'dzkeys'),
    'COND': ('PAD_COND', 0),
    'SIMPLEDEF': ('PAD_SIMPLEDEF', 0),
    'INIKEYS': ('PAD_INIKEYS', 0),
    'USER32': ('EXTRAS_DATA', 'user32'),
    'DLGBOXPROC': ('EXTRAS_DATA', 'dlgboxproc'),
    'CHECKS': ('EXTRAS_DATA', 'checks'),
    'F11WRAP': ('F11PAUSE', 'f11wrap'),
    'F11CHECKS': ('F11PAUSE', 'f11checks'),
    'DLGPROC': ('DEBUGBOX', 'dlgproc'),
    'LOADSIMPLE': ('INILOAD', 'loadsimple'),
    'DZSEED': ('PAGESEL', 'dzseed'),
    'PARSE12': ('INIPARSE', 'parse12'),
    'DZSAVE': ('INIPARSE', 'dzsave'),
    'HEXCHAR': ('BINDBLOCK', 'hexchar'),
    'POLLPADS': ('PADX', 'pollpads'),
    'TICK': ('PADX', 'tick'),
    'CAMSKIP': ('CAMSKIP', 0),
    # The game
    'EXIT1P': 0x00442ec4,          # where the 1P profile switch resumes
    'KBD1P': 0x00443074,           # stock keyboard handler, called by the tick
    'KBHANDLER1': 0x00443074,      # the stock 1P keyboard handler
    'CASEB': 0x00496b23,           # the stock device 1 apply-and-serialize
    'CROSSCHECK': 0x0049776e,      # look at 1P's key map
    'KBACCEPT': 0x004977c6,        # take the key
    'RESUME': 0x0049789a,          # what the Default button does next
    'DEFAULTS': 0x0049790f,        # fill a player's binds from the shipped set
    'DIGITLOOP': 0x00497c70,       # bind page fill: the digit loop
    'LISTLOOP': 0x00497cb0,        # and the list loop
    'FILLDONE': 0x00497cf7,        # where the fill loop's jge went
    'STORESHIFT': 0x00497e74,      # bind page store: shift down, try digits
    'STORELIST': 0x00497e99,       # and the list id store
    'SELDIGITS': 0x00498059,       # bind page preselect: the digit loop
    'SELLIST': 0x0049809d,         # the list loop
    'MAPDONE': 0x004980d9,         # where the search loop's jge went
    'SELSET': 0x004980d9,          # and set the selection
    'CURSOR': 0x004cd8c3,          # (column, row), cdecl
    'PRINT': 0x004ceeeb,           # (text), cdecl, from the cursor
    'WRITELINE': 0x005b1833,       # (key, value): one v_on.ini line
    'FINDLINE': 0x005b1871,        # (key) -> value text, 0 if absent
    'EXIT2P': 0x005bcd57,          # and the 2P one
    'KBD2P': 0x005bceed,
    'KBHANDLER2': 0x005bceed,      # and 2P
    'GPAUSE': 0x005c67c5,          # the built-in dialogs' pause, arg 0
    'GRESUME': 0x005c680b,         # and their resume
    'ORIGWNDPROC': 0x005c6857,     # the handler the hook falls through to
    'ORIG': 0x005c80df,            # the call this one is made in place of
    'DRAW': 0x005c991c,            # (text, x, y, colour, flag), cdecl
    'MEMCPY': 0x005e6030,
    'ORIGENTRY': 0x005e7930,       # the entry point this replaces
    'CDMUTE': 0x0063f430,
    'NOSHOT': 0x00652fd8,          # F11 check boxes: the flags they toggle
    'MASK1A': 0x00653690,          # 1P key masks
    'MASK1B': 0x0065369d,
    'KEYLIST': 0x0066d438,         # the game's 33 named keys
    'SEMUTE': 0x006bcc4c,
    'MASK2A': 0x006beb08,          # 2P
    'MASK2B': 0x006beb15,
    'HALF': 0x006bf560,            # coordinates on, at 0x5c9a98
    'WIDE': 0x006bf598,            # the two the pause text halves its own
    'PREV': 0x006c3d48,            # last frame's slot, shared with nameentry.asm
    'HELD': 0x006c3d49,            # and how long this press has lasted
    'CAMERA1': 0x00bf0457,         # and the one Select writes, which is what
    'ACCEPT1': 0x00bf0481,         # 1P's key buffer slot for A and Space
    'BLOCKS': 0x00bf6838,          # per player: this + player * 0x70; the
    'CURPLAYER': 0x00bf6bac,       # the side being configured, 0 or 1
    'PHASE': 0x01ad0964,           # where the credits sequence is up to
    'CAM2': 0x01ad0d94,
    'CAMERA2': 0x01ad0d94,
    'ACC2': 0x01ad0db1,
    'ACCEPT2': 0x01ad0db1,         # and 2P
    'FLAG': 0x01ae1c1c,            # the displaced write
    'MODE': 0x01ae3594,            # game state and sub-state, the pair the
    'SUBMODE': 0x01ae3690,         # tick already gates its bind slots on
    'MOVIEX': 0x01ae5f34,          # the offsets the replaced code read
    'MOVIEY': 0x01ae5f38,
    'PRIMARY': 0x01ae5f40,         # the surface DRAW paints on, and the one
    'HWND': 0x01ae5f58,            # the game's window
    'BACK': 0x01ae5f5c,            # that is about to be flipped over it
    'LEV1A': 0x01cb14c4,           # 1P lever words, left then right
    'LEV1B': 0x01cb14c6,
    'EDGEA': 0x01ed5ec5,           # press edges, lever A byte: bit 0 is LT
    'EDGEB': 0x01ed5ec6,           # and lever B's, where 2P's RT is
    'LEV2A': 0x01ee3ee4,           # 2P
    'LEV2B': 0x01ee3ee6,
    'MOVIEHWND': 0x01ef88c8,       # the mciavi window, from MCI_ANIM_STATUS_HWND
    'MOVIEDEV': 0x01ef88f0,        # its device id
    'LIVE': 0x03651470,            # + player * 0x18
    'BINDS1': 0x03651470,          # 1P bind bytes
    'BINDS2': 0x03651488,          # 2P bind bytes
    'DEVICES': 0x03651540,         # 1P's profile, 0 being the keyboard
    'XIFN': 0x0365cb40,            # resolved XInputGetState: 0 not yet, 1 failed
    'STATE': 0x0365cb44,           # the tick's XINPUT_STATE
    'BTN': 0x0365cb48,             # wButtons in it; the condition table's
    'SCR1': 0x0365cb60,            # scratch the tick keeps per player
    'SCRATCH1': 0x0365cb60,        # a byte of scratch per player, past .data
    'SCR2': 0x0365cb61,
    'SCRATCH2': 0x0365cb61,
    'PSTATE': 0x0365cb70,          # the pump's own XINPUT_STATE, so the two
    'PBTN': 0x0365cb74,            # pollers cannot tread on each other
    'SLEEPFN': 0x0365cb80,         # resolved Sleep: 0 not yet, 1 failed
    'PADPREV': 0x0365cb84,         # last polled buttons, one word per pad,
    'DZTHR1': 0x0365cb8c,          # stick thresholds out of 32767, 1P then
    'DZSTR1': 0x0365cb94,          # the digit pairs; see asm/padxinput.asm
    'GETMODULE': 0x0365d4a0,       # GetModuleHandleA
    'LOADLIB': 0x0365d504,         # LoadLibraryA
    'GETPROC': 0x0365d508,         # GetProcAddress
    'SENDMSG': 0x0365d52c,         # SendMessageA
    'ENDDIALOG': 0x0365d538,       # EndDialog
    'CHECKDLGBTN': 0x0365d544,     # CheckDlgButton
    'GETDLGITEM': 0x0365d54c,      # GetDlgItem
    'POSTMSG': 0x0365d56c,         # PostMessageA
    'GETMSG': 0x0365d58c,          # GetMessageA, the call this replaced
    'PEEKMSG': 0x0365d590,         # PeekMessageA
    'GETCLIENT': 0x0365d5d4,       # GetClientRect, the hooked one
    'MOVEWINDOW': 0x0365d5e0,      # MoveWindow
    'MCISEND': 0x0365d648,         # mciSendCommandA
})

# GENERATED - do not edit the hex by hand.
#
# Assembled from the sources in asm/. To change any of them: edit the source
# and run
#     python3 asm/build.py
# which rewrites everything between the markers below. CI runs
# `asm/build.py --check` on every push and fails if the two have drifted.
#
# Each entry is (code, fixups, labels). A fixup is (offset, kind, symbol,
# addend): 'abs' puts the symbol's address plus the addend at the offset,
# 'rel' the distance from the end of the slot to it. The symbol '.' is the
# blob's own address. Labels are offsets into the code, for the symbols
# above and the site table to name.

# BLOBS BLOB BEGIN
BLOBS = {
    'TIMER': (bytes.fromhex(
        '6824000000ff1500000000682e00000050ff150000000085c074046a01ffd0e9'
        'fcffffff77696e6d6d2e646c6c0074696d65426567696e506572696f6400'
    ), (
        (0x1, 'abs', '.', 36),
        (0x7, 'abs', 'LOADLIB', 0),
        (0xc, 'abs', '.', 46),
        (0x13, 'abs', 'GETPROC', 0),
        (0x20, 'rel', 'ORIGENTRY', -4),
    ), {
        'start': 0x0,
        'winmm': 0x24,
        'procname': 0x2e,
    }),
    'DEBUGBOX': (bytes.fromhex(
        '558bec53817d0c000100007532817d107a00000075296800000000ff15000000'
        '00680000000050ff150000000085c074078bd8e8fcffffff33c05b5dc210005b'
        '5de9fcffffff00000000000000000000000000000000000000000000558bec53'
        '56578b450c3d100100007532ff7508e8fcffffff31ff8d475250ff7508ff1500'
        '0000008d14bd00000000526a006a0c50ff15000000004783ff0272daeb453d11'
        '01000075450fb74d108d51ae83fa01763283f902740d83f954740881f9419c00'
        '0075308b5508e8eaeaeaea85c074146a00516811010000ff3500000000ff1500'
        '000000b801000000eb0233c05f5e5b5dc2100083f9517513833d000000000475'
        '086a1f8f0500000000ebd8ebc2'
    ), (
        (0x17, 'abs', 'USER32', 0),
        (0x1d, 'abs', 'LOADLIB', 0),
        (0x22, 'abs', 'DLGBOXPROC', 0),
        (0x29, 'abs', 'GETPROC', 0),
        (0x34, 'rel', 'F11WRAP', -4),
        (0x42, 'rel', 'ORIGWNDPROC', -4),
        (0x70, 'rel', 'F11CHECKS', -4),
        (0x7f, 'abs', 'GETDLGITEM', 0),
        (0x86, 'abs', 'DZSTR1', 0),
        (0x92, 'abs', 'SENDMSG', 0),
        (0xd9, 'abs', 'HWND', 0),
        (0xdf, 'abs', 'POSTMSG', 0),
        (0xfa, 'abs', 'MODE', 0),
        (0x105, 'abs', 'SUBMODE', 0),
    ), {
        'hook': 0x0,
        'dlgproc': 0x5c,
        'credits': 0xf3,
    }),
    'PADX': (bytes.fromhex(
        '68c2020000e8ef00000083c404e9fcffffff68ea020000e8dd00000083c404e9'
        'fcffffff0000000000000000000000000000000000000000e807000000ff2500'
        '00000000609ce80f02000083f801767431f683fe02736d680000000056ff1500'
        '00000085c0755a0fb71d000000008d14b5000000000fb72a66891a31ff83ff02'
        '733f8d0cbdc70000000fb70189da21c221e839c27428b80001000085d2750b80'
        '7903007419b8010100006a000fb651025250ff3500000000ff150000000047eb'
        'bc46eb8e9d61c310007200001020000000000000000000000000000000000000'
        '000000000000000000000000000000000000000000000000005589e583ec0453'
        '56578b5d08c745fc00000000e84901000083f80174416800000000ff33ffd085'
        'c07534c745fc010000000fb70500000000a900100000740b8b5318c60280e8fc'
        'ffffff0fb70500000000a92000000074068b5324c602808b4320ffd0837dfc00'
        '0f843c01000031f683fe0c0f83a1000000833d00000000047512833d00000000'
        '087c09833d000000000c7e0f83fe04747b83fe05747683fe077f778b53040fb6'
        '04722de0000000726383f810735e8d3cc5000000000fb6070fb757028b4f0483'
        'f800741783f801741d83f802742c0fb6820000000039c8772eeb310fbf820000'
        '0000f7d8eb070fbf82000000008b0b3b048d000000007f0feb120fb705000000'
        '0085c87502eb05e82500000046e956ffffff31f683fe040f83850000000fb705'
        '000000000fa3f07305e80300000046ebe38b53100fb60c32f7d18b53080fb702'
        '21c86689028b53140fb60c32f7d18b530c0fb70221c8668902c3a10000000085'
        'c075385631f683fe0373258b04b5a702000050ff150000000085c0750346ebe6'
        '68b302000050ff150000000085c07505b801000000a3000000005ec300000000'
        '00005f5e5bc9c312030000200300002e03000058496e70757447657453746174'
        '6500000000000000000000000000000000000000000000000000000000000000'
        '0000000000000000000001000000000000000000000000000000000000000000'
        '00000000000000000000000000000000000078696e707574315f342e646c6c00'
        '78696e707574315f332e646c6c0078696e707574395f315f302e646c6c00'
    ), (
        (0x1, 'abs', '.', 706),
        (0xe, 'rel', 'EXIT1P', -4),
        (0x13, 'abs', '.', 746),
        (0x20, 'rel', 'EXIT2P', -4),
        (0x3f, 'abs', 'PEEKMSG', 0),
        (0x58, 'abs', 'PSTATE', 0),
        (0x5f, 'abs', 'XIFN', 0),
        (0x6a, 'abs', 'PBTN', 0),
        (0x71, 'abs', 'PADPREV', 0),
        (0x85, 'abs', '.', 199),
        (0xb4, 'abs', 'HWND', 0),
        (0xba, 'abs', 'POSTMSG', 0),
        (0x117, 'abs', 'STATE', 0),
        (0x12d, 'abs', 'BTN', 0),
        (0x13f, 'rel', 'CAMSKIP', -4),
        (0x146, 'abs', 'BTN', 0),
        (0x173, 'abs', 'MODE', 0),
        (0x17c, 'abs', 'SUBMODE', 0),
        (0x185, 'abs', 'SUBMODE', 0),
        (0x1b1, 'abs', 'COND', 0),
        (0x1d1, 'abs', 'BTN', 0),
        (0x1de, 'abs', 'BTN', 0),
        (0x1e9, 'abs', 'BTN', 0),
        (0x1f2, 'abs', 'DZTHR1', 0),
        (0x1fd, 'abs', 'BTN', 0),
        (0x220, 'abs', 'BTN', 0),
        (0x25b, 'abs', 'XIFN', 0),
        (0x26e, 'abs', '.', 679),
        (0x275, 'abs', 'LOADLIB', 0),
        (0x281, 'abs', '.', 691),
        (0x288, 'abs', 'GETPROC', 0),
        (0x296, 'abs', 'XIFN', 0),
        (0x2a7, 'abs', '.', 786),
        (0x2ab, 'abs', '.', 800),
        (0x2af, 'abs', '.', 814),
        (0x2c6, 'abs', 'BINDS1', 0),
        (0x2ca, 'abs', 'LEV1A', 0),
        (0x2ce, 'abs', 'LEV1B', 0),
        (0x2d2, 'abs', 'MASK1A', 0),
        (0x2d6, 'abs', 'MASK1B', 0),
        (0x2da, 'abs', 'ACCEPT1', 0),
        (0x2de, 'abs', 'SCRATCH1', 0),
        (0x2e2, 'abs', 'KBHANDLER1', 0),
        (0x2e6, 'abs', 'CAMERA1', 0),
        (0x2ee, 'abs', 'BINDS2', 0),
        (0x2f2, 'abs', 'LEV2A', 0),
        (0x2f6, 'abs', 'LEV2B', 0),
        (0x2fa, 'abs', 'MASK2A', 0),
        (0x2fe, 'abs', 'MASK2B', 0),
        (0x302, 'abs', 'ACCEPT2', 0),
        (0x306, 'abs', 'SCRATCH2', 0),
        (0x30a, 'abs', 'KBHANDLER2', 0),
        (0x30e, 'abs', 'CAMERA2', 0),
    ), {
        'entry1p': 0x0,
        'entry2p': 0x12,
        'pump': 0x38,
        'pollpads': 0x44,
        'keytab': 0xc7,
        'keytab_end': 0xcf,
        'tick': 0xf9,
        'apply': 0x231,
        'resolve': 0x25a,
        'epilogue': 0x2a2,
        'dlltab': 0x2a7,
        'procname': 0x2b3,
        'block1': 0x2c2,
        'block2': 0x2ea,
        'dll14': 0x312,
        'dll13': 0x320,
        'dll910': 0x32e,
    }),
    'LEVERS': (bytes.fromhex(
        '837dfc0074288b53088b4b0cf60280750df601407508800a708009b0eb10f602'
        '40750bf601807506800ab08009705f5e5bc9c3'
    ), (
    ), {
    }),
    'TWIN': (bytes.fromhex(
        '6854000000e8fcffffff83c404e9fcffffff687c000000e8fcffffff83c404e9'
        'fcffffffe800e900ea00eb00ec00ed00ee00ef00e600e700e400e50020108040'
        '0000000001000200000000002010804000010002000000002400000000000000'
        '000000003c000000480000000000000000000000000000000000000001000000'
        '2400000000000000000000003c00000048000000000000000000000000000000'
        '00000000'
    ), (
        (0x1, 'abs', '.', 84),
        (0x6, 'rel', 'TICK', -4),
        (0xe, 'rel', 'EXIT1P', -4),
        (0x13, 'abs', '.', 124),
        (0x18, 'rel', 'TICK', -4),
        (0x20, 'rel', 'EXIT2P', -4),
        (0x58, 'abs', '.', 36),
        (0x5c, 'abs', 'LEV1A', 0),
        (0x60, 'abs', 'LEV1B', 0),
        (0x64, 'abs', '.', 60),
        (0x68, 'abs', '.', 72),
        (0x6c, 'abs', 'ACCEPT1', 0),
        (0x70, 'abs', 'SCR1', 0),
        (0x74, 'abs', 'KBD1P', 0),
        (0x78, 'abs', 'CAMERA1', 0),
        (0x80, 'abs', '.', 36),
        (0x84, 'abs', 'LEV2A', 0),
        (0x88, 'abs', 'LEV2B', 0),
        (0x8c, 'abs', '.', 60),
        (0x90, 'abs', '.', 72),
        (0x94, 'abs', 'ACC2', 0),
        (0x98, 'abs', 'SCR2', 0),
        (0x9c, 'abs', 'KBD2P', 0),
        (0xa0, 'abs', 'CAM2', 0),
    ), {
        'stub1p': 0x0,
        'stub2p': 0x12,
        'binds': 0x24,
        'maska': 0x3c,
        'maskb': 0x48,
        'block1': 0x54,
        'block2': 0x7c,
    }),
    'INTROWAIT': (bytes.fromhex(
        'e8fcffffff6a006a006a006a00ff742414ff150000000085c07509e80a000000'
        '85c075dcff2500000000a10000000085c0740f83f801743a6a08ffd0b8010000'
        '00c36875000000ff150000000085c07417688200000050ff150000000085c074'
        '07a300000000ebd0c705000000000100000031c0c36b65726e656c33322e646c'
        '6c00536c65657000'
    ), (
        (0x1, 'rel', 'POLLPADS', -4),
        (0x13, 'abs', 'PEEKMSG', 0),
        (0x26, 'abs', 'GETMSG', 0),
        (0x2b, 'abs', 'SLEEPFN', 0),
        (0x43, 'abs', '.', 117),
        (0x49, 'abs', 'LOADLIB', 0),
        (0x52, 'abs', '.', 130),
        (0x59, 'abs', 'GETPROC', 0),
        (0x62, 'abs', 'SLEEPFN', 0),
        (0x6a, 'abs', 'SLEEPFN', 0),
    ), {
        'introwait': 0x0,
        'nap': 0x2a,
        'kern32': 0x75,
        'sleepnm': 0x82,
    }),
    'KBPAGE': (bytes.fromhex(
        '833d00000000017510a1000000004883f8017605e9fcffffffe9fcffffff9090'
        '6a01ff3500000000e8fcffffff83c408e9fcffffff'
    ), (
        (0x2, 'abs', 'CURPLAYER', 0),
        (0xa, 'abs', 'DEVICES', 0),
        (0x15, 'rel', 'CROSSCHECK', -4),
        (0x1a, 'rel', 'KBACCEPT', -4),
        (0x24, 'abs', 'CURPLAYER', 0),
        (0x29, 'rel', 'DEFAULTS', -4),
        (0x31, 'rel', 'RESUME', -4),
    ), {
        'dupkey': 0x0,
        'default_button': 0x20,
    }),
    'BINDLIST': (bytes.fromhex(
        '50a1000000006bc07083b8000000000358c3909090909090e8e3ffffff740683'
        '7df810eb04837df8217c0883c404e9fcffffffc390909090e8c3ffffff74088b'
        '04c500000000c38b04c500000000c390e8abffffff74088a04c504000000c38a'
        '04c504000000c3'
    ), (
        (0x2, 'abs', 'CURPLAYER', 0),
        (0xb, 'abs', 'BLOCKS', 0),
        (0x2f, 'rel', 'FILLDONE', -4),
        (0x42, 'abs', 'PADLIST', 0),
        (0x4a, 'abs', 'KEYLIST', 0),
        (0x5a, 'abs', 'PADLIST', 4),
        (0x62, 'abs', 'KEYLIST', 4),
    ), {
        'devcur': 0x0,
        'fillcount': 0x18,
        'fillname': 0x38,
        'storeid': 0x50,
    }),
    'BINDMAP': (bytes.fromhex(
        '50a1000000006bc07083b8000000000358c3909090909090e8e3ffffff740683'
        '7df410eb04837df4217c0883c404e9fcffffffc390909090e8c3ffffff740839'
        '0cc504000000c3390cc504000000c3908b4424046bc87081c1380000006bc018'
        '05000000006a185051e8fcffffff83c40cc3'
    ), (
        (0x2, 'abs', 'CURPLAYER', 0),
        (0xb, 'abs', 'BLOCKS', 0),
        (0x2f, 'rel', 'MAPDONE', -4),
        (0x42, 'abs', 'PADLIST', 4),
        (0x4a, 'abs', 'KEYLIST', 4),
        (0x59, 'abs', 'BLOCKS', 56),
        (0x61, 'abs', 'SIMPLEDEF', 0),
        (0x6a, 'rel', 'MEMCPY', -4),
    ), {
        'devcur': 0x0,
        'mapcount': 0x18,
        'mapid': 0x38,
        'simple_defaults': 0x50,
    }),
    'BINDBLOCK': (bytes.fromhex(
        '83b8000000000374060508000000c30538000000c39090906bd07083ba000000'
        '00036bc01874060500d66600c30500000000c39083b9000000000374088a8441'
        '08000000c38a844138000000c3909090240f3c0a720204270430880747c3'
    ), (
        (0x2, 'abs', 'BLOCKS', 0),
        (0xa, 'abs', 'BLOCKS', 8),
        (0x10, 'abs', 'BLOCKS', 56),
        (0x1d, 'abs', 'BLOCKS', 0),
        (0x2e, 'abs', 'SIMPLEDEF', 0),
        (0x36, 'abs', 'BLOCKS', 0),
        (0x40, 'abs', 'BLOCKS', 8),
        (0x48, 'abs', 'BLOCKS', 56),
    ), {
        'blockaddr': 0x0,
        'defsource': 0x18,
        'preselbind': 0x34,
        'hexchar': 0x50,
    }),
    'INISAVE': (bytes.fromhex(
        '5356578b9d38ffffff6bf3708db6380000008bbd34ffffff31c98a040e88c2c0'
        'e804e8fcffffff88d0e8fcffffff4183f9187ce6c607006bc3110500000000ff'
        'b534ffffff50e8fcffffff83c4085f5e5be9fcffffff'
    ), (
        (0xe, 'abs', 'BLOCKS', 56),
        (0x23, 'rel', 'HEXCHAR', -4),
        (0x2a, 'rel', 'HEXCHAR', -4),
        (0x3b, 'abs', 'INIKEYS', 0),
        (0x47, 'rel', 'WRITELINE', -4),
        (0x52, 'rel', 'CASEB', -4),
    ), {
        'savesimple': 0x0,
    }),
    'INILOAD': (bytes.fromhex(
        '5356578b5c24106bfb708dbf380000006bc3110500000000e8fcffffff83ef30'
        '6bc3138d8022000000e8fcffffff833c9d000000000375138d77306bfb188dbf'
        '00000000b906000000f3a55f5e5bc3'
    ), (
        (0xc, 'abs', 'BLOCKS', 56),
        (0x14, 'abs', 'INIKEYS', 0),
        (0x19, 'rel', 'PARSE12', -4),
        (0x25, 'abs', 'INIKEYS', 34),
        (0x2a, 'rel', 'PARSE12', -4),
        (0x31, 'abs', 'DEVICES', 0),
        (0x40, 'abs', 'LIVE', 0),
    ), {
        'loadsimple': 0x0,
    }),
    'BLOCKCUR': (bytes.fromhex(
        '518b0d000000006bc97083b900000000035974078d8008000000c38d80380000'
        '00c390909090909050518b45086bc8708b048500000000398100000000595875'
        '05e9fcffffffc3'
    ), (
        (0x3, 'abs', 'CURPLAYER', 0),
        (0xc, 'abs', 'BLOCKS', 0),
        (0x16, 'abs', 'BLOCKS', 8),
        (0x1d, 'abs', 'BLOCKS', 56),
        (0x33, 'abs', 'DEVICES', 0),
        (0x39, 'abs', 'BLOCKS', 0),
        (0x42, 'rel', 'MEMCPY', -4),
    ), {
        'blockcur': 0x0,
        'syncshim': 0x28,
    }),
    'INIPARSE': (bytes.fromhex(
        '50e8fcffffff83c40485c0741e89c631c9e816000000c0e00488c2e80c000000'
        '08d088040f4183f9187ce6c30fb606462c303c0976022c27c30000006a015e8d'
        '04b500000000506bc60c050000000050e8fcffffff83c4084e79e4c3'
    ), (
        (0x2, 'rel', 'FINDLINE', -4),
        (0x42, 'abs', 'DZSTR1', 0),
        (0x4b, 'abs', 'DZKEYS', 0),
        (0x51, 'rel', 'WRITELINE', -4),
    ), {
        'parse12': 0x0,
        'nibble': 0x2c,
        'dzsave': 0x3c,
    }),
    'PAGESEC': (bytes.fromhex(
        'e8fcffffff750f837df81a7d01c383c404e9fcffffff83c404e9fcffffffe8fc'
        'ffffff750f837dec1a7d01c383c404e9fcffffff83c404e9fcffffff'
    ), (
        (0x1, 'rel', 'DEVCUR', -4),
        (0x12, 'rel', 'DIGITLOOP', -4),
        (0x1a, 'rel', 'LISTLOOP', -4),
        (0x1f, 'rel', 'DEVCUR', -4),
        (0x30, 'rel', 'STORESHIFT', -4),
        (0x38, 'rel', 'STORELIST', -4),
    ), {
        'fillsec': 0x0,
        'storesec': 0x1e,
    }),
    'PAGESEL': (bytes.fromhex(
        'e8fcffffff750f837df41a7d01c383c404e9fcffffff83c404e9fcffffffe8fc'
        'ffffff75048345f42483c404e9fcffffff00000069c14701000089049d000000'
        '00880c9d0300000088c8d40a6605303086c46689049d00000000c3'
    ), (
        (0x1, 'rel', 'DEVCUR', -4),
        (0x12, 'rel', 'SELDIGITS', -4),
        (0x1a, 'rel', 'SELLIST', -4),
        (0x1f, 'rel', 'DEVCUR', -4),
        (0x2d, 'rel', 'SELSET', -4),
        (0x3d, 'abs', 'DZTHR1', 0),
        (0x44, 'abs', 'DZSTR1', 3),
        (0x56, 'abs', 'DZSTR1', 0),
    ), {
        'selsec': 0x0,
        'selidx': 0x1e,
        'dzseed': 0x34,
    }),
    'COMMITDEV': (bytes.fromhex(
        '89048d0000000083f801740583f80375275657516bf1708db60800000083f803'
        '750383c6306bf9188dbf00000000b906000000f3a5595f5ec3'
    ), (
        (0x3, 'abs', 'DEVICES', 0),
        (0x19, 'abs', 'BLOCKS', 8),
        (0x2a, 'abs', 'LIVE', 0),
    ), {
        'commitdev': 0x0,
    }),
    'INIALL': (bytes.fromhex(
        '6a016a00e8fcffffff6a016a01e8fcffffff83c410536a015b6bc30c05000000'
        '0050e8fcffffff5a6a285985c0741f668b00662d30303c09771480fc09770f86'
        'c4d50a3c5f77073c0572030fb6c8e8fcffffff4b79c35bc3'
    ), (
        (0x5, 'rel', 'LOADSIMPLE', -4),
        (0xe, 'rel', 'LOADSIMPLE', -4),
        (0x1d, 'abs', 'DZKEYS', 0),
        (0x23, 'rel', 'FINDLINE', -4),
        (0x4f, 'rel', 'DZSEED', -4),
    ), {
        'iniall': 0x0,
    }),
    'DEVORDER': (bytes.fromhex(
        '030001020405060701020300040506078b800000000083f80777070fb6800000'
        '0000c38b45f483f80777070fb680080000008b4decc3'
    ), (
        (0x12, 'abs', 'BLOCKS', 0),
        (0x1e, 'abs', '.', 0),
        (0x2e, 'abs', '.', 8),
    ), {
        'posof': 0x0,
        'devof': 0x8,
        'posshim': 0x10,
        'devshim': 0x23,
    }),
    'F11PAUSE': (bytes.fromhex(
        '6a00e8fcffffff83c4046a006800000000ff750868e7e7e7e76a00ff15000000'
        '0050ffd3e8fcffffffc30000be000000006a035f8b068b0031c983f8010f94c1'
        '51ff7604ff74240cff150000000083c6084f75e0c20400'
    ), (
        (0x3, 'rel', 'GPAUSE', -4),
        (0xd, 'abs', 'DLGPROC', 0),
        (0x1d, 'abs', 'GETMODULE', 0),
        (0x25, 'rel', 'GRESUME', -4),
        (0x2d, 'abs', 'CHECKS', 0),
        (0x4a, 'abs', 'CHECKDLGBTN', 0),
    ), {
        'f11wrap': 0x0,
        'f11checks': 0x2c,
    }),
    'VOXT': (bytes.fromhex(
        '89d381f9419c00000f84bb00000083f95474766a015f8d47525053ff15000000'
        '008d34bd00000000566a036a0d50ff15000000000fb60683e83083f80977190f'
        'b6560183ea3083fa0977056bc00a01d08d50fb83fa5a76040fb64603ba000000'
        '00803a0074085389c18bdfffd25b4f79a5b8000000008038007402ffd06a0053'
        'ff150000000031c0c3ba00000000803a0074336a015f536a28598bdfffd25b4f'
        '79f46a015f8d47525053ff15000000008d14bd00000000526a006a0c50ff1500'
        '0000004f79df31c0c36a0053ff150000000068419c0000596a0158c3'
    ), (
        (0x1d, 'abs', 'GETDLGITEM', 0),
        (0x24, 'abs', 'DZSTR1', 0),
        (0x30, 'abs', 'SENDMSG', 0),
        (0x5d, 'abs', 'DZSEED', 0),
        (0x72, 'abs', 'DZSAVE', 0),
        (0x82, 'abs', 'ENDDIALOG', 0),
        (0x8a, 'abs', 'DZSEED', 0),
        (0xac, 'abs', 'GETDLGITEM', 0),
        (0xb3, 'abs', 'DZSTR1', 0),
        (0xbf, 'abs', 'SENDMSG', 0),
        (0xce, 'abs', 'ENDDIALOG', 0),
    ), {
        'annex': 0x0,
    }),
    'MOVIE': (bytes.fromhex(
        '5589e583ec405356578b7d08a1000000008947f4a1000000008947f031f66858'
        '010000ff150000000085c0742b686201000050ff150000000085c0741b89c368'
        '73010000ff150000000085c0740a687e01000050ffd389c685f675068b350000'
        '00008d45f050ff7708ffd685c00f84e00000008b45f82b45f08945d885c00f8e'
        'cf0000008b45fc2b45f48945d485c00f8ebe0000000fbf47108945d085c00f8e'
        'af0000000fbf47148945cc85c00f8ea00000008b45d8f76dcc89c38b45d4f76d'
        'd039c37f118b45d88945c8f76dccf77dd08945c4eb0f8b45d48945c4f76dd0f7'
        '7dcc8945c88b45d82b45c8d1f88947f48b45d42b45c4d1f88947f08b45c88947'
        '108b45c48947146a01ff75c4ff75c8ff77f0ff77f4ff3500000000ff15000000'
        '0031c08945dc8945e08945e48b45c88945e88b45c48945ec8d45dc5068000005'
        '006842080000ff3500000000a100000000ffd05f5e5bc9c364647261772e646c'
        '6c00444447657450726f6341646472657373007573657233322e646c6c004765'
        '74436c69656e745265637400'
    ), (
        (0xd, 'abs', 'MOVIEX', 0),
        (0x15, 'abs', 'MOVIEY', 0),
        (0x1f, 'abs', '.', 344),
        (0x25, 'abs', 'GETMODULE', 0),
        (0x2e, 'abs', '.', 354),
        (0x35, 'abs', 'GETPROC', 0),
        (0x40, 'abs', '.', 371),
        (0x46, 'abs', 'GETMODULE', 0),
        (0x4f, 'abs', '.', 382),
        (0x5e, 'abs', 'GETCLIENT', 0),
        (0x117, 'abs', 'MOVIEHWND', 0),
        (0x11d, 'abs', 'MOVEWINDOW', 0),
        (0x148, 'abs', 'MOVIEDEV', 0),
        (0x14d, 'abs', 'MCISEND', 0),
    ), {
        'movie_place': 0x0,
        's_ddraw': 0x158,
        's_ddgpa': 0x162,
        's_user32': 0x173,
        's_getclient': 0x17e,
    }),
    'CREDITS': (bytes.fromhex(
        'a0000000000a05000000008a1500000000a200000000803d0000000002753284'
        'c0742e84d27421803d00000000007428fe0500000000803d000000003c7219c6'
        '050000000003eb10c6050000000001eb07c6050000000000c705000000000000'
        '0000c3'
    ), (
        (0x1, 'abs', 'ACCEPT1', 0),
        (0x7, 'abs', 'CAMERA1', 0),
        (0xd, 'abs', 'PREV', 0),
        (0x12, 'abs', 'PREV', 0),
        (0x18, 'abs', 'PHASE', 0),
        (0x29, 'abs', 'HELD', 0),
        (0x32, 'abs', 'HELD', 0),
        (0x38, 'abs', 'HELD', 0),
        (0x41, 'abs', 'PHASE', 0),
        (0x4a, 'abs', 'HELD', 0),
        (0x53, 'abs', 'HELD', 0),
        (0x5a, 'abs', 'FLAG', 0),
    ), {
        'skip': 0x0,
    }),
    'NAMEENTRY': (bytes.fromhex(
        'a0000000000a0500000000240188c4a0000000000a05000000008a1500000000'
        'a20000000084e4750884c0740784d27503b001c330c0c3'
    ), (
        (0x1, 'abs', 'EDGEA', 0),
        (0x7, 'abs', 'EDGEB', 0),
        (0x10, 'abs', 'ACCEPT1', 0),
        (0x16, 'abs', 'CAMERA1', 0),
        (0x1c, 'abs', 'PREV', 0),
        (0x21, 'abs', 'PREV', 0),
    ), {
        'confirm': 0x0,
    }),
    'CAMSKIP': (bytes.fromhex(
        '833d00000000047518833d000000000c7409833d000000001475068b5324c602'
        '80c3'
    ), (
        (0x2, 'abs', 'MODE', 0),
        (0xb, 'abs', 'SUBMODE', 0),
        (0x14, 'abs', 'SUBMODE', 0),
    ), {
        'camskip': 0x0,
    }),
    'OVERLAY': (bytes.fromhex(
        'ff742404e8fcffffff83c404833d0000000004756b833d00000000207562803d'
        '00000000027559803d00000000007450b840010000bab8010000f60500000000'
        '04740d833d00000000007404d1f8d1fa8b0d00000000518b0d00000000890d00'
        '0000006a016800ff000052506881000000e8fcffffff83c41459890d00000000'
        'c3484f4c4420544f20534b495000'
    ), (
        (0x5, 'rel', 'ORIG', -4),
        (0xe, 'abs', 'MODE', 0),
        (0x17, 'abs', 'SUBMODE', 0),
        (0x20, 'abs', 'PHASE', 0),
        (0x29, 'abs', 'HELD', 0),
        (0x3c, 'abs', 'WIDE', 0),
        (0x45, 'abs', 'HALF', 0),
        (0x52, 'abs', 'PRIMARY', 0),
        (0x59, 'abs', 'BACK', 0),
        (0x5f, 'abs', 'PRIMARY', 0),
        (0x6d, 'abs', '.', 129),
        (0x72, 'rel', 'DRAW', -4),
        (0x7c, 'abs', 'PRIMARY', 0),
    ), {
        'overlay': 0x0,
        'prompt': 0x81,
    }),
    'TITLEVER': (bytes.fromhex(
        'a10000000083f8017545a10000000083f806740a83f817740583f8117531ba55'
        '00000031c9803c0a00740341ebf7b84f00000029c829c86a3250e8fcffffff83'
        'c4086855000000e8fcffffff83c404a100000000c30000000000000000000000'
        '00000000000000000000000000'
    ), (
        (0x1, 'abs', 'MODE', 0),
        (0xb, 'abs', 'SUBMODE', 0),
        (0x1f, 'abs', '.', 85),
        (0x3b, 'rel', 'CURSOR', -4),
        (0x43, 'abs', '.', 85),
        (0x48, 'rel', 'PRINT', -4),
        (0x50, 'abs', 'PRIMARY', 0),
    ), {
        'titlever': 0x0,
        'text': 0x55,
    }),
    'PAD_COND': (bytes.fromhex(
        '0200000000100000020000000020000002000000004000000200000000800000'
        '0200000000010000020000000002000003000200400000000300030040000000'
        '01000600c83200000000060038cdffff0000040038cdffff01000400c8320000'
        '01000a00c832000000000a0038cdffff0000080038cdffff01000800c8320000'
    ), (
    ), {
    }),
    'PAD_BINDS': (bytes.fromhex(
        '00000000e000000000000000e100000000000000e200000000000000e3000000'
        '00000000e400000000000000e500000000000000e600000000000000e7000000'
        '00000000e800000000000000e900000000000000ea00000000000000eb000000'
        '00000000ec00000000000000ed00000000000000ee00000000000000ef000000'
    ), (
        (0x0, 'abs', ('PAD_NAMES', 0), 0),
        (0x8, 'abs', ('PAD_NAMES', 2), 0),
        (0x10, 'abs', ('PAD_NAMES', 4), 0),
        (0x18, 'abs', ('PAD_NAMES', 6), 0),
        (0x20, 'abs', ('PAD_NAMES', 8), 0),
        (0x28, 'abs', ('PAD_NAMES', 11), 0),
        (0x30, 'abs', ('PAD_NAMES', 14), 0),
        (0x38, 'abs', ('PAD_NAMES', 17), 0),
        (0x40, 'abs', ('PAD_NAMES', 20), 0),
        (0x48, 'abs', ('PAD_NAMES', 26), 0),
        (0x50, 'abs', ('PAD_NAMES', 34), 0),
        (0x58, 'abs', ('PAD_NAMES', 42), 0),
        (0x60, 'abs', ('PAD_NAMES', 51), 0),
        (0x68, 'abs', ('PAD_NAMES', 57), 0),
        (0x70, 'abs', ('PAD_NAMES', 65), 0),
        (0x78, 'abs', ('PAD_NAMES', 73), 0),
    ), {
    }),
    'PAD_NAMES': (bytes.fromhex(
        '41004200580059004c42005242004c54005254004c53205570004c5320446f77'
        '6e004c53204c656674004c5320526967687400525320557000525320446f776e'
        '005253204c6566740052532052696768740047616d65706164202858496e7075'
        '7429005477696e2d737469636b202858496e70757429004b6579626f61726420'
        '2853696d706c6529004b6579626f61726420285265616c290031502044656164'
        '7a6f6e6500325020446561647a6f6e6500'
    ), (
    ), {
        'dzkeys': 0x99,
    }),
    'PAD_DEVLIST': (bytes.fromhex(
        '0000000000000000000000000000000000000000000000000000000000000000'
    ), (
        (0x0, 'abs', ('PAD_NAMES', 82), 0),
        (0x4, 'abs', ('PAD_NAMES', 99), 0),
        (0x8, 'abs', ('PAD_NAMES', 119), 0),
        (0xc, 'abs', ('PAD_NAMES', 137), 0),
    ), {
    }),
    'PAD_SIMPLEDEF': (bytes.fromhex(
        '11001f001e002000100012002e0022002d0013002f002100c700cf00d300d100'
        'd200c900520051004f004c0053005000'
    ), (
    ), {
    }),
    'PAD_INIKEYS': (bytes.fromhex(
        '31502053696d706c652041737369676e0032502053696d706c65204173736967'
        '6e003150204b6579626f6172642041737369676e003250204b6579626f617264'
        '2041737369676e00'
    ), (
    ), {
    }),
    'EXTRAS_DATA': (bytes.fromhex(
        '5553455233322e444c4c004469616c6f67426f78496e64697265637450617261'
        '6d41000000000000479c0000000000005b9c0000000000005c9c0000'
    ), (
        (0x24, 'abs', 'NOSHOT', 0),
        (0x2c, 'abs', 'SEMUTE', 0),
        (0x34, 'abs', 'CDMUTE', 0),
    ), {
        'user32': 0x0,
        'dlgboxproc': 0xb,
        'checks': 0x24,
    }),
}
# BLOBS BLOB END


def cave_va(name, build, blobs=None):
    """Where a blob goes in this build, as a virtual address."""
    blobs = BLOBS if blobs is None else blobs
    at = build.caves[name]
    if isinstance(at, tuple):
        inner, label = at
        return cave_va(inner, build, blobs) + label_at(inner, label, blobs)
    return at


def label_at(name, label, blobs=None):
    """A label's offset in a blob; 'end' is its length."""
    blobs = BLOBS if blobs is None else blobs
    code, _fixups, labels = blobs[name]
    if label == 'end':
        return len(code)
    return label if isinstance(label, int) else labels[label]


def symbol_va(sym, build, blobs=None):
    """A symbol's address in this build: a place in the game, or (blob,
    label) for a place in one of ours."""
    if isinstance(sym, tuple):
        inner, label = sym
        return cave_va(inner, build, blobs) + label_at(inner, label, blobs)
    value = build.symbols[sym]
    return symbol_va(value, build, blobs) if isinstance(value, tuple) else value


def link(name, build, blobs=None, base=None):
    """A blob's code with its fixups filled in for this build.

    `base` overrides the cave, for a blob whose address is only known at
    apply time; None means the blob must not name itself."""
    blobs = BLOBS if blobs is None else blobs
    code, fixups, _labels = blobs[name]
    if base is None and name in build.caves:
        base = cave_va(name, build, blobs)
    out = bytearray(code)
    for at, kind, sym, addend in fixups:
        if sym == '.' or kind == 'rel':
            if base is None:
                raise ValueError('%s names its own address, but has none '
                                 'in this build' % name)
        target = base if sym == '.' else symbol_va(sym, build, blobs)
        value = target + addend
        if kind == 'rel':
            value -= base + at
        struct.pack_into('<I', out, at, value & 0xffffffff)
    return bytes(out)


def call(off, target, pad=0, op='e8'):
    """The hex a site writes to call a symbol: the opcode, the rel32 to the
    target, and `pad` bytes of nop over the rest of what it replaced."""
    rel = symbol_va(target, RETAIL) - (RETAIL.va(off) + 5)
    return op + struct.pack('<i', rel).hex() + '90' * pad


def jump(off, target, pad=0):
    return call(off, target, pad, op='e9')


def abs32(target):
    """A symbol's address as the four bytes an instruction carries, as hex."""
    return struct.pack('<I', symbol_va(target, RETAIL)).hex()


def site(name):
    """The file offset a blob is written at, from its cave."""
    return RETAIL.off(cave_va(name, RETAIL))


# The blobs as the retail build writes them. The names below are what the
# site table and the apply code use.
TIMER_CODE = link('TIMER', RETAIL)
PADX_CODE = link('PADX', RETAIL)
# padxinput.asm pins six addresses inside itself and pads to this length,
# because levers.asm is written at the byte after it.
PADX_LEN = 830
LEVERS_CODE = link('LEVERS', RETAIL)
TWIN_CODE = link('TWIN', RETAIL)
INTROWAIT_CODE = link('INTROWAIT', RETAIL)
KBPAGE_CODE = link('KBPAGE', RETAIL)
BINDLIST_CODE = link('BINDLIST', RETAIL)
BINDMAP_CODE = link('BINDMAP', RETAIL)
BINDBLOCK_CODE = link('BINDBLOCK', RETAIL)
INISAVE_CODE = link('INISAVE', RETAIL)
INILOAD_CODE = link('INILOAD', RETAIL)
BLOCKCUR_CODE = link('BLOCKCUR', RETAIL)
INIPARSE_CODE = link('INIPARSE', RETAIL)
PAGESEC_CODE = link('PAGESEC', RETAIL)
PAGESEL_CODE = link('PAGESEL', RETAIL)
COMMITDEV_CODE = link('COMMITDEV', RETAIL)
INIALL_CODE = link('INIALL', RETAIL)
DEVORDER_CODE = link('DEVORDER', RETAIL)
F11PAUSE_CODE = link('F11PAUSE', RETAIL)
MOVIE_CODE = link('MOVIE', RETAIL)
CREDITS_CODE = link('CREDITS', RETAIL)
NAMEENTRY_CODE = link('NAMEENTRY', RETAIL)
CAMSKIP_CODE = link('CAMSKIP', RETAIL)
OVERLAY_CODE = link('OVERLAY', RETAIL)
TITLEVER_CODE = link('TITLEVER', RETAIL)
# voxt.asm rides in the appended .voxt section and reaches everything through
# absolute addresses, so it links without a cave.
VOXT_CODE = link('VOXT', RETAIL)
PAD_COND = link('PAD_COND', RETAIL)
PAD_BINDS = link('PAD_BINDS', RETAIL)
PAD_NAMES = link('PAD_NAMES', RETAIL)
PAD_DEVLIST = link('PAD_DEVLIST', RETAIL)
PAD_SIMPLEDEF = link('PAD_SIMPLEDEF', RETAIL)
PAD_INIKEYS = link('PAD_INIKEYS', RETAIL)
EXTRAS_DATA = link('EXTRAS_DATA', RETAIL)

# debugbox.asm assembles as one run, but the patch writes it as two sites:
# the byte in front of the dialog procedure is alignment padding that has
# never been written, so it is dropped rather than assume what the original
# file has there.
DEBUGBOX_SPLIT = label_at('DEBUGBOX', 'dlgproc') - 1
_dbg = link('DEBUGBOX', RETAIL)
DEBUGBOX_HOOK = _dbg[:DEBUGBOX_SPLIT]
DEBUGBOX_PROC = _dbg[DEBUGBOX_SPLIT + 1:]
DBGPROC_AT = site('DEBUGBOX') + DEBUGBOX_SPLIT + 1
DBGPROC_VA = cave_va('DEBUGBOX', RETAIL) + DEBUGBOX_SPLIT + 1
F11PAUSE_AT = site('F11PAUSE')

# DIALOGS BLOB BEGIN
EXTRAS_TPL = bytes.fromhex(
    'c000c88000000000130000000000d400a0000000000045007800740072006100'
    '7300000008004d0053002000530061006e007300200053006500720069006600'
    '0000000007000050000000000a000400c0004e00ffffffff8000440065006200'
    '750067000000000003000150000000001000120038000c00479cffff80004e00'
    '6f002000730068006f0074000000000003000150000000005000120024000c00'
    '5b9cffff80005300450000000000000003000150000000007c00120024000c00'
    '5c9cffff80004300440000000000000000000150000000001000280032000e00'
    '619cffff80004b0069006c006c00200031005000000000000000015000000000'
    '4600280032000e00629cffff80004b0069006c006c0020003200500000000000'
    '00000150000000007c00280032000e005100ffff800043007200650064006900'
    '7400730000000000000001500000000010003e0048000e00679cffff80005300'
    '63006f00720065006b0065006500700069006e00670000000000000007000050'
    '000000000a005600c0002e00ffffffff800053007400690063006b0020004400'
    '6500610064007a006f006e0065002000250020005b002000580049006e007000'
    '7500740020005d00000000000000005000000000120064000c000a00ffffffff'
    '82003100500000000000000000208150000000002000620016000c005200ffff'
    '810000000000000000000050000000003900640008000a00ffffffff82002500'
    '0000000000000050000000004a0064000c000a00ffffffff8200320050000000'
    '0000000000208150000000005800620016000c005300ffff8100000000000000'
    '00000050000000007100640008000a00ffffffff820025000000000000000150'
    '000000008c00610036000e005400ffff8000440065006600610075006c007400'
    '730000000000000000000050000000001200740064000a00ffffffff82006d00'
    '69006e002000300035002c0020006d0061007800200039003500000000000000'
    '00000150000000000a008c003c000e00419cffff800051007500690074002000'
    '470061006d00650000000000000001500000000098008c0032000e000200ffff'
    '800043006c006f007300650000000000'
)

F5_STOCK = bytes.fromhex(
    '230008002c04ffff800046006100730074000000000000000900015000000000'
    '85005400290008002d04ffff800053006d006f006f0074006800000000000000'
    '090003500000000044006200240008002e04ffff800054007900700065003100'
    '0000000009000150000000006e006200240008002f04ffff8000540079007000'
    '6500320000000000090001500000000098006200240008003104ffff80005400'
    '79007000650033000000000000000250000000000f00050022000800ffffffff'
    '8200530063007200650065006e0000000000000000000250000000000f005400'
    '32000800ffffffff82004d006f00740069006f006e0020005400790070006500'
    '0000000000000250000000000f00600032000a00ffffffff8200530063007200'
    '650065006e002000530070006c00690074000000000000000700005000000000'
    '10001900af001900ffffffff8000540065007800740075007200650000000000'
    '070000500000000010003700af001800ffffffff800044006900730070006c00'
    '6100790020004f0062006a006500630074007300000000000000025000000000'
    '0f000f002b000900ffffffff82004600690065006c0064002000470072006100'
    '7000680069006300000000000000025000000000160069001a000800ffffffff'
    '820028003200500020005600530029000000000000000000'
)

F5_FPS = bytes.fromhex(
    '290008002c04ffff800033003000200046005000530000000000000009000150'
    '0000000085005400290008002d04ffff80003600300020004600500053000000'
    '00000000090003500000000044006200240008002e04ffff8000540079007000'
    '650031000000000009000150000000006e006200240008002f04ffff80005400'
    '790070006500320000000000090001500000000098006200240008003104ffff'
    '8000540079007000650033000000000000000250000000000f00050022000800'
    'ffffffff8200530063007200650065006e000000000000000000025000000000'
    '0f00540032000800ffffffff82004d006f00740069006f006e00200054007900'
    '700065000000000000000250000000000f00600032000a00ffffffff82005300'
    '63007200650065006e002000530070006c006900740000000000000007000050'
    '0000000010001900af001900ffffffff80005400650078007400750072006500'
    '00000000070000500000000010003700af001800ffffffff8000440069007300'
    '70006c006100790020004f0062006a0065006300740073000000000000000250'
    '000000000f000f002b000900ffffffff82004600690065006c00640020004700'
    '720061007000680069006300000000000000025000000000160069001a000800'
    'ffffffff8200280032005000200056005300290000000000'
)
# DIALOGS BLOB END

# Where the blob lands, where the version goes inside it, and how much room
# it has. The blob carries zeros there: the version comes from the git tag,
# and the blobs are built from source, so the patcher writes the string in
# afterwards rather than the patch table carrying it.
TITLEVER_AT = site('TITLEVER')
TITLEVER_LEN = 24


def _check_titlever():
    """The version goes in the last TITLEVER_LEN bytes of the blob, which
    titlever.asm reserves as zeros. Growing the code without growing this
    would write the version over the end of it."""
    if any(TITLEVER_CODE[-TITLEVER_LEN:]):
        raise AssertionError('the last %d bytes of the titlever blob are not '
                             'zeros' % TITLEVER_LEN)
    if not TITLEVER_CODE[-TITLEVER_LEN - 1]:
        raise AssertionError('the titlever text field runs further back than '
                             '%d bytes' % TITLEVER_LEN)


_check_titlever()


def version_text():
    """What the title screen shows, truncated to the field it goes in.

    No v in front of the number: a tag build reads 0.8.7 but a commit build
    reads a short SHA, and "vo_patch v1a2b3c4" is nonsense."""
    return ('%s %s' % (NAME, VERSION))[:TITLEVER_LEN - 1]


def stamp_version(buf):
    """Write the version into the titlever blob already sitting in buf.

    Deliberately not a patch site. Every other byte the patcher writes is the
    same for everyone, which is what lets selftest.py compare the whole
    output against one digest; these two dozen change with the tag. So they
    are written here, after apply_selected has run, and selftest checks the
    file without them."""
    text = version_text().encode('ascii') + b'\x00'
    at = TITLEVER_AT + len(TITLEVER_CODE) - TITLEVER_LEN
    buf[at:at + len(text)] = text


# Each site: (offset, original, patched).

# The title and scoreboard prompt is not text: it is 42x3 cells of 8x8 tiles
# drawn from a table of tile indices. Only the indices are in the executable;
# the artwork is in escrgame.bin. Kept here as a 1bpp 336x24 bitmap, an
# eighth the size of the tiles it expands to.
BANNER_BITS = bytes.fromhex(
    '0000000000000000000000000000000000000000000000000000000000000000'
    '0000000000000000000000000000000000000000000000000000000000000000'
    '000000000000000000000000000000000000000000ffffff0000000000000000'
    '00000000000000fff800003fffff8000000000000000000000000000000000ff'
    'ffffc00000000000000000000000000001fffc00003ffffff000000003ff80ff'
    'e00000000000000001fffffff00000000000000000000000000003fffc00007f'
    'fffff800000003ff80ffe00000000000000001fffffff8000000000000000000'
    '0000000007fffc00007ff8fffc00000003ff81ffc00000000000000001ffe0ff'
    'f8001fc0ff8000ffe001ffc000000ffffe0000fff03ffc00000007ff81ffc000'
    'ffc000007f8003ffc07ffffffffffff81fffff3ffffe00001ffffe0000fff03f'
    'fdffe07fffffffffffdffffc1ffffff003ffc07ffffffffffffe7ffffffffffe'
    '00001ffffe0000fff07ff9ffc0ffffffffffffffffff1ffffff803ffc07fffff'
    'fffffffffffffffffffe00003fffff0000fff1fff1ffc0ffffffffffffffffff'
    'bffffffc03ff81ffffffe7ff83ffffc01fff803e0000fff7ff0001ffffffc3ff'
    'c0ffeffe07ff87ffc3ffffff3ffc07ffffffffff0fff03ffffc003ff80000000'
    'ffe7ff0001ffffff83ff81ffcffe07ff07ff81fffffc3ffc07ffffffdffe1fff'
    'ffffffff81ffff000001ffc7ff8001ffffffe3ff81ffdffc07ff0fff01fffff8'
    '3ffc07ffffff3ffc1ffffffffffff1fffff00003ff87ff8003ffc1fff3ff01ff'
    'dffc07ff0ffe01fffff03ff80ffffff03ff81ffffffffffffcfffff80007ff83'
    'ff8003ffc07ff7ff03ffbffc0ffe1ffe01fffff03ff80fff00003ff81ffc0000'
    '0ffffe1ffffc000fffffff8003ff807ff7ff03ffbff80ffe1ffc03ffffe07ff8'
    '1ffe00007ff81ffc0000001ffe003ffc001fffffffc007ff80ffffff07ffbff8'
    '0ffe1ffe07ffffe07ff01ffe00007ff01ffc003f800fff001ffc003fffffffc0'
    '07ff81ffefff0fffbff81ffe1ffe0fffffe07ff01ffe00007ff01fff87fffc1f'
    'fff83ffc007ff801ffc00fffffffefffffff3fffffffffffffffffc0fff03ffc'
    '00007ff00ffffffffffffffffff800fff001ffe00fffffff87ffffff3fffffff'
    'fffffff9ffc0ffe03ffc0000ffe003ffffffffffffffffe000ffe000ffe00fff'
    'fffc03ffffff1fffe7fff9ffffe1ffc0ffe03ff80000ffe000ffffc3ffff83ff'
    'ff0000ffc000ffe00fffff8001ffeffe03ffe0fff07fff01ff80ffc000000000'
    '0000000000000000000000000000000000000000000000000000000000000000'
    '0000000000000000000000000000000000000000000000000000000000000000'
    '00000000000000000000000000000000'
)
# CREDITLINE BLOB BEGIN - tools/vocredits.py
CREDIT1_W = 42
CREDIT1_H = 3
CREDIT1_BITS = bytes.fromhex(
    '000000000000000000000000000000000000000000007fe0000000000c000180'
    '0000000000000600000000000000000000000000000000000000000000000000'
    '7ff00000c0000c00018000000000000006000000000000000000000000000000'
    '0000000000000000000060380000c0000c000180000000000000000000000000'
    '000000000000000000000000000000000000000060180000c0000c0001800000'
    '0000000000000000000000000000000000000000000000000000000000006018'
    '1f03f03e0cf0019e06030067803e063303e00000000000000000000000000000'
    '000000000000000060183f83f07f0df801ff0603006fc07f063f07f000000000'
    '000000000000000000000000000000000000601871c0c0e38f1c01e383060078'
    'e0e386380e3800000000000000000000000000000000000000000000603060c0'
    'c0c18e0c01c1c306007070c186380c1800000000000000000000000000000000'
    '0000000000007ff000c0c1800c0c0180c306006030018630180c000000000000'
    '000000000000000000000000000000007fe00fc0c1800c0c0180c18c0060301f'
    '8630180c0000000000000000000000000000000000000000000060007fc0c180'
    '0c0c0180c18c006030ff8630180c000000000000000000000000000000000000'
    '000000006000f0c0c1800c0c0180c18c006031e18630180c0000000000000000'
    '00000000000000000000000000006000c0c0c1818c0c0180c0d8006031818630'
    '180c000000000000000000000000000000000000000000006000c0c0c1c38c0c'
    '01c180d80070618186300c180000000000000000000000000000000000000000'
    '00006000e3c0c0c30c0c01e380d80078e1c786300e3800000000000000000000'
    '00000000000000000000000060007ee0f07f0c0c01ff0070007fc0fdc63007f0'
    '0000000000000000000000000000000000000000000060003c60703c0c0c019e'
    '007000678078c63003e000000000000000000000000000000000000000000000'
    '0000000000000000000000600060000000000000000000000000000000000000'
    '0000000000000000000000000000000000000000006000600000000000000000'
    '00000000000000000000000000000000000000000000000000000000000000c0'
    '0060000000000000000000000000000000000000000000000000000000000000'
    '000000000000000007c000600000000000000000000000000000000000000000'
    '0000000000000000000000000000000000000780006000000000000000000000'
    '0000000000000000000000000000000000000000000000000000000000000000'
    '0000000000000000000000000000000000000000000000000000000000000000'
    '00000000000000000000000000000000'
)

CREDIT2_W = 42
CREDIT2_H = 2
CREDIT2_BITS = bytes.fromhex(
    '0000000003c13fe409027f0078078202027f0304ff01e0808304090601e00480'
    '20f0001fc0c1ff07820400000000042102040902408084084306024084848082'
    '10c1848609090210048021080010212010084204000000000811020409024081'
    '0210228a04408484808408a28485090904080840420400102120101022040000'
    '000010090204090240820120128a04408484808804a284850909080408404402'
    '00102120102012040000000010010204090240820020128a08408844810804a2'
    '8844891088001020840200102210102002040000000010f90207f9027f020020'
    '1252087f0844fe08049488448910880010208402001fc210102003fc00000000'
    '1009020409024082002012521040084481080494884449108800201104020010'
    '0210102002040000000010090204090240820120125210400fc4808804948fc4'
    '491f880420110402001003f01020120400000000081102040902408102102222'
    '2040102480840888902429204408400a02040010040810102204000000000421'
    '020408844080840842222040102480821088902429204210400a010800100408'
    '100842040000000003c1020408787f10780782224040102480c1e08890241920'
    '41e0800400f00010040810078204000000000000000000000000000000000000'
    '0000000000000000000000000000000000000000000000000000000000000000'
    '0000000000000000000000000000000000000000000000000000000000000000'
    '0000000000000000000000000000000000000000000000000000000000000000'
    '000000007f800000000000000000000000000000000000000000000000000000'
    '0000000000000000000000000000000000000000000000000000000000000000'
    '0000000000000000000000000000000000000000000000000000000000000000'
)
# CREDITLINE BLOB END

# Two lines added to the ending roll, built by tools/vocredits.py. The first
# is the roll's 24px body size, cut glyph by glyph out of the existing
# artwork. The second is the 11px face the title sets CYBER TROOPERS in,
# which is why it is upper case - that face has capitals only, and covers
# just the letters of that phrase, so the rest are drawn. Both are padded to
# end on the title's right edge, at column 46.
CREDITS = ((CREDIT1_W, CREDIT1_H, CREDIT1_BITS),
           (CREDIT2_W, CREDIT2_H, CREDIT2_BITS))

# The roll is a list of blocks, not a grid: 12 bytes each, (flag, width in
# cells, height in cells), width 0 being a blank spacer. Each is placed on 51
# cells by its flag, below, and revealed eight ticks per row.
CREDIT_TABLE = 0x006bcd48       # v_on.exe, the block list
CREDIT_AFTER = 1                # the first spacer, just past the title
# The flag picks how the block is placed: below zero centres it on the 51
# cells, 0x63 pushes it flush to the right of them. The roll's own text
# carries 0x63; these two take the title's -1 and centre with it, since the
# lines are right-aligned inside their own bitmaps already.
CREDIT_FLAG = 0xffffffff
# The title is followed by five blank spacers of four rows before "PC version
# STAFF". The lines go inside that run rather than after it, centred: seven
# blank rows, the two lines, seven more. Five entries replaced by five, twenty
# rows by twenty, so nothing below moves and the roll keeps its length.
CREDIT_SPACERS = 5              # blank blocks the lines are placed into
CREDIT_GAP_TOP = 7              # blank rows above the lines
CREDIT_GAP_BOTTOM = 7           # and below - the six text rows leave 14
CREDIT_GAP = 1                  # between the two lines
# Where the new cells go in the map. The title block is 42x3, so the two new
# blocks start right after its 126 cells.
CREDIT_CELLS_AT = 126
# Screen pixel the title's lettering ends at, and the lines with it. Measured
# by tools/vocredits.py from the artwork; recorded here so the check can
# confirm the placement rule as well as the bitmap.
CREDIT_RIGHT = 366

SCRSTFCG = 'scrstfcg.bin'       # the tile sheet: 1129 tiles, 8x8, 16bpp
SCRSTFCG_SIZE = 144512
SCRSTFCG_MD5 = '1141876c33fe75fe6aaaf5780ae730d8'
SCRSTFMP = 'scrstfmp.bin'       # one 16-bit tile index per cell
SCRSTFMP_SIZE = 9288
SCRSTFMP_MD5 = '4cb38735719c986f7a303e8215466220'
CREDIT_INK = 0xffbf             # the only non-zero value in the sheet

BANNER_W, BANNER_H = 42, 3      # cells
BANNER_TABLE = 0x00269b60       # v_on.exe, the tile index for each cell
BANNER_TILE_OFF = 0x21c000      # escrgame.bin, first tile slot
BANNER_TILE_BASE = 17280        # its tile index within that file
BANNER_TILE_MAX = 109           # slots this banner owns
BANNER_SPILL = 24845            # a run of 116 empty tiles further in
BANNER_INK = 0xfca0             # the orange the original uses
ESCRGAME = 'escrgame.bin'
ESCRGAME_SIZE = 4194304
ESCRGAME_MD5 = 'f0c2b33c6d32e8e25cee840a0de65dc0'


def banner_tiles():
    """Expand the bitmap into 8x8 tiles, and the tile index for each cell.

    Deduplicated: 126 cells do not otherwise fit in 109 slots. Anything past
    those 109 goes to a run of empty tiles elsewhere in the file rather than
    over the neighbouring banner."""
    want = BANNER_W * 8 * BANNER_H * 8 // 8
    if len(BANNER_BITS) != want:
        raise AssertionError('banner bitmap is %d bytes, expected %d for '
                             '%dx%d' % (len(BANNER_BITS), want,
                                        BANNER_W * 8, BANNER_H * 8))
    tiles, table = [], []
    for r in range(BANNER_H):
        for c in range(BANNER_W):
            raw = bytearray()
            for y in range(8):
                base = (r * 8 + y) * BANNER_W * 8
                for x in range(c * 8, c * 8 + 8):
                    i = base + x
                    on = BANNER_BITS[i >> 3] >> (7 - (i & 7)) & 1
                    raw += (BANNER_INK if on else 0).to_bytes(2, 'little')
            raw = bytes(raw)
            if raw not in tiles:
                tiles.append(raw)
            table.append(tiles.index(raw))
    spill = BANNER_SPILL - BANNER_TILE_BASE
    table = [t if t < BANNER_TILE_MAX else spill + t - BANNER_TILE_MAX
             for t in table]
    return tiles, table


def credit_tiles():
    """Expand both lines into 8x8 tiles and the cell index for each.

    Deduplicated across both lines, and blank cells cost no tile at all -
    the map stores 0 for those and the renderer skips them, which is why a
    line of text needs far fewer tiles than it has cells.

    The tiles go on the end of scrstfcg.bin, so their indices carry on from
    the 1129 already there. Bit 15 is set on every non-zero entry because
    the loader tests the whole word for zero before rebasing it."""
    tiles, cells = [], []
    for width, height, bits in CREDITS:
        for r in range(height):
            for c in range(width):
                raw = bytearray()
                for y in range(8):
                    base = (r * 8 + y) * width
                    for x in range(c * 8, c * 8 + 8):
                        on = bits[base + (x >> 3)] >> (7 - (x & 7)) & 1
                        raw += (CREDIT_INK if on else 0).to_bytes(2, 'little')
                raw = bytes(raw)
                if not any(raw):
                    cells.append(0)         # blank: no tile, no index
                    continue
                if raw not in tiles:
                    tiles.append(raw)
                cells.append(0x8000 | (SCRSTFCG_SIZE // 128 + tiles.index(raw)))
    return tiles, cells


CREDIT_NEW_TILES, CREDIT_CELLS = credit_tiles()


def credit_table(original):
    """The block list with the two new lines placed after the title.

    Nothing calls this at patch time - the result is committed as the site's
    hex - but tools/credittest.py runs it against the original bytes to check
    that what is committed is still what this produces.

    The five blank spacers between the title and "PC version STAFF" are
    replaced by five entries carrying the same twenty rows, with the lines
    centred in them. Nothing shifts and the roll does not get longer."""
    entry = struct.Struct('<3I')
    rows = [entry.unpack_from(original, i * 12)
            for i in range(len(original) // 12)]
    gap = rows[CREDIT_AFTER:CREDIT_AFTER + CREDIT_SPACERS]
    # Text in there would mean the table moved under us and the lines would
    # land on top of somebody's credit.
    if any(w for _flag, w, _h in gap):
        raise AssertionError('block %d..%d are not the blank spacers this '
                             'expects' % (CREDIT_AFTER,
                                          CREDIT_AFTER + CREDIT_SPACERS - 1))
    placed = [(0, 0, CREDIT_GAP_TOP),
              (CREDIT_FLAG, CREDITS[0][0], CREDITS[0][1]),
              (0, 0, CREDIT_GAP),
              (CREDIT_FLAG, CREDITS[1][0], CREDITS[1][1]),
              (0, 0, CREDIT_GAP_BOTTOM)]
    if sum(h for _f, _w, h in placed) != sum(h for _f, _w, h in gap):
        raise AssertionError('the lines do not fit the twenty rows they '
                             'replace')
    rows[CREDIT_AFTER:CREDIT_AFTER + CREDIT_SPACERS] = placed
    return b''.join(entry.pack(*r) for r in rows[:len(original) // 12])


BANNER_TILES, BANNER_CELLS = banner_tiles()
BANNER_NEW = b''.join(t.to_bytes(2, 'little') for t in BANNER_CELLS).hex()


def _check_banner():
    """The bitmap is generated by tools/vonbanner.py and checked in, so
    nothing regenerates it here. These are the ways a hand-edited or
    mis-generated one goes wrong, and every one of them would otherwise show
    up as a scrambled title screen.

    Returns (unique tiles, how many spill past the slots this banner owns).
    """
    if len(BANNER_CELLS) != BANNER_W * BANNER_H:
        raise AssertionError('banner table is %d entries, expected %d'
                             % (len(BANNER_CELLS), BANNER_W * BANNER_H))
    spill = sum(1 for t in BANNER_TILES) - BANNER_TILE_MAX
    if spill > 116:
        raise AssertionError('banner needs %d tiles: %d of its own and %d '
                             'spare, but only 116 spare are free'
                             % (len(BANNER_TILES), BANNER_TILE_MAX, spill))
    for t in BANNER_CELLS:
        # the renderer masks the map entry with 0x3fff, so an index past
        # that draws some other tile entirely
        if not 0 <= t + 0x380 < 0x4000:
            raise AssertionError('banner tile index %d is outside the 14 bits '
                                 'the renderer reads' % t)
        off = (BANNER_TILE_OFF + t * 128 if t < BANNER_TILE_MAX
               else (BANNER_TILE_BASE + t) * 128)
        if off + 128 > ESCRGAME_SIZE:
            raise AssertionError('banner tile %d lands past the end of %s'
                                 % (t, ESCRGAME))
    return len(BANNER_TILES), max(0, spill)


BANNER_UNIQUE, BANNER_SPILLED = _check_banner()


FEATURES = [
    ('sound', 'Sound fixes',
     'Three small fixes.\n'
     '\n'
     'Sound effects\tThe built-in delay before each one is removed.\n'
     'Output frequency\t22050 to 44100 Hz. The samples are 8-bit either way.\n'
     'Enemy Fei-Yen\tRestores the hypermode sound a bug left silent.', [
         (0x002bba60, '0f', '01'),
         (0x00189546, '2256', '44ac'),
         (0x00189552, '88580100', '10b10200'),
         (0x00058189, '01', '02'),
         (0x00170dc9, '01', '02')]),

    ('credits', 'Version and credit in the game',
     'Two places, one box.\n'
     '\n'
     'Title screen\tThe version of the patcher in the bottom right, in\n'
     '\tthe game\'s own lettering.\n'
     'Ending roll\tTwo lines under the CYBER TROOPERS VIRTUAL-ON title\n'
     '\tat the top of the credits, in the roll\'s lettering.\n'
     'Files\tscrstfcg.bin and scrstfmp.bin are rewritten and backed\n'
     '\tup. Restore original puts them back. Missing, nothing is\n'
     '\twritten at all, the version included.', [
         # The loader takes both byte counts from here rather than from
         # the files, so these have to grow with them or the tail of each
         # never loads: the new tiles would be past the count, and the walk
         # would run off the end of the map.
         (0x001fcec8, '80340200', '006e0200'),
         (0x001fcecc, '48240000', 'ec250000'),
         (0x2bbb54, '000000000000000004000000000000000000000004000000000000000000000004000000000000000000000004000000000000000000000004000000ffffffff1800000003000000000000000000000004000000000000000000000004000000000000000000000002000000000000001700000003000000000000000000000003000000630000001d00000003000000000000000000000004000000000000000000000004000000000000000000000002000000000000001100000003000000000000000000000003000000630000001e00000003000000000000000000000001000000630000001500000003000000000000000000000001000000630000001c00000003000000000000000000000001000000630000002400000003000000000000000000000001000000630000001800000003000000000000000000000004000000000000000000000004000000000000000000000002000000000000001800000003000000000000000000000003000000630000002400000003000000000000000000000001000000630000002000000003000000000000000000000001000000630000001f00000003000000000000000000000001000000630000002000000003000000000000000000000004000000000000000000000004000000000000000000000002000000000000001a00000003000000000000000000000003000000630000001d00000003000000000000000000000004000000000000000000000004000000000000000000000002000000000000002700000003000000000000000000000003000000630000002100000003000000000000000000000004000000000000000000000004000000000000000000000002000000000000000c00000003000000000000000000000003000000630000001100000003000000000000000000000001000000630000001900000003000000000000000000000004000000000000000000000004000000000000000000000002000000000000000b00000003000000000000000000000003000000630000001b00000003000000000000000000000004000000000000000000000004000000000000000000000002000000000000000c00000003000000000000000000000003000000630000001600000003000000000000000000000001000000630000002200000003000000000000000000000001000000630000001700000003000000000000000000000004000000000000000000000004000000000000000000000004000000000000000000000002000000000000001600000003000000000000000000000003000000630000001800000003000000000000000000000004000000000000000000000004000000000000000000000002000000000000002200000003000000000000000000000003000000630000001f00000003000000000000000000000004000000000000000000000004000000000000000000000002000000000000002100000003000000000000000000000003000000630000001b00000003000000000000000000000001000000630000001800000003000000000000000000000001000000630000001f00000003000000000000000000000004000000000000000000000004000000000000000000000002000000000000001200000003000000000000000000000003000000630000001d00000003000000000000000000000004000000000000000000000004000000000000000000000002000000000000000e00000003000000000000000000000003000000630000001600000003000000000000000000000004000000000000000000000004000000000000000000000004000000000000000000000002000000000000000a00000003000000000000000000000003000000630000001c00000003000000000000000000000001000000630000002200000003000000000000000000000001000000630000002100000003000000000000000000000001000000630000001500000003000000000000000000000004000000000000000000000004000000000000000000000004000000000000000000000002000000000000001c00000003000000000000000000000003000000630000002700000003000000000000000000000001000000630000001900000003000000000000000000000001000000630000002f00000003000000000000000000000001000000630000003100000003000000000000000000000001000000630000002000000003000000000000000000000001000000630000003300000003000000000000000000000006000000630000001c00000003000000000000000000000001000000630000001800000003000000000000000000000004000000000000000000000004000000000000000000000004000000000000000000000002000000000000000000000004000000040000000500000003000000000000000000000004000000000000000000000004000000000000000000000002000000040000001900000003000000000000000000000001000000630000001500000003000000000000000000000002000000000000000000000002000000000000000000000002000000000000000000000002000000',
          '000000000000000007000000ffffffff2a00000003000000000000000000000001000000ffffffff2a00000002000000000000000000000007000000ffffffff1800000003000000000000000000000004000000000000000000000004000000000000000000000002000000000000001700000003000000000000000000000003000000630000001d00000003000000000000000000000004000000000000000000000004000000000000000000000002000000000000001100000003000000000000000000000003000000630000001e00000003000000000000000000000001000000630000001500000003000000000000000000000001000000630000001c00000003000000000000000000000001000000630000002400000003000000000000000000000001000000630000001800000003000000000000000000000004000000000000000000000004000000000000000000000002000000000000001800000003000000000000000000000003000000630000002400000003000000000000000000000001000000630000002000000003000000000000000000000001000000630000001f00000003000000000000000000000001000000630000002000000003000000000000000000000004000000000000000000000004000000000000000000000002000000000000001a00000003000000000000000000000003000000630000001d00000003000000000000000000000004000000000000000000000004000000000000000000000002000000000000002700000003000000000000000000000003000000630000002100000003000000000000000000000004000000000000000000000004000000000000000000000002000000000000000c00000003000000000000000000000003000000630000001100000003000000000000000000000001000000630000001900000003000000000000000000000004000000000000000000000004000000000000000000000002000000000000000b00000003000000000000000000000003000000630000001b00000003000000000000000000000004000000000000000000000004000000000000000000000002000000000000000c00000003000000000000000000000003000000630000001600000003000000000000000000000001000000630000002200000003000000000000000000000001000000630000001700000003000000000000000000000004000000000000000000000004000000000000000000000004000000000000000000000002000000000000001600000003000000000000000000000003000000630000001800000003000000000000000000000004000000000000000000000004000000000000000000000002000000000000002200000003000000000000000000000003000000630000001f00000003000000000000000000000004000000000000000000000004000000000000000000000002000000000000002100000003000000000000000000000003000000630000001b00000003000000000000000000000001000000630000001800000003000000000000000000000001000000630000001f00000003000000000000000000000004000000000000000000000004000000000000000000000002000000000000001200000003000000000000000000000003000000630000001d00000003000000000000000000000004000000000000000000000004000000000000000000000002000000000000000e00000003000000000000000000000003000000630000001600000003000000000000000000000004000000000000000000000004000000000000000000000004000000000000000000000002000000000000000a00000003000000000000000000000003000000630000001c00000003000000000000000000000001000000630000002200000003000000000000000000000001000000630000002100000003000000000000000000000001000000630000001500000003000000000000000000000004000000000000000000000004000000000000000000000004000000000000000000000002000000000000001c00000003000000000000000000000003000000630000002700000003000000000000000000000001000000630000001900000003000000000000000000000001000000630000002f00000003000000000000000000000001000000630000003100000003000000000000000000000001000000630000002000000003000000000000000000000001000000630000003300000003000000000000000000000006000000630000001c00000003000000000000000000000001000000630000001800000003000000000000000000000004000000000000000000000004000000000000000000000004000000000000000000000002000000000000000000000004000000040000000500000003000000000000000000000004000000000000000000000004000000000000000000000002000000040000001900000003000000000000000000000001000000630000001500000003000000000000000000000002000000000000000000000002000000000000000000000002000000000000000000000002000000'),
         # The version in the corner of the title screen, drawn through
         # GDI after the frame and before the flip. The overlay took the
         # call five bytes before it, so this takes the load four
         # instructions further on and the two share no bytes. See
         # asm/titlever.asm.
         #
         #   call titlever
         (0x001c5900, 'a1405fae01', call(0x001c5900, ('TITLEVER', 'titlever'))),
         (TITLEVER_AT, '00' * len(TITLEVER_CODE),
          TITLEVER_CODE.hex())]),

    ('movie', 'Intro, loading and ending screens',
     'Four fixes to the screens either side of the fighting.\n'
     '\n'
     'Intro movie\tFitted to the window. The game sizes it for 640x480,\n'
     '\tso scaled up it sat small in a corner.\n'
     'Loading text\t"Now Loading . . ." is hidden. The load is over\n'
     '\tbefore you read it.\n'
     'Ending credits\tSkippable - hold A, Select or Space for a second.\n'
     '\tStock has no way past them.\n'
     'Initials\tThe screen after takes those buttons too, and either\n'
     '\tweapon trigger.\n'
     '2P\tA and Select are 1P\'s, so 2P skips with RT.', [
         # The movie is not drawn through DirectDraw: mciavi opens it as a
         # WS_CHILD of the main window and the game places that window
         # itself, from an offset it reads from two globals. Each is a
         # hardcoded centre for one movie size in a 640x480 picture.
         #
         # Reading the window's real size instead is not something the game
         # can do - cnc-ddraw hooks GetClientRect and answers 640x480 - so
         # the work goes to a stub. See asm/movie.asm.
         #
         #   push ebp
         #   call movie_place
         #   add  esp, 4
         (0x0014dc42,
          'c745f400000000c745f028000000a1345fae018945f4a1385fae018945f0',
          '55e8149e110383c404909090909090909090909090909090909090909090'),
         # movie.asm, in the .rsrc padding past VirtualSize - after the
         # four bytes of it the frame rate patch's F5 labels use
         (site('MOVIE'), '00' * len(MOVIE_CODE), MOVIE_CODE.hex()),
         # "Now Loading . . ." - the first byte to NUL ends the string
         (0x002c7678, '4e', '00'),
         # which the loader maps but does not make executable
         (0x0000023f, '40', '60'),
         # The credits are sub-state 0x20, a phase machine on 0x1ad0964:
         # 0 and 1 are the cutscene and the mission complete screen, 2 is
         # the roll, anything else is the tail that ends the sequence. So
         # the stub only has to put the phase past 2. See asm/credits.asm.
         #
         #   call skip
         (0x0018fc25, 'c7051c1cae0100000000', call(0x0018fc25, ('CREDITS', 'skip'), 5)),
         (site('CREDITS'), '00' * len(CREDITS_CODE), CREDITS_CODE.hex()),
         # The initials screen after them takes a letter on the weapon
         # triggers only, LT for 1P and RT for 2P. Both tests go to a call
         # answering the same question with A folded in, so the triggers
         # still work. Same key slot as the skip above, so this needs no
         # gamepad either. See asm/nameentry.asm.
         #
         #   call confirm
         #   test al, al
         #   jne  take the letter
         #   jmp  carry on
         (0x000d60c8, 'f605c55eed01010f850d000000f605c65eed01010f84f2010000', call(0x000d60c8, ('NAMEENTRY', 'confirm'))
          + '84c07511e9fe010000909090909090909090909090'),
         (site('NAMEENTRY'), '00' * len(NAMEENTRY_CODE), NAMEENTRY_CODE.hex()),
         # HOLD TO SKIP over the credits, drawn through GDI so it does
         # not scroll with the tilemap. The call five bytes before the
         # surface is flipped is made in the stub instead, which is
         # what puts the text on the frame about to be shown.
         (0x001c58e7, 'e8f31b0000', call(0x001c58e7, ('OVERLAY', 'overlay'))),
         (site('OVERLAY'), '00' * len(OVERLAY_CODE), OVERLAY_CODE.hex())]),


    ('defaults', 'Better defaults with no v_on.ini',
     'Changes what the game falls back on when a setting is missing from\n'
     'v_on.ini, which on a first run is all of them. A setting already there\n'
     'wins, and F5 overrides both.\n'
     '\n'
     'Sky\tOn, was Off.\n'
     'Texture\tAll three on, were all off.\n'
     'Field Graphic\tRich, was Normal.\n'
     'Screen\tLarge, was Normal.', [
         (0x0010acd7, '00000000', '01000000'),
         (0x0010b088, '01000000', '00000000'),
         (0x0010b131, 'c705c817680000000000e950000000',
                      'e92300000090909090909090909090'),
         (0x0010b1b0, '00000000', '01000000'),
         (0x0010b1ba, '00000000', '01000000'),
         (0x0010b1c4, '00000000', '01000000')]),

    ('nodisc', 'No disc required',
     'Removes the disc check. The soundtrack then has to come from\n'
     'somewhere, so the same patch adds playback from files.\n'
     '\n'
     'Disc check\tNot done. The drive is still read for music, so a\n'
     '\tmounted image works.\n'
     'Music\tRead from music\\trackNN.wav beside the game. Rip\n'
     '\tthem under DISC; with none there, the game reads the\n'
     '\tdrive.', [
         (0x001c76d4, '0f840a000000', '909090909090')]),

    ('nocpucheck', 'Skip processor check',
     'The game will not start on a modern CPU without this. It removes the\n'
     'MMX, Pentium and vendor checks as well.', [
         (0x00107930, '830dc884bf0001', '90909090909090')]),
    ('framerate', 'Fix frame rate (60 FPS)',
     'Three fixes, all for the game not running at full speed.\n'
     '\n'
     'Timer resolution\tWithout it the game runs at about 70 per cent\n'
     '\tspeed on Windows. Not needed under Wine.\n'
     'Frame divisor\tThe game ignored its own setting for how many\n'
     '\tframes to draw, and wrote it back. It works now.\n'
     'Speed choice\tThe two on F5 never reached 60 fps. They read\n'
     '\t30 FPS and 60 FPS now, and set those.', [
         (site('TIMER'), '00' * len(TIMER_CODE), TIMER_CODE.hex()),
         (0x000000a8, '30791e00', '3e4e1f00'),
         (0x000273c1, '833d0843be0003', '833d0843be0002'),
         (0x000275d3, 'c7050843be0003000000', 'c7050843be0002000000'),
         (0x000275e2, 'c7050843be0002000000', 'c7050843be0001000000'),
         (0x006035ac, '2c040000', '30040000'),
         (0x0060c064, F5_STOCK.hex(), F5_FPS.hex()),
         (0x0010afbe, 'c705d0846c0003000000', 'c705d0846c0001000000'),
         (0x0010afeb, 'c705d0846c0003000000', 'c705d0846c0001000000'),
         (0x0010b002, 'c705d0846c0003000000', 'c705d0846c0001000000'),
         (0x001c6941, 'c705d0846c0002000000', '90909090909090909090'),
         (0x001c6950, 'c705d0846c0003000000', '90909090909090909090'),
         (0x001c6d8c, 'c705d0846c0002000000', '90909090909090909090'),
         (0x001c6d9b, 'c705d0846c0003000000', '90909090909090909090'),
         (0x001c6dfc, 'c705d0846c0002000000', '90909090909090909090'),
         (0x001c6e0b, 'c705d0846c0003000000', '90909090909090909090'),
         (0x001c8bc4, 'c705d0846c0002000000', '90909090909090909090'),
         (0x001c8bd3, 'c705d0846c0003000000', '90909090909090909090')]),

    ('debugbox', 'Disable menu bar (Extras menu on F11)',
     'Removes the menu bar. F11 opens a dialog with the Debug options in\n'
     'its place: No shot, SE, CD, Kill 1P, Kill 2P, Scorekeeping, Credits\n'
     'and Quit Game. Motion has moved to F5.\n'
     '\n'
     'With the gamepad patch in, the dialog sets each player\'s stick\n'
     'deadzone too.\n'
     '\n'
     'Credits is not one of the game\'s own: it runs the ending roll from\n'
     'wherever you are in a match.\n'
     '\n'
     'Every other menu was already on a key.\n'
     '\n'
     'F1\tHelp\n'
     'F3\tPause\n'
     'F4\tHigh / low resolution\n'
     'F5\tGraphic Settings\n'
     'F6\tMode Settings\n'
     'F7\tDevice Settings\n'
     'F8\tSound Test\n'
     'F11\tExtras, the new dialog', [
         (0x001c4d42, '0f850c000000', '909090909090'),
         (0x001c4d4b, '65000000', '00000000'),
         (0x001c4d7e, '57685c00', abs32(('DEBUGBOX', 'hook'))),
         (site('DEBUGBOX'), '00' * len(DEBUGBOX_HOOK), DEBUGBOX_HOOK.hex()),
         # the pause-and-resume wrapper the hook runs the dialog through,
         # matching the built-in F-key dialogs; see asm/f11pause.asm
         (site('F11PAUSE'), '00' * len(F11PAUSE_CODE), F11PAUSE_CODE.hex()),
         (DBGPROC_AT, '00' * len(DEBUGBOX_PROC), DEBUGBOX_PROC.hex()),
         (site('EXTRAS_DATA'), '00' * len(EXTRAS_DATA), EXTRAS_DATA.hex())]),
    ('continuefix', 'Fix crash on round loss',
     'Stops the crash when you lose a round as Temjin, Viper II, Apharmd or\n'
     'Raiden.', [
         (0x00077f5a, '8b45fcd94008d9e083ec04d91c248b45fc8b4004508b45fcd900d9e083ec04d91c24e8df63f9ff83c40c',
          '90' * 42),
         (0x00078b1c, '8b45fcd94008d9e083ec04d91c248b45fc8b4004508b45fcd900d9e083ec04d91c24e81d58f9ff83c40c',
          '90' * 42),
         (0x00079bb6, '8b45fcd94008d9e083ec04d91c248b45fc8b4004508b45fcd900d9e083ec04d91c24e88347f9ff83c40c',
          '90' * 42),
         (0x00079f3f, '8b45fcd94008d9e083ec04d91c248b45fc8b4004508b45fcd900d9e083ec04d91c24e8fa43f9ff83c40c',
          '90' * 42),
         (0x0007d04a, '8b45fcd94008d9e083ec04d91c248b45fc8b4004508b45fcd900d9e083ec04d91c24e8ef12f9ff83c40c',
          '90' * 42),
         (0x000bb9ea, '8b45fcd94008d9e083ec04d91c248b45fc8b4004508b45fcd900d9e083ec04d91c24e80fc1f4ff83c40c',
          '90' * 42),
         (0x000bc5ac, '8b45fcd94008d9e083ec04d91c248b45fc8b4004508b45fcd900d9e083ec04d91c24e84db5f4ff83c40c',
          '90' * 42),
         (0x000bd646, '8b45fcd94008d9e083ec04d91c248b45fc8b4004508b45fcd900d9e083ec04d91c24e8b3a4f4ff83c40c',
          '90' * 42),
         (0x000bd9cf, '8b45fcd94008d9e083ec04d91c248b45fc8b4004508b45fcd900d9e083ec04d91c24e82aa1f4ff83c40c',
          '90' * 42),
         (0x000c0ada, '8b45fcd94008d9e083ec04d91c248b45fc8b4004508b45fcd900d9e083ec04d91c24e81f70f4ff83c40c',
          '90' * 42)]),

    ('padxinput', 'XInput gamepad support',
     'Four profiles on the F7 screen, any of them for either player.\n'
     '\n'
     'Gamepad (XInput)\ttwelve named actions, bind them yourself\n'
     'Twin-stick (XInput)\tthe arcade levers, nothing to bind\n'
     'Keyboard (Simple)\tevery action on a bindable key\n'
     'Keyboard (Real)\tthe two-lever keyboard scheme\n'
     '\n'
     'Simple and the gamepad share one bind page, but each sees only its\n'
     'own inputs, and both bind sets are saved.\n'
     '\n'
     'A accepts, Select is the camera, Start pauses, and the D-pad works\n'
     'the menus. A or Select skips the win and lose screens between\n'
     'rounds. On-screen prompts name the button rather than a key.\n'
     '\n'
     'Stick deadzone\tEach player has one, 40% to start, set in the F11\n'
     '\tExtras dialog.\n'
     '\n'
     'v_on.ini and escrgame.bin are moved aside and rewritten. Restore\n'
     'original puts both back.', [
         # The bind page serves the gamepad and Keyboard (Simple) both, so
         # its list length and address go through asm/bindlist.asm and
         # asm/bindmap.asm, which pick them by device. The letter and
         # digit sections are Simple's alone; asm/pagesec.asm and
         # asm/pagesel.asm skip them for the gamepad.
         (0x000970bf, '837df8210f8d2e000000', call(0x000970bf, ('BINDLIST', 'fillcount'), 5)),
         (0x000970d5, '8b04c538d46600', call(0x000970d5, ('BINDLIST', 'fillname'), 2)),
         (0x0009729c, '8a04c53cd46600', call(0x0009729c, ('BINDLIST', 'storeid'), 2)),
         (0x000974ac, '837df4210f8d23000000', call(0x000974ac, ('BINDMAP', 'mapcount'), 5)),
         (0x000974be, '390cc53cd46600', call(0x000974be, ('BINDMAP', 'mapid'), 2)),
         (site('BINDLIST'), '00' * len(BINDLIST_CODE), BINDLIST_CODE.hex()),
         (site('BINDMAP'), '00' * len(BINDMAP_CODE), BINDMAP_CODE.hex()),
         # Which saved block that page reads and writes is picked the same
         # way: +0x08 for the gamepad, +0x38 - the hidden 2 Joysticks
         # profile's, inside the structure v_on.ini keeps - for Simple.
         # See asm/bindblock.asm; the player comes from the maths in
         # flight, not 0xbf6bac, because the Default copier also runs at
         # startup for both sides.
         (0x00095f35, '053868bf0083c008', call(0x00095f35, ('BINDBLOCK', 'blockaddr'), 3)),
         (0x0009724c, '053868bf0083c008', call(0x0009724c, ('BLOCKCUR', 'blockcur'), 3)),
         (0x0009736d, '053868bf0083c008', call(0x0009736d, ('BINDBLOCK', 'blockaddr'), 3)),
         # the Default button's shipped set comes from the same pick:
         # the gamepad's table or SIMPLEDEF, by the pending device
         (0x00097355, '8d04408d04c500d66600', call(0x00097355, ('BINDBLOCK', 'defsource'), 5)),
         (0x00097397, '053868bf0083c008', call(0x00097397, ('BINDBLOCK', 'blockaddr'), 3)),
         (0x00097531, '053868bf0083c008', call(0x00097531, ('BINDBLOCK', 'blockaddr'), 3)),
         (0x0009740f, '8a84414068bf00', call(0x0009740f, ('BINDBLOCK', 'preselbind'), 2)),
         (site('BINDBLOCK'), '00' * len(BINDBLOCK_CODE), BINDBLOCK_CODE.hex()),
         (site('INISAVE'), '00' * len(INISAVE_CODE), INISAVE_CODE.hex()),
         (site('INILOAD'), '00' * len(INILOAD_CODE), INILOAD_CODE.hex()),
         (site('BLOCKCUR'), '00' * len(BLOCKCUR_CODE), BLOCKCUR_CODE.hex()),
         (site('INIPARSE'), '00' * len(INIPARSE_CODE), INIPARSE_CODE.hex()),
         (site('PAGESEC'), '00' * len(PAGESEC_CODE), PAGESEC_CODE.hex()),
         (site('PAGESEL'), '00' * len(PAGESEL_CODE), PAGESEL_CODE.hex()),
         (site('COMMITDEV'), '00' * len(COMMITDEV_CODE), COMMITDEV_CODE.hex()),
         (site('INIALL'), '00' * len(INIALL_CODE), INIALL_CODE.hex()),
         (site('DEVORDER'), '00' * len(DEVORDER_CODE), DEVORDER_CODE.hex()),
         (site('PAD_INIKEYS'), '00' * len(PAD_INIKEYS), PAD_INIKEYS.hex()),
         # window title, shared by both pages now
         (0x0026c88c, '4b6579626f617264206f6e6c79202853696d706c652074797065202d2025645020736964652900',
                      '42696e64696e6773202d20256450207369646500000000000000000000000000000000000000'
                      '00'),
         # the gamepad's default binds, 1P and 2P, twelve slots of stride
         # 2. Keyboard (Simple)'s shipped set moves to PAD_SIMPLEDEF.
         (0x0026c400, '11001f001e002000100012002e0022002d0013002f002100', 'e800e900ea00eb00ee00ef00e600e500e400e000e200e700'),
         (0x0026c418, 'c700cf00d300d100d200c900520051004f004c0053005000', 'e800e900ea00eb00ee00ef00e600e500e400e000e200e700'),
         (site('PAD_SIMPLEDEF'), '00' * len(PAD_SIMPLEDEF), PAD_SIMPLEDEF.hex()),
         # each player's profile 1 dispatches to the routine
         (0x000422a8, '502e4400', abs32(('PADX', 'entry1p'))),
         (0x001bc13b, 'e3cc5b00', abs32(('PADX', 'entry2p'))),
         # PeekMessage: Start and A must reach the game while it is paused,
         # where the input tick does not run
         (0x001c530e, 'ff1590d56503', call(0x001c530e, ('PADX', 'pump'), 1)),
         # two pads are separate devices, so 2P may reuse 1P's inputs
         (0x000971bd, '0f8558000000', 'e95900000090'),
         # device list: Keyboard (Real), Gamepad (XInput), Twin-stick,
         # Keyboard (Simple)
         (0x0026c218, 'ecd36600d4d36600c0d36600b4d3660090d3660080d3660068d3660058d36600',
          PAD_DEVLIST.hex()),
         # profile switch, both players: slot 2 gets the twin-stick stubs
         # and slot 3 the stubs Keyboard (Simple) always had, back from
         # slot 1. Slot 1 is the gamepad, repointed above; slot 0 is the
         # game's own keyboard handler and is left alone.
         (0x000422ac, '5a2e4400', abs32(('TWIN', 'stub1p'))),
         (0x001bc13f, 'edcc5b00', abs32(('TWIN', 'stub2p'))),
         (0x000422b0, '6b2e4400', '502e4400'),
         (0x001bc143, 'fecc5b00', 'e3cc5b00'),
         # The keyboard profile shared its twenty-four bind slots with
         # Simple, and the gamepad took those. Move it to the block owned by
         # the hidden Joystick + Keyboard profile, whose "Joy+Key Assign"
         # v_on.ini line keeps it persistent.
         (0x00095e46, '83c008', '83c020'),
         (0x00095ec7, '4068bf00', '5868bf00'),
         (0x00096d37, '83c008', '83c020'),
         (0x00096d61, '83c008', '83c020'),
         (0x00096f19, '83c008', '83c020'),
         (0x00096c0a, '4068bf00', '5868bf00'),
         (0x00096c40, '4068bf00', '5868bf00'),
         (0x00096c6d, '4068bf00', '5868bf00'),
         (0x00096de8, '4068bf00', '5868bf00'),
         (0x00096e3f, '4068bf00', '5868bf00'),
         (0x00096e9a, '4068bf00', '5868bf00'),
         # 2P could not take a key 1P had bound, even with 1P on a pad and
         # those binds dormant. Gate that on 1P actually being on the
         # keyboard. Entry only; see asm/kbpage.asm for what that misses.
         (0x00096b61, '833dac6bbf00010f8558000000', jump(0x00096b61, ('KBPAGE', 'dupkey'), 8)),
         # and Default passed a hardcoded player 0, so on the 2P side it
         # reset 1P's binds. The other two pages pass ds:0xbf6bac here.
         (0x00096c8e, '6a016a00e87800000083c408', jump(0x00096c8e, ('KBPAGE', 'default_button'), 7)),
         (site('KBPAGE'), '00' * len(KBPAGE_CODE), KBPAGE_CODE.hex()),
         # Startup defaults every block in a fixed order, and Joystick +
         # Keyboard writes that block after the keyboard profile does. It is
         # hidden, so drop its call; the pushes around it stay balanced.
         (0x00094ea0, 'e8592b0000', '9090909090'),
         # The call after it filled +0x38 with 2 Joysticks defaults; that
         # block is Keyboard (Simple)'s now, so it goes to the writer at
         # the end of asm/bindmap.asm instead.
         (0x00094eaf, 'ca330000', 'a17e1600'),
         # Startup validates the device saved in v_on.ini through a table
         # at 0x495e0f indexed by device - 2, so this entry is device 4,
         # a legacy joystick profile. It is hidden and unreachable, and
         # this only spares it a check it would fail.
         (0x00095217, '415d4900', '235e4900'),
         # and this one is device 3, Keyboard (Simple) now: a keyboard must
         # not fail the joystick-presence check, or the saved device resets
         # at every launch.
         (0x00095213, '185d4900', '235e4900'),
         # The device page's OK handler counts the joysticks enumerated at
         # startup and spends one per selection, refusing the page if a
         # counter goes negative. Twin-stick reads the pad through XInput
         # and spends nothing, so send its case straight to that check,
         # where the keyboard and gamepad selections already arrive.
         (0x00096731, 'e0724900', '49734900'),
         # and device 3, Keyboard (Simple), spends nothing either
         (0x00096735, '06734900', '49734900'),
         # F7 page table: twin-stick binds nothing, so it takes the case
         # that opens no dialog and reports success.
         (0x00095bdc, '28674900', 'a9674900'),
         # and slot 3 opens the twelve-bind page the gamepad shares
         (0x00095be0, 'a9674900', 'e9664900'),
         # The apply-and-serialize switch behind OK: devices 1 and 3 both
         # run asm/inisave.asm, which writes "NP Simple Assign" from +0x38
         # and falls into the stock device 1 case - so both profiles'
         # lines are always written, whichever is selected.
         (0x00096253, '236b4900', abs32(('INISAVE', 'savesimple'))),
         (0x0009625b, '2b6e4900', abs32(('INISAVE', 'savesimple'))),
         # And the startup call that refilled +0x38 with legacy joystick
         # defaults every launch parses that line back instead. See
         # asm/iniload.asm.
         (0x00095604, 'e8742c0000', call(0x00095604, ('INILOAD', 'loadsimple'))),
         # The ini loader routes each player by saved device, and slot 3's
         # route was "load nothing" - right for 2 Joysticks, whose data was
         # re-derived, wrong for Simple. Route it through the section that
         # runs asm/iniload.asm instead.
         (0x000958a1, '03', '02'),
         # And whatever the saved devices, both keyboard-page blocks load
         # their lines at the loop's exit; see asm/iniall.asm.
         (0x000958aa, 'e900000000', call(0x000958aa, ('INIALL', 'iniall'))),
         # A last common check demanded the DirectInput joystick subsystem
         # whenever either player's saved device was 3 or 7, or it forced
         # both to Gamepad and skipped the whole ini load. Simple needs no
         # joystick; the check is 7-only now.
         (0x0009522e, '03', '7f'),
         (0x00095248, '03', '7f'),
         # The list shows gamepad, twin-stick, Simple, Real; positions and
         # device numbers are mapped both ways by asm/devorder.asm.
         (0x0009651a, '8b803868bf00', call(0x0009651a, ('DEVORDER', 'posshim'), 1)),
         (0x00096784, '8b45f48b4dec', call(0x00096784, ('DEVORDER', 'devshim'), 1)),
         # The device page's plain OK only commits the device number; the
         # shared live table must be reseeded for the new device too. See
         # asm/commitdev.asm.
         (0x000959f7, '89048d40156503', call(0x000959f7, ('COMMITDEV', 'commitdev'), 2)),
         # The shared page's template labels its list "1P side" - baked-in
         # text the original also showed on the 2P pass. Neutral now that
         # the window title carries the side.
         (0x0060b34e, '3100500020007300690064006500', '41006300740069006f006e007300'),
         # The bind page's seed of its block from the live table is only
         # right when the page's device is the committed one; see
         # asm/blockcur.asm.
         # The letter and digit sections belong to the keyboard profile;
         # the gamepad page lists only its pad inputs. Fill, store and
         # preselect skip them together: asm/pagesec.asm, asm/pagesel.asm.
         (0x0009703f, '837df81a0f8d27000000', call(0x0009703f, ('PAGESEC', 'fillsec'), 5)),
         (0x00097257, '837dec1a0f8d13000000', call(0x00097257, ('PAGESEC', 'storesec'), 5)),
         (0x00097428, '837df41a0f8d27000000', call(0x00097428, ('PAGESEL', 'selsec'), 5)),
         (0x000974cb, '8345f424e905000000', call(0x000974cb, ('PAGESEL', 'selidx'), 4)),
         (0x0009753a, 'e8f1de1400', call(0x0009753a, ('BLOCKCUR', 'syncshim'))),
         (site('TWIN'), '00' * len(TWIN_CODE), TWIN_CODE.hex()),
         # the intro movie blocks the message loop in GetMessageA, where the
         # pump stub does not run. Poll from the call itself instead, so a
         # pad press reaches the window procedure and Space skips the movie.
         (site('INTROWAIT'), '00' * len(INTROWAIT_CODE), INTROWAIT_CODE.hex()),
         (0x001c52ac, 'ff158cd56503', call(0x001c52ac, ('INTROWAIT', 'introwait'), 1)),
         # what each pad input is and what it is called
         (site('PAD_COND'), '00' * len(PAD_COND), PAD_COND.hex()),
         (site('PAD_BINDS'), '00' * len(PAD_BINDS), PAD_BINDS.hex()),
         (site('PAD_NAMES'), '00' * len(PAD_NAMES), PAD_NAMES.hex()),
         # The win and lose screens read the camera key, not the accept
         # key, which is why Select skips them and A does not. The tick
         # calls this to write the camera slot for A as well, on those
         # screens only. See asm/camskip.asm.
         (site('CAMSKIP'), '00' * len(CAMSKIP_CODE), CAMSKIP_CODE.hex()),
         # the routine itself: entry stubs, pump stub, tick, blocks
         (site('PADX'), '00' * len(PADX_CODE), PADX_CODE.hex()),
         # the tick ORs every active input together, but the game's
         # gestures are exclusive lever positions, so a held direction
         # contaminates jump and guard. Strip it back off at the end,
         # and only when a pad was actually read, so the keyboard path
         # is left exactly as it was.
         (0x00207702, '5f5e5bc9c3', jump(0x00207702, ('LEVERS', 0x0))),
         (site('LEVERS'), '00' * len(LEVERS_CODE), LEVERS_CODE.hex()),
         # Two prompts naming a key the pad now covers, so they are only
         # true with this patch on.
         #
         # The scoreboard screen's, tile-grid text in a fixed 16-cell slot
         # blanked by overwriting with the 16 spaces at 0x00285df0, so the
         # replacement has to be the same width.
         (0x00285e04, '20505245535320535041434520424152',
                      '205052455353204120425554544f4e20'),
         # The pause screen's, a C string drawn with TextOutA onto a DC from
         # the DirectDraw surface. The site runs to the four bytes of padding
         # after it; PAUSE follows at 0x2c7670.
         (0x002c7654,
          '546f20526573756d652047616d652c20507265737320463300000000',
          '505245535320535441525420544f20554e5041555345000000000000'),
         # The title and scoreboard banner says the same thing in artwork.
         # Only the tile indices are here; escrgame.bin holds the tiles and
         # is written after the executable, and backed up the same way.
         (BANNER_TABLE, '000001000200030004000500060007000800090007000a000b000c000d000e000f00100011001200040004001300090004001400150004001600170007000800180019001a001b001c0014001d001e001f0020002100220023002400250026002700280029002a002b002c002d002e002f0030003100320033003400350036003700380039003a003b003c003d003e00280029003f003a0040004100420043004400450046004700480049004a004b004c004a004d004e004f0050005100520053005400550056005700580059005a005b005c005d005e005f0060006100620063004d004e004f006400650066006700680069006a006b006c004a00', BANNER_NEW)]),
]

# Found by signature rather than offset, so it cannot live in FEATURES.
# SetCooperativeLevel: DISCL_FOREGROUND -> DISCL_BACKGROUND.
DI_FIND = re.compile(
    rb'\x6a\x06[\s\S]{0,20}?\xff(?:[\x50-\x57]\x34|[\x90-\x97]\x34\x00\x00\x00)')

# key -> (label, description, sites), with sites None meaning DI_FIND.
BY_KEY = {key: (label, tip, sites) for key, label, tip, sites in FEATURES}

# The patches a lockstep match cannot differ on are the frame rate and the
# round-loss fix: both change what the simulation computes. Both are Essential
# and always applied, so this patcher cannot produce a build missing them -
# SYNC_SITES reads them back out of a file an older release may have written
# without, and net/dpctrl.c fingerprints the same two bytes.
BY_KEY['dinput'] = (
    'Fix keyboard input after ALT+TAB',
    'Without this, alt-tabbing away or opening an F-key dialog kills\n'
    'keyboard input until the game is restarted.', None)

# Both padxinput and the ending stubs put routines in .rdata, which the
# loader maps without making it executable. The site that changes the section
# flag belongs to neither of them: written twice it would fail its own
# original check, so it is applied once for whichever patch is ticked.
RDATA_EXEC = (0x000001c4, '40000040', '40000060')
RDATA_EXEC_KEYS = ('padxinput', 'movie', 'credits')

# Display order only; see apply_order for the write order. Essential fixes
# what is broken on modern systems, extra is taste. Both start ticked, extra
# running from the biggest change down to the smallest.
ESSENTIAL = ('nocpucheck', 'framerate', 'continuefix', 'dinput')
# Every Essential patch, shown without a tick box. Unticking any of them
# produced a game that is broken in a way nobody was choosing on purpose:
# no start on a modern CPU, a crash on a lost round, a third of the frame
# rate, or dead keys after ALT+TAB. Two of them are also what internet play
# needs, so forcing them removes a way to build a game that patches cleanly
# and then refuses to connect.
ALWAYS = ESSENTIAL
EXTRA = ('padxinput', 'nodisc', 'debugbox', 'defaults', 'sound', 'movie')
# Its own group so it stays out of the patch list: it fixes nothing and
# undoes nothing the game does, so it belongs beside the version and the
# link rather than among the patches. Ticked by default all the same.
ABOUT = ('credits',)


def apply_order():
    """Display order, except that nodisc has to be last: it appends a
    section and chains the entry point, so it must see every other edit.
    The menu bar patch appends a section too - the F11 template's - but
    earlier is fine: each append places itself from the headers as they
    are, so the two stack in whatever combination is ticked."""
    keys = [k for k in ESSENTIAL + EXTRA + ABOUT if k != 'nodisc']
    return keys + ['nodisc']


def _check_table():
    """Fail at import, not half way through somebody's executable.

    Three things go wrong silently otherwise: a length mismatch patches the
    wrong bytes, an offset past the end of the file patches nothing, and two
    features writing the same byte make the result depend on the tick boxes.

    Sites inside one feature *may* overlap - the XInput routine is written
    whole and then has its epilogue rewritten - but only where the later site
    expects exactly what the earlier one left there. Anything else means the
    list has been reordered and the patch would fail against a real file."""
    if set(BY_KEY) != set(ESSENTIAL) | set(EXTRA) | set(ABOUT):
        raise AssertionError('patch list and display order disagree')
    if apply_order()[-1] != 'nodisc':
        raise AssertionError('nodisc must be applied last')

    # RDATA_EXEC is not in any feature's list, so seed it here or the four
    # bytes it writes are the one place two patches could collide unnoticed.
    owner = dict.fromkeys(range(RDATA_EXEC[0],
                                RDATA_EXEC[0] + len(RDATA_EXEC[1]) // 2),
                          'the .rdata executable flag')
    for key in BY_KEY:
        written = {}
        for off, old, new in BY_KEY[key][2] or ():
            if len(old) != len(new):
                raise AssertionError('%s at 0x%08x: %d bytes replaced by %d'
                                     % (key, off, len(old) // 2,
                                        len(new) // 2))
            old_b, new_b = bytes.fromhex(old), bytes.fromhex(new)
            if off + len(old_b) > EXE_SIZE:
                raise AssertionError('%s at 0x%08x runs %d bytes past the end'
                                     % (key, off,
                                        off + len(old_b) - EXE_SIZE))
            for i, byte in enumerate(range(off, off + len(old_b))):
                if owner.setdefault(byte, key) != key:
                    raise AssertionError('%s and %s both patch 0x%08x'
                                         % (owner[byte], key, byte))
                if byte in written and written[byte] != old_b[i]:
                    raise AssertionError(
                        '%s at 0x%08x expects %02x where an earlier site in '
                        'the same patch wrote %02x - has the site list been '
                        'reordered?' % (key, byte, old_b[i], written[byte]))
                written[byte] = new_b[i]
    return sum(len(v[2] or ()) for v in BY_KEY.values()), len(owner)


_check_table()


def default_state():
    return {key: True for key in BY_KEY}


def apply_feature(buf, sites):
    """Write one patch's sites into buf, in list order.

    Every site is checked against the bytes it expects before anything is
    written, and the first mismatch aborts the whole patch - this is the
    safety model, not a nicety. Order matters: a later site may overwrite an
    earlier one in the same patch, and _check_table enforces that the later
    one expects what the earlier one left.
    """
    for off, old, new in sites:
        old, new = bytes.fromhex(old), bytes.fromhex(new)
        if buf[off:off + len(old)] != old:
            raise ValueError('unexpected bytes at 0x%08x' % off)
        buf[off:off + len(old)] = new


def apply_dinput(buf):
    hits = list(DI_FIND.finditer(buf))
    if len(hits) != 1:
        raise ValueError('expected one call site, found %d' % len(hits))
    # +1 is the operand of the push; 6 is FOREGROUND|NONEXCLUSIVE and
    # 0x0A is BACKGROUND|NONEXCLUSIVE.
    buf[hits[0].start() + 1] = 0x0A


# --- ripping -------------------------------------------------------------
# bin/cue or a CD drive into the WAV files the CD audio patch plays. Standard
# library only, so the packaged build carries it too.

RAW = 2352                  # bytes per CD-DA sector
RATE = 44100
WAV_HDR = 44


# --------------------------------------------------------------------- WAV

def _wav_header(pcm_bytes):
    """Canonical 44-byte header for CD-DA: PCM, stereo, 44100, 16-bit.

    The CD audio patch divides by these to get track lengths, so they are not
    free to change. 36 is everything after the RIFF size field; the fmt
    numbers are chunk size 16, format 1 (PCM), 2 channels, sample rate, byte
    rate, block align 4, bits 16.
    """
    return (b'RIFF' + struct.pack('<I', 36 + pcm_bytes) + b'WAVEfmt ' +
            struct.pack('<IHHIIHH', 16, 1, 2, RATE, RATE * 4, 4, 16) +
            b'data' + struct.pack('<I', pcm_bytes))


class WavWriter:
    """Writes a WAV whose length is not known until the end."""

    def __init__(self, path):
        self.path = path
        self.f = open(path, 'wb')
        self.f.write(b'\0' * WAV_HDR)
        self.n = 0

    def write(self, data):
        self.f.write(data)
        self.n += len(data)

    def close(self):
        self.f.seek(0)
        self.f.write(_wav_header(self.n))
        self.f.close()

    def abort(self):
        """Throw the partial file away.

        Without this a rip that fails half way through leaves a short file
        with a valid header. Nothing downstream can tell it from a good one:
        the folder looks ripped, and the game reads its track length from the
        file size, so the track would just end early.
        """
        self.f.close()
        try:
            os.remove(self.path)
        except OSError:
            pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, *_rest):
        if exc_type is None:
            self.close()
        else:
            self.abort()


# --------------------------------------------------------------------- cue

_MSF = re.compile(r'(\d+):(\d+):(\d+)')


def _msf_to_sectors(text):
    stamp = _MSF.match(text)
    if not stamp:
        # Every other bad line in a sheet gets a sentence naming it, and an
        # AttributeError out of a regex is not one.
        raise ValueError('not a cue sheet timestamp: %r' % text)
    m, s, f = (int(x) for x in stamp.groups())
    return (m * 60 + s) * 75 + f


def _cue_file(base, line):
    """Where a cue sheet's FILE line actually points.

    Sheets travel with the images they describe and are written by tools on
    another machine, so the name in them is often not the name on this disk:
    a different case, a backslash path that means nothing here, or an
    absolute path to a drive letter that no longer exists. The bin sits
    beside the cue in every one of those, so the name is what matters and
    the path around it does not.
    """
    if '"' in line:
        name = line.split('"')[1]
    else:
        # FILE NAME TYPE, unquoted. The type is the last word; anything
        # between it and FILE is the name, spaces and all.
        parts = line.split()
        name = ' '.join(parts[1:-1]) if len(parts) > 2 else parts[-1]

    here = os.path.join(base, name)
    if os.path.exists(here):
        return here

    # A backslash is a separator to the tool that wrote it and a character
    # in a name to this one, so try it as a path first.
    walked = name.replace('\\', '/').rstrip('/')
    here = os.path.join(base, *walked.split('/'))
    if os.path.exists(here):
        return here

    # Then as a name, since the bin sits beside the sheet in every sheet
    # that has travelled.
    plain = walked.rsplit('/', 1)[-1]
    here = os.path.join(base, plain)
    if os.path.exists(here):
        return here

    try:
        for entry in os.listdir(base):
            if entry.lower() == plain.lower():
                return os.path.join(base, entry)
    except OSError:
        pass
    return os.path.join(base, plain)


def parse_cue(path):
    """Every track in a cue sheet, in sheet order.

    One dict per track: 'no', 'mode', 'bin' (the resolved path), 'start' from
    INDEX 01 and 'pregap' from its own INDEX 00, or None where there is none.
    A track's audio stops at the *next* track's pregap, which is rip_cue's
    business rather than this one's.
    """
    base = os.path.dirname(os.path.abspath(path))
    tracks, curbin, cur = [], None, None

    # utf-8-sig, because a sheet written on Windows can start with a BOM and
    # the first line is the one naming the bin.
    with open(path, 'r', encoding='utf-8-sig', errors='replace') as fh:
        for line in fh:
            line = line.strip()
            up = line.upper()

            if up.startswith('FILE'):
                curbin = _cue_file(base, line)

            elif up.startswith('TRACK'):
                parts = line.split()
                cur = {'no': int(parts[1]), 'mode': parts[2].upper(),
                       'bin': curbin, 'start': None, 'pregap': None}
                tracks.append(cur)

            elif up.startswith('INDEX') and cur is not None:
                parts = line.split()
                sec = _msf_to_sectors(parts[2])
                if parts[1] == '00':
                    cur['pregap'] = sec
                elif parts[1] == '01' and cur['start'] is None:
                    cur['start'] = sec

    if not tracks:
        raise ValueError('no TRACK entries in %s' % path)
    for t in tracks:
        if t['bin'] is None or not os.path.exists(t['bin']):
            raise IOError('bin file not found for track %d' % t['no'])
        if t['start'] is None:
            raise ValueError('track %d has no INDEX 01 in %s'
                             % (t['no'], path))
    return tracks


def _audio_spans(tracks):
    """(track, first sector, last sector) for every audio track worth writing.

    Where a track ends: the next track's pregap when the two share a bin file,
    so trailing silence is dropped, and the end of the file otherwise.
    """
    for i, t in enumerate(tracks):
        if 'AUDIO' not in t['mode']:
            continue
        nxt = tracks[i + 1] if i + 1 < len(tracks) else None
        if nxt is None or nxt['bin'] != t['bin']:
            end = os.path.getsize(t['bin']) // RAW
        else:
            end = nxt['pregap'] if nxt['pregap'] is not None else nxt['start']
        if end > t['start']:
            yield t, t['start'], end


def rip_bytes(cue_path):
    """How much room the WAV files will need, from the cue sheet alone."""
    return sum((end - start) * RAW + WAV_HDR
               for _t, start, end in _audio_spans(parse_cue(cue_path)))


def rip_cue(cue_path, outdir, progress=None):
    """Extract every AUDIO track from a bin/cue pair."""
    tracks = parse_cue(cue_path)
    os.makedirs(outdir, exist_ok=True)
    written = []

    for t, start, end in _audio_spans(tracks):
        out = os.path.join(outdir, 'track%02d.wav' % t['no'])
        total = (end - start) * RAW

        with open(t['bin'], 'rb') as src, WavWriter(out) as dst:
            src.seek(start * RAW)
            left = total
            while left > 0:
                chunk = src.read(min(left, RAW * 512))
                if not chunk:
                    break
                dst.write(chunk)
                left -= len(chunk)
                if progress:
                    progress(t['no'], total - left, total)

        written.append(out)

    if not written:
        raise ValueError('no audio tracks in %s - data-only image?' % cue_path)
    return written


# ------------------------------------------------------------ Linux device

CDROMREADTOCHDR = 0x5305
CDROMREADTOCENTRY = 0x5306
CDROMREADAUDIO = 0x530E
CDROM_LBA = 0x01
CDROM_DATA_TRACK = 0x04


def _linux_toc(fd):
    import fcntl
    hdr = bytearray(2)
    fcntl.ioctl(fd, CDROMREADTOCHDR, hdr)
    first, last = hdr[0], hdr[1]

    entries = []
    for no in list(range(first, last + 1)) + [0xAA]:      # 0xAA = lead-out
        buf = bytearray(struct.pack('<BBBxIB3x', no, 0, CDROM_LBA, 0, 0))
        fcntl.ioctl(fd, CDROMREADTOCENTRY, buf)
        trk, adrctrl, fmt, lba, _dm = struct.unpack('<BBBxIB3x', bytes(buf))
        entries.append({'no': trk, 'lba': lba,
                        'audio': not (adrctrl >> 4) & CDROM_DATA_TRACK})
    return entries


# struct cdrom_read_audio: addr, addr_format, nframes, then a pointer. The
# pointer's alignment is what differs between word sizes, so the padding has
# to as well.
_READ_AUDIO = '<IBxxxi4xQ' if struct.calcsize('P') == 8 else '<IBxxxiI'


def _linux_read(fd, lba, frames, buf):
    import fcntl
    req = struct.pack(_READ_AUDIO, lba, CDROM_LBA, frames,
                      ctypes.addressof(buf))
    fcntl.ioctl(fd, CDROMREADAUDIO, req)
    return bytes(buf)[:frames * RAW]


def _rip_linux(device, outdir, progress=None, chunk=8):
    fd = os.open(device, os.O_RDONLY | os.O_NONBLOCK)
    try:
        toc = _linux_toc(fd)
        os.makedirs(outdir, exist_ok=True)
        buf = ctypes.create_string_buffer(chunk * RAW)
        written = []

        for i, t in enumerate(toc):
            if t['no'] == 0xAA or not t['audio']:
                continue
            start, end = t['lba'], toc[i + 1]['lba']
            total = (end - start) * RAW
            out = os.path.join(outdir, 'track%02d.wav' % t['no'])

            with WavWriter(out) as dst:
                lba = start
                while lba < end:
                    n = min(chunk, end - lba)
                    dst.write(_linux_read(fd, lba, n, buf))
                    lba += n
                    if progress:
                        progress(t['no'], (lba - start) * RAW, total)
            written.append(out)
        if not written:
            raise ValueError('no audio tracks on %s - data-only disc?'
                             % device)
        return written
    finally:
        os.close(fd)


# ------------------------------------------------------------------ public

# Said in one place, because three of them used to answer differently: the
# window refused a drive letter, the CLI listed them and offered to rip one,
# and the README said drives were not read at all.
NO_WINDOWS_DRIVE = ('Reading a drive directly is not supported on Windows. '
                    'Image the disc to bin/cue first - ImgBurn in Read mode, '
                    'with the output set to BIN/CUE rather than ISO - and '
                    'give the .cue sheet instead.')


def rip_device(device, outdir, progress=None):
    """Rip audio tracks from a CD drive, real or cdemu-backed.

    Linux only. The Windows path was a raw DeviceIoControl read that no check
    could reach without a drive and a disc in it, and imaging the disc is the
    answer the rest of the patcher gives anyway - the installer reads nothing
    but bin/cue either."""
    if os.name == 'nt':
        raise ValueError(NO_WINDOWS_DRIVE)
    return _rip_linux(device, outdir, progress)


def list_devices():
    """Candidate optical devices, best-effort. Linux only; see rip_device."""
    if os.name == 'nt':
        return []
    return [os.path.join('/dev', d) for d in sorted(os.listdir('/dev'))
            if re.match(r'^sr\d+$', d)]


MUSIC_SUBDIR = 'music'


def outdir_for(gamedir):
    """Where the tracks live, given the directory holding v_on.exe."""
    return os.path.join(gamedir, MUSIC_SUBDIR)


# Every pressing carries the same 26 audio tracks, numbered 02 to 27, and the
# game asks for them by number. A cue with a different layout is a different
# disc, and ripping it fills the music folder with tracks nothing asks for.
VO_AUDIO = tuple(range(2, 28))


def looks_like_drive(source):
    """A device node the ripper can read directly.

    Linux only, and only for ripping: a cdemu device or a real drive answers
    the same ioctls. Windows drive letters are not accepted here - the raw
    read behind them is the one path with no test over it, and imaging the
    disc first is the answer the README gives instead."""
    return bool(re.match(r'^/dev/', source.strip()))


def audio_tracks(cue_path):
    """The numbers of the audio tracks in a cue sheet. Raises like
    parse_cue does when the sheet or its bin files are wrong."""
    return [t['no'] for t in parse_cue(cue_path) if 'AUDIO' in t['mode']]


def rip(source, outdir, progress=None):
    """Dispatch on what the source looks like."""
    if source.lower().endswith('.cue'):
        return rip_cue(source, outdir, progress)
    return rip_device(source, outdir, progress)


# Returned by music_status when it has no folder to describe.
MUSIC_NEEDS_EXE = 'Choose a folder above, or pick your v_on.exe.'


def music_status(gamedir):
    """One line on the music folder, for the GUI.

    It names the folder either way. Where the tracks are going is the thing
    someone with the game already installed cannot otherwise see, because
    that folder is worked out from their v_on.exe rather than typed in."""
    if not gamedir:
        return MUSIC_NEEDS_EXE
    out = outdir_for(gamedir)
    found = ([f for f in os.listdir(out)
              if re.match(r'track\d+\.wav$', f, re.I)]
             if os.path.isdir(out) else [])
    if not found:
        return 'No tracks yet. They go to %s' % out
    mb = sum(os.path.getsize(os.path.join(out, f)) for f in found) // (1 << 20)
    return '%d tracks in %s (%d MB)' % (len(found), out, mb)


# --- installing from a disc image ---------------------------------------
# The disc is read directly: no mounting, no virtual drive, no setup.exe.
# Sega's own installer is driven by ssp.ini in the root of the disc, so the
# copy rules are taken from there rather than guessed at, and the same code
# handles the retail and OEM pressings, which disagree about where the help
# files live.

LOGICAL = 2048                  # user bytes in a sector, whatever its form

# Sector layouts, walked in order; the one whose sector 16 holds an ISO9660
# descriptor wins. Looking rather than trusting TRACK 01 means a cue sheet
# that names the wrong mode still works, and the four cover every pressing.
#   name            stride  offset of the user bytes
SECTOR_FORMS = (
    ('MODE1/2352', 2352, 16),
    ('MODE2/2352', 2352, 24),
    ('MODE1/2048', 2048, 0),
    ('MODE2/2336', 2336, 8),
)

PRIMARY_VD = 16                 # where the descriptors start, by the standard

# Sections of ssp.ini that describe the installer rather than a language.
NOT_A_LANGUAGE = ('OPTION', 'RUNTIME', 'DIRECTX')


class DiscError(Exception):
    """The image cannot be read as a Virtual-On disc. The message is shown
    to the user as it is, so it says what to do about it."""


class DataTrack:
    """2048-byte logical sectors out of a cue sheet's data track."""

    def __init__(self, path, start=0):
        self.path, self.start = path, start
        self.fh = open(path, 'rb')
        size = os.path.getsize(path)
        for name, stride, offset in SECTOR_FORMS:
            at = (start + PRIMARY_VD) * stride + offset
            if at + 6 <= size:
                self.fh.seek(at)
                if self.fh.read(6)[1:6] == b'CD001':
                    self.form, self.stride, self.offset = name, stride, offset
                    return
        self.fh.close()
        raise DiscError('No filesystem in %s. The image is damaged, or the '
                        'cue sheet names the wrong file for track 1.'
                        % os.path.basename(path))

    def close(self):
        self.fh.close()

    def sector(self, lba):
        self.fh.seek((self.start + lba) * self.stride + self.offset)
        data = self.fh.read(LOGICAL)
        if len(data) != LOGICAL:
            raise DiscError('The image ends early. It is truncated, or one of '
                            'the bin files beside the cue sheet is missing.')
        return data

    def read(self, lba, length):
        out = bytearray()
        while len(out) < length:
            out += self.sector(lba + len(out) // LOGICAL)
        return bytes(out[:length])

    def extract(self, lba, length, dest, progress=None, done=0, total=0):
        """Stream one file to disk, returning the running byte count."""
        left = length
        with open(dest, 'wb') as out:
            while left > 0:
                chunk = self.sector(lba)[:min(left, LOGICAL)]
                out.write(chunk)
                left -= len(chunk)
                lba += 1
                done += len(chunk)
                if progress:
                    progress(done, total)
        return done


def _iso_records(data):
    """Directory records in one extent. A record never straddles a sector,
    so a zero length byte means skip to the next one, not end of list."""
    at = 0
    while at < len(data):
        length = data[at]
        if length == 0:
            at = (at // LOGICAL + 1) * LOGICAL
            continue
        yield data[at:at + length]
        at += length


def iso_entries(track, lba, size):
    """{lowercased name: (is_dir, lba, size)} for one directory."""
    out = {}
    for rec in _iso_records(track.read(lba, size)):
        if len(rec) < 34:
            continue
        flags = rec[25]
        if flags & 0x80:
            # A file split over several extents. No Virtual-On pressing has
            # one, and guessing at the continuation would corrupt the copy
            # without saying so.
            raise DiscError('This image uses multi-extent files, which the '
                            'patcher cannot read. Install the disc the usual '
                            'way and pick the installed v_on.exe.')
        name_len = rec[32]
        raw = rec[33:33 + name_len]
        if name_len == 1 and raw in (b'\x00', b'\x01'):
            continue                            # . and ..
        name = raw.decode('latin-1').split(';')[0].rstrip('.')
        out[name.lower()] = (bool(flags & 0x02),
                             int.from_bytes(rec[2:6], 'little'),
                             int.from_bytes(rec[10:14], 'little'))
    return out


def iso_root(track):
    pvd = track.sector(PRIMARY_VD)
    if pvd[0] != 1:
        raise DiscError('Sector %d of this image is not a volume descriptor.'
                        % PRIMARY_VD)
    root = pvd[156:190]
    return iso_entries(track, int.from_bytes(root[2:6], 'little'),
                       int.from_bytes(root[10:14], 'little'))


def _iso_files(track, entry, what):
    """The files in one directory named by a root entry, sorted."""
    if not entry or not entry[0]:
        raise DiscError('No %s directory on this disc.' % what.upper())
    found = iso_entries(track, entry[1], entry[2])
    return sorted((name, lba, size) for name, (is_dir, lba, size)
                  in found.items() if not is_dir)


def parse_ssp(text):
    """The disc's own install manifest. Sections upper, keys lower."""
    out, current = {}, None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(';'):
            continue
        if line.startswith('[') and line.endswith(']'):
            current = line[1:-1].strip().upper()
            out.setdefault(current, {})
        elif '=' in line and current is not None:
            key, value = line.split('=', 1)
            out[current][key.strip().lower()] = value.strip()
    return out


class DiscPlan:
    """What to copy where, read off the disc before anything is written."""

    def __init__(self, track, root, ssp):
        self.track, self.root, self.ssp = track, root, ssp
        option = ssp.get('OPTION', {})
        self.source_dir = (option.get('sourcepath1') or 'V_ON').lower()
        # Whether a language directory is copied at all is a property of the
        # pressing: the retail discs say so in Select1 and keep the help
        # files in english/, the OEM disc does not and keeps them in v_on/.
        self.wants_language = 'langexeclusive' in option.get('select1',
                                                             '').lower()
        # Only the ones this disc can actually give. A section names a
        # directory, and a pressing that ships fewer manuals than its
        # ssp.ini lists would otherwise offer a language and then fail on
        # the copy. A pressing that copies no language directory at all -
        # the OEM disc, whose manual sits in v_on\ - offers none, rather
        # than listing sections that decide nothing.
        self.languages = ([name for name in ssp if name not in NOT_A_LANGUAGE
                           and self._has_language(name)]
                          if self.wants_language else [])
        self.default = (option.get('defaultsection') or '').upper()
        if self.default not in self.languages:
            self.default = self.languages[0] if self.languages else ''

    def _has_language(self, name):
        folder = self.ssp.get(name, {}).get('langexeclusive', '').strip()
        entry = self.root.get(folder.lower()) if folder else None
        return bool(entry and entry[0])

    def language_dir(self, language):
        if not self.wants_language:
            return None
        # A name this disc does not have would otherwise resolve to no
        # directory at all, and the install would quietly finish without a
        # manual. Only checked when one was asked for by name: the default
        # is empty on a disc that lists no usable section.
        if language and language.upper() not in self.languages:
            raise DiscError('This disc has no %s manual. It carries: %s.'
                            % (language, ', '.join(self.languages) or 'none'))
        section = self.ssp.get((language or self.default).upper(), {})
        return section.get('langexeclusive', '').strip().lower() or None

    def files(self, language=None):
        """[(name, lba, size)] in the order they are written."""
        out = _iso_files(self.track, self.root.get(self.source_dir),
                         self.source_dir)
        folder = self.language_dir(language)
        if folder:
            out += _iso_files(self.track, self.root.get(folder), folder)
        return out


def open_disc(cue_path):
    """(DataTrack, DiscPlan) for a cue sheet. Raises DiscError or OSError."""
    tracks = parse_cue(cue_path)
    data = [t for t in tracks if 'AUDIO' not in t['mode']]
    if not data:
        raise DiscError('This cue sheet lists only audio tracks, so the game '
                        'files are not in it.')
    track = DataTrack(data[0]['bin'], data[0]['start'])
    try:
        root = iso_root(track)
        entry = root.get('ssp.ini')
        if not entry or entry[0]:
            raise DiscError('No ssp.ini in the root of this image, so it is '
                            'not a Virtual-On disc.')
        ssp = parse_ssp(track.read(entry[1], entry[2]).decode('latin-1'))
        if 'OPTION' not in ssp:
            raise DiscError('The ssp.ini on this disc has no [option] '
                            'section, so there is nothing to install from.')
        return track, DiscPlan(track, root, ssp)
    except Exception:
        track.close()
        raise


def disc_build(track, plan):
    """Identify v_on.exe on the disc without extracting anything else."""
    for name, lba, size in plan.files():
        if name == 'v_on.exe':
            digest = hashlib.md5(track.read(lba, size)).hexdigest()
            known = OTHER_BUILDS.get(digest)
            return {
                'size': size, 'md5': digest,
                'supported': digest == ORIGINAL_MD5,
                'name': ('retail disc' if digest == ORIGINAL_MD5
                         else known[1] if known else 'unrecognised'),
                'why': ('' if digest == ORIGINAL_MD5 else
                        known[2] if known else
                        'Not a v_on.exe this patcher knows - a bad rip, a '
                        'repack, or a disc it has not seen.'),
            }
    raise DiscError('No v_on.exe in the %s directory of this image.'
                    % plan.source_dir.upper())


def probe_disc(cue_path):
    """Everything the window needs to describe a disc, writing nothing."""
    track, plan = open_disc(cue_path)
    try:
        files = plan.files(plan.default)
        return {
            'form': track.form,
            'source_dir': plan.source_dir,
            'languages': plan.languages,
            'default_language': plan.default,
            'wants_language': plan.wants_language,
            'build': disc_build(track, plan),
            'bytes': sum(size for _n, _l, size in files),
            'count': len(files),
        }
    finally:
        track.close()


def install_disc(cue_path, dest, language=None, progress=None):
    """Copy the game out of the image. Returns the names written."""
    track, plan = open_disc(cue_path)
    try:
        files = plan.files(language)
        total = sum(size for _n, _l, size in files)
        os.makedirs(dest, exist_ok=True)
        written, done = [], 0
        for name, lba, size in files:
            done = track.extract(lba, size, os.path.join(dest, name),
                                 progress, done, total)
            written.append(name)
        # No v_on.ini is written, deliberately.
        #
        # Sega's installer asks a dialog and copies v_on_a.ini or v_on_b.ini
        # over it, then deletes both (setup.exe 0x408acf). Doing the same
        # would fight the patches: v_on_a.ini carries Motion=3, a frame
        # divisor the patched game obeys, so a freshly installed and patched
        # copy would run at a third speed - the opposite of what the frame
        # rate patch is for. An ini that is there wins over the defaults the
        # patches set.
        #
        # Both files are copied as the disc has them, so the settings are
        # not lost, and the game writes its own v_on.ini on first run.
        return written
    finally:
        track.close()


def install_in_background(cue_path, dest, language, progress, done):
    """Extract on a worker thread. Both callbacks fire off the UI thread."""
    def work():
        try:
            written = install_disc(cue_path, dest, language, progress)
        except Exception as exc:                    # any failure, one path
            done(exc, None)
        else:
            done(None, written)

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    return thread


def why_unwritable(folder, exc, name=None, elsewhere=None):
    """Turn a failed write into advice.

    Windows is checked first, because it folds several different causes into
    EACCES: a write-protected drive, a file another process has open, and an
    actual permission problem all arrive as errno 13, and the answer to each
    is different."""
    elsewhere = elsewhere or ('Copy the game folder somewhere you own - your '
                              'home or Documents - and patch it there.')
    # The caller passes both, rather than this guessing from the path:
    # splitting a Windows path on a Linux box gets it wrong, and the
    # sentences need the folder in some cases and the file in others.
    name = name or folder
    win = getattr(exc, 'winerror', None)
    if win == 32 or win == 33:              # SHARING_VIOLATION, LOCK_VIOLATION
        return ('Something else has %s open. Close the game and any launcher '
                'or anti-virus scanning it, then try again.' % name)
    if win == 19:                           # WRITE_PROTECT
        return ('%s is write protected. If the game is on a mounted disc '
                'image, copy it to your hard drive first.' % folder)
    if exc.errno in (errno.EACCES, errno.EPERM):
        # Deliberately not "run as administrator": that writes files the
        # player then cannot delete, and Program Files is the usual cause.
        return 'No permission to write in %s. %s' % (folder, elsewhere)
    if exc.errno == errno.EROFS:
        return ('%s is read-only. If the game is on a mounted disc image, '
                'copy it to your hard drive first.' % folder)
    if exc.errno == errno.ENOSPC:
        return 'No space left on the drive holding %s.' % folder
    if exc.errno == errno.ENOENT:
        return ('%s is gone. Has the folder been moved, or a drive '
                'disconnected, since the file was selected?' % folder)
    if exc.errno == errno.ETXTBSY:          # the same thing on Linux
        return '%s is in use. Close the game and try again.' % name
    # Anything else: report it and stop guessing at the cause.
    return 'Cannot write in %s: %s.' % (folder, exc.strerror or exc)


def copy_failure(folder, exc):
    """What to say when a copy or a rip stops part way.

    The destination is checked before either one starts, so anything that
    gets here happened during the write - a disk that filled up, a drive
    pulled out - and reads as a bare OSError otherwise."""
    if isinstance(exc, OSError):
        return why_unwritable(folder, exc)
    return str(exc)


def writable(folder):
    """Can a file actually be created here? Returns (ok, why not).

    A real write, not os.access: on Windows os.access(W_OK) only reports the
    read-only attribute and says nothing about ACLs, so a folder under
    Program Files passes it and then fails on the first file. Creating and
    deleting a probe file is the only answer that holds on both platforms,
    and it costs nothing next to copying 95 MB.

    A PyInstaller build carries a manifest, so Windows does not silently
    redirect the write into VirtualStore: an unelevated write to a folder
    the user does not own arrives here as EACCES rather than appearing to
    succeed somewhere else."""
    probe = os.path.join(folder, '.vo_patch-write-test')
    try:
        with open(probe, 'wb') as fh:
            fh.write(b'x')
        os.remove(probe)
    except OSError as exc:
        return False, why_unwritable(
            folder, exc, elsewhere='Choose a folder you own - your home, '
                                   'Documents or Games.')
    return True, ''


def room_for(folder, needed, what=''):
    """A message if `folder` cannot take `needed` more bytes, else ''.

    The folder may not exist yet - it is often the one about to be created -
    so the nearest parent that does is what gets asked. No answer at all is
    not the same as a bad one, and gets out of the way."""
    if not needed:
        return ''
    while folder and not os.path.isdir(folder):
        parent = os.path.dirname(folder)
        if parent == folder:
            return ''
        folder = parent
    try:
        free = shutil.disk_usage(folder or '.').free
    except OSError:
        return ''
    if free >= needed:
        return ''
    return ('Not enough room%s: %d MB free, %d MB needed.'
            % (' for ' + what if what else '', free >> 20, needed >> 20))


def dest_problem(path, needed):
    """(message, level) for a destination folder, or (None, None).

    Checked before the copy starts: filling a disk and then failing on the
    last file leaves a half-installed game and no clue why."""
    if not path:
        return None, None
    exists = os.path.isdir(path)
    probe = path if exists else os.path.dirname(os.path.abspath(path)) or '.'
    if not os.path.isdir(probe):
        return 'There is no %s to create that folder in.' % probe, 'bad'
    ok, why = writable(probe)
    if not ok:
        return why, 'bad'
    short = room_for(probe, needed)
    if short:
        return short, 'bad'
    if exists and os.path.exists(os.path.join(path, 'v_on.exe')):
        # Worth its own message: installing over a patched copy replaces
        # v_on.exe with the stock one and leaves the .bak beside it no
        # longer matching, which Restore original would then act on.
        return ('A game is already installed there. Installing replaces it, '
                'settings and patches included.'), 'warn'
    if exists and os.listdir(path):
        return ('That folder is not empty. Files with the same name are '
                'replaced.'), 'warn'
    return None, None


class Cancelled(Exception):
    """Raised out of a progress callback to stop a copy or a rip.

    It travels the same path as a real failure, so the WavWriter context
    manager discards the partial track on the way out."""



def rip_in_background(source, gamedir, progress, done):
    """Rip on a worker thread. Both callbacks fire off the UI thread."""
    def work():
        try:
            files = rip(source, outdir_for(gamedir), progress)
        except Exception as exc:                    # any failure, one path
            done(exc, None)
        else:
            done(None, files)

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    return thread


# --- netplay ------------------------------------------------------------
# The DirectPlay replacement, compiled and baked in by net/build.py so the
# patcher stays one file. Unlike cnc-ddraw this is ours, and it replaces a
# file the game already has, so the original is kept as dpctrl.dll.stock.

# --- netplay blob: written by net/build.py, do not edit ---
# Source: net/dpctrl.c, compiled by net/build.py.
NETPLAY_SRC_SHA = '20697af788f6e38e4401ab296ccafc8175889c1b54b5258948a149b6985a481a'
# sha256 of the compiled DLL, so the patcher can tell its own build
# from an older one already installed.
NETPLAY_DLL_SHA = '307a62454f56f27209d6c5f9898d1a8441272e2136caba3103fe3e80dddad477'
NETPLAY_DLL_Z = (
    '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'
    'rlvpqw=='
)
# --- end netplay blob ---

NETPLAY_NAME = 'dpctrl.dll'
NETPLAY_KEEP = 'dpctrl.dll.stock'

# Ours contains this - the log file the DLL writes - and the game's own does
# not. A hash of the current blob would not do: the linker stamps a build
# time and an image base, so rebuilding from identical source gives
# different bytes and a DLL installed by an earlier release would read as a
# stranger.
NETPLAY_MARK = b'vo-net.log'


def netplay_dll():
    """The DLL bytes, unpacked from the blob."""
    if not NETPLAY_DLL_Z:
        raise ValueError('this build carries no netplay DLL')
    return zlib.decompress(base64.b64decode(NETPLAY_DLL_Z))


def _netplay_read(path):
    try:
        with open(path, 'rb') as fh:
            return fh.read()
    except OSError:
        return None


def _netplay_is_ours(path):
    data = _netplay_read(path)
    return data is not None and NETPLAY_MARK in data


# The two patches that must match for a lockstep match not to drift: the
# frame-rate divisor and the round-loss crash fix. The DLL fingerprints the
# same two bytes from the running exe; here the patcher reads them from disk
# so it can warn at install time rather than leaving it to a refused match.
# File offsets into v_on.exe, with the byte that differs patched vs stock.
# These are the same two bytes net/dpctrl.c fingerprints as FP_DIVISOR_VA and
# FP_CONTINUE_VA (VA = offset + 0x400c00); change both together.
SYNC_SITES = ((0x0010afc4, 0x01), (0x00077f5a, 0x90))   # divisor, continuefix


def netplay_sync_ready(gamedir):
    """True if v_on.exe here has both simulation-affecting patches, False if
    either is missing, None if the exe cannot be read (so: do not warn)."""
    try:
        with open(os.path.join(gamedir, 'v_on.exe'), 'rb') as fh:
            data = fh.read()
    except OSError:
        return None
    for off, patched in SYNC_SITES:
        if off >= len(data) or data[off] != patched:
            return False
    return True


def netplay_status(gamedir):
    """'current', 'old', 'stock', or None if there is nothing to look at.

    'old' is one of ours from an earlier build: it carries our marker but
    its bytes do not hash to the DLL this patcher ships, so Install would
    replace it with a newer one. The file itself is read rather than the
    .stock copy taken as proof: a reinstall can put the game's own DLL back
    and leave .stock where it was, and the button would then offer to
    remove something that is not there."""
    if not gamedir:
        return None
    path = os.path.join(gamedir, NETPLAY_NAME)
    data = _netplay_read(path)
    if data is None:
        return None
    if NETPLAY_MARK not in data:
        return 'stock'
    return 'current' if hashlib.sha256(data).hexdigest() == NETPLAY_DLL_SHA \
        else 'old'


def install_netplay(gamedir):
    """Put our DLL in place, keeping the game's own copy beside it."""
    path = os.path.join(gamedir, NETPLAY_NAME)
    keep = os.path.join(gamedir, NETPLAY_KEEP)
    if not os.path.exists(path) and not os.path.exists(keep):
        raise ValueError('no %s here - is this the game folder?'
                         % NETPLAY_NAME)
    # Only ever keep a DLL that is not ours. Installing over our own copy
    # with the .stock one missing used to save that copy as the stock file,
    # and the game's own was then gone for good.
    if (os.path.exists(path) and not os.path.exists(keep)
            and not _netplay_is_ours(path)):
        shutil.copy2(path, keep)
    with open(path, 'wb') as f:
        f.write(netplay_dll())
    return path


def remove_netplay(gamedir):
    """Put the game's own DirectPlay DLL back."""
    path = os.path.join(gamedir, NETPLAY_NAME)
    keep = os.path.join(gamedir, NETPLAY_KEEP)
    if not os.path.exists(keep):
        raise ValueError('no %s to restore' % NETPLAY_KEEP)
    shutil.copy2(keep, path)
    os.remove(keep)
    return path


# --- cnc-ddraw -----------------------------------------------------------
# Not ours and not bundled. cnc-ddraw is GPLv3, so the patcher fetches it
# from upstream at the user's request rather than shipping a copy: that
# keeps this a convenience wrapper around a download the user could do by
# hand, and leaves distribution where it belongs. Do not embed the zip.

DDRAW_URL = ('https://github.com/FunkyFr3sh/cnc-ddraw/releases/latest/'
             'download/cnc-ddraw.zip')
DDRAW_MAX = 32 << 20            # sanity bound on the download
DDRAW_FILES = ('ddraw.dll', 'ddraw.ini', 'cnc-ddraw config.exe')
DDRAW_DIRS = ('Shaders/',)

# Applied to the [ddraw] section of the archive's own ddraw.ini the first
# time it is written, and never afterwards. The rest of that file - the
# comments and the 280-odd game specific sections - is left as it comes.
# Together these give a borderless window at the right aspect ratio, which
# the stock defaults do not.
DDRAW_SETTINGS = (
    ('fullscreen', 'true'),         # stretch to the screen
    ('windowed', 'true'),           # with fullscreen=true, borderless
    ('maintas', 'true'),            # 4:3, the whole point
    ('noactivateapp', 'true'),      # survive alt+tab
    ('toggle_borderless', 'true'),  # let alt+enter switch back
    ('devmode', 'true'),            # cnc-ddraw's name for "do not trap the
                                    # cursor". The game does not use the
                                    # mouse, so trapping it only makes the
                                    # second monitor unreachable.
    ('game_handles_close', 'true'),  # without it cnc-ddraw answers the close
                                    # button with ExitProcess, so the game
                                    # never gets WM_DESTROY and never writes
                                    # v_on.ini or BkUp.bin.
)


def _ddraw_wanted(name):
    """True for the members worth extracting, false for anything else.

    A whitelist rather than extractall: a zip can name ..\\..\\somewhere
    and the standard library will write it.
    """
    if name.endswith('/'):
        return False
    if '\\' in name or name.startswith('/') or '..' in name.split('/'):
        return False
    if name in DDRAW_FILES:
        return True
    return any(name.startswith(d) for d in DDRAW_DIRS)


def ddraw_status(gamedir):
    """Which of the pieces are already beside the game."""
    if not gamedir:
        return None
    dll = os.path.join(gamedir, 'ddraw.dll')
    return os.path.exists(dll)


def _urlopen(req, timeout=30):
    """urlopen, retried against a bundled CA list.

    Windows keeps only a small set of root certificates and fetches the rest
    on demand through CryptoAPI, which OpenSSL never consults - so a machine
    that has not needed this root before reports it as missing and the
    download fails. certifi is bundled in the release build for that case.
    Anything else, including a genuinely bad certificate, is raised as it
    was."""
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.URLError as exc:
        if not isinstance(getattr(exc, 'reason', None),
                          ssl.SSLCertVerificationError):
            raise
        try:
            import certifi
        except ImportError:
            raise
        context = ssl.create_default_context(cafile=certifi.where())
    return urllib.request.urlopen(req, timeout=timeout, context=context)


def _ddraw_configure(text):
    """Set DDRAW_SETTINGS in the [ddraw] section, leave everything else.

    The file is edited line by line rather than parsed and rewritten: it is
    mostly comments and per-game sections, and configparser would throw all
    of that away. A key that is not found is added at the end of the section
    rather than silently dropped."""
    lines = text.split('\n')
    want = dict(DDRAW_SETTINGS)
    section, end = None, None
    for n, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith('['):
            if section == 'ddraw':
                break
            section = stripped[1:-1].lower()
            continue
        if section != 'ddraw' or stripped.startswith(';') or '=' not in line:
            continue
        key = line.split('=', 1)[0].strip()
        end = n
        if key in want:
            lines[n] = '%s=%s' % (key, want.pop(key))
    if want and end is not None:
        lines[end + 1:end + 1] = ['%s=%s' % kv for kv in DDRAW_SETTINGS
                                  if kv[0] in want]
    return '\n'.join(lines)


def install_ddraw(gamedir, progress=None):
    """Download cnc-ddraw and unpack it beside the game.

    Returns the list of files written. An existing ddraw.ini is left alone,
    so re-running to update never discards someone's settings.
    """
    req = urllib.request.Request(DDRAW_URL, headers={
        'User-Agent': '%s/%s' % (NAME, VERSION)})
    blob = io.BytesIO()
    with _urlopen(req, timeout=30) as resp:
        total = int(resp.headers.get('Content-Length') or 0)
        while True:
            chunk = resp.read(64 << 10)
            if not chunk:
                break
            blob.write(chunk)
            if blob.tell() > DDRAW_MAX:
                raise ValueError('download is larger than %d MB'
                                 % (DDRAW_MAX >> 20))
            if progress:
                progress(blob.tell(), total)

    written = []
    with zipfile.ZipFile(blob) as zf:
        members = [m for m in zf.namelist() if _ddraw_wanted(m)]
        if 'ddraw.dll' not in members:
            raise ValueError('no ddraw.dll in the archive')
        for name in members:
            dest = os.path.join(gamedir, *name.split('/'))
            if name == 'ddraw.ini' and os.path.exists(dest):
                continue                        # never clobber settings
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            if name == 'ddraw.ini':
                # Written once, on a folder that has none. cnc-ddraw reads
                # this file as CRLF text, so keep it that way.
                text = zf.read(name).decode('utf-8', 'replace')
                text = _ddraw_configure(text.replace('\r\n', '\n'))
                with open(dest, 'wb') as out:
                    out.write(text.replace('\n', '\r\n').encode('utf-8'))
            else:
                with zf.open(name) as src, open(dest, 'wb') as out:
                    shutil.copyfileobj(src, out)
            written.append(name)
    return written


def remove_ddraw(gamedir):
    """Take it back out again, leaving ddraw.ini in case it is wanted."""
    gone = []
    for name in DDRAW_FILES:
        if name == 'ddraw.ini':
            continue
        path = os.path.join(gamedir, name)
        if os.path.exists(path):
            os.remove(path)
            gone.append(name)
    shaders = os.path.join(gamedir, 'Shaders')
    if os.path.isdir(shaders):
        shutil.rmtree(shaders, ignore_errors=True)
        gone.append('Shaders')
    return gone


def install_ddraw_in_background(gamedir, progress, done):
    """Same shape as rip_in_background: both callbacks fire off the UI
    thread."""
    def work():
        try:
            files = install_ddraw(gamedir, progress)
        except Exception as exc:
            done(exc, None)
        else:
            done(None, files)

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    return thread


# --- CD audio ------------------------------------------------------------
# GENERATED - do not edit the hex by hand.
#
# Assembled from asm/vocd.asm and asm/layout.py. To change it: edit those and
# run
#     python3 asm/build.py
# which rewrites everything between the markers below, including VOCD_MAGICS.
# CI runs `asm/build.py --check` on every push and fails if the two have
# drifted.
#
# The only absolute addresses in VOCD_CODE are the placeholders below, which
# apply_cdaudio fills in once it has read the executable. VOCD_DATA is the
# string table and the scratch space.

# The F11 dialog template and the dialog's long code paths (asm/voxt.asm)
# ride in their own appended section: the dead menu resource the template
# used to squeeze into caps it at 460 bytes, and the dialog procedure's
# cave is nearly full. f11pause.asm pushes MAGIC_TEMPLATE where the
# template's address belongs, and debugbox.asm calls through ANNEXREL, a
# rel32 to the code at the template's tail; apply_extras_template() below
# fills both once the section exists, the way apply_cdaudio() fills
# .vocd's.
MAGIC_TEMPLATE = 0xE7E7E7E7
MAGIC_ANNEXREL = 0xEAEAEAEA


def apply_extras_template(buf):
    """Append the template-and-annex section, point f11pause at the
    template and the dialog procedure's call at the annex."""
    pe = _PE(buf)
    gap = (-len(EXTRAS_TPL)) % 16
    rva = pe.add_section('.voxt', EXTRAS_TPL + b'\0' * gap + VOXT_CODE,
                         chars=0x60000040)

    pattern = struct.pack('<I', MAGIC_TEMPLATE)
    if F11PAUSE_CODE.count(pattern) != 1:
        raise ValueError('the TEMPLATE placeholder should appear exactly '
                         'once in the f11pause blob')
    at = F11PAUSE_AT + F11PAUSE_CODE.index(pattern)
    if pe.d[at:at + 4] != pattern:
        raise ValueError('the TEMPLATE placeholder is not where the '
                         'f11pause site put it')
    struct.pack_into('<I', pe.d, at, pe.base + rva)

    pattern = struct.pack('<I', MAGIC_ANNEXREL)
    if DEBUGBOX_PROC.count(pattern) != 1:
        raise ValueError('the ANNEXREL placeholder should appear exactly '
                         'once in the dialog procedure blob')
    idx = DEBUGBOX_PROC.index(pattern)
    at = DBGPROC_AT + idx
    if pe.d[at:at + 4] != pattern:
        raise ValueError('the ANNEXREL placeholder is not where the '
                         'dialog procedure site put it')
    annex = pe.base + rva + len(EXTRAS_TPL) + gap
    struct.pack_into('<i', pe.d, at,
                     annex - (DBGPROC_VA + idx + 4))
    return pe.d


# VOCD BLOB BEGIN
VOCD_MAGICS = {
    'MAGIC_ORIGENTRY': 0xE1E1E1E1,     # VA of the entry point we chain to
    'MAGIC_IATMCI': 0xE2E2E2E2,        # VA of the mciSendCommandA IAT slot
    'MAGIC_LOADLIB': 0xE3E3E3E3,       # VA of the LoadLibraryA IAT slot
    'MAGIC_GETPROC': 0xE4E4E4E4,       # VA of the GetProcAddress IAT slot
    'MAGIC_DATA': 0xE5E5E5E5,          # VA the data blob lands at
}

VOCD_CODE = bytes.fromhex(
    'e9d7010000eb1aacaa84c075fa4fc331d2b90a000000f7f10430aa88d00430aa'
    'c360bbe5e5e5e5837b2c000f8545010000c7432c010000008d83c005000050ff'
    '15e3e3e3e389c68d83e60500005056ff15e4e4e4e48943148d83f90500005056'
    'ff15e4e4e4e48943188d83050600005056ff15e4e4e4e489431c8d8311060000'
    '5056ff15e4e4e4e48943208d831d0600005056ff15e4e4e4e48943288d83cd05'
    '000050ff15e3e3e3e385c00f84c500000089c68d83d70500005056ff15e4e4e4'
    'e489431085c00f84aa0000008d83d00100006808010000506a00ff531485c00f'
    '84910000008dbbd001000001c74f803f5c740a8d83d001000039c777f0c64701'
    '00be02000000e8720000006a0068800000006a036a006a0168000000808d83e0'
    '02000050ff531883f8ff744489c76a0057ff531c89c557ff532083ed2c763189'
    'e831d2b930090000f7f131d2b994110000f7f189c589d031d2b94b000000f7f1'
    'c1e00809c5c1e21009d5896cb34089334683fe6472906168e1e1e1e1c3568dbb'
    'e00200008db3d0010000e878feffff8db32f060000e86dfeffff8b0424e86dfe'
    'ffff8db33b060000e85afeffff5ec36a006a006a008d040350ff5310c3837b04'
    '007418b8c2060000e8e2ffffffc7430400000000c7430800000000c3fc5589e5'
    '535657bbe5e5e5e5833b000f84940000008b450c3d0308000075408b5510f7c2'
    '00200000747ff7c20010000075778b751485f674708b460885c074698d932706'
    '00005250ff532885c0755ae88dffffffc74604cefa000031c0eb5c817d08cefa'
    '000075413d140800000f84590100003d060800000f84b40000003d0808000074'
    '583d09080000746a3d550800000f84800000003d0408000074363d0b08000074'
    '1d31c0eb12ff7514ff7510ff750cff7508ff15e2e2e2e25f5e5b5dc210008b75'
    '1485f67407c746040100000031c0ebe7e808ffffff31c0ebde837b0400740fb8'
    '98060000e8e6feffffe8effeffff31c0ebc5837b04007417837b08007511b8a5'
    '060000e8c7feffffc743080100000031c0eba4837b08007411b8b3060000e8ac'
    'feffffc743080000000031c0eb898b5510f7c2040000000f84840000008b7514'
    '85f6747d8b46040fb6f083fe02727283fe64736d8b44b34085c07465e87cfeff'
    'ffe837feffff8dbb000400008db340060000e8b0fcffff8db3e0020000e8a5fc'
    'ffff8db347060000e89afcffff6a006a006a008d830004000050ff531085c075'
    '208b45148b40040fb6c0894304b866060000e818feffffb88b060000e80efeff'
    'ff31c0e9effeffff8b751485f6750731c0e9e1feffff8b460883f80375078b13'
    'e9e300000083f801752d8b5510f7c2100000000f84cd0000008b4e0c83f9010f'
    '82c100000083f9640f83b80000008b548b40e9b100000083f8047556ba0d0200'
    '00837b08007544837b04000f84970000006a006a408d8300040000508d83d006'
    '000050ff531085c0757e8d83e4060000508d830004000050ff5328ba0d020000'
    '85c07564ba0e020000eb5dba11020000eb5683f808750e8b530485d2754aba01'
    '000000eb4383f805743583f807743083f8067507ba0a000000eb2d3d01400000'
    '7524ba400400008b4d10f7c1100000007416837e0c017510ba41040000eb09ba'
    '01000000eb0231d289560431c0e9e5fdffff'
)

VOCD_DATA = bytes.fromhex(
    '0000000000000000000000000000000000000000000000000000000000000000'
    '0000000000000000000000000000000000000000000000000000000000000000'
    '000000000b331a00000000000000000000000000000000000000000000000000'
    '0000000000000000000000000000000000000000000000000000000000000000'
    '0000000000000000000000000000000000000000000000000000000000000000'
    '0000000000000000000000000000000000000000000000000000000000000000'
    '0000000000000000000000000000000000000000000000000000000000000000'
    '0000000000000000000000000000000000000000000000000000000000000000'
    '0000000000000000000000000000000000000000000000000000000000000000'
    '0000000000000000000000000000000000000000000000000000000000000000'
    '0000000000000000000000000000000000000000000000000000000000000000'
    '0000000000000000000000000000000000000000000000000000000000000000'
    '0000000000000000000000000000000000000000000000000000000000000000'
    '0000000000000000000000000000000000000000000000000000000000000000'
    '0000000000000000000000000000000000000000000000000000000000000000'
    '0000000000000000000000000000000000000000000000000000000000000000'
    '0000000000000000000000000000000000000000000000000000000000000000'
    '0000000000000000000000000000000000000000000000000000000000000000'
    '0000000000000000000000000000000000000000000000000000000000000000'
    '0000000000000000000000000000000000000000000000000000000000000000'
    '0000000000000000000000000000000000000000000000000000000000000000'
    '0000000000000000000000000000000000000000000000000000000000000000'
    '0000000000000000000000000000000000000000000000000000000000000000'
    '0000000000000000000000000000000000000000000000000000000000000000'
    '0000000000000000000000000000000000000000000000000000000000000000'
    '0000000000000000000000000000000000000000000000000000000000000000'
    '0000000000000000000000000000000000000000000000000000000000000000'
    '0000000000000000000000000000000000000000000000000000000000000000'
    '0000000000000000000000000000000000000000000000000000000000000000'
    '0000000000000000000000000000000000000000000000000000000000000000'
    '0000000000000000000000000000000000000000000000000000000000000000'
    '0000000000000000000000000000000000000000000000000000000000000000'
    '0000000000000000000000000000000000000000000000000000000000000000'
    '0000000000000000000000000000000000000000000000000000000000000000'
    '0000000000000000000000000000000000000000000000000000000000000000'
    '0000000000000000000000000000000000000000000000000000000000000000'
    '0000000000000000000000000000000000000000000000000000000000000000'
    '0000000000000000000000000000000000000000000000000000000000000000'
    '0000000000000000000000000000000000000000000000000000000000000000'
    '0000000000000000000000000000000000000000000000000000000000000000'
    '0000000000000000000000000000000000000000000000000000000000000000'
    '0000000000000000000000000000000000000000000000000000000000000000'
    '0000000000000000000000000000000000000000000000000000000000000000'
    '0000000000000000000000000000000000000000000000000000000000000000'
    '0000000000000000000000000000000000000000000000000000000000000000'
    '0000000000000000000000000000000000000000000000000000000000000000'
    '6b65726e656c33322e646c6c0077696e6d6d2e646c6c006d636953656e645374'
    '72696e6741004765744d6f64756c6546696c654e616d65410043726561746546'
    '696c65410047657446696c6553697a6500436c6f736548616e646c65006c7374'
    '72636d706941006364617564696f006d757369635c747261636b002e77617600'
    '6f70656e2022002220747970652077617665617564696f20616c69617320766f'
    '636462676d0073657420766f636462676d2074696d6520666f726d6174206d69'
    '6c6c697365636f6e647300706c617920766f636462676d0073746f7020766f63'
    '6462676d00706175736520766f636462676d00726573756d6520766f63646267'
    '6d00636c6f736520766f636462676d0073746174757320766f636462676d206d'
    '6f646500706c6179696e6700'
)
# VOCD BLOB END


class _PE:
    """Just enough PE for one import lookup and one appended section."""

    def __init__(self, buf):
        self.d = buf
        self.pe = struct.unpack_from('<I', self.d, 0x3C)[0]   # e_lfanew
        self.opt = self.pe + 24
        self.nsec, = struct.unpack_from('<H', self.d, self.pe + 6)
        self.optsz, = struct.unpack_from('<H', self.d, self.pe + 20)
        self.off_entry = self.opt + 16
        self.entry_rva, = struct.unpack_from('<I', self.d, self.off_entry)
        self.base, = struct.unpack_from('<I', self.d, self.opt + 28)
        self.salign, = struct.unpack_from('<I', self.d, self.opt + 32)
        self.falign, = struct.unpack_from('<I', self.d, self.opt + 36)
        self.import_rva, = struct.unpack_from('<I', self.d, self.opt + 104)

        self.sections = []
        pos = self.opt + self.optsz
        for _ in range(self.nsec):
            name, vsize, vaddr, rsize, raddr = struct.unpack_from(
                '<8sIIII', self.d, pos)
            self.sections.append({'name': name.rstrip(b'\0'), 'vsize': vsize,
                                  'vaddr': vaddr, 'rsize': rsize,
                                  'raddr': raddr})
            pos += 40

    def off(self, rva):
        """RVA to file offset.

        The span is max(vsize, rsize), not vsize. Five of the patch sites sit
        past their section's VirtualSize, in the raw padding before the next
        section - the timer stub, three F11 dialog blobs and a string table.
        Windows maps that padding, so the addresses are real, but anything
        that trusts VirtualSize cannot see them.
        """
        for x in self.sections:
            span = max(x['vsize'], x['rsize'])
            if x['vaddr'] <= rva < x['vaddr'] + span:
                return x['raddr'] + (rva - x['vaddr'])
        raise ValueError('rva 0x%08x is outside every section' % rva)

    def cstr(self, rva):
        start = self.off(rva)
        end = self.d.index(b'\0', start)
        return bytes(self.d[start:end]).decode('ascii', 'replace')

    def iat_slot(self, dll, func):
        """VA of the import address table entry for dll!func."""
        desc = self.import_rva
        while True:
            ilt, _t, _f, name, iat = struct.unpack_from('<IIIII', self.d,
                                                        self.off(desc))
            if name == 0:
                break
            if self.cstr(name).lower() == dll:
                thunks, i = ilt or iat, 0
                while True:
                    entry, = struct.unpack_from('<I', self.d,
                                                self.off(thunks) + i * 4)
                    if entry == 0:
                        break
                    # Top bit set means imported by ordinal, so there is no
                    # name to compare. Otherwise the entry points at a hint
                    # word followed by the name.
                    if not entry & 0x80000000 and self.cstr(entry + 2) == func:
                        return self.base + iat + i * 4
                    i += 1
            desc += 20
        raise ValueError('%s does not import %s' % (dll, func))

    def add_section(self, name, payload, chars=0xE0000040):
        """Append a section rather than borrow padding: .text has no room
        left and the zero runs in .data are globals the game writes."""
        def up(value, unit):
            return (value + unit - 1) // unit * unit

        hdr = self.opt + self.optsz + self.nsec * 40
        first_raw = min(x['raddr'] for x in self.sections if x['raddr'])
        if hdr + 40 > first_raw:
            raise ValueError('no room in the header for another section')

        last = max(self.sections, key=lambda x: x['vaddr'])
        vaddr = up(last['vaddr'] + last['vsize'], self.salign)
        raddr = up(len(self.d), self.falign)
        self.d += b'\0' * (raddr - len(self.d))
        rsize = up(len(payload), self.falign)
        self.d += payload + b'\0' * (rsize - len(payload))

        struct.pack_into('<8sIIII', self.d, hdr, name.encode('ascii')[:8],
                         len(payload), vaddr, rsize, raddr)
        struct.pack_into('<I', self.d, hdr + 36, chars)
        struct.pack_into('<H', self.d, self.pe + 6, self.nsec + 1)
        struct.pack_into('<I', self.d, self.opt + 56,
                         up(vaddr + len(payload), self.salign))
        self.nsec += 1
        self.sections.append({'name': name.encode('ascii'),
                              'vsize': len(payload), 'vaddr': vaddr,
                              'rsize': rsize, 'raddr': raddr})
        return vaddr


def apply_cdaudio(buf):
    """Install the file-based CD audio hook. Returns the grown buffer.

    The blob goes in a new .vocd section and the entry point is repointed at
    its init thunk, which chains to whatever it was before - so this runs
    after every other patch."""
    pe = _PE(buf)

    gap = (-len(VOCD_CODE)) % 16
    code_rva = pe.add_section('.vocd', VOCD_CODE + b'\0' * gap + VOCD_DATA)
    hook_va = pe.base + code_rva
    values = {
        'MAGIC_ORIGENTRY': pe.base + pe.entry_rva,
        'MAGIC_IATMCI':    pe.iat_slot('winmm.dll', 'mciSendCommandA'),
        'MAGIC_LOADLIB':   pe.iat_slot('kernel32.dll', 'LoadLibraryA'),
        'MAGIC_GETPROC':   pe.iat_slot('kernel32.dll', 'GetProcAddress'),
        'MAGIC_DATA':      pe.base + code_rva + len(VOCD_CODE) + gap,
    }

    # Substitution is a plain replace over the whole blob, so check each
    # placeholder still occurs exactly as often as it did in the source: an
    # address written earlier could in principle spell a later placeholder.
    code = bytes(VOCD_CODE)
    for name, magic in VOCD_MAGICS.items():
        pattern = struct.pack('<I', magic)
        want = VOCD_CODE.count(pattern)
        if not want:
            raise ValueError('%s is missing from the blob' % name)
        if code.count(pattern) != want:
            raise ValueError('%s: a filled-in address spells a placeholder'
                             % name)
        code = code.replace(pattern, struct.pack('<I', values[name]))
    for magic in VOCD_MAGICS.values():
        if struct.pack('<I', magic) in code:
            raise ValueError('a placeholder was left unfilled')

    start = pe.off(code_rva)
    pe.d[start:start + len(code)] = code
    struct.pack_into('<I', pe.d, pe.off_entry, code_rva + 5)
    _repoint_mci_calls(pe, values['MAGIC_IATMCI'], hook_va)
    return pe.d


# Every call the game makes to mciSendCommandA, all six-byte indirect.
MCI_CALL_SITES = 37


def _repoint_mci_calls(pe, slot_va, hook_va):
    """Point the game's mciSendCommandA calls straight at the hook.

    Owning the import slot is not enough: any DLL that hooks the same import
    by name overwrites it, and the hook drops out of the chain with nothing to
    show for it - the game keeps running, the music stops. Rewritten call
    sites cannot be undone that way, and the hook still forwards through the
    slot, so a wrapper that does own it stays in the chain underneath.

        FF 15 <slot>   call dword [__imp__mciSendCommandA]
        E8 <rel32> 90  call hook ; nop

    Both are six bytes, so nothing moves.
    """
    text = next((s for s in pe.sections if s['name'] == b'.text'), None)
    if text is None:
        raise ValueError('no .text section')
    lo = text['raddr']
    hi = lo + min(text['rsize'], len(pe.d) - lo)

    pattern = b'\xff\x15' + struct.pack('<I', slot_va)
    sites, at = [], pe.d.find(pattern, lo, hi)
    while at != -1:
        sites.append(at)
        at = pe.d.find(pattern, at + 1, hi)

    # Wrong count: not the executable these were measured against, or an
    # earlier patch landed on a call site. Rewriting part of them would leave
    # the game half hooked.
    if len(sites) != MCI_CALL_SITES:
        raise ValueError('expected %d mciSendCommandA call sites, found %d'
                         % (MCI_CALL_SITES, len(sites)))

    for off in sites:
        site_va = pe.base + text['vaddr'] + (off - text['raddr'])
        pe.d[off] = 0xE8
        struct.pack_into('<i', pe.d, off + 1, hook_va - (site_va + 5))
        pe.d[off + 5] = 0x90


def _same_file(a, b):
    """Byte comparison, for deciding whether a file is worth keeping."""
    try:
        with open(a, 'rb') as fa, open(b, 'rb') as fb:
            return fa.read() == fb.read()
    except OSError:
        return False


class PatchFailed(Exception):
    """A patch did not apply. Nothing has been written: the caller holds the
    only copy of the buffer."""

    def __init__(self, key, cause):
        super().__init__('%s: %s' % (BY_KEY[key][0], cause))
        self.key = key


def apply_selected(buf, wanted):
    """Write every wanted patch into buf, in apply_order().

    Returns (buffer, applied keys, [(skipped key, why)]). The buffer is not
    the one passed in once nodisc has appended its section, so use the one
    that comes back.

    Anything that does not apply raises PatchFailed, with the first mismatch
    aborting the lot. dinput is the exception: it is found by signature
    rather than offset, so a miss means this build does not have the call
    site, and the rest still go on.
    """
    applied, skipped = [], []
    shared = [key for key in RDATA_EXEC_KEYS if wanted.get(key)]
    if shared:
        try:
            apply_feature(buf, [RDATA_EXEC])
        except ValueError as exc:
            # Named after the first patch that wanted it, so the message the
            # user gets points at a box they ticked rather than at a site
            # that belongs to no patch.
            raise PatchFailed(shared[0], exc) from exc
    for key in apply_order():
        if not wanted.get(key):
            continue
        sites = BY_KEY[key][2]
        try:
            if sites is not None:
                apply_feature(buf, sites)
            else:
                apply_dinput(buf)
            if key == 'debugbox':        # bytes first, then the template
                buf = apply_extras_template(buf)
            if key == 'nodisc':          # bytes first, then the section
                buf = apply_cdaudio(buf)
        except ValueError as exc:
            if sites is None:            # signature miss, not fatal
                skipped.append((key, exc))
                continue
            raise PatchFailed(key, exc) from exc
        except Exception as exc:         # a bug in here, not a bad file
            raise PatchFailed(key, exc) from exc
        applied.append(key)
    return buf, applied, skipped


def backup_is_original(path):
    """Is this .bak the file the patcher started from?

    Checking the backup rather than the exe is what makes "already patched"
    reliable: the patched file's own size and checksum depend on which boxes
    were ticked, but the backup is always the untouched original."""
    try:
        if os.path.getsize(path) != EXE_SIZE:
            return False
        with open(path, 'rb') as fh:
            return hashlib.md5(fh.read()).hexdigest() == ORIGINAL_MD5
    except OSError:
        return False


def compare_report(size, digest, why, hint, level):
    """What the patcher wants against what it was given.

    Two rows so the difference is visible rather than described. The short
    hash is what goes on screen; the log gets both in full, because that is
    what ends up in a bug report."""
    return {
        'rows': [('SUPPORTED', EXE_SIZE, ORIGINAL_MD5),
                 ('YOURS', size, digest)],
        'why': why,
        'hint': hint,
        'level': level,
        'log': ['supported: %d bytes  %s' % (EXE_SIZE, ORIGINAL_MD5),
                'yours:     %d bytes  %s' % (size, digest),
                why, hint],
    }


def _note(text):
    """One log line, in the log's shape: subject, colon, lower-case detail.

    Every line the window writes reads `subject: what happened`, so a run
    scans as a list of steps rather than a mix of sentences and fragments.
    Messages built for the card above are sentences, so the first letter is
    folded here rather than written twice."""
    return 'patch: ' + (text[:1].lower() + text[1:] if text else text)


NOTHING = 'patch: nothing written, the game is untouched'


class Patcher:
    """All the file handling. Nothing in here touches Tk."""

    def __init__(self):
        self.exe_path = None
        self.compare = None

    def load(self, path):
        """Return (description, accepted). Raises OSError.

        The old path is dropped first. Keeping it meant a failed read left
        can_restore() answering for the file before this one, so Restore
        original stayed lit and rewrote an executable the window was no
        longer showing."""
        self.exe_path = None
        self.compare = None

        # Decided on the name, before the read: a disc image is hundreds of
        # megabytes and hashing one to conclude "that is not an executable"
        # freezes the window for no reason.
        kind = DISC_IMAGES.get(os.path.splitext(path)[1].lower())
        if kind:
            self.compare = {
                'rows': [],
                'why': 'This is a %s, not the game.' % kind,
                'hint': 'Pick v_on.exe here. The disc image goes in Source '
                        'under INSTALL, which installs and rips from it.',
                'level': 'warn',
                'log': ['%s is a %s' % (os.path.basename(path), kind)],
            }
            return 'CANNOT PATCH - that is a %s.' % kind, False

        with open(path, 'rb') as fh:
            data = fh.read()
        self.exe_path = path
        digest = hashlib.md5(data).hexdigest()
        if digest == ORIGINAL_MD5:
            return READY_TAG, True

        known = OTHER_BUILDS.get(digest)
        if known:
            # A real release, just not this one. Amber: nothing is wrong with
            # the file, it is the wrong file.
            what, why, hint, level = ('%s build' % known[1], known[2],
                                      RETAIL_HINT, 'warn')
        elif backup_is_original(path + '.bak'):
            # The size cannot be part of this test: No disc required appends
            # a section, so a patched file is 3 KB larger than the original
            # and looked unrecognisable here.
            what, level = 'already patched', 'warn'
            why = 'The v_on.exe.bak beside it is the unmodified original.'
            hint = 'Press Restore original, or copy the .bak back by hand.'
        else:
            what, level = 'unrecognised file', 'bad'
            why = ('Not a v_on.exe this patcher knows - a bad rip, a repack, '
                   'or a copy already patched or otherwise modified.')
            hint = RETAIL_HINT
        self.compare = compare_report(len(data), digest, why, hint, level)
        return 'CANNOT PATCH - %s.' % what, False

    def apply(self, wanted):
        """Patch a clean original in place.

        Returns (ok, log lines). Nothing is written unless ok is True, so the
        caller must not report success on the strength of having a log.
        """
        log = []
        try:
            with open(self.exe_path, 'rb') as fh:
                buf = bytearray(fh.read())
        except OSError as exc:
            return False, ['Could not read the executable: %s' % exc]

        if wanted.get('credits'):
            ready, why = self._credit_files()
            if ready is None:
                # Dropped rather than fatal: it is the one patch that fixes
                # nothing, so it should never cost somebody the rest.
                wanted = dict(wanted, credits=False)
                log.append(_note('%s - the credit is skipped, version and '
                                 'all, everything else applies' % why))

        try:
            buf, applied, skipped = apply_selected(buf, wanted)
        except PatchFailed as exc:
            return False, [_note(str(exc)), NOTHING]
        except Exception as exc:             # a bug in here, not a bad file
            return False, ['patch: failed - %s' % exc, NOTHING]
        if 'credits' in applied:
            stamp_version(buf)
        for key, why in skipped:
            log.append('patch: skipped %s - %s' % (BY_KEY[key][0], why))

        if not applied:
            if skipped:
                log.append('patch: %s was the only one selected and its '
                           'call site was not found' % BY_KEY[skipped[0][0]][0])
                log.append(NOTHING)
            else:
                log.append('patch: nothing selected, nothing written')
            return False, log

        # The banner's tile indices go in the executable and the tiles go in
        # escrgame.bin. One without the other draws the old artwork through
        # the new table, which is worse than not patching at all - so check
        # the file is there and writable before anything is written.
        writable, why = self._folder_writable()
        if not writable:
            return False, log + [_note(why), NOTHING]

        if 'padxinput' in applied:
            ready, why = self._banner_ready()
            if not ready:
                return False, log + [_note(why), NOTHING]

        if not self._backup(self.exe_path, log):
            return False, log + [NOTHING]
        # Written beside the game and renamed over it, so a full disk or a
        # pulled stick leaves the original where it was rather than half an
        # executable. Same filesystem, so the rename is atomic.
        temp = self.exe_path + '.new'
        try:
            with open(temp, 'wb') as fh:
                fh.write(buf)
            os.replace(temp, self.exe_path)
        except OSError as exc:
            try:
                os.remove(temp)
            except OSError:
                pass
            return False, log + [
                _note(why_unwritable(
                    os.path.dirname(self.exe_path) or '.', exc,
                    os.path.basename(self.exe_path))),
                NOTHING]
        log += ['patch: applied %s' % BY_KEY[key][0] for key in applied]
        log.append('patch: wrote %s' % self.exe_path)
        if 'padxinput' in applied:
            self._retire_ini(log)
            self._write_banner(log)
        if 'credits' in applied:
            self._write_credits(log)
        return True, log

    def can_restore(self):
        return bool(self.exe_path) and os.path.exists(self.exe_path + '.bak')

    def restore(self):
        """Put every file this patcher touched back. Returns log lines.

        One rule for all of them: copy the .bak over the top and leave the
        .bak where it is, so restoring twice does the same thing as
        restoring once. Only v_on.ini keeps what it replaces, as .patched -
        the pad binds are the player's work, where a patched exe and
        generated artwork are not."""
        folder = os.path.dirname(self.exe_path)
        targets = [(self.exe_path, False),
                   (os.path.join(folder, ESCRGAME), False),
                   (os.path.join(folder, SCRSTFCG), False),
                   (os.path.join(folder, SCRSTFMP), False),
                   (os.path.join(folder, 'v_on.ini'), True)]
        log = []
        for path, keep_current in targets:
            bak = path + '.bak'
            name = os.path.basename(path)
            if not os.path.exists(bak):
                continue
            try:
                if (keep_current and os.path.exists(path)
                        and not _same_file(path, bak)):
                    # Only when it differs: restoring twice would otherwise
                    # overwrite the player's binds with the copy of the
                    # original that the first restore just put there.
                    os.replace(path, path + '.patched')
                    log.append('restore: kept the patched %s as %s.patched'
                               % (name, name))
                # Copied beside and renamed over, as apply does, so a
                # failure mid-copy leaves the patched file rather than
                # half of one.
                temp = path + '.new'
                try:
                    shutil.copy(bak, temp)
                    os.replace(temp, path)
                except OSError:
                    try:
                        os.remove(temp)
                    except OSError:
                        pass
                    raise
                log.append('restore: put back %s' % name)
            except OSError as exc:
                log.append('restore: failed on %s - %s' % (name, exc))
        if not log:
            return ['Nothing to restore - no backups found']
        return log

    def _folder_writable(self):
        """Can anything be written beside the game? Returns (ok, why not).

        Checked before the backup rather than discovered halfway through.
        The advice depends on why it failed, so the reason is read off errno
        rather than pasting the OS message into a sentence about permissions
        - "cannot write here (No such file or directory)" helps nobody."""
        return writable(os.path.dirname(self.exe_path) or '.')

    def _banner_ready(self):
        """Can escrgame.bin take the new tiles? Returns (ok, why not).

        An already-modified copy is refused rather than backed up: the backup
        would then hold somebody else's edit, and Restore original would put
        that back instead of the original."""
        path = os.path.join(os.path.dirname(self.exe_path), ESCRGAME)
        if not os.path.exists(path):
            return False, ('%s is missing. XInput gamepad support renames the '
                           'title prompt, which is artwork in that file.'
                           % ESCRGAME)
        try:
            with open(path, 'rb') as fh:
                data = fh.read()
        except OSError as exc:
            return False, 'Could not read %s: %s' % (ESCRGAME, exc)
        if len(data) != ESCRGAME_SIZE:
            return False, ('%s is %d bytes, expected %d.'
                           % (ESCRGAME, len(data), ESCRGAME_SIZE))
        digest = hashlib.md5(data).hexdigest()
        if digest != ESCRGAME_MD5:
            if os.path.exists(path + '.bak'):
                return True, ''          # ours from a previous run
            return False, ('%s has been modified and there is no %s.bak '
                           'beside it. It holds the title screen artwork, so '
                           'reinstall the game to get the original back. '
                           '(MD5 %s, expected %s)'
                           % (ESCRGAME, ESCRGAME, digest, ESCRGAME_MD5))
        return True, ''

    def _credit_files(self):
        """Read both roll files. Returns ([(path, data, stock)], why).

        The list is None when the line cannot go in. Asked before the
        executable is written: the block list goes in the executable and
        the cells and tiles go in these two, and a list naming blocks the
        map has no cells for walks the renderer off the end of it - so if
        they are not both here and known, the whole patch stands down.

        A copy that is already ours, with a stock .bak beside it, is
        accepted with stock=False: that is a re-run, and the line is
        already in it."""
        folder = os.path.dirname(self.exe_path)
        out = []
        for name, size, want in ((SCRSTFCG, SCRSTFCG_SIZE, SCRSTFCG_MD5),
                                 (SCRSTFMP, SCRSTFMP_SIZE, SCRSTFMP_MD5)):
            path = os.path.join(folder, name)
            try:
                with open(path, 'rb') as fh:
                    data = fh.read()
            except OSError as exc:
                return None, 'Could not read %s: %s' % (name, exc)
            if hashlib.md5(data).hexdigest() == want:
                out.append((path, bytearray(data), True))
                continue
            bak = path + '.bak'
            try:
                with open(bak, 'rb') as fh:
                    ours = hashlib.md5(fh.read()).hexdigest() == want
            except OSError:
                ours = False
            if ours:
                out.append((path, bytearray(data), False))
                continue
            if len(data) != size:
                return None, ('%s is %d bytes, expected %d.'
                              % (name, len(data), size))
            return None, ('%s is not the file this patch was built '
                          'against.' % name)
        return out, ''

    def _write_credits(self, log):
        """Append the new tiles and splice the new cells into the map.

        Both files grow. They load to a fixed address with everything before
        them, so the room for that is finite - 580424 bytes are used of the
        594072 before .idata, and this adds 14756."""
        found, why = self._credit_files()
        if found is None:            # checked before apply, so unlikely
            log.append('patch: credit line skipped - %s' % why)
            return False
        if not any(stock for _p, _d, stock in found):
            log.append('patch: credit line already in place')
            return True
        # Per file, not all-or-nothing: a failure between the two renames
        # below leaves one done and one not, and the next run must finish
        # the second rather than see the first and call the job done - or
        # worse, append the tiles a second time.
        (cg_path, cg, cg_stock), (mp_path, mp, mp_stock) = found
        for path, stock in ((cg_path, cg_stock), (mp_path, mp_stock)):
            if stock and not self._backup(path, log):
                log.append('patch: credit line skipped')
                return False
        if cg_stock:
            cg += b''.join(CREDIT_NEW_TILES)
        at = CREDIT_CELLS_AT * 2
        if mp_stock:
            mp[at:at] = b''.join(c.to_bytes(2, 'little') for c in CREDIT_CELLS)
        # Both temps in full before either rename: the block list is already
        # in the executable, and these two have to change together or the
        # renderer walks a map that disagrees with it. A rename can still
        # fail where a write cannot, so the failure message says what to
        # press.
        pending = []
        try:
            for path, data, stock in ((cg_path, cg, cg_stock),
                                      (mp_path, mp, mp_stock)):
                if not stock:
                    continue
                temp = path + '.new'
                with open(temp, 'wb') as fh:
                    fh.write(data)
                pending.append((temp, path))
            for temp, path in pending:
                os.replace(temp, path)
                log.append('patch: wrote %s' % os.path.basename(path))
        except OSError as exc:
            for temp, _path in pending:
                try:
                    os.remove(temp)
                except OSError:
                    pass
            log.append('patch: could not write the credit line - %s. Press '
                       'Restore '
                       'original to put the roll files back.' % exc)
            return False
        return True

    def _write_banner(self, log):
        """The tile indices went into the executable; the tiles themselves
        live in escrgame.bin, so that has to be written too or the prompt
        draws the old artwork through the new table.

        A missing or unexpected escrgame.bin is not fatal - every other patch
        has already been written - but it does have to be said out loud."""
        path = os.path.join(os.path.dirname(self.exe_path), ESCRGAME)
        try:
            with open(path, 'rb') as fh:
                data = bytearray(fh.read())
        except OSError as exc:
            log.append('patch: could not read %s - %s' % (ESCRGAME, exc))
            return
        if len(data) != ESCRGAME_SIZE:
            log.append('patch: %s is %d bytes, expected %d - left alone'
                       % (ESCRGAME, len(data), ESCRGAME_SIZE))
            return
        if not self._backup(path, log):
            log.append('patch: %s left alone' % ESCRGAME)
            return
        for i, raw in enumerate(BANNER_TILES):
            off = (BANNER_TILE_OFF + i * 128 if i < BANNER_TILE_MAX
                   else (BANNER_SPILL + i - BANNER_TILE_MAX) * 128)
            data[off:off + 128] = raw
        for i in range(len(BANNER_TILES), BANNER_TILE_MAX):
            off = BANNER_TILE_OFF + i * 128
            data[off:off + 128] = b'\x00' * 128
        temp = path + '.new'
        try:
            with open(temp, 'wb') as fh:
                fh.write(data)
            os.replace(temp, path)
        except OSError as exc:
            try:
                os.remove(temp)
            except OSError:
                pass
            log.append('patch: could not write %s - %s' % (ESCRGAME, exc))
            return
        log.append('patch: wrote %s' % path)

    def _retire_ini(self, log):
        """Binds written by the unpatched game do not fit the new device
        list, so the file has to go and the game rebuilds it.

        Backed up the same way as everything else, then deleted: "move it
        aside" and "keep a copy" are the same thing here, and treating it as
        one avoids the ini having rules of its own."""
        ini = os.path.join(os.path.dirname(self.exe_path), 'v_on.ini')
        if not os.path.exists(ini):
            return
        if not self._backup(ini, log):
            log.append('patch: v_on.ini left alone - the gamepad profile '
                       'may not '
                       'work until you delete it by hand')
            return
        try:
            os.remove(ini)
            log.append('patch: moved v_on.ini aside, the game will write a '
                       'fresh one')
        except OSError as exc:
            log.append('patch: could not move v_on.ini - %s, delete it by '
                       'hand'
                       % exc)

    @staticmethod
    def _backup(path, log):
        """Copy the original aside. False means it did not happen and nothing
        should be written: a read-only folder or a locked file would otherwise
        leave a patched game with no way back."""
        bak = path + '.bak'
        if os.path.exists(bak):
            return True
        try:
            shutil.copy(path, bak)
        except OSError as exc:
            log.append('patch: backup failed for %s - %s' % (path, exc))
            return False
        log.append('patch: backed up %s' % bak)
        return True


def describe(text):
    """Split a description into prose and any 'key<TAB>meaning' rows.

    A blank line starts a paragraph; the breaks stay in the prose so it
    can go into one wrapped label."""
    paragraphs, para, rows = [], [], []
    for line in text.split('\n'):
        if '\t' in line:
            key, _, meaning = line.partition('\t')
            if not key.strip() and rows:
                # A continuation of the row above. The source wraps long
                # meanings to keep its own lines short; the bubble wraps
                # them again at its own width, so join them back up first.
                rows[-1] = (rows[-1][0], rows[-1][1] + ' ' + meaning.strip())
            else:
                rows.append((key.strip(), meaning.strip()))
        elif line.strip():
            para.append(line.strip())
        elif para:
            paragraphs.append(' '.join(para))
            para = []
    if para:
        paragraphs.append(' '.join(para))
    return '\n\n'.join(paragraphs), rows


# From the game's artwork. The window paints itself with this rather than
# following the desktop theme.
PALETTE = {
    'ink': '#0b1020',       # window
    'card': '#151d33',      # panel
    'head': '#1e2947',      # section header and status bar
    'line': '#2c3960',      # borders
    'text': '#e6ebf7',
    'dim': '#93a0c4',       # hints, disabled, the log
    'cyan': '#3fd8f0',      # headings, ticks, Apply
    'cyan_hi': '#7ce7f7',   # Apply, hovered
    'cyan_lo': '#2ec3db',   # Apply, pressed
    'amber': '#ffa62b',     # the key column of a description
    'ok': '#42e08a',
    'bad': '#ff6b6b',
}

# The version is in the title because it is the only place a Windows user
# who double-clicked the exe can see it, and it is the first thing worth
# knowing about a bug report.
TITLE = '%s %s' % (NAME, VERSION)
# How long after the last resize event the static widgets are redrawn, in
# milliseconds. See App._nudge.
NUDGE_MS = 60
# Column widths in characters of the hint font rather than in pixels, so
# they hold at any display scaling: at 125% or 200% the font grows and the
# columns have to grow with it, or the same paragraph wraps a line deeper
# every step up. Sixty to ninety characters is the readable range for a
# line of prose and these sit inside it.
MIN_CHARS = 68                  # per column; narrower and hints wrap badly
# And the widest, per column. Past this the extra room goes into longer
# lines of hint text, which is harder to read rather than easier - a
# paragraph wants sixty to ninety characters a line and this is already at
# the top of that. Maximising lands here rather than filling a 34-inch
# monitor with one sentence per line.
MAX_CHARS = 88
GUTTER_CHARS = 2                # between the two columns
ALPHABET = 'abcdefghijklmnopqrstuvwxyz'
# One character of the default font on an unscaled display. Every fixed gap
# in this window was chosen against it, so those numbers stay written as the
# pixel counts they were and scaled by how far the real font has moved.
BASE_EM = 6.8


def scaled(value, em):
    """A gap chosen at 100%, in the pixels it should be now.

    Takes a number or a pack/grid pair and gives back the same shape, so a
    call site keeps reading as the spacing it asks for."""
    if isinstance(value, tuple):
        return tuple(scaled(part, em) for part in value)
    return max(1, int(round(value * em / BASE_EM))) if value else value
NO_FILE = 'No file selected'

FILE_HINT = ('Installing above fills this in. Browse for it if the game is '
             'already on your disk.')

INSTALL_HINT = ('Copies the game and its soundtrack off a disc image, onto '
                'your disk.')

INSTALL_TIP = ('Source\tThe .cue sheet beside the .bin files, not the .bin '
               'itself. On Linux a device node such as /dev/sr0 works too, '
               'for the soundtrack only.\n'
               'Install game\tThe game folder, about 95 MB.\n'
               'Rip soundtrack\tmusic\\track02.wav onward, about 320 MB. '
               'Needed unless you keep a disc in the drive.\n'
               'Manual\tWhich readme and help file is copied. Every '
               'pressing carries one v_on.exe and it is English, so there '
               'is no translated game to install.')

INSTALL_PICK = 'Give the .cue sheet, not the .bin.'
INSTALL_NOT_CUE = 'That is a %s. Give the .cue sheet beside it.'
INSTALL_NEEDS_DEST = 'Choose where to install it.'
INSTALL_BUSY = 'Copying\u2026'
INSTALL_CANCELLED = 'Cancelled. The folder holds a part-written copy.'
INSTALL_OK = 'Installed %d files to %s.'
INSTALL_DRIVE_ONLY = ('A drive can only be used for the soundtrack. Give a '
                      '.cue sheet to install.')
INSTALL_NO_PATH = 'There is nothing at that path.'
INSTALL_NO_DRIVE = 'There is no such device on this machine.'
INSTALL_NOT_A_CUE = 'Give the .cue sheet of a disc image.'
INSTALL_NO_AUDIO = ('This image has no audio tracks - the soundtrack is not '
                    'in it. Only the data half was ripped.')
# The game asks for tracks by number, so a different count is a different
# disc. Said rather than refused: it is their disc and their call.
INSTALL_ODD_AUDIO = ('This image has %d audio tracks; Virtual-On has %d. '
                     'Ripping it will not give the right music.')
# What a good disc looks like, in one line: enough to tell a full rip from a
# data-only one before anything is written.
INSTALL_FOUND = 'Retail disc. %d files, %d MB.'
# The copy is the same work whichever build is on the disc, so it runs; only
# the patches are English-retail-only, which the card below spells out.
INSTALL_FOUND_OTHER = '%s build. %d files, %d MB - installs, does not patch.'

ESSENTIAL_HINT = ('Always applied. Each fixes something that is broken on a '
                  'modern system, and none of them has a trade-off.')
EXTRA_HINT = 'Optional. Untick what you do not want.'

ADDONS_HINT = ('Extra files beside the game rather than edits to it. '
               'Apply and Restore leave these alone; install and remove '
               'them here.')

# Not a patch and not bundled: a separate download that does the things a
# byte edit cannot, so it sits under ADD-ONS with the netplay DLL.
DDRAW_LINK = ('Resolution and windowing', 'cnc-ddraw',
              'https://github.com/FunkyFr3sh/cnc-ddraw',
              'Windowed and borderless modes, and 640x480 scaled to your '
              'monitor without stretching. Install downloads it and puts '
              'it beside v_on.exe.')

# Dropping the files in is not enough under Wine, and someone who misses
# this sees no change at all, so it gets the accent colour rather than
# being buried in the sentence above.
DDRAW_WINE = ('Linux: also set ddraw to native in winecfg for this prefix, '
              'or run cnc-ddraw config.exe once. Without that step nothing '
              'changes.')

DDRAW_BUSY = 'Downloading\u2026'
DDRAW_GONE = 'Removed. ddraw.ini was left in place.'

NETPLAY_LABEL = 'Internet play'
NETPLAY_NOTE = ('The stock game finds opponents by broadcasting on the '
                'local network, which no router forwards. This replaces its '
                'DirectPlay layer with plain UDP: the host gets a code, the '
                'other player types it in, and nobody forwards a port. The '
                'original dpctrl.dll is kept as dpctrl.dll.stock.')
NETPLAY_PORT = 'Direct IP is still there for LAN play; the host forwards UDP 47624.'
NETPLAY_NEEDS_EXE = 'Pick v_on.exe first: this replaces a file beside it.'
NETPLAY_IN = 'Installed. Both players need this add-on.'
NETPLAY_UPDATED = 'Updated to this build.'
NETPLAY_OUT = 'Original dpctrl.dll restored.'
NETPLAY_OLD = 'An older netplay DLL is installed. Install to update it.'
# Only reachable for a file this patcher did not write: an older release let
# the two simulation patches be unticked, and this build cannot.
NETPLAY_NOSYNC = ('Installed. Note: this v_on.exe is missing a gameplay patch '
                  'online play needs, so matches will refuse to connect. '
                  'Restore original and apply again with this version.')
DDRAW_NEEDS_EXE = 'Pick v_on.exe first: cnc-ddraw goes in the same folder.'
DDRAW_LOCKED = ('Close the game first: Windows will not let the patcher '
                'replace a DLL that is loaded.')

MUSIC_HINT = ('Rips the soundtrack to music\\ beside the game, where the '
              'No disc required patch reads it. About 320 MB.')

# %d is the number of patches written. The count is the one thing someone
# can check against what they ticked, and it is what a bug report needs.
DONE = 'Done - %d patches written. Restore original puts v_on.exe.bak back.'
FAILED = 'Nothing was written and the game is untouched - see the log below.'
# DONE_NOSYNC is gone: Apply can no longer leave a sync patch out.
READY = 'READY - %d patches selected. Press Apply patches.'
# Under 52 characters: the status bar cuts longer text, and the log below
# names which patch.


def win_dpi():
    """Ask Windows not to scale our window, and report the real DPI.

    A process that has not declared awareness gets its window rendered at 96
    DPI and bitmap-stretched to whatever the display is set to, which softens
    every border and glyph. This has to happen before the first window
    exists. Returns the DPI so Tk can be told, or None off Windows and on
    releases without the call.
    """
    if sys.platform != 'win32':
        return None
    for dll, call, arg in (('shcore', 'SetProcessDpiAwareness', 2),
                           ('user32', 'SetProcessDPIAware', None)):
        try:
            fn = getattr(getattr(ctypes.windll, dll), call)
            fn() if arg is None else fn(arg)
            break
        except (AttributeError, OSError):
            continue
    else:
        return None
    try:
        dc = ctypes.windll.user32.GetDC(0)
        dpi = ctypes.windll.gdi32.GetDeviceCaps(dc, 88)      # LOGPIXELSX
        ctypes.windll.user32.ReleaseDC(0, dc)
        return dpi or None
    except (AttributeError, OSError):
        return None


def run_tk():
    import tkinter as tk
    import tkinter.font as tkfont
    from tkinter import ttk, filedialog

    showing = []

    def close_info(_event=None):
        for bubble in list(showing):
            bubble.hide()

    class Info:
        """Click-to-open description bubble; Tk has no popover."""

        def __init__(self, parent, title, text, app):
            self.app, self.title, self.text, self.win = app, title, text, None
            self.btn = app._static_label(ttk.Label(
                parent, text='\u24d8', style='Card.TLabel',
                foreground=PALETTE['dim'], cursor='question_arrow'))
            self.btn.bind('<Button-1>', self.toggle)
            self.btn.bind('<Enter>', lambda _e: self.btn.config(
                foreground=PALETTE['text']))
            self.btn.bind('<Leave>', lambda _e: self.btn.config(
                foreground=PALETTE['dim']))

        def toggle(self, _event=None):
            was_open = self.win is not None
            close_info()
            if not was_open:
                self.show()
            return 'break'                  # keep close_info from undoing it

        def show(self):
            prose, rows = describe(self.text)
            self.win = win = tk.Toplevel(self.app.root)
            win.wm_overrideredirect(True)
            frame = tk.Frame(win, background=PALETTE['card'], borderwidth=0,
                             highlightbackground=PALETTE['cyan'],
                             highlightthickness=1)
            frame.pack()
            body = tk.Frame(frame, background=PALETTE['card'])
            body.pack(padx=11, pady=10)     # one margin, the same on all sides
            # One text width for the whole bubble, measured in the font
            # rather than fixed in pixels so it holds at any scaling. Two
            # widths wrap the prose and the table differently and let a long
            # meaning run off the screen.
            alphabet = 'abcdefghijklmnopqrstuvwxyz'
            em = self.app.small.measure(alphabet) / 26.0
            gap = 12
            keys = max([self.app.bold.measure(key) for key, _ in rows] or [0])
            widest = max([self.app.small.measure(m) for _, m in rows] or [0])
            # Let the table widen the bubble if it only wants a little more:
            # a fixed split leaves one row wrapping on its own among short
            # ones, which reads worse than a slightly wider box. Meanings
            # that are whole sentences run past the cap and wrap anyway.
            wrap = min(int(em * 62), max(int(em * 58), keys + gap + widest))
            self._line(body, self.title, self.app.bold,
                       colour=PALETTE['cyan']).pack(anchor='w')
            if prose:
                self._line(body, prose, self.app.small, wrap=wrap).pack(
                    anchor='w', pady=(4, 0))
            if rows:
                table = tk.Frame(body, background=PALETTE['card'])
                table.pack(anchor='w', pady=(8, 0))
                for line, (key, meaning) in enumerate(rows):
                    self._line(table, key, self.app.bold,
                               colour=PALETTE['amber']).grid(
                                   row=line, column=0, sticky='nw',
                                   padx=(0, gap), pady=1)
                    self._line(table, meaning, self.app.small,
                               wrap=max(140, wrap - keys - gap)).grid(
                        row=line, column=1, sticky='w', pady=1)
            win.update_idletasks()
            wide, high = win.winfo_reqwidth(), win.winfo_reqheight()
            x = self.btn.winfo_rootx() + self.btn.winfo_width() - wide
            x = max(4, min(x, self.btn.winfo_screenwidth() - wide - 4))
            # Below the button by preference. The tall ones are 350px and
            # more, so from a checkbox low on the screen there is no room
            # below: flip above, and clamp only if neither side fits.
            below = self.btn.winfo_rooty() + self.btn.winfo_height() + 3
            screen = self.btn.winfo_screenheight()
            if below + high > screen - 4:
                above = self.btn.winfo_rooty() - high - 3
                y = above if above >= 4 else max(4, screen - high - 4)
            else:
                y = below
            win.wm_geometry('+%d+%d' % (x, y))
            showing.append(self)

        @staticmethod
        def _line(parent, text, font, wrap=0, colour=None):
            return tk.Label(parent, text=text, background=PALETTE['card'],
                            fg=colour or PALETTE['text'], font=font,
                            justify='left', wraplength=wrap)

        def hide(self):
            if self.win:
                self.win.destroy()
                self.win = None
            if self in showing:
                showing.remove(self)

    def _blend(a, b, t):
        a, b = int(a[1:], 16), int(b[1:], 16)
        return '#%02x%02x%02x' % tuple(
            round(((a >> shift) & 255) * (1 - t) + ((b >> shift) & 255) * t)
            for shift in (16, 8, 0))

    TICK = (((4.0, 8.0), (6.6, 10.8)), ((6.6, 10.8), (11.6, 4.8)))

    def _rounded(width_px, height_px, back, fill, edge, tick=None,
                 radius=4.0, line=1.4, corners='nw ne sw se', grow='',
                 scale=1.0):
        """A rounded rectangle, which is how the checkboxes are drawn. It
        works by coverage rather than by pixels, each point blending by its
        distance to the shape's edge, because Tk has no drawing API past
        put() and its -subsample does not average.

        Only fixed-size widgets get this. A ttk image element with a border
        re-composites its nine-patch on every expose, in software.

        clam has no border radius and its checkbox is a flat square with two
        settable colours, so anything rounded has to be an image. The cards
        used to have rounded corners too, faked with four small images
        placed at each one; they were repositioned on every step of a window
        drag and cost more than the rounding was worth."""
        def cover(distance):
            return min(1.0, max(0.0, 0.5 - distance))

        img = tk.PhotoImage(width=width_px, height=height_px)
        cx, cy = (width_px - 1) / 2.0, (height_px - 1) / 2.0
        hw, hh = width_px / 2.0 - 0.5, height_px / 2.0 - 0.5
        rows = []
        for y in range(height_px):
            row = []
            for x in range(width_px):
                vert = 'n' if y < cy else 's'
                horz = 'w' if x < cx else 'e'
                r = radius if vert + horz in corners else 0.0
                # a side named in grow runs off the image, so no edge is
                # drawn there: it is a seam against another card half, not
                # against the window behind.
                dx = abs(x - cx) - (hw + (2.0 if horz in grow else 0.0) - r)
                dy = abs(y - cy) - (hh + (2.0 if vert in grow else 0.0) - r)
                # distance to the edge: the corner arc where both axes are
                # past it, the nearer side otherwise. Taking only the first
                # term leaves every square corner at zero, which paints that
                # whole quadrant a half blend instead of the fill.
                edge_d = ((max(dx, 0.0) ** 2 + max(dy, 0.0) ** 2) ** 0.5
                          + min(max(dx, dy), 0.0) - r)
                px = _blend(back, edge, cover(edge_d))
                px = _blend(px, fill, cover(edge_d + line))
                for (x0, y0), (x1, y1) in (
                        [[(a * scale, b * scale) for a, b in seg]
                         for seg in TICK] if tick else ()):
                    vx, vy = x1 - x0, y1 - y0
                    along = max(0.0, min(1.0, ((x - x0) * vx + (y - y0) * vy)
                                         / (vx * vx + vy * vy)))
                    ex, ey = x - x0 - vx * along, y - y0 - vy * along
                    px = _blend(px, tick,
                                cover((ex * ex + ey * ey) ** 0.5
                                      - 1.1 * scale))
                row.append(px)
            rows.append('{%s}' % ' '.join(row))
        img.put(' '.join(rows))
        return img

    def _em(font):
        """The width of one character of a font, which everything laid out
        in pixels is measured against."""
        return max(1.0, font.measure(ALPHABET) / len(ALPHABET))

    def _hint(parent, text, colour, font, pady=0, gutter=0):
        """The quiet explanatory line under a section heading; most of the
        cards have one and they only differ in their text.

        The width is taken from a holder frame rather than the card body.
        A ttk frame's winfo_width() counts its own padding, so wrapping to
        that made every hint 26px wider than the space it had and clipped
        the last word against the card edge. An empty frame filled to the
        content area measures it exactly.

        gutter keeps the text clear of anything floated to the right of
        it - on the add-on rows, a button. Pass a callable to have it
        measured when the line is laid out rather than guessed at now.

        Packs itself, because the holder is nobody else's business."""
        # A floor in characters rather than pixels, for the same reason the
        # columns are: at 200% scaling 140px is ten characters.
        em = _em(font)
        holder = ttk.Frame(parent, style='Card.TFrame')
        holder.pack(fill='x', pady=scaled(pady, em))
        label = ttk.Label(holder, text=text, style='Card.TLabel',
                          foreground=colour, font=font, justify='left')

        def fit(_event=None):
            # Unconditional, every event, as it has always been. Skipping
            # the write when the wrap would not move is an obvious saving
            # and it is not one: setting wraplength is also what marks the
            # label for redraw, and without that a label Tk has not
            # repainted stays blank until something else touches it.
            width = holder.winfo_width()
            if width > 1:
                edge = gutter() if callable(gutter) else gutter
                # Capped as well as floored. A card that spans the whole
                # window is wider than a line of prose should ever be -
                # around 140 characters against the 88 a column is cut to -
                # so the text stops where it would stop in a column and
                # leaves the rest of the card empty.
                label.configure(wraplength=min(
                    int(MAX_CHARS * em),
                    max(int(20 * em), width - 2 - edge)))
        holder.bind('<Configure>', fit, add='+')
        label.bind('<Map>', fit, add='+')       # a collapsed card gets no
        #                                         Configure until it reopens
        label.pack(anchor='w')
        return label

    def _gap(image, extra, colour):
        """Widen an image with blank space on its right. A layout cannot
        carry padding on an element, so the gap between a checkbox and its
        label has to be part of the picture."""
        wide = tk.PhotoImage(width=image.width() + extra, height=image.height())
        wide.put(colour, to=(0, 0, wide.width(), wide.height()))
        wide.tk.call(wide, 'copy', image, '-to', 0, 0)
        return wide

    class Compare:
        """The "what is wanted / what you gave" panel.

        Two cards use it - the game file and the disc image - and both want
        the same thing: the numbers side by side rather than a sentence
        about them, then why it matters and what to do."""

        def __init__(self, app, parent, before=None):
            # pack() appends, so a panel that appears later would land at
            # the bottom of the card rather than under the line it explains.
            # before names the widget it has to stay above.
            self.before = before
            self.frame = ttk.Frame(parent, style='Card.TFrame')
            self.wrap = tk.Frame(self.frame, background=PALETTE['line'])
            self.wrap.pack(fill='x', pady=(8, 0))
            inner = tk.Frame(self.wrap, background=PALETTE['ink'])
            inner.pack(fill='x', padx=1, pady=1)
            self.rows = []
            for colour in (app.dim, PALETTE['bad']):
                row = tk.Label(inner, text='', font=app.mono, anchor='w',
                               justify='left', padx=8, pady=2,
                               background=PALETTE['ink'], foreground=colour)
                row.pack(fill='x')
                self.rows.append(row)
            self.why = _hint(self.frame, '', app.dim, app.small, pady=(6, 0))
            self.advice = _hint(self.frame, '', app.dim, app.small,
                                pady=(4, 0))

        def show(self, report):
            """Amber for a file that is simply the wrong one, red for one
            that looks damaged - the advice differs, so the colour does."""
            if not report:
                self.frame.pack_forget()
                return
            colour = PALETTE['amber' if report['level'] == 'warn' else 'bad']
            # Some refusals have nothing to compare - a cue sheet has no
            # business being weighed against the executable's checksum.
            # Hide the table rather than leave the last file's numbers
            # under a message about this one.
            if report['rows']:
                for row, (name, size, digest) in zip(self.rows,
                                                     report['rows']):
                    row.config(text='%-10s%11s B  %s'
                                    % (name, '{:,}'.format(size),
                                       digest[:12]))
                self.rows[1].config(foreground=colour)
                self.wrap.pack(fill='x', pady=(8, 0))
            else:
                self.wrap.pack_forget()
            self.why.config(text=report['why'], foreground=colour)
            self.advice.config(text=report['hint'])
            if self.before is not None:
                self.frame.pack(fill='x', before=self.before)
            else:
                self.frame.pack(fill='x')

    class App:

        def __init__(self, root):
            self.root = root
            self.core = Patcher()
            self.vars, self.checks = {}, {}
            self._bodies = []
            self._openers = {}
            self._rip_thread, self._rip_dir = None, None
            self._install_thread = None
            self._cancel_rip = self._cancel_install = False
            # Widgets whose text is written once and never touched again.
            # Those are the ones left blank after a resize; see _nudge.
            # Set while a copy or a rip is running. Both fields stay live
            # while one is going, and editing either used to run
            # _sync_buttons and light the buttons back up - a second Install
            # would then be writing the same files as the first.
            self._busy = None
            # Whether this file was ever accepted, so a selection made
            # against it can be told from a list nobody was able to touch.
            self._chose = False
            self._disc_after = None
            self._status_text, self._status_font = NO_FILE, None
            self._static, self._nudge_after = [], None
            self._nudge_at = 0.0
            root.title(TITLE)
            root.minsize(430, 0)
            # Set again at the end of __init__, once the content has been
            # measured. This is only a floor to build against.
            root.maxsize(root.winfo_screenwidth(),
                         root.winfo_screenheight())

            root.bind_all('<Button-1>', close_info, add='+')
            root.bind_all('<Escape>', close_info, add='+')
            root.protocol('WM_DELETE_WINDOW', self._close)

            self._styles()

            outer = ttk.Frame(root, style='Ink.TFrame')
            outer.pack(fill='both', expand=True)
            self._statusbar(outer)                  # pinned before the body
            body = self._body(outer)

            # Top to bottom in the order the work is done. Installing comes
            # first because someone who has only a disc image cannot do
            # anything else until it is unpacked, and it is the step that
            # used to happen outside this window entirely.
            # Two columns where there is room: getting the game in place is
            # one job and patching it is another, and side by side neither
            # has to be scrolled past to reach the other. On a narrow screen
            # _body gives back the same frame twice and it stacks instead.
            left, right, band, foot_left, foot_right = body
            self._section(left, '1  INSTALL', self._install_body)
            self._section(left, '2  GAME FILE', self._file_body)
            self._section(right, '3  ESSENTIAL PATCHES',
                          lambda p: self._patch_body(p, ESSENTIAL,
                                                     ESSENTIAL_HINT))
            self._section(right, '4  EXTRA PATCHES',
                          lambda p: self._patch_body(p, EXTRA, EXTRA_HINT))
            # Separate, because these are not patches: Apply never touches
            # them and they write files rather than bytes. Collapsed,
            # because open they push Apply below the fold.
            # Full width as well. Its rows are a title, a paragraph and a
            # button, which read better across the window than down half of
            # it, and opening the tallest section in the window inside one
            # column left the other half empty.
            self._section(band, '5  ADD-ONS', self._addons_body,
                          expanded=False)
            # Full width, under both columns. It is about the whole window
            # rather than the patching half, it reads better with the long
            # paths it prints on one line, and opening it no longer makes
            # one column half as tall again as the other.
            # Side by side at the foot, on the same split as the columns
            # above, so the two headings line up whatever is open. About
            # was pinned to the bottom of the left column before, which
            # only lined up when that column happened to be the shorter
            # one, and left a gap over it that changed as it opened.
            self._section(foot_left, 'LOG', self._log_body, expanded=False)
            self._section(foot_right, 'ABOUT', self._about_body,
                          expanded=False)

            # The last card in each column stretches to the bottom of it.
            # The columns are as tall as the taller one, so without this
            # the shorter column stops early and its last card's lower edge
            # sits opposite nothing.
            for column in (left, right, foot_left, foot_right):
                cards = column.winfo_children()
                if cards and column is not self.inner:
                    last = cards[-1]
                    last.fills = True
                    if last.winfo_children()[-1].winfo_manager():
                        last.pack_configure(fill='both', expand=True)

            # Width is settled here rather than on the canvas's first
            # <Configure>, which arrives while the sections are still being
            # built and measures whatever exists at that point.
            # Height allows for every section open, capped, so expanding one
            # scrolls instead of moving the window. Measuring the collapsed
            # content gives a window barely taller than the headers.
            for body, shown in self._bodies:
                if not shown:
                    body.pack(fill='x')

            # Hold the content to the width it is meant to have before
            # measuring anything. Left to itself a paragraph asks for the
            # width of its longest line unwrapped, so the window came out as
            # wide as the longest sentence in it and could not be dragged
            # any narrower - the minimum below is taken from this. The
            # hints wrap to whatever the content is given, so give it the
            # answer first and let them settle against it.
            wide = self.min_content * self.columns \
                + (self.gutter if self.columns > 1 else 0)
            self.canvas.itemconfigure(self.window, width=wide)
            root.update_idletasks()

            # With two columns the window has to fit the taller of them, not
            # the sum: the grid puts them side by side and reqheight already
            # reports the taller, but only once both have been laid out.
            full = self.inner.winfo_reqheight()
            # Anything still asking for more than the target cannot be
            # wrapped - a row of fixed-width boxes, say - so the window
            # grows to it rather than cutting it off.
            wide = max(wide, self.inner.winfo_reqwidth())
            for body, shown in self._bodies:
                if not shown:
                    body.pack_forget()
            root.update_idletasks()
            self.canvas.configure(width=wide, height=min(full, self.cap))
            # the minimum has to leave room for the scrollbar as well
            bar = self.vbar.winfo_reqwidth()
            root.minsize(wide + bar, self.px(320))
            # And a maximum, so that maximising lands on the largest size
            # that is any use rather than on the size of the screen. Wider
            # only stretches the hints; taller only adds empty space under
            # the last card, since the content is as tall as it gets with
            # every section open. Both are clamped to the screen, and a
            # window manager that ignores size hints - a tiling one, say -
            # is no worse off than before.
            root.update_idletasks()
            # Everything the window holds that is not the scrolling area:
            # the status bar. The tallest it is ever worth being is all of
            # the content plus that.
            chrome = root.winfo_reqheight() - min(full, self.cap)
            root.maxsize(
                min(root.winfo_screenwidth() - self.px(40),
                    max(wide + int(8 * self.em),
                        self.max_content * self.columns
                        + (self.gutter if self.columns > 1 else 0)) + bar),
                min(root.winfo_screenheight() - self.px(60),
                    full + chrome))
            self._fit()

        def _body(self, parent):
            """Size to the content, scrolling only if it outgrows the
            screen.

            Returns the two columns, the full-width band under them, and
            the two half-width feet under that. With one column they are
            all the same frame and the sections simply stack."""
            holder = ttk.Frame(parent, style='Ink.TFrame')
            holder.pack(fill='both', expand=True)
            self.canvas = tk.Canvas(holder, highlightthickness=0,
                                    borderwidth=0,
                                    background=PALETTE['ink'])
            self.vbar = ttk.Scrollbar(holder, orient='vertical',
                                      style='Vo.Vertical.TScrollbar',
                                      command=self.canvas.yview)
            # The bar is packed first so pack reserves its width. The other
            # way round the canvas expands into the whole row and the bar is
            # squeezed off the edge at the minimum window width.
            self.vbar.pack(side='right', fill='y')
            self.canvas.pack(side='left', fill='both', expand=True)
            self.canvas.configure(yscrollcommand=self.vbar.set)

            self.inner = ttk.Frame(self.canvas, padding=self.px(12),
                                   style='Ink.TFrame')
            self.window = self.canvas.create_window((0, 0), window=self.inner,
                                                    anchor='nw')
            # How tall the window may grow before the content scrolls
            # instead. The screen is the real limit; the line count is only
            # there to stop a very tall display giving a window nobody wants
            # to drag. Sized so that a 1600x900 screen at 100% shows every
            # section with the log open, which is the state people spend the
            # most time looking at.
            row = self.small.metrics('linespace')
            self.cap = min(max(self.px(360),
                               parent.winfo_screenheight() - self.px(150)),
                           row * 56)
            self.inner.bind('<Configure>', self._fit)
            self.canvas.bind('<Configure>', self._fit)
            self.canvas.bind('<Configure>', self._nudge, add='+')
            for seq in ('<MouseWheel>', '<Button-4>', '<Button-5>'):
                self.canvas.bind_all(seq, self._wheel)

            # Two columns need both of them at the readable width plus the
            # padding, the gutter and the scrollbar. Below that, one column
            # and the sections stack as before - a squeezed pair wraps every
            # hint to three lines and reads worse than scrolling.
            self.columns = 2 if (parent.winfo_screenwidth() - 80
                                 >= 2 * self.min_content + self.gutter
                                 + int(6 * self.em)) else 1
            if self.columns == 1:
                self.left = self.right = self.band = self.inner
                self.foot_left = self.foot_right = self.inner
                return (self.inner,) * 5
            self.inner.columnconfigure(0, weight=1, uniform='col')
            self.inner.columnconfigure(1, weight=1, uniform='col')
            self.left = ttk.Frame(self.inner, style='Ink.TFrame')
            self.left.grid(row=0, column=0, sticky='nsew',
                           padx=(0, self.gutter // 2))
            self.right = ttk.Frame(self.inner, style='Ink.TFrame')
            self.right.grid(row=0, column=1, sticky='nsew',
                            padx=(self.gutter // 2, 0))
            self.band = ttk.Frame(self.inner, style='Ink.TFrame')
            self.band.grid(row=1, column=0, columnspan=2, sticky='ew')
            # The two short reference cards sit beside each other under it,
            # on the same split as the columns, so their headings line up.
            # Their cards stretch to the taller of the two, as the columns
            # above do, so the pair reads as one row rather than as two
            # boxes that happen to start together.
            self.foot_left = ttk.Frame(self.inner, style='Ink.TFrame')
            self.foot_left.grid(row=2, column=0, sticky='nsew',
                                padx=(0, self.gutter // 2))
            self.foot_right = ttk.Frame(self.inner, style='Ink.TFrame')
            self.foot_right.grid(row=2, column=1, sticky='nsew',
                                 padx=(self.gutter // 2, 0))
            return (self.left, self.right, self.band,
                    self.foot_left, self.foot_right)

        def _fit(self, _event=None):
            """Answer a resize, in the event that caused it, doing the same
            work every time.

            Two attempts at making this cheaper both broke the drawing on a
            real window manager - deferring it onto a timer, and skipping
            the writes when they would not change anything. Tk repaints as
            part of handling the event, and these calls are what mark the
            canvas and its window item as needing it. Anything that runs
            later, or does not run at all, leaves the window changed with
            nothing left to redraw it."""
            need = self.inner.winfo_reqheight()
            wide = self.canvas.winfo_width()
            if wide > 1:
                self.canvas.itemconfigure(self.window, width=wide)
            # Only the scroll extent. Height is settled at startup and left
            # alone: driving it from here resizes the window on every expand
            # and collapse.
            self.canvas.configure(
                scrollregion=(0, 0, self.inner.winfo_reqwidth(), need))
            # The bar stays packed whether it is needed or not. Showing and
            # hiding it moves the window by its own width the moment the
            # content outgrows the cap. Tk fills the trough when there is
            # nothing to scroll.
            if need <= max(self.canvas.winfo_height(), 1) + 1:
                self.canvas.yview_moveto(0)

        def _wheel(self, event):
            # bind_all reaches every toplevel, so an open description bubble
            # would otherwise scroll the window behind it.
            if event.widget.winfo_toplevel() is not self.root:
                return
            # The log scrolls itself, and so does its scrollbar. Tk widget
            # names are paths, so one prefix covers the pair.
            log = getattr(self, 'log_wrap', None)
            if log is not None and str(event.widget).startswith(str(log)):
                return
            if self.inner.winfo_reqheight() <= self.canvas.winfo_height():
                return
            step = -1 if getattr(event, 'num', 0) == 4 or \
                getattr(event, 'delta', 0) > 0 else 1
            self.canvas.yview_scroll(step, 'units')

        # -- look

        def _styles(self):
            """Theme the widgets. clam is the only stock theme where every
            colour can be set.

            These must all stay *named* styles. Setting the root '.' style
            also repaints Tk's file dialog, whose file list is a canvas
            iconlist.tcl hardcodes to white."""
            p = PALETTE
            style = ttk.Style()
            if 'clam' in style.theme_names():
                style.theme_use('clam')
            self.root.configure(background=p['ink'])
            edges = dict(bordercolor=p['line'], darkcolor=p['line'],
                         lightcolor=p['line'], troughcolor=p['card'])
            style.configure('Ink.TFrame', background=p['ink'])
            style.configure('Card.TFrame', background=p['card'])
            style.configure('Body.TFrame', background=p['card'])
            style.configure('Card.TLabel', background=p['card'],
                            foreground=p['text'])
            style.configure('Dim.TLabel', background=p['card'],
                            foreground=p['dim'])
            style.configure('Link.TLabel', background=p['card'],
                            foreground=p['cyan'])
            style.configure('Head.TFrame', background=p['head'])
            style.configure('Head.TLabel', background=p['head'],
                            foreground=p['cyan'])
            style.configure('Bar.TFrame', background=p['head'])
            style.configure('Bar.TLabel', background=p['head'],
                            foreground=p['dim'])

            style.configure('Card.TCheckbutton', background=p['card'],
                            foreground=p['text'], focuscolor=p['card'],
                            indicatorbackground=p['ink'],
                            indicatorforeground=p['ink'], **edges)
            style.map(
                'Card.TCheckbutton',
                background=[('active', p['card'])],
                foreground=[('disabled', p['dim'])],
                # ttk takes the first spec that matches, so the disabled
                # pairs go first or a disabled tick paints itself bright.
                indicatorbackground=[('disabled', 'selected', p['line']),
                                     ('disabled', p['ink']),
                                     ('selected', p['cyan']),
                                     ('!selected', p['ink'])],
                indicatorforeground=[('disabled', 'selected', p['dim']),
                                     ('selected', p['ink'])])

            style.configure('Vo.TButton', background=p['head'],
                            foreground=p['text'], focuscolor=p['head'],
                            **edges)
            style.map('Vo.TButton',
                      background=[('pressed', p['line']),
                                  ('active', p['line']),
                                  ('disabled', p['card'])],
                      foreground=[('disabled', p['dim'])])
            style.configure('Go.TButton', background=p['cyan'],
                            foreground=p['ink'], focuscolor=p['cyan'],
                            **edges)
            style.map('Go.TButton',
                      background=[('pressed', p['cyan_lo']),
                                  ('active', p['cyan_hi']),
                                  ('disabled', p['card'])],
                      foreground=[('pressed', p['ink']),
                                  ('active', p['ink']),
                                  ('disabled', p['dim'])])

            style.configure('Vo.TEntry', fieldbackground=p['ink'],
                            foreground=p['text'], insertcolor=p['cyan'],
                            **edges)
            style.map('Vo.TEntry',
                      fieldbackground=[('readonly', p['ink'])],
                      foreground=[('readonly', p['dim'])])
            style.configure('Vo.TCombobox', fieldbackground=p['ink'],
                            background=p['head'], foreground=p['text'],
                            arrowcolor=p['dim'], **edges)
            style.map('Vo.TCombobox',
                      fieldbackground=[('readonly', p['ink'])],
                      foreground=[('readonly', p['text'])],
                      selectbackground=[('readonly', p['ink'])],
                      selectforeground=[('readonly', p['text'])])
            # The dropdown is a plain Tk listbox that ttk does not theme, so
            # its colours have to be set through the option database.
            for option, colour in (('background', p['ink']),
                                   ('foreground', p['text']),
                                   ('selectBackground', p['cyan']),
                                   ('selectForeground', p['ink'])):
                self.root.option_add('*TCombobox*Listbox.%s' % option, colour)
            style.configure('Vo.Vertical.TScrollbar', background=p['card'],
                            arrowcolor=p['dim'], **edges)
            style.map('Vo.Vertical.TScrollbar',
                      background=[('active', p['line'])])

            default = tkfont.nametofont('TkDefaultFont')
            small = max(7, abs(default.cget('size')) - 1)
            self.head_font = default.copy()
            self.head_font.configure(size=small, weight='bold')
            self.small = default.copy()
            self.small.configure(size=small)
            self.bold = default.copy()
            self.bold.configure(weight='bold')
            # the comparison rows only line up in a fixed pitch
            self.mono = tkfont.nametofont('TkFixedFont').copy()
            self.mono.configure(size=small)
            self.dim = p['dim']

            # One character of the hint font, which everything laid out in
            # pixels is measured against.
            self.em = _em(self.small)
            self.min_content = int(MIN_CHARS * self.em)
            self.max_content = int(MAX_CHARS * self.em)
            self.gutter = max(2, int(GUTTER_CHARS * self.em))

            # Drawn last, because the tick box is sized against the text it
            # sits beside: a fixed 16px box next to 27px letters at 200%
            # looked like a mistake.
            self._draw_indicator(style, p)

        def px(self, value):
            """A gap written as pixels at 100%, in this display's pixels."""
            return scaled(value, self.em)

        def _draw_indicator(self, style, p):
            """Swap clam's indicator for drawn images. Keep the references:
            Tk does not own them, and a collected image leaves a blank box."""
            side = max(16, int(round(self.em * 2.4)))
            scale = side / 16.0
            gap = max(6, int(round(self.em)))
            try:
                self._boxes = tuple(
                    _gap(box, gap, p['card']) for box in (
                        _rounded(side, side, p['card'], p['ink'], p['line'],
                                 radius=4.0 * scale, line=1.4 * scale),
                        _rounded(side, side, p['card'], p['cyan'], p['cyan'],
                                 tick=p['ink'], radius=4.0 * scale,
                                 line=1.4 * scale, scale=scale),
                        _rounded(side, side, p['card'], p['ink'], p['card'],
                                 radius=4.0 * scale, line=1.4 * scale),
                        _rounded(side, side, p['card'], p['line'], p['line'],
                                 tick=p['dim'], radius=4.0 * scale,
                                 line=1.4 * scale, scale=scale)))
                off, on, off_off, on_off = self._boxes
                style.element_create(
                    'Vo.indicator', 'image', off,
                    ('disabled', 'selected', on_off),
                    ('disabled', off_off),
                    ('selected', on), sticky='')
                style.layout('Card.TCheckbutton', [
                    ('Checkbutton.padding', {'sticky': 'nswe', 'children': [
                        ('Vo.indicator', {'side': 'left', 'sticky': ''}),
                        ('Checkbutton.focus', {
                            'side': 'left', 'sticky': 'w', 'children': [
                                ('Checkbutton.label', {'sticky': 'nswe'})]})]})])
                style.configure('Card.TCheckbutton',
                            padding=self.px((0, 3, 0, 3)))
            except tk.TclError:
                pass            # keep clam's square rather than no box at all

        def _section(self, parent, title, build, expanded=True):
            card = ttk.Frame(parent, style='Card.TFrame')
            card.pack(fill='x', pady=self.px((0, 10)))

            head = ttk.Frame(card, style='Head.TFrame',
                             padding=self.px((10, 7)))
            head.pack(fill='x')
            arrow = self._static_label(ttk.Label(
                head, style='Head.TLabel',
                text='\u25be' if expanded else '\u25b8'))
            arrow.pack(side='left', padx=self.px((0, 8)))
            name = arrow                # rebound below; keeps the bind list
            # The step number is set apart from the name, in the amber the
            # description tables already use for a key. It is the one thing
            # in the heading that is a sequence rather than a label.
            number, _, rest = title.partition('  ')
            labels = [name]
            if rest:
                step = self._static_label(ttk.Label(
                    head, text=number, style='Head.TLabel',
                    foreground=PALETTE['amber'], font=self.head_font))
                step.pack(side='left', padx=self.px((0, 8)))
                labels.append(step)
            else:
                rest = number
            name = self._static_label(ttk.Label(
                head, text=rest, style='Head.TLabel', font=self.head_font))
            name.pack(side='left')
            labels.append(name)

            inner = ttk.Frame(card, style='Body.TFrame',
                              padding=self.px((14, 10, 12, 12)))
            self._bodies.append((inner, expanded))
            if expanded:
                inner.pack(fill='x')
            build(inner)

            state = {'open': expanded}

            def set_open(flag):
                # Drives the widget rather than trusting the flag: the sizing
                # pass hides bodies by their starting state, so anything that
                # opened one before that ran would be left marked open and
                # packed away.
                state['open'] = flag
                arrow.config(text='\u25be' if flag else '\u25b8')
                if flag:
                    inner.pack(fill='x')
                else:
                    inner.pack_forget()
                # The last card in a column or a foot takes up the slack, so
                # that the two sides end level - but only while it has
                # something in it. Stretching a closed one turns a heading
                # into a tall empty box that reads as an open section with
                # nothing in it, which is what opening About did to the log.
                if getattr(card, 'fills', False):
                    card.pack_configure(fill='both' if flag else 'x',
                                        expand=flag)

            def toggle(_event=None):
                set_open(not inner.winfo_manager())

            for widget in [head] + labels:
                widget.bind('<Button-1>', toggle)

            self._openers[title] = lambda: set_open(True)
            return inner

        def _file_body(self, parent):
            _hint(parent, FILE_HINT, self.dim, self.small, pady=(0, 8))
            grid = ttk.Frame(parent, style='Card.TFrame')
            grid.pack(fill='x')
            grid.columnconfigure(1, weight=1)
            self.path_var = tk.StringVar()
            # Same grid and the same label width as the install card above,
            # so the two sets of boxes line up down the window.
            self._field(grid, 0, 'v_on.exe', self.path_var,
                        self._pick).state(['readonly'])
            self.file_note = _hint(parent, NO_FILE, self.dim, self.small,
                                   pady=(8, 0))
            # Only packed when a file was rejected. "Not the original" on its
            # own leaves nothing to act on.
            self.file_compare = Compare(self, parent)

        def _compare(self, report):
            self.file_compare.show(report)

        def _install_body(self, parent):
            """Disc image in, game folder out.

            One source field drives both jobs: the same cue sheet holds the
            game and the soundtrack, and asking for it twice is how the old
            layout lost people."""
            _hint(parent, INSTALL_HINT, self.dim, self.small, pady=(0, 8))

            grid = ttk.Frame(parent, style='Card.TFrame')
            grid.pack(fill='x')
            grid.columnconfigure(1, weight=1)

            self.disc_var = tk.StringVar()
            self._field(grid, 0, 'Source', self.disc_var, self._pick_disc)
            Info(grid, 'INSTALL', INSTALL_TIP, self).btn.grid(
                row=0, column=3, sticky='e', padx=(6, 2))

            self.dest_var = tk.StringVar()
            self._field(grid, 1, 'Install to', self.dest_var, self._pick_dest)

            # Only packed once a disc has been read and offers a choice: the
            # OEM pressing has no language directories at all.
            self.lang_row = ttk.Frame(grid, style='Card.TFrame')
            # The note absorbs the slack, not the box: with the weight on
            # column 1 the row overflowed and the combobox was squeezed
            # until GERMAN read as GERI.
            self.lang_row.columnconfigure(2, weight=1)
            # Named "Manual", not "Language": the disc has one v_on.exe and
            # it is English, so a language label promises a translated game
            # that no pressing carries. This is the language of the papers.
            self._static_label(ttk.Label(
                self.lang_row, text='Manual', style='Card.TLabel',
                font=self.small, width=10, anchor='w')).grid(
                    row=0, column=0, sticky='w', padx=(0, 8))
            self.lang_var = tk.StringVar()
            self.lang_box = ttk.Combobox(self.lang_row, state='readonly',
                                         style='Vo.TCombobox', width=14,
                                         textvariable=self.lang_var)
            self.lang_box.grid(row=0, column=1, sticky='w')

            self.disc_note = _hint(parent, INSTALL_PICK, PALETTE['cyan'],
                                   self.small, pady=(8, 0))
            self.dest_note = _hint(parent, '', self.dim, self.small,
                                   pady=(4, 0))

            buttons = ttk.Frame(parent, style='Card.TFrame')
            buttons.pack(fill='x', pady=(10, 0))
            self.disc_compare = Compare(self, parent, before=buttons)
            self.install_btn = ttk.Button(buttons, text='Install game',
                                          style='Vo.TButton', state='disabled',
                                          command=self._install)
            self.install_btn.pack(side='left')
            self.rip_btn = ttk.Button(buttons, text='Rip soundtrack',
                                      style='Vo.TButton', state='disabled',
                                      command=self._rip)
            self.rip_btn.pack(side='left', padx=(8, 0))

            _hint(parent, MUSIC_HINT, self.dim, self.small, pady=(8, 0))
            self.music_note = _hint(parent, '', self.dim, self.small,
                                    pady=(4, 0))
            self.disc_ok = False
            self.rip_ok = False
            self._audio_warning = ''
            self._disc_bytes = 0
            self._rip_bytes = 0
            self._music('')
            # Typing a path counts as picking one. The disc read opens files,
            # so it waits for a pause rather than running on every keystroke.
            self.disc_var.trace_add('write', self._disc_typed)
            self.dest_var.trace_add('write',
                                    lambda *_a: self._sync_buttons())

        def _disc_typed(self, *_args):
            if self._disc_after:
                self.root.after_cancel(self._disc_after)
            self._disc_after = self.root.after(
                400, lambda: self._check_disc(self.disc_var.get()))

        def _field(self, grid, line, label, var, browse):
            """One labelled path row. The three of them share a grid so the
            entries line up rather than each starting after its own word."""
            self._static_label(ttk.Label(
                grid, text=label, style='Card.TLabel', font=self.small,
                width=10, anchor='w')).grid(row=line, column=0, sticky='w',
                                            padx=(0, 8), pady=(0, 6))
            # width=12 on purpose: it expands into whatever the row has
            # spare, and a larger request only widens the window.
            entry = ttk.Entry(grid, textvariable=var, style='Vo.TEntry',
                              width=12)
            entry.grid(row=line, column=1, sticky='ew', pady=(0, 6))
            ttk.Button(grid, text='Browse\u2026', style='Vo.TButton',
                       command=browse).grid(row=line, column=2, sticky='w',
                                            padx=(8, 0), pady=(0, 6))
            return entry

        # -- source

        def _pick_disc(self):
            path = filedialog.askopenfilename(
                title='Select the disc image cue sheet',
                filetypes=[('Cue sheets', '*.cue *.CUE'), ('All files', '*')])
            if path:
                self.disc_var.set(path)       # the trace runs the check

        def _check_disc(self, source):
            """Read the disc and say what is in it, or what is wrong.

            Everything here is cheap - a few kilobytes of directory and one
            hash of v_on.exe - so it runs on the UI thread the moment a
            source is picked, and the buttons below light up or do not."""
            self.disc_ok = False        # a Virtual-On disc: install and rip
            self.rip_ok = False         # anything the ripper can read at all
            self._audio_warning = ''
            self._disc_bytes = self._rip_bytes = 0
            self.disc_compare.show(None)
            self.lang_row.grid_forget()
            # Cleared with the row: a name left over from the last disc is
            # one this one may not carry, and the copy would refuse it.
            self.lang_var.set('')
            source = (source or '').strip()
            extension = os.path.splitext(source)[1].lower()

            if not source:
                self._disc_note(INSTALL_PICK, PALETTE['cyan'])
            elif looks_like_drive(source):
                self.rip_ok = os.path.exists(source)
                self._disc_note(INSTALL_DRIVE_ONLY if self.rip_ok
                                else INSTALL_NO_DRIVE,
                                PALETTE['amber'] if self.rip_ok
                                else PALETTE['bad'])
            elif not os.path.exists(source):
                self._disc_note(INSTALL_NO_PATH, PALETTE['bad'])
            elif extension != '.cue':
                kind = DISC_IMAGES.get(extension)
                self._disc_note(INSTALL_NOT_CUE % kind if kind
                                else INSTALL_NOT_A_CUE, PALETTE['bad'])
            else:
                self._check_cue(source)
            self._sync_buttons()

        def _check_cue(self, source):
            """A cue sheet, which may or may not be a Virtual-On one.

            The two questions are separate. Whether the ripper can read it is
            answered by the cue; whether the installer can use it is answered
            by what is inside the data track. A cue for some other game fails
            the second and passes the first."""
            try:
                audio = audio_tracks(source)
            except (OSError, ValueError) as exc:
                # parse_cue names the missing bin or the bad line, and that
                # is the most useful thing there is to say.
                self._disc_note(str(exc), PALETTE['bad'])
                return
            self.rip_ok = bool(audio)
            # From the sheet, without reading a sector: the tracks are 320 MB
            # and go wherever the game is, which the install check above may
            # never have looked at.
            self._rip_bytes = rip_bytes(source) if audio else 0

            try:
                info = probe_disc(source)
            except (DiscError, OSError, ValueError) as exc:
                self._disc_note(str(exc), PALETTE['bad'])
            else:
                self._describe_disc(info)

            if not audio:
                self._audio_warning = INSTALL_NO_AUDIO
            elif tuple(audio) != VO_AUDIO:
                self._audio_warning = INSTALL_ODD_AUDIO % (len(audio),
                                                           len(VO_AUDIO))

        def _describe_disc(self, info):
            build = info['build']
            self._disc_bytes = info['bytes']
            if info['wants_language'] and len(info['languages']) > 1:
                self.lang_box.config(values=info['languages'])
                self.lang_var.set(info['default_language'])
                self.lang_row.grid(row=2, column=0, columnspan=3,
                                   sticky='ew', pady=(0, 6))
            if build['supported']:
                self.disc_ok = True
                self._disc_note(INSTALL_FOUND % (info['count'],
                                                 info['bytes'] >> 20),
                                PALETTE['ok'])
                # The sector layout is worth having in a bug report and
                # nowhere near worth a line in the window.
                self._log('disc: %s, %d files, %d MB'
                          % (info['form'], info['count'],
                             info['bytes'] >> 20))
            else:
                # The same panel the game file card uses, for the same
                # reason: the numbers say more than "wrong version" does.
                self.disc_ok = True
                self.disc_compare.show(compare_report(
                    build['size'], build['md5'], build['why'],
                    'Installing and ripping still work; only patching needs '
                    'the English retail build.',
                    'warn'))
                self._disc_note(INSTALL_FOUND_OTHER
                                % (build['name'], info['count'],
                                   info['bytes'] >> 20),
                                PALETTE['amber'])
                self._log('disc: v_on.exe is the %s build (%s)'
                          % (build['name'], build['md5']))

        def _disc_note(self, text, colour):
            self.disc_note.config(text=text, foreground=colour)

        # -- destination

        def _pick_dest(self):
            path = filedialog.askdirectory(title='Install the game where?')
            if path:
                self.dest_var.set(path)       # the trace syncs the buttons

        def _sync_buttons(self, *_args):
            """One place decides what is clickable, because three things
            feed it: the source, the destination and the game file."""
            path = self.dest_var.get().strip()
            if path:
                why, level = dest_problem(path, self._disc_bytes)
            elif self.disc_ok and not self.core.exe_path:
                # Only a prompt for someone who has no game yet. With one
                # already picked, the disc is here for the soundtrack and
                # asking where to install is pointing at a step they have
                # already done.
                why, level = INSTALL_NEEDS_DEST, 'warn'
            else:
                why, level = None, None
            self.dest_note.config(
                text=why or '',
                foreground=PALETTE['bad'] if level == 'bad'
                else PALETTE['amber'] if level == 'warn' else self.dim)
            # Three things can be said about the music folder, in this
            # order: no room is the one that stops the rip, a disc with the
            # wrong tracks is the reason not to press the button, and where
            # the tracks go is what is left.
            short = (room_for(self._target(), self._rip_bytes,
                              'the soundtrack')
                     if self.rip_ok and self._target() else '')
            if short:
                self.music_note.config(text=short, foreground=PALETTE['bad'])
            elif self._audio_warning:
                self.music_note.config(text=self._audio_warning,
                                       foreground=PALETTE['amber'])
            else:
                # The prompt only helps once there is a disc to rip from;
                # before that it is a second cyan line saying nothing new.
                self._music(music_status(self._target())
                            if self.disc_var.get().strip() or self._target()
                            else '')

            if self._busy:
                self.install_btn.state(['disabled'])
                self.rip_btn.state(['disabled'])
                return
            self.install_btn.state(
                ['!disabled'] if self.disc_ok and path and level != 'bad'
                else ['disabled'])
            # Ripping needs a source the ripper can actually read and
            # somewhere to put the tracks. It does not care which build the
            # disc holds, or whether the game beside it can be patched - a
            # cue for another pressing still rips.
            can_rip = self.rip_ok and bool(self._target()) and not short
            self.rip_btn.state(['!disabled'] if can_rip else ['disabled'])

        def _target(self):
            """Where the tracks go: the install folder if one is set, the
            folder holding the chosen v_on.exe otherwise."""
            path = self.dest_var.get().strip()
            if path:
                return path
            if self.core.exe_path:
                return os.path.dirname(self.core.exe_path)
            return None

        # -- installing

        def _install(self):
            dest = self.dest_var.get().strip()
            source = self.disc_var.get().strip()
            language = self.lang_var.get() or None
            self._busy = 'install'
            self._cancel_install = False
            self.install_btn.state(['disabled'])
            self.rip_btn.state(['disabled'])
            self._disc_note(INSTALL_BUSY, self.dim)
            self._log('install: reading %s' % source)
            self._installq = queue.Queue()
            self._install_dest = dest
            last = [-1]

            def progress(done, total):
                # Runs on the worker. Raising here unwinds out of the copy,
                # which is how closing the window stops it.
                if self._cancel_install:
                    raise Cancelled('cancelled')
                pct = done * 100 // max(total, 1)
                if pct != last[0]:
                    last[0] = pct
                    self._installq.put(('progress', pct))

            def finished(error, written):
                self._installq.put(('done', error, written))

            self._install_thread = install_in_background(
                source, dest, language, progress, finished)
            self._poll_install()

        def _poll_install(self):
            """Drain the worker's queue on the UI thread. Tk is not safe to
            call from another one."""
            try:
                while True:
                    message = self._installq.get_nowait()
                    if message[0] == 'progress':
                        self._disc_note('%s %d%%' % (INSTALL_BUSY,
                                                     message[1]), self.dim)
                    else:
                        self._installed(message[1], message[2])
                        return
            except queue.Empty:
                pass
            self.root.after(100, self._poll_install)

        def _installed(self, error, written):
            dest = self._install_dest
            self._busy = None
            if isinstance(error, Cancelled):
                self._disc_note(INSTALL_CANCELLED, PALETTE['amber'])
                self._log('install: cancelled, %s holds a part-written copy'
                          % dest)
                self._sync_buttons()
                return
            if error is not None:
                why = copy_failure(dest, error)
                self._disc_note(why, PALETTE['bad'])
                self._log('install: failed - %s' % why)
                self._sync_buttons()
                return
            self._disc_note(INSTALL_OK % (len(written), dest), PALETTE['ok'])
            self._log('install: %d files written to %s'
                      % (len(written), dest))
            # Hand the result straight to the next step rather than making
            # someone browse to a folder this window just created.
            exe = os.path.join(dest, 'v_on.exe')
            if os.path.exists(exe):
                self.path_var.set(exe)
                self._check_file(exe)
            self._sync_buttons()

        # -- soundtrack

        def _music(self, text):
            """The prompt to pick somewhere is the one line people miss, so
            it gets the accent colour; everything else is a quiet hint."""
            colour = PALETTE['cyan'] if text == MUSIC_NEEDS_EXE else self.dim
            self.music_note.config(text=text or '', foreground=colour)

        def _rip(self):
            source = self.disc_var.get().strip()
            target = self._target()
            if not target:
                self._music(MUSIC_NEEDS_EXE)
                return
            # Captured now: the destination can be changed from under a
            # running rip, and the finished message names where they went.
            self._rip_dir = target
            self._busy = 'music'
            self.rip_btn.state(['disabled'])
            self.install_btn.state(['disabled'])
            self._log('music: ripping from %s' % source)
            self._cancel_rip = False
            self._ripq = queue.Queue()
            last = [-1]

            def progress(track, done, total):
                # Runs on the worker. Raising here unwinds through
                # WavWriter's context manager, which throws the partial
                # track away rather than leaving a short but valid file.
                if self._cancel_rip:
                    raise Cancelled('cancelled')
                pct = done * 100 // max(total, 1)
                if pct != last[0]:
                    last[0] = pct
                    self._ripq.put(('progress', track, pct))

            def finished(error, files):
                self._ripq.put(('done', error, files))

            self._rip_thread = rip_in_background(source, self._rip_dir,
                                                 progress, finished)
            self._poll_rip()

        def _poll_rip(self):
            try:
                while True:
                    message = self._ripq.get_nowait()
                    if message[0] == 'progress':
                        self.music_note.config(
                            text='Track %02d  %d%%' % message[1:],
                            foreground=self.dim)
                    else:
                        self._ripped(message[1], message[2])
                        return
            except queue.Empty:
                pass
            self.root.after(100, self._poll_rip)

        def _ripped(self, error, files):
            self._busy = None
            if isinstance(error, Cancelled):
                self._log('music: cancelled, the part-written track was '
                          'discarded')
            elif error is not None:
                why = copy_failure(outdir_for(self._rip_dir), error)
                self._log('music: failed - %s' % why)
                self.music_note.config(text=why, foreground=PALETTE['bad'])
                self._sync_buttons()
                return
            else:
                self._log('music: %d tracks written to %s'
                          % (len(files), outdir_for(self._rip_dir)))
            self._music(music_status(self._rip_dir))
            self._sync_buttons()

        def _close(self):
            """Stop a running copy or rip before the interpreter goes away.

            Both write files, and a worker still running when the
            interpreter is torn down leaves whichever one it was on half
            written. Asking it to stop and waiting a moment is enough: both
            check on the next chunk."""
            self._cancel_rip = True
            self._cancel_install = True
            for name in ('_rip_thread', '_install_thread'):
                thread = getattr(self, name, None)
                if thread is not None and thread.is_alive():
                    thread.join(1.5)
            self.root.destroy()

        def _link_row(self, parent, label, name, url, note):
            """cnc-ddraw: somebody else's program, so the project name is a
            link to it. Not a patch - Apply never touches this."""
            self.ddraw_btn, text = self._addon_head(
                parent, label, name, url, self._ddraw_click, first=True)
            _hint(text, note, self.dim, self.small, pady=(4, 0))
            _hint(text, DDRAW_WINE, PALETTE['amber'], self.small,
                  pady=(4, 0))
            self.ddraw_note = _hint(text, '', self.dim, self.small,
                                    pady=(4, 0))
            self.ddraw_installed = False

        def _addon_head(self, grid, label, name=None, url=None,
                        command=None, first=False):
            """One add-on: its title and description on the left of the
            split, its button on the right of it.

            Returns (button, text side) - the caller's descriptions go into
            the text side, so they wrap to half the card rather than to all
            of it and stop where the button starts."""
            line = grid.grid_size()[1]
            if not first:
                rule = tk.Frame(grid, background=PALETTE['line'], height=1)
                rule.grid(row=line, column=0, columnspan=2, sticky='ew',
                          pady=self.px((14, 0)))
                line += 1

            text = ttk.Frame(grid, style='Card.TFrame')
            text.grid(row=line, column=0, sticky='ew',
                      pady=self.px((12, 4)))
            row = ttk.Frame(text, style='Card.TFrame')
            row.pack(fill='x')
            self._static_label(ttk.Label(
                row, text=label, style='Card.TLabel',
                foreground=PALETTE['text'])).pack(side='left')
            if name:
                link = self._static_label(tk.Label(
                    row, text=name, cursor='hand2',
                    background=PALETTE['card'],
                    foreground=PALETTE['cyan']))
                link.pack(side='left', padx=self.px((6, 0)))
                link.bind('<Button-1>', lambda _e: webbrowser.open(url))
                link.bind('<Enter>', lambda _e: link.config(
                    foreground=PALETTE['cyan_hi']))
                link.bind('<Leave>', lambda _e: link.config(
                    foreground=PALETTE['cyan']))
            btn = ttk.Button(grid, text='Install', style='Vo.TButton',
                             command=command)
            # No sticky north or south, so grid centres it against the whole
            # entry; sticky west puts it at the start of its half, which is
            # the gutter the columns above are split on.
            btn.grid(row=line, column=1, sticky='w',
                     padx=self.px((14, 2)))
            return btn, text

        def _addons_body(self, parent):
            """Separate files that sit beside the game, not byte patches.
            Apply and Restore do not touch either of these; each row
            installs and removes itself.

            Split down the same line as the two columns above, because this
            card spans both: what reads goes on the left, the button on the
            right of the split, so it sits by the gutter and level with the
            middle of the entry it belongs to rather than out at the far
            edge of the window."""
            _hint(parent, ADDONS_HINT, self.dim, self.small, pady=(0, 4))
            grid = ttk.Frame(parent, style='Card.TFrame')
            grid.pack(fill='x')
            grid.columnconfigure(0, weight=1, uniform='addon')
            grid.columnconfigure(1, weight=1, uniform='addon')
            self._link_row(grid, *DDRAW_LINK)
            self._netplay_row(grid)

        def _netplay_row(self, parent):
            """Ours, so there is nowhere to link: the explanation lives in
            the README."""
            self.net_btn, text = self._addon_head(
                parent, NETPLAY_LABEL, command=self._netplay_click)
            _hint(text, NETPLAY_NOTE, self.dim, self.small, pady=(4, 0))
            _hint(text, NETPLAY_PORT, PALETTE['amber'], self.small,
                  pady=(4, 0))
            self.net_note = _hint(text, '', self.dim, self.small,
                                  pady=(4, 0))
            self.net_state = None

        def _netplay_sync(self):
            gamedir = self._ddraw_dir()
            self.net_state = netplay_status(gamedir)
            # Remove only when our current build is in place. An old build
            # of ours takes Install, which replaces it; the note says so.
            self.net_btn.config(
                text='Remove' if self.net_state == 'current' else 'Install')
            if self.net_state == 'old':
                self.net_note.config(text=NETPLAY_OLD,
                                     foreground=PALETTE['amber'])

        def _netplay_click(self):
            gamedir = self._ddraw_dir()
            if not gamedir:
                self.net_note.config(text=NETPLAY_NEEDS_EXE,
                                     foreground=PALETTE['bad'])
                return
            try:
                if self.net_state == 'current':
                    remove_netplay(gamedir)
                    self.net_note.config(text=NETPLAY_OUT,
                                         foreground=self.dim)
                    self._log('netplay: removed, original dpctrl.dll '
                              'restored')
                else:
                    updating = self.net_state == 'old'
                    install_netplay(gamedir)
                    if netplay_sync_ready(gamedir) is False:
                        self.net_note.config(text=NETPLAY_NOSYNC,
                                             foreground=PALETTE['amber'])
                        self._log('netplay: installed, but this v_on.exe is '
                                  'missing a patch matches need')
                    else:
                        self.net_note.config(
                            text=NETPLAY_UPDATED if updating else NETPLAY_IN,
                            foreground=PALETTE['ok'])
                        self._log('netplay: %s, UDP dpctrl.dll in place'
                                  % ('updated' if updating else 'installed'))
            except (OSError, ValueError) as exc:
                self.net_note.config(text=str(exc), foreground=PALETTE['bad'])
                self._log('netplay: failed - %s' % exc)
            self._netplay_sync()

        def _ddraw_dir(self):
            return (os.path.dirname(self.core.exe_path)
                    if self.core.exe_path else None)

        def _ddraw_sync(self):
            """Point the button at whichever job is available."""
            gamedir = self._ddraw_dir()
            self.ddraw_installed = bool(gamedir and ddraw_status(gamedir))
            self.ddraw_btn.config(
                text='Remove' if self.ddraw_installed else 'Install')

        def _ddraw_click(self):
            if self.ddraw_installed:
                self._remove_ddraw()
            else:
                self._install_ddraw()

        def _remove_ddraw(self):
            gamedir = self._ddraw_dir()
            if not gamedir:
                return
            try:
                gone = remove_ddraw(gamedir)
            except OSError as exc:
                self.ddraw_note.config(text=DDRAW_LOCKED,
                                       foreground=PALETTE['bad'])
                self._log('ddraw: failed - %s' % exc)
            else:
                self.ddraw_note.config(text=DDRAW_GONE, foreground=self.dim)
                self._log('ddraw: removed %s' % ', '.join(gone))
            self._ddraw_sync()

        def _install_ddraw(self):
            gamedir = (os.path.dirname(self.core.exe_path)
                       if self.core.exe_path else None)
            if not gamedir:
                self._log(DDRAW_NEEDS_EXE)
                self.ddraw_note.config(text=DDRAW_NEEDS_EXE,
                                       foreground=PALETTE['cyan'])
                return

            self.ddraw_btn.state(['disabled'])
            self.ddraw_note.config(text=DDRAW_BUSY, foreground=self.dim)
            self._log('ddraw: fetching %s' % DDRAW_URL)
            self._ddrawq = queue.Queue()

            def progress(got, total):
                self._ddrawq.put(('progress', got, total))

            def done(exc, files):
                self._ddrawq.put(('done', exc, files))

            install_ddraw_in_background(gamedir, progress, done)
            self._drain_ddraw()

        def _drain_ddraw(self):
            """Poll the worker's queue on the UI thread, as the rip does."""
            try:
                while True:
                    item = self._ddrawq.get_nowait()
                    if item[0] == 'progress':
                        _kind, got, total = item
                        if total:
                            self.ddraw_note.config(
                                text='%s %d%%' % (DDRAW_BUSY,
                                                  100 * got // total))
                        continue
                    _kind, exc, files = item
                    self.ddraw_btn.state(['!disabled'])
                    if isinstance(exc, PermissionError):
                        self.ddraw_note.config(text=DDRAW_LOCKED,
                                               foreground=PALETTE['bad'])
                        self._log('ddraw: failed - %s' % DDRAW_LOCKED)
                    elif exc:
                        self.ddraw_note.config(text='Download failed.',
                                               foreground=PALETTE['bad'])
                        self._log('ddraw: failed - %s' % exc)
                        self._log('ddraw: install it by hand from the link '
                                  'in the add-on row')
                    else:
                        self.ddraw_note.config(
                            text='Installed %d files beside v_on.exe.'
                                 % len(files), foreground=PALETTE['ok'])
                        self._log('ddraw: installed %s'
                                  % ', '.join(sorted(files)[:6]))
                    self._ddraw_sync()
                    return
            except queue.Empty:
                pass
            self.root.after(100, self._drain_ddraw)

        def _patch_body(self, parent, keys, hint):
            if hint:
                _hint(parent, hint, self.dim, self.small, pady=(0, 8))
            state = default_state()
            for key in keys:
                label, tip, _sites = BY_KEY[key]
                row = ttk.Frame(parent, style='Card.TFrame')
                row.pack(fill='x', pady=self.px(3))
                var = tk.BooleanVar(value=state[key])
                self.vars[key] = var
                if key in ALWAYS:
                    # A permanently ticked box that cannot be clicked reads
                    # like something is broken. A plain line does not, and
                    # the card's own heading says these are always applied.
                    self._static_label(ttk.Label(
                        row, text=label, style='Card.TLabel',
                        padding=(2, 3))).pack(side='left')
                else:
                    check = self._static_label(ttk.Checkbutton(
                        row, text=label, variable=var,
                        style='Card.TCheckbutton', command=self._retally))
                    check.state(['disabled'])
                    check.pack(side='left')
                    self.checks[key] = check
                Info(row, label, tip, self).btn.pack(side='right',
                                                     padx=(6, 2))


        def _about_body(self, parent):
            self._static_label(ttk.Label(
                parent, text=TITLE, style='Card.TLabel',
                font=self.bold)).pack(anchor='w')
            # Without the scheme, which is nine characters of nothing and
            # makes the line wider than the card wants to be.
            short = REPO_URL.split('//', 1)[-1]
            link = self._static_label(ttk.Label(
                parent, text=short, style='Link.TLabel', font=self.small,
                cursor='hand2'))
            link.pack(anchor='w', pady=(1, 0))
            link.bind('<Button-1>', lambda _e: webbrowser.open(REPO_URL))
            # A ttk separator takes the theme's colour, which is not one of
            # ours; a one pixel frame in the palette's line colour is.
            tk.Frame(parent, height=1, background=PALETTE['line'],
                     borderwidth=0, highlightthickness=0).pack(
                         fill='x', pady=(10, 8))
            self._patch_body(parent, ABOUT, None)

        def _log_body(self, parent):
            wrap = self.log_wrap = tk.Frame(parent, background=PALETTE['line'],
                                            borderwidth=0,
                                            highlightthickness=0)
            wrap.pack(fill='both', expand=True, padx=1, pady=1)
            self.log_box = tk.Text(wrap, height=5, width=34, wrap='word',
                                   state='disabled', relief='flat',
                                   highlightthickness=0, padx=6, pady=4,
                                   font=self.small,
                                   background=PALETTE['ink'],
                                   foreground=PALETTE['dim'],
                                   insertbackground=PALETTE['cyan'])
            self.log_box.pack(side='left', fill='both', expand=True)
            bar = ttk.Scrollbar(wrap, orient='vertical',
                                style='Vo.Vertical.TScrollbar',
                                command=self.log_box.yview)
            bar.pack(side='right', fill='y')
            self.log_box.configure(yscrollcommand=bar.set)

        def _statusbar(self, parent):
            bar = ttk.Frame(parent, style='Bar.TFrame',
                            padding=self.px((12, 8)))
            bar.pack(fill='x', side='bottom')
            self.apply_btn = ttk.Button(bar, text='Apply patches',
                                        style='Go.TButton', state='disabled',
                                        command=self._apply)
            self.apply_btn.pack(side='right')
            self.restore_btn = ttk.Button(bar, text='Restore original',
                                          style='Vo.TButton',
                                          state='disabled',
                                          command=self._restore)
            self.restore_btn.pack(side='right', padx=(0, 8))
            # width=1 so a long note cannot widen the window
            self.status = ttk.Label(bar, text=NO_FILE, style='Bar.TLabel',
                                    foreground=self.dim, font=self.small,
                                    width=1, anchor='w')
            self.status.pack(side='left', fill='x', expand=True)
            # The font is only known once the styles have run, and the
            # first <Configure> arrives while the window is being built.
            self._status_font = self.small
            self.status.bind('<Configure>', self._fit_status, add='+')

        # -- behaviour

        def _set_status(self, text, ok=None, level='bad'):
            # ok True/False is green/red; None is a quiet note; 'warn' is a
            # success worth a second look, in amber.
            if ok == 'warn':
                colour = PALETTE['amber']
            elif ok is None:
                colour = self.dim
            else:
                colour = PALETTE['ok'] if ok else PALETTE[level]
            font = self.small if ok is None else self.bold
            self._status_text, self._status_font = text, font
            self.status.config(foreground=colour, font=font)
            self.file_note.config(text=text, foreground=colour, font=font)
            self._fit_status()

        def _static_label(self, widget):
            """Remember a widget whose text is written once."""
            self._static.append(widget)
            return widget

        def _nudge(self, _event=None):
            """Rewrite the text of every widget that never changes it.

            Resizing this window leaves some widgets undrawn on some X
            stacks: the pixels are missing while the widget itself is
            present and the right size, and running the pointer over it
            brings the text back. Every widget that survives is one that
            gets written to during the resize - the hints re-wrap, so they
            repaint; a section heading is set once at startup, so it does
            not, and it is the headings that come back blank.

            Writing a widget's own text back to it costs nothing and marks
            it for redraw, which is the part that was missing. It runs once
            the resize has stopped rather than on every event, because it is
            about the state the window is left in, and because a write is
            safe to defer in a way that a geometry change is not.
            """
            # A drag sends an event a pixel. Noting the time is free;
            # cancelling and rescheduling a Tcl timer for each one is not,
            # so the timer is armed once and asks on arrival whether the
            # window has stopped moving yet.
            self._nudge_at = time.monotonic()
            if self._nudge_after is None:
                self._nudge_after = self.root.after(NUDGE_MS, self._settled)

        def _settled(self):
            self._nudge_after = None
            if (time.monotonic() - self._nudge_at) * 1000 < NUDGE_MS:
                self._nudge_after = self.root.after(NUDGE_MS, self._settled)
                return                  # still moving, come back later
            self._redraw_static()

        def _redraw_static(self):
            for widget in self._static:
                try:
                    widget.configure(text=widget.cget('text'))
                except tk.TclError:
                    pass                # destroyed with the window

        def _fit_status(self, _event=None):
            """Trim the status line to the room it actually has.

            It used to cut at 52 characters, chosen for a 430px window. The
            window is twice that now, so the number is measured in the font
            against the label's own width instead - and on a wide window
            nothing is cut at all."""
            text, font = self._status_text, self._status_font
            room = self.status.winfo_width()
            if room <= 1 or font.measure(text) <= room:
                self.status.config(text=text)
                return
            # Bisected rather than walked back a character at a time: each
            # measure is a call into Tcl, and a long message in a narrow
            # window cost one per character on every step of a drag.
            ellipsis = font.measure('\u2026')
            low, high = 1, len(text)
            while low < high:
                mid = (low + high + 1) // 2
                if font.measure(text[:mid]) + ellipsis <= room:
                    low = mid
                else:
                    high = mid - 1
            self.status.config(text=text[:low].rstrip() + '\u2026')

        def _pick(self):
            path = filedialog.askopenfilename(
                title='Select v_on.exe',
                filetypes=[('Game executable', '*.exe'),
                           ('All files', '*.*')])
            if path:
                self.path_var.set(path)
                self._check_file(path)

        def _check_file(self, path):
            try:
                note, ok = self.core.load(path)
            except OSError as exc:
                self._set_status('Could not read it: %s' % exc, False)
                self._compare(None)
                for key, check in self.checks.items():
                    self.vars[key].set(False)
                    check.state(['disabled'])
                # ALWAYS keys have no widget to disable; _apply forces them.
                self._chose = False
                self.apply_btn.state(['disabled'])
                self.restore_btn.state(['disabled'])
                self._sync_buttons()
                self._ddraw_sync()
                self._netplay_sync()
                return
            level = (self.core.compare or {}).get('level', 'bad')
            if note == READY_TAG:
                note = READY % self._selected()
            self._set_status(note, ok, 'amber' if level == 'warn' else 'bad')
            self._compare(self.core.compare)
            state = default_state()
            for key, check in self.checks.items():
                self.vars[key].set(state[key] if ok else False)
                check.state(['!disabled'] if ok else ['disabled'])
            self._chose = bool(ok)
            self.apply_btn.state(['!disabled'] if ok else ['disabled'])
            self.restore_btn.state(
                ['!disabled'] if self.core.can_restore() else ['disabled'])
            # Ripping only needs a folder, so it stays available for a file
            # that cannot be patched - already patched, most likely.
            self._sync_buttons()
            self._ddraw_sync()
            self._netplay_sync()
            if not ok:
                self._log(note)
                for line in (self.core.compare or {}).get('log', ()):
                    self._log(line)

        def _selected(self):
            """How many patches Apply would write right now."""
            return sum(1 for key, var in self.vars.items()
                       if key in ALWAYS or var.get())

        def _retally(self, *_args):
            """Keep the count honest as boxes are ticked."""
            if self.core.exe_path and not self.core.compare:
                self._set_status(READY % self._selected(), True)

        def _apply(self):
            wanted = {k: v.get() for k, v in self.vars.items()}
            wanted.update({key: True for key in ALWAYS})
            ok, lines = self.core.apply(wanted)
            for line in lines:
                self._log(line)
            self.restore_btn.state(
                ['!disabled'] if self.core.can_restore() else ['disabled'])
            if not ok:                      # leave everything as it was
                self._set_status(FAILED, False)
                return
            self.apply_btn.state(['disabled'])
            for check in self.checks.values():
                check.state(['disabled'])
            # No sync warning here any more: both patches internet play
            # needs are in ALWAYS, so Apply cannot produce a file without
            # them. netplay_sync_ready still checks the file on disk when
            # the add-on is installed, which catches a copy patched by an
            # older release.
            self._set_status(DONE % sum(1 for v in wanted.values() if v), True)

        def _restore(self):
            # A selection is worth keeping across the reload only if there
            # was one to make - _chose says whether the boxes were ever
            # usable for this file. They are disabled after an apply as well
            # as after a refusal, so their own state cannot answer it. Somebody who unticked two patches, applied, and
            # restored should get their two back rather than a fresh set of
            # defaults - but somebody who opened the patcher on an already
            # patched copy never chose anything: every box was unticked and
            # disabled because the file could not be patched, and carrying
            # that forward left the whole list off after the restore had
            # made it patchable again.
            chosen = ({key: var.get() for key, var in self.vars.items()}
                      if self._chose else None)
            for line in self.core.restore():
                self._log(line)
            self._check_file(self.core.exe_path)
            if chosen:
                for key, was in chosen.items():
                    if key in self.checks:
                        self.vars[key].set(was)
            self._retally()

        def _log(self, text):
            # Open it on the first line written: collapsed by default, but
            # "see the log" is useless if the log is hidden.
            opener = self._openers.get('LOG')
            if opener:
                opener()
            self.log_box.config(state='normal')
            self.log_box.insert('end', text + '\n')
            self.log_box.see('end')
            self.log_box.config(state='disabled')

    dpi = win_dpi()
    try:
        _root = tk.Tk()
    except tk.TclError as exc:
        # Tk imports fine on a headless box and then fails here. Only the
        # window needs a display; --rip does not.
        return ('Cannot open a window: %s\n'
                'Set DISPLAY or WAYLAND_DISPLAY, or rip from the terminal '
                'with --rip.' % exc)
    if dpi:
        # Tk sizes fonts in points against 72 dpi unless told otherwise.
        _root.tk.call('tk', 'scaling', dpi / 72.0)
    App(_root)
    _root.mainloop()
    return 0


def probe_tk():
    """The module only. Whether it can reach a display is run_tk's problem."""
    try:
        __import__('tkinter')
    except ImportError as exc:
        return str(exc)
    return None


USAGE = """vo_patch.py %s - Virtual-On (PC, 1997) patcher

  vo_patch.py                     open the patcher
  vo_patch.py --install CUE DIR   copy the game out of a disc image into DIR
                                  (--language NAME picks the manual)
  vo_patch.py --rip SOURCE DIR    rip the soundtrack; SOURCE is a .cue sheet
                                  or, on Linux, a CD drive. DIR holds v_on.exe
  vo_patch.py --rip               list the drives it can see (Linux)
  vo_patch.py --ddraw DIR         download cnc-ddraw into DIR (holds v_on.exe)
  vo_patch.py --netplay DIR       install the UDP netplay DLL (--remove undoes)
  vo_patch.py --selfcheck         validate the patch tables and exit
  vo_patch.py --version
"""


def selfcheck():
    """Run the import-time table checks and say what they covered.

    The tables are the whole patcher, and nothing else exercises them without
    a copy of the game, so this is what to run after editing one."""
    sites, byte_count = _check_table()
    lines = ['vo_patch.py %s' % VERSION,
             '%d patches, %d sites, %d bytes of the executable touched'
             % (len(BY_KEY), sites, byte_count),
             'expects %d bytes, MD5 %s' % (EXE_SIZE, ORIGINAL_MD5),
             'CD audio blob: %d bytes of code, %d of data, %d placeholders'
             % (len(VOCD_CODE), len(VOCD_DATA), len(VOCD_MAGICS)),
             'lever routine: %d bytes' % len(LEVERS_CODE),
             'title banner: %d tiles (%d spare), %d bytes of %s'
             % (BANNER_UNIQUE, BANNER_SPILLED, BANNER_UNIQUE * 128, ESCRGAME),
             'write order: %s' % ' '.join(apply_order()),
             'tables OK']
    print('\n'.join(lines))
    return None


def netplay_cli(argv):
    """--netplay GAMEDIR [--remove]."""
    if not argv or len(argv) > 2:
        return 'Usage: vo_patch.py --netplay GAMEDIR [--remove]'
    gamedir = argv[0]
    if not os.path.isdir(gamedir):
        return 'Not a directory: %s' % gamedir
    try:
        if '--remove' in argv[1:]:
            remove_netplay(gamedir)
            print('Original dpctrl.dll restored.')
        else:
            updating = netplay_status(gamedir) == 'old'
            install_netplay(gamedir)
            print('Netplay dpctrl.dll %s. Both players need it.'
                  % ('updated to this build' if updating else 'installed'))
            if netplay_sync_ready(gamedir) is False:
                print('Warning: this v_on.exe is missing a gameplay patch '
                      'online play needs, so matches will refuse to connect. '
                      'Re-patch with Fix frame rate and Fix crash on round loss.')
            print('Matchcode needs no forwarding; direct IP needs UDP 47624 at the host.')
    except (OSError, ValueError) as exc:
        return str(exc)
    return None


def ddraw_cli(argv):
    """--ddraw GAMEDIR, for a machine with no display."""
    if len(argv) != 1:
        return 'Usage: vo_patch.py --ddraw GAMEDIR'
    gamedir = argv[0]
    if not os.path.isdir(gamedir):
        return 'Not a directory: %s' % gamedir

    def progress(got, total):
        if total:
            sys.stderr.write('\r%5.1f%%  ' % (100.0 * got / total))

    try:
        files = install_ddraw(gamedir, progress)
    except Exception as exc:
        return '\ncnc-ddraw failed: %s' % exc
    sys.stderr.write('\r')
    print('Installed %d files into %s' % (len(files), gamedir))
    print('On Linux, set ddraw to native in winecfg for that prefix.')
    return None


def install_cli(args):
    """--install CUE DIR [--language NAME]"""
    usage = 'Usage: --install CUE DIR [--language NAME]'
    language = None
    if '--language' in args:
        at = args.index('--language')
        if at + 1 >= len(args):
            return usage
        language = args[at + 1]
        args = args[:at] + args[at + 2:]
    if len(args) != 2:
        return usage
    cue, dest = args
    try:
        info = probe_disc(cue)
    except (DiscError, OSError, ValueError) as exc:
        return str(exc)
    print('%s, %d files, %d MB' % (info['form'], info['count'],
                                   info['bytes'] >> 20))
    if info['languages']:
        print('manuals: %s (default %s)' % (', '.join(info['languages']),
                                            info['default_language']))
    build = info['build']
    if not build['supported']:
        print('This disc holds the %s build of v_on.exe (%s).\n%s\n'
              'It installs, but the patches need the English retail build.'
              % (build['name'], build['md5'], build['why']))
    why, level = dest_problem(dest, info['bytes'])
    if level == 'bad':
        return why
    if why:
        print(why)
    last = [-1]

    def progress(done, total):
        pct = done * 100 // max(total, 1)
        if pct != last[0]:
            last[0] = pct
            sys.stdout.write('\r%3d%%' % pct)
            sys.stdout.flush()

    try:
        written = install_disc(cue, dest, language, progress)
    except (DiscError, OSError, ValueError) as exc:
        return '\n%s' % copy_failure(dest, exc)
    print('\rInstalled %d files to %s' % (len(written), dest))
    return 0


def rip_cli(argv):
    """--rip SOURCE GAMEDIR, for scripting or a machine with no display."""
    if len(argv) == 0:
        if os.name == 'nt':
            print(NO_WINDOWS_DRIVE)
            return None
        found = list_devices()
        print('Drives visible here: %s' % (', '.join(found) or 'none'))
        print('Rip one with: vo_patch.py --rip SOURCE GAMEDIR')
        return None
    if len(argv) != 2:
        return 'Usage: vo_patch.py --rip SOURCE GAMEDIR'

    source, gamedir = argv
    seen = [None]

    def progress(track, done, total):
        if seen[0] != track:
            seen[0] = track
            sys.stderr.write('\n')
        sys.stderr.write('\rtrack %02d  %5.1f%%  '
                         % (track, 100.0 * done / max(total, 1)))

    try:
        if source.lower().endswith('.cue'):
            short = room_for(outdir_for(gamedir), rip_bytes(source),
                             'the soundtrack')
            if short:
                return short
        files = rip(source, outdir_for(gamedir), progress)
    except Exception as exc:
        return '\nRipping failed: %s' % copy_failure(outdir_for(gamedir), exc)
    sys.stderr.write('\n')
    print('%d tracks written to %s' % (len(files), outdir_for(gamedir)))
    return None


def main():
    """Open the window, or explain how to install Tk."""
    args = sys.argv[1:]
    if '--help' in args or '-h' in args:
        print(USAGE % VERSION)
        return None
    if '--version' in args:
        print(VERSION)
        return None
    if '--selfcheck' in args:
        return selfcheck()
    if '--netplay' in args:
        return netplay_cli(sys.argv[sys.argv.index('--netplay') + 1:])
    if '--ddraw' in args:
        return ddraw_cli(sys.argv[sys.argv.index('--ddraw') + 1:])
    if '--rip' in args:
        return rip_cli(sys.argv[sys.argv.index('--rip') + 1:])
    if '--install' in args:
        return install_cli(sys.argv[sys.argv.index('--install') + 1:])
    # Anything left that looks like an option is a typo. Opening the window
    # instead looks like it worked.
    for arg in args:
        if arg.startswith('-'):
            return '%s\n%s' % (USAGE % VERSION, 'Unknown option: %s' % arg)

    why = probe_tk()
    if why is None:
        return run_tk()
    return ('Tk is not available: %s\n'
            'Install it: python3-tk on Debian, Ubuntu and Mint, '
            'python3-tkinter on Fedora, tk on Arch.' % why)


if __name__ == '__main__':
    sys.exit(main())
