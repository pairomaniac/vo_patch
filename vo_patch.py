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

# Other builds that turn up and cannot be patched, listed so the patcher can
# name what it was handed instead of saying "not the original".
# md5 -> (size, name, why). The builds that can be patched are BUILDS.
OTHER_BUILDS = {
    '4c70f780a7f0d98d74be62304fb99021': (
        6649344, 'USA OEM',
        'A different release of the same game, which the patcher has no '
        'tables for yet.'),
}

RETAIL_HINT = ('Install from a retail disc image above, or pick a copy '
               'installed from one. Its v_on.exe alone will not do: the '
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
    def __init__(self, name, md5, size, sections, caves, symbols, art,
                 sites=None, annex=None):
        self.name, self.md5, self.size = name, md5, size
        # (file offset, virtual address) of each section, in file order
        self.sections = sections
        self.caves, self.symbols = caves, symbols
        # Blobs with no safe cave go in a section appended before any patch
        # is written, in this order, 16-aligned. Where it lands is fixed by
        # the file's own headers - the first appended section can only go
        # in one place - so its blobs link at import like any cave:
        # (virtual address, file offset, names).
        self.annex = None
        if annex:
            va, raw = annex_place(size, sections)
            self.annex = (va, raw, tuple(annex))
            self.sections = tuple(sections) + ((raw, va),)
        # the title artwork the banner patch redraws: name, size, MD5 (None
        # when no copy has been through here to take one from)
        self.art = art
        # retail site offset -> (this build's offset, its original bytes),
        # for the sites the table names by retail offset; None for retail
        self.sites = sites

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


def annex_place(size, sections):
    """Where a section appended to a file of this shape lands: the next
    0x1000-aligned address after the last section, at the next 0x200 of
    the file. The section table gives the last section's start; its size
    is the distance to the file's end, which is what add_section rounds."""
    raw, va = sections[-1]
    length = size - raw
    return (va + length + 0xfff) & ~0xfff, (size + 0x1ff) & ~0x1ff


RETAIL = Build('English retail', ORIGINAL_MD5, EXE_SIZE, sections=(
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
    'PAD_PROFILES': 0x00624c08,     # the rest of the same run
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
    # Locals of the game's own functions our stubs read, as frame offsets;
    # a recompile lays its frames out differently
    'FILLIDX': -0x8,                # the bind page fill's loop counter
    'STOREIDX': -0x14,              # its store's combo index
    'SELIDX': -0xc,                 # the preselect's loop counter
    'DEVSEL': -0xc,                 # the F7 page's combo selection
    'DEVNUM': -0x14,                # and the device it maps to
    'SAVEPLAYER': -0xc8,            # the OK handler's player
    'SAVELINE': -0xcc,              # and its line buffer
    'F_X': -0xc,                    # the movie placer's X and Y
    'F_Y': -0x10,
    # The game
    'GAMEPADDEF': 0x0066d600,      # the gamepad's shipped binds
    'SPEEDSEL': 0x00be4308,        # the F5 speed choice
    'FRAMEDIV': 0x006c84d0,        # frames per draw, which it set
    'SIMPLESTUB1': 0x00442e50,     # Keyboard (Simple)'s profile stubs
    'SIMPLESTUB2': 0x005bcce3,
    'JOYCHECKOK': 0x00495e23,      # startup device check: passes
    'SPENDNONE': 0x00497349,       # F7 OK: a device that spends no joystick
    'NODIALOG': 0x004967a9,        # F7 page table: no dialog, success
    'BINDPAGE12': 0x004966e9,      # and the twelve-bind page
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
    'PREV': 0x006c3d48,            # last frame's slot; credits, nameentry
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
    'MOVIEHWND': 0x01ef88c8,       # the mciavi window (MCI_ANIM_STATUS_HWND)
    'MOVIEDEV': 0x01ef88f0,        # its device id
    'LIVE': 0x03651470,            # + player * 0x18
    'BINDS1': 0x03651470,          # 1P bind bytes
    'BINDS2': 0x03651488,          # 2P bind bytes
    'DEVICES': 0x03651540,         # 1P's profile, 0 being the keyboard
    'XIFN': 0x0365cb40,            # XInputGetState; 0 not yet, 1 failed
    'STATE': 0x0365cb44,           # the tick's XINPUT_STATE
    'BTN': 0x0365cb48,             # wButtons in it; the condition table's
    'SCR1': 0x0365cb60,            # scratch the tick keeps per player
    'SCR2': 0x0365cb61,
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
}, art=('escrgame.bin', 4194304, 'f0c2b33c6d32e8e25cee840a0de65dc0'))


# The Japanese rerelease: the same source through the same toolchain four
# months on, with every address moved. See docs/NOTES.md, and tools/vomap.py
# for how the addresses were found.
JAPAN_MD5 = 'd19320bdc3381a48228990907910a391'
JAPAN_SIZE = 6621696
JAPAN = Build('Japanese rerelease', JAPAN_MD5, JAPAN_SIZE, sections=(
    (0x00000400, 0x00401000),       # .text
    (0x001eec00, 0x005f0000),       # .rdata
    (0x00239200, 0x0063b000),       # .data
    (0x005fba00, 0x03658000),       # .idata
    (0x005fcc00, 0x0365a000),       # .rsrc
    (0x00606400, 0x03664000),       # .reloc
), caves={
    # Two places the game itself looks: the F7 device list's own run, and
    # the levers tail inside the XInput routine. Everything else is in the
    # annex: the runs of zeros this .rdata offered turned out to be the
    # NULL tails of handler tables, which the game calls through, and there
    # is no room in what is left. See docs/NOTES.md.
    'PAD_DEVLIST': 0x006693a0,
    'LEVERS': ('PADX', 'end'),
}, symbols={
    # Places inside our own blobs, the same labels as retail
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
    'GAMEPADDEF': 0x00669588,
    'SPEEDSEL': 0x00bdef5c,
    'FRAMEDIV': 0x006c41e8,
    'SIMPLESTUB1': 0x00442510,
    'SIMPLESTUB2': 0x005b7653,
    'JOYCHECKOK': 0x00494944,
    'SPENDNONE': 0x00495ea8,
    'NODIALOG': 0x00495308,
    'BINDPAGE12': 0x00495248,
    # Locals of the game's own functions our stubs read; the frames grew
    'FILLIDX': -0x18,              # the bind page fill's loop counter
    'STOREIDX': -0x14,             # its store's combo index
    'SELIDX': -0xc,                # the preselect's loop counter
    'DEVSEL': -0x10,               # the F7 page's combo selection
    'DEVNUM': -0x14,               # and the device it maps to
    'SAVEPLAYER': -0xc8,           # the OK handler's player
    'SAVELINE': -0x18c,
    'F_X': -0x14,
    'F_Y': -0x18,
    'EXIT1P': 0x00442584,          # where the 1P profile switch resumes
    'KBD1P': 0x00442734,           # stock keyboard handler, called by the tick
    'KBHANDLER1': 0x00442734,      # the stock 1P keyboard handler
    'CASEB': 0x00495682,           # the stock device 1 apply-and-serialize
    'CROSSCHECK': 0x004962cc,      # look at 1P's key map
    'KBACCEPT': 0x00496324,        # take the key
    'RESUME': 0x004963f7,          # what the Default button does next
    'DEFAULTS': 0x0049646c,        # fill a player's binds from the shipped set
    'DIGITLOOP': 0x004967cd,       # bind page fill: the digit loop
    'LISTLOOP': 0x0049680d,        # and the list loop
    'FILLDONE': 0x00496854,        # where the fill loop's jge went
    'STORESHIFT': 0x004969d1,      # bind page store: shift down, try digits
    'STORELIST': 0x004969f6,       # and the list id store
    'SELDIGITS': 0x00496bb6,       # bind page preselect: the digit loop
    'SELLIST': 0x00496bfa,         # the list loop
    'MAPDONE': 0x00496c36,         # where the search loop's jge went
    'SELSET': 0x00496c36,          # and set the selection
    'CURSOR': 0x004cb463,          # (column, row), cdecl
    'PRINT': 0x004cca8b,           # (text), cdecl, from the cursor
    'WRITELINE': 0x005ac4f3,       # (key, value): one v_on.ini line
    'FINDLINE': 0x005ac531,        # (key) -> value text, 0 if absent
    'EXIT2P': 0x005b76c7,          # and the 2P one
    'KBD2P': 0x005b785d,
    'KBHANDLER2': 0x005b785d,      # and 2P
    'GPAUSE': 0x005c1094,          # the built-in dialogs' pause, arg 0
    'GRESUME': 0x005c10da,         # and their resume
    'ORIGWNDPROC': 0x005c1126,     # the handler the hook falls through to
    'ORIG': 0x005c29ae,            # the call this one is made in place of
    'DRAW': 0x005c4198,            # (text, x, y, colour, flag), cdecl
    'MEMCPY': 0x005dfb30,
    'ORIGENTRY': 0x005e1570,       # the entry point this replaces
    'CDMUTE': 0x0063b430,
    'NOSHOT': 0x0064efd8,          # F11 check boxes: the flags they toggle
    'MASK1A': 0x0064f690,          # 1P key masks
    'MASK1B': 0x0064f69d,
    'KEYLIST': 0x006693c0,         # the game's 33 named keys
    'SEMUTE': 0x006b89ac,
    'MASK2A': 0x006ba820,          # 2P
    'MASK2B': 0x006ba82d,
    'HALF': 0x006bb278,            # coordinates on, at 0x5c9a98
    'WIDE': 0x006bb2b0,            # the two the pause text halves its own
    'PREV': 0x006bf0d0,            # last frame's slot; credits, nameentry
    'HELD': 0x006bf0d1,            # and how long this press has lasted
    'CAMERA1': 0x00beb0af,         # and the one Select writes, which is what
    'ACCEPT1': 0x00beb0d9,         # 1P's key buffer slot for A and Space
    'BLOCKS': 0x00bf1730,          # per player: this + player * 0x70; the
    'CURPLAYER': 0x00bf1590,       # the side being configured, 0 or 1
    'PHASE': 0x01acb68c,           # where the credits sequence is up to
    'CAM2': 0x01acba14,
    'CAMERA2': 0x01acba14,
    'ACC2': 0x01acba31,
    'ACCEPT2': 0x01acba31,         # and 2P
    'FLAG': 0x01adcdf8,            # the displaced write
    'MODE': 0x01adcdf0,            # game state and sub-state, the pair the
    'SUBMODE': 0x01addc40,         # tick already gates its bind slots on
    'MOVIEX': 0x01ae0c1c,          # the offsets the replaced code read
    'MOVIEY': 0x01ae0c20,
    'PRIMARY': 0x01ae0c34,         # the surface DRAW paints on, and the one
    'HWND': 0x01ae0c38,            # the game's window
    'BACK': 0x01ae0c18,            # that is about to be flipped over it
    'LEV1A': 0x01b13204,           # 1P lever words, left then right
    'LEV1B': 0x01b13206,
    'EDGEA': 0x01ed0b65,           # press edges, lever A byte: bit 0 is LT
    'EDGEB': 0x01ed0b66,           # and lever B's, where 2P's RT is
    'LEV2A': 0x01ef3534,           # 2P
    'LEV2B': 0x01ef3536,
    'MOVIEHWND': 0x01ef3570,       # the mciavi window (MCI_ANIM_STATUS_HWND)
    'MOVIEDEV': 0x01ef3590,        # its device id
    'LIVE': 0x0364c170,            # + player * 0x18
    'BINDS1': 0x0364c170,          # 1P bind bytes
    'BINDS2': 0x0364c188,          # 2P bind bytes
    'DEVICES': 0x0364c580,         # 1P's profile, 0 being the keyboard
    # scratch in the page slack past .data, as retail's is
    'XIFN': 0x036577a0,            # XInputGetState; 0 not yet, 1 failed
    'STATE': 0x036577a4,           # the tick's XINPUT_STATE
    'BTN': 0x036577a8,             # wButtons in it; the condition table's
    'SCR1': 0x036577c0,            # scratch the tick keeps per player
    'SCR2': 0x036577c1,
    'PSTATE': 0x036577d0,          # the pump's own XINPUT_STATE, so the two
    'PBTN': 0x036577d4,            # pollers cannot tread on each other
    'SLEEPFN': 0x036577e0,         # resolved Sleep: 0 not yet, 1 failed
    'PADPREV': 0x036577e4,         # last polled buttons, one word per pad,
    'DZTHR1': 0x036577ec,          # stick thresholds out of 32767, 1P then
    'DZSTR1': 0x036577f4,          # the digit pairs; see asm/padxinput.asm
    'GETMODULE': 0x036584bc,       # GetModuleHandleA
    'LOADLIB': 0x03658514,         # LoadLibraryA
    'GETPROC': 0x03658518,         # GetProcAddress
    'SENDMSG': 0x03658538,         # SendMessageA
    'ENDDIALOG': 0x03658548,       # EndDialog
    'CHECKDLGBTN': 0x03658554,     # CheckDlgButton
    'GETDLGITEM': 0x0365855c,      # GetDlgItem
    'POSTMSG': 0x0365857c,         # PostMessageA
    'GETMSG': 0x0365859c,          # GetMessageA, the call this replaced
    'PEEKMSG': 0x036585a0,         # PeekMessageA
    'GETCLIENT': 0x036585e4,       # GetClientRect, the hooked one
    'MOVEWINDOW': 0x036585f0,      # MoveWindow
    'MCISEND': 0x0365864c,         # mciSendCommandA
}, art=('jscrgame.bin', 4194304, None), sites=None, annex=(
    'TIMER', 'DEBUGBOX', 'PADX', 'TWIN', 'INTROWAIT', 'KBPAGE',
    'BINDLIST', 'BINDMAP', 'BINDBLOCK', 'INISAVE', 'INILOAD', 'BLOCKCUR',
    'INIPARSE', 'PAGESEC', 'PAGESEL', 'COMMITDEV', 'INIALL', 'DEVORDER',
    'F11PAUSE', 'MOVIE', 'CREDITS', 'NAMEENTRY', 'CAMSKIP', 'OVERLAY',
    'TITLEVER', 'PAD_COND', 'PAD_BINDS', 'PAD_NAMES', 'PAD_PROFILES',
    'PAD_SIMPLEDEF', 'PAD_INIKEYS', 'EXTRAS_DATA'))

BUILDS = {RETAIL.md5: RETAIL, JAPAN.md5: JAPAN}

# GENERATED by tools/buildsites.py - do not edit by hand.
#
# For each build but retail: where every site the table names by retail
# offset is in that build, and what it holds there. Sites at a blob's cave
# or in the annex are not here; those come from the build's tables.

# SITES JAPAN BEGIN
JAPAN.sites = {
    0x002bba60: (0x002b6bc0, '0f'),
    0x00189546: (0x0018491a, '2256'),
    0x00189552: (0x00184926, '88580100'),
    0x00058189: (0x0005749d, '01'),
    0x00170dc9: (0x0016c10d, '01'),
    0x001fcec8: (0x001f76d0, '80340200'),
    0x001fcecc: (0x001f76d4, '48240000'),
    0x002bbb54: (0x002b6c74,
        '0000000000000000040000000000000000000000040000000000000000000000'
        '04000000000000000000000004000000000000000000000004000000ffffffff'
        '1800000003000000000000000000000004000000000000000000000004000000'
        '0000000000000000020000000000000017000000030000000000000000000000'
        '03000000630000001d0000000300000000000000000000000400000000000000'
        '0000000004000000000000000000000002000000000000001100000003000000'
        '000000000000000003000000630000001e000000030000000000000000000000'
        '0100000063000000150000000300000000000000000000000100000063000000'
        '1c00000003000000000000000000000001000000630000002400000003000000'
        '0000000000000000010000006300000018000000030000000000000000000000'
        '0400000000000000000000000400000000000000000000000200000000000000'
        '1800000003000000000000000000000003000000630000002400000003000000'
        '0000000000000000010000006300000020000000030000000000000000000000'
        '01000000630000001f0000000300000000000000000000000100000063000000'
        '2000000003000000000000000000000004000000000000000000000004000000'
        '000000000000000002000000000000001a000000030000000000000000000000'
        '03000000630000001d0000000300000000000000000000000400000000000000'
        '0000000004000000000000000000000002000000000000002700000003000000'
        '0000000000000000030000006300000021000000030000000000000000000000'
        '0400000000000000000000000400000000000000000000000200000000000000'
        '0c00000003000000000000000000000003000000630000001100000003000000'
        '0000000000000000010000006300000019000000030000000000000000000000'
        '0400000000000000000000000400000000000000000000000200000000000000'
        '0b00000003000000000000000000000003000000630000001b00000003000000'
        '0000000000000000040000000000000000000000040000000000000000000000'
        '02000000000000000c0000000300000000000000000000000300000063000000'
        '1600000003000000000000000000000001000000630000002200000003000000'
        '0000000000000000010000006300000017000000030000000000000000000000'
        '0400000000000000000000000400000000000000000000000400000000000000'
        '0000000002000000000000001600000003000000000000000000000003000000'
        '6300000018000000030000000000000000000000040000000000000000000000'
        '0400000000000000000000000200000000000000220000000300000000000000'
        '0000000003000000630000001f00000003000000000000000000000004000000'
        '0000000000000000040000000000000000000000020000000000000021000000'
        '03000000000000000000000003000000630000001b0000000300000000000000'
        '0000000001000000630000001800000003000000000000000000000001000000'
        '630000001f000000030000000000000000000000040000000000000000000000'
        '0400000000000000000000000200000000000000120000000300000000000000'
        '0000000003000000630000001d00000003000000000000000000000004000000'
        '000000000000000004000000000000000000000002000000000000000e000000'
        '0300000000000000000000000300000063000000160000000300000000000000'
        '0000000004000000000000000000000004000000000000000000000004000000'
        '000000000000000002000000000000000a000000030000000000000000000000'
        '03000000630000001c0000000300000000000000000000000100000063000000'
        '2200000003000000000000000000000001000000630000002100000003000000'
        '0000000000000000010000006300000015000000030000000000000000000000'
        '0400000000000000000000000400000000000000000000000400000000000000'
        '0000000002000000000000001c00000003000000000000000000000003000000'
        '6300000027000000030000000000000000000000010000006300000019000000'
        '03000000000000000000000001000000630000002f0000000300000000000000'
        '0000000001000000630000003100000003000000000000000000000001000000'
        '6300000020000000030000000000000000000000010000006300000033000000'
        '03000000000000000000000006000000630000001c0000000300000000000000'
        '0000000001000000630000001800000003000000000000000000000004000000'
        '0000000000000000040000000000000000000000040000000000000000000000'
        '0200000000000000000000000400000004000000050000000300000000000000'
        '0000000004000000000000000000000004000000000000000000000002000000'
        '0400000019000000030000000000000000000000010000006300000015000000'
        '0300000000000000000000000200000000000000000000000200000000000000'
        '0000000002000000000000000000000002000000'
    ),
    0x001c5900: (0x001c01cf, 'a1340cae01'),
    0x0014dc42: (0x001491d3,
        'c745ec00000000c745e828000000a11c0cae018945eca1200cae018945e8'
    ),
    0x002c7678: (0x002c2808, '4e'),
    0x0000023f: (0x0000023f, '40'),
    0x0018fc25: (0x0018ac79, 'c705f8cdad0100000000'),
    0x000d60c8: (0x000d352a,
        'f605650bed01010f850d000000f605660bed01010f84f2010000'
    ),
    0x001c58e7: (0x001c01b6, 'e8f31b0000'),
    0x0010acd7: (0x0010745d, '00000000'),
    0x0010b088: (0x0010780e, '01000000'),
    0x0010b131: (0x001078b7, 'c70550d7670000000000e950000000'),
    0x0010b1b0: (0x00107936, '00000000'),
    0x0010b1ba: (0x00107940, '00000000'),
    0x0010b1c4: (0x0010794a, '00000000'),
    0x001c76d4: (0x001c1fa3, '0f840a000000'),
    0x00107930: (0x001040f0, '830d5031bf0001'),
    0x000000a8: (0x000000a8, '70151e00'),
    0x000273c1: (0x00027021, '833d5cefbd0003'),
    0x000275d3: (0x00027233, 'c7055cefbd0003000000'),
    0x000275e2: (0x00027242, 'c7055cefbd0002000000'),
    0x006035ac: (0x005fd5ac, '2c040000'),
    0x0060c064: (0x00606064,
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
    ),
    0x0010afbe: (0x00107744, 'c705e8416c0003000000'),
    0x0010afeb: (0x00107771, 'c705e8416c0003000000'),
    0x0010b002: (0x00107788, 'c705e8416c0003000000'),
    0x001c6941: (0x001c1210, 'c705e8416c0002000000'),
    0x001c6950: (0x001c121f, 'c705e8416c0003000000'),
    0x001c6d8c: (0x001c165b, 'c705e8416c0002000000'),
    0x001c6d9b: (0x001c166a, 'c705e8416c0003000000'),
    0x001c6dfc: (0x001c16cb, 'c705e8416c0002000000'),
    0x001c6e0b: (0x001c16da, 'c705e8416c0003000000'),
    0x001c8bc4: (0x001c3440, 'c705e8416c0002000000'),
    0x001c8bd3: (0x001c344f, 'c705e8416c0003000000'),
    0x001c4d42: (0x001bf627, '0f850c000000'),
    0x001c4d4b: (0x001bf630, '65000000'),
    0x001c4d7e: (0x001bf663, '26115c00'),
    0x00077f5a: (0x00076b0a,
        '8b45ecd94008d9e083ec04d91c248b45ec8b4004508b45ecd900d9e083ec04d9'
        '1c24e87f77f9ff83c40c'
    ),
    0x00078b1c: (0x000776cc,
        '8b45ecd94008d9e083ec04d91c248b45ec8b4004508b45ecd900d9e083ec04d9'
        '1c24e8bd6bf9ff83c40c'
    ),
    0x00079bb6: (0x00078766,
        '8b45ecd94008d9e083ec04d91c248b45ec8b4004508b45ecd900d9e083ec04d9'
        '1c24e8235bf9ff83c40c'
    ),
    0x00079f3f: (0x00078aef,
        '8b45ecd94008d9e083ec04d91c248b45ec8b4004508b45ecd900d9e083ec04d9'
        '1c24e89a57f9ff83c40c'
    ),
    0x0007d04a: (0x0007bbfa,
        '8b45ecd94008d9e083ec04d91c248b45ec8b4004508b45ecd900d9e083ec04d9'
        '1c24e88f26f9ff83c40c'
    ),
    0x000bb9ea: (0x000b986a,
        '8b45ecd94008d9e083ec04d91c248b45ec8b4004508b45ecd900d9e083ec04d9'
        '1c24e8ffe1f4ff83c40c'
    ),
    0x000bc5ac: (0x000ba42c,
        '8b45ecd94008d9e083ec04d91c248b45ec8b4004508b45ecd900d9e083ec04d9'
        '1c24e83dd6f4ff83c40c'
    ),
    0x000bd646: (0x000bb4c6,
        '8b45ecd94008d9e083ec04d91c248b45ec8b4004508b45ecd900d9e083ec04d9'
        '1c24e8a3c5f4ff83c40c'
    ),
    0x000bd9cf: (0x000bb84f,
        '8b45ecd94008d9e083ec04d91c248b45ec8b4004508b45ecd900d9e083ec04d9'
        '1c24e81ac2f4ff83c40c'
    ),
    0x000c0ada: (0x000be95a,
        '8b45ecd94008d9e083ec04d91c248b45ec8b4004508b45ecd900d9e083ec04d9'
        '1c24e80f91f4ff83c40c'
    ),
    0x000970bf: (0x00095c1c, '837de8210f8d2e000000'),
    0x000970d5: (0x00095c32, '8b04c5c0936600'),
    0x0009729c: (0x00095df9, '8a04c5c4936600'),
    0x000974ac: (0x00096009, '837df4210f8d23000000'),
    0x000974be: (0x0009601b, '390cc5c4936600'),
    0x00095f35: (0x00094a94, '053017bf0083c008'),
    0x0009724c: (0x00095da9, '053017bf0083c008'),
    0x0009736d: (0x00095eca, '053017bf0083c008'),
    0x00097355: (0x00095eb2, '8d04408d04c588956600'),
    0x00097397: (0x00095ef4, '053017bf0083c008'),
    0x00097531: (0x0009608e, '053017bf0083c008'),
    0x0009740f: (0x00095f6c, '8a84413817bf00'),
    0x0026c88c: (0x00267c14,
        '4b6579626f617264206f6e6c79202853696d706c652074797065202d20256450'
        '20736964652900'
    ),
    0x0026c400: (0x00267788,
        '11001f001e002000100012002e0022002d0013002f002100'
    ),
    0x0026c418: (0x002677a0,
        'c700cf00d300d100d200c900520051004f004c0053005000'
    ),
    0x000422a8: (0x00041968, '10254400'),
    0x001bc13b: (0x001b6aab, '53765b00'),
    0x001c530e: (0x001bfbdd, 'ff15a0856503'),
    0x000971bd: (0x00095d1a, '0f8558000000'),
    0x0026c218: (0x002675a0,
        '749366005c936600489366003c9366001893660008936600f0926600e0926600'
    ),
    0x000422ac: (0x0004196c, '1a254400'),
    0x001bc13f: (0x001b6aaf, '5d765b00'),
    0x000422b0: (0x00041970, '2b254400'),
    0x001bc143: (0x001b6ab3, '6e765b00'),
    0x00095e46: (0x000949a5, '83c008'),
    0x00095ec7: (0x00094a26, '3817bf00'),
    0x00096d37: (0x00095894, '83c008'),
    0x00096d61: (0x000958be, '83c008'),
    0x00096f19: (0x00095a76, '83c008'),
    0x00096c0a: (0x00095767, '3817bf00'),
    0x00096c40: (0x0009579d, '3817bf00'),
    0x00096c6d: (0x000957ca, '3817bf00'),
    0x00096de8: (0x00095945, '3817bf00'),
    0x00096e3f: (0x0009599c, '3817bf00'),
    0x00096e9a: (0x000959f7, '3817bf00'),
    0x00096b61: (0x000956bf, '833d9015bf00010f8558000000'),
    0x00096c8e: (0x000957eb, '6a016a00e87800000083c408'),
    0x00094ea0: (0x00093985, 'e8d12b0000'),
    0x00094eaf: (0x00093997, '3f340000'),
    0x00095217: (0x00093d38, '4d484900'),
    0x00095213: (0x00093d34, '1e484900'),
    0x00096731: (0x00095290, '3f5e4900'),
    0x00096735: (0x00095294, '655e4900'),
    0x00095bdc: (0x0009473b, '87524900'),
    0x00095be0: (0x0009473f, '08534900'),
    0x00096253: (0x00094db2, '82564900'),
    0x0009625b: (0x00094dba, '8a594900'),
    0x00095604: (0x00094155, 'e8802c0000'),
    0x000958a1: (0x00094401, '03'),
    0x000958aa: (0x0009440a, 'e900000000'),
    0x0009522e: (0x00093d4f, '03'),
    0x00095248: (0x00093d69, '03'),
    0x0009651a: (0x00095079, '8b803017bf00'),
    0x00096784: (0x000952e3, '8b45f08b4dec'),
    0x000959f7: (0x00094556, '89048d80c56403'),
    0x0060b34e: (0x0060534e, '3100500020007300690064006500'),
    0x0009703f: (0x00095b9c, '837de81a0f8d27000000'),
    0x00097257: (0x00095db4, '837dec1a0f8d13000000'),
    0x00097428: (0x00095f85, '837df41a0f8d27000000'),
    0x000974cb: (0x00096028, '8345f424e905000000'),
    0x0009753a: (0x00096097, 'e8948e1400'),
    0x001c52ac: (0x001bfb7b, 'ff159c856503'),
    0x00285e04: (0x0028118c, '20505245535320535041434520424152'),
    0x002c7654: (0x002c27e4,
        '546f20526573756d652047616d652c20507265737320463300000000'
    ),
    0x00269b60: (0x00264f60,
        '000001000200030004000500060007000800090007000a000b000c000d000e00'
        '0f00100011001200040004001300090004001400150004001600170007000800'
        '180019001a001b001c0014001d001e001f002000210022002300240025002600'
        '2700280029002a002b002c002d002e002f003000310032003300340035003600'
        '3700380039003a003b003c003d003e00280029003f003a004000410042004300'
        '4400450046004700480049004a004b004c004a004d004e004f00500051005200'
        '53005400550056005700580059005a005b005c005d005e005f00600061006200'
        '63004d004e004f006400650066006700680069006a006b006c004a00'
    ),
}
# SITES JAPAN END

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
# 'rel' the distance from the end of the slot to it, 'abs8' one byte of it -
# a frame offset, where the symbol is where the caller keeps a local and
# differs between compiles of the game. The symbol '.' is the blob's own
# address. Labels are offsets into the code, for the symbols above and the
# site table to name.

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
        (0x2de, 'abs', 'SCR1', 0),
        (0x2e2, 'abs', 'KBHANDLER1', 0),
        (0x2e6, 'abs', 'CAMERA1', 0),
        (0x2ee, 'abs', 'BINDS2', 0),
        (0x2f2, 'abs', 'LEV2A', 0),
        (0x2f6, 'abs', 'LEV2B', 0),
        (0x2fa, 'abs', 'MASK2A', 0),
        (0x2fe, 'abs', 'MASK2B', 0),
        (0x302, 'abs', 'ACCEPT2', 0),
        (0x306, 'abs', 'SCR2', 0),
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
        (0x21, 'abs8', 'FILLIDX', 0),
        (0x27, 'abs8', 'FILLIDX', 0),
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
        (0x21, 'abs8', 'SELIDX', 0),
        (0x27, 'abs8', 'SELIDX', 0),
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
        '00036bc01874060500000000c30500000000c39083b9000000000374088a8441'
        '08000000c38a844138000000c3909090240f3c0a720204270430880747c3'
    ), (
        (0x2, 'abs', 'BLOCKS', 0),
        (0xa, 'abs', 'BLOCKS', 8),
        (0x10, 'abs', 'BLOCKS', 56),
        (0x1d, 'abs', 'BLOCKS', 0),
        (0x28, 'abs', 'GAMEPADDEF', 0),
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
        (0x5, 'abs', 'SAVEPLAYER', 0),
        (0xe, 'abs', 'BLOCKS', 56),
        (0x14, 'abs', 'SAVELINE', 0),
        (0x23, 'rel', 'HEXCHAR', -4),
        (0x2a, 'rel', 'HEXCHAR', -4),
        (0x3b, 'abs', 'INIKEYS', 0),
        (0x41, 'abs', 'SAVELINE', 0),
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
        (0x9, 'abs8', 'FILLIDX', 0),
        (0x12, 'rel', 'DIGITLOOP', -4),
        (0x1a, 'rel', 'LISTLOOP', -4),
        (0x1f, 'rel', 'DEVCUR', -4),
        (0x27, 'abs8', 'STOREIDX', 0),
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
        (0x9, 'abs8', 'SELIDX', 0),
        (0x12, 'rel', 'SELDIGITS', -4),
        (0x1a, 'rel', 'SELLIST', -4),
        (0x1f, 'rel', 'DEVCUR', -4),
        (0x27, 'abs8', 'SELIDX', 0),
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
        (0x25, 'abs8', 'DEVSEL', 0),
        (0x2e, 'abs', '.', 8),
        (0x34, 'abs8', 'DEVNUM', 0),
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
        (0x13, 'abs8', 'F_X', 0),
        (0x15, 'abs', 'MOVIEY', 0),
        (0x1b, 'abs8', 'F_Y', 0),
        (0x1f, 'abs', '.', 344),
        (0x25, 'abs', 'GETMODULE', 0),
        (0x2e, 'abs', '.', 354),
        (0x35, 'abs', 'GETPROC', 0),
        (0x40, 'abs', '.', 371),
        (0x46, 'abs', 'GETMODULE', 0),
        (0x4f, 'abs', '.', 382),
        (0x5e, 'abs', 'GETCLIENT', 0),
        (0xef, 'abs8', 'F_X', 0),
        (0xfa, 'abs8', 'F_Y', 0),
        (0x111, 'abs8', 'F_Y', 0),
        (0x114, 'abs8', 'F_X', 0),
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
        '005253204c65667400525320526967687400315020446561647a6f6e65003250'
        '20446561647a6f6e6500'
    ), (
    ), {
        'dzkeys': 0x52,
    }),
    'PAD_DEVLIST': (bytes.fromhex(
        '0000000000000000000000000000000000000000000000000000000000000000'
    ), (
        (0x0, 'abs', ('PAD_PROFILES', 0), 0),
        (0x4, 'abs', ('PAD_PROFILES', 17), 0),
        (0x8, 'abs', ('PAD_PROFILES', 37), 0),
        (0xc, 'abs', ('PAD_PROFILES', 55), 0),
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
    'PAD_PROFILES': (bytes.fromhex(
        '47616d65706164202858496e70757429005477696e2d737469636b202858496e'
        '70757429004b6579626f617264202853696d706c6529004b6579626f61726420'
        '285265616c2900'
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


def annex_layout(build, blobs=None):
    """name -> offset into the annex, and the annex's padded length."""
    blobs = BLOBS if blobs is None else blobs
    out, at = {}, 0
    for name in build.annex[2]:
        out[name] = at
        length = len(blobs[name][0])
        if name == 'PADX':
            length += len(blobs['LEVERS'][0])    # written straight after it
        at = (at + length + 15) & ~15
    return out, (at + 0x1ff) & ~0x1ff


def cave_va(name, build, blobs=None):
    """Where a blob goes in this build, as a virtual address."""
    blobs = BLOBS if blobs is None else blobs
    if build.annex and name in build.annex[2]:
        return build.annex[0] + annex_layout(build, blobs)[0][name]
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
    if isinstance(value, tuple):
        return symbol_va(value, build, blobs)
    return value


def link(name, build, blobs=None, base=None):
    """A blob's code with its fixups filled in for this build.

    `base` overrides the cave, for a blob whose address is only known at
    apply time; None means the blob must not name itself."""
    blobs = BLOBS if blobs is None else blobs
    code, fixups, _labels = blobs[name]
    if base is None and (name in build.caves
                         or (build.annex and name in build.annex[2])):
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
        if kind == 'abs8':
            struct.pack_into('<b', out, at, value)
        else:
            struct.pack_into('<I', out, at, value & 0xffffffff)
    return bytes(out)


class Sym(str):
    """A hex string the site table writes that depends on the build: a
    blob, a hook's rel32, a symbol's address. It reads as the retail bytes,
    and remembers how it was made so features() can remake it for another
    build. Concatenating two keeps both halves' recipes."""
    def __new__(cls, parts, build=None):
        flat = []
        for p in parts:
            flat.extend(p.parts if isinstance(p, Sym) else [p])
        text = ''.join(p(build or RETAIL) if callable(p) else p
                       for p in flat)
        obj = str.__new__(cls, text)
        obj.parts = flat
        return obj

    def for_build(self, build):
        return Sym(self.parts, build)

    def __add__(self, other):
        return Sym([self, other])

    def __radd__(self, other):
        return Sym([other, self])


class At(int):
    """A file offset in the site table that a build decides: where a blob
    goes, or a fixed distance into one."""
    def __new__(cls, sym, build=None):
        va = symbol_va(sym, build or RETAIL)
        obj = int.__new__(cls, (build or RETAIL).off(va))
        obj.sym = sym
        return obj

    def for_build(self, build):
        return At(self.sym, build)


def site_in(off, build):
    """A retail site offset as this build has it."""
    if isinstance(off, At):
        return off.for_build(build)
    if build.sites is not None:
        # a hook that starts a byte or more into its site names that spot
        for back in range(16):
            if off - back in build.sites:
                return build.sites[off - back][0] + back
        raise KeyError(off)
    return off


def call(off, target, pad=0, op='e8'):
    """The hex a site writes to call a symbol: the opcode, the rel32 to the
    target, and `pad` bytes of nop over the rest of what it replaced."""
    def rel32(build):
        at = build.va(site_in(off, build))
        return struct.pack('<i', symbol_va(target, build) - (at + 5)).hex()
    return Sym([op, rel32, '90' * pad])


def jump(off, target, pad=0):
    return call(off, target, pad, op='e9')


def rel(off, target):
    """The four bytes of rel32 alone, for a site that keeps its opcode: the
    distance from the byte after them to the symbol."""
    def rel32(build):
        at = build.va(site_in(off, build))
        return struct.pack('<i', symbol_va(target, build) - (at + 4)).hex()
    return Sym([rel32])


def abs32(target, addend=0):
    """A symbol's address as the four bytes an instruction carries, as hex."""
    return Sym([lambda build: struct.pack(
        '<I', symbol_va(target, build) + addend).hex()])


def rva(target):
    """A symbol's address relative to the image base, as a PE header field."""
    return Sym([lambda build: struct.pack(
        '<I', symbol_va(target, build) - 0x400000).hex()])


def blob(name):
    """A blob as this build writes it."""
    return Sym([lambda build: link(name, build).hex()])


def zeros(name):
    """What a cave holds before the blob goes in."""
    return Sym([lambda build: '00' * len(BLOBS[name][0])])


def site(name):
    """The file offset a blob is written at, from its cave."""
    return At((name, 0))


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
PAD_PROFILES = link('PAD_PROFILES', RETAIL)
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
# and as the site table writes the two halves, for any build with a cave
debugbox_hook = Sym([lambda b: link('DEBUGBOX', b)[:DEBUGBOX_SPLIT].hex()])
debugbox_proc = Sym([lambda b: link('DEBUGBOX', b)[DEBUGBOX_SPLIT + 1:].hex()])

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


def stamp_version(buf, build=RETAIL):
    """Write the version into the titlever blob already sitting in buf.

    Deliberately not a patch site. Every other byte the patcher writes is the
    same for everyone, which is what lets selftest.py compare the whole
    output against one digest; these two dozen change with the tag. So they
    are written here, after apply_selected has run, and selftest checks the
    file without them."""
    text = version_text().encode('ascii') + b'\x00'
    at = site('TITLEVER').for_build(build) + len(TITLEVER_CODE) - TITLEVER_LEN
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
ESCRGAME, ESCRGAME_SIZE, ESCRGAME_MD5 = RETAIL.art


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
         (site('TITLEVER'), zeros('TITLEVER'), blob('TITLEVER'))]),

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
          '55' + call(0x0014dc43, ('MOVIE', 'movie_place')) + '83c404'
          + '90' * 21),
         # movie.asm, in the .rsrc padding past VirtualSize - after the
         # four bytes of it the frame rate patch's F5 labels use
         (site('MOVIE'), zeros('MOVIE'), blob('MOVIE')),
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
         (site('CREDITS'), zeros('CREDITS'), blob('CREDITS')),
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
         (site('NAMEENTRY'), zeros('NAMEENTRY'), blob('NAMEENTRY')),
         # HOLD TO SKIP over the credits, drawn through GDI so it does
         # not scroll with the tilemap. The call five bytes before the
         # surface is flipped is made in the stub instead, which is
         # what puts the text on the frame about to be shown.
         (0x001c58e7, 'e8f31b0000', call(0x001c58e7, ('OVERLAY', 'overlay'))),
         (site('OVERLAY'), zeros('OVERLAY'), blob('OVERLAY'))]),


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
         (site('TIMER'), zeros('TIMER'), blob('TIMER')),
         (0x000000a8, '30791e00', rva(('TIMER', 0))),
         (0x000273c1, '833d0843be0003', '833d' + abs32('SPEEDSEL') + '02'),
         (0x000275d3, 'c7050843be0003000000',
          'c705' + abs32('SPEEDSEL') + '02000000'),
         (0x000275e2, 'c7050843be0002000000',
          'c705' + abs32('SPEEDSEL') + '01000000'),
         (0x006035ac, '2c040000', '30040000'),
         (0x0060c064, F5_STOCK.hex(), F5_FPS.hex()),
         (0x0010afbe, 'c705d0846c0003000000',
          'c705' + abs32('FRAMEDIV') + '01000000'),
         (0x0010afeb, 'c705d0846c0003000000',
          'c705' + abs32('FRAMEDIV') + '01000000'),
         (0x0010b002, 'c705d0846c0003000000',
          'c705' + abs32('FRAMEDIV') + '01000000'),
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
         (site('DEBUGBOX'), '00' * DEBUGBOX_SPLIT, debugbox_hook),
         # the pause-and-resume wrapper the hook runs the dialog through,
         # matching the built-in F-key dialogs; see asm/f11pause.asm
         (site('F11PAUSE'), zeros('F11PAUSE'), blob('F11PAUSE')),
         (At(('DEBUGBOX', 'dlgproc')), '00' * len(DEBUGBOX_PROC), debugbox_proc),
         (site('EXTRAS_DATA'), zeros('EXTRAS_DATA'), blob('EXTRAS_DATA'))]),
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
         (site('BINDLIST'), zeros('BINDLIST'), blob('BINDLIST')),
         (site('BINDMAP'), zeros('BINDMAP'), blob('BINDMAP')),
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
         (site('BINDBLOCK'), zeros('BINDBLOCK'), blob('BINDBLOCK')),
         (site('INISAVE'), zeros('INISAVE'), blob('INISAVE')),
         (site('INILOAD'), zeros('INILOAD'), blob('INILOAD')),
         (site('BLOCKCUR'), zeros('BLOCKCUR'), blob('BLOCKCUR')),
         (site('INIPARSE'), zeros('INIPARSE'), blob('INIPARSE')),
         (site('PAGESEC'), zeros('PAGESEC'), blob('PAGESEC')),
         (site('PAGESEL'), zeros('PAGESEL'), blob('PAGESEL')),
         (site('COMMITDEV'), zeros('COMMITDEV'), blob('COMMITDEV')),
         (site('INIALL'), zeros('INIALL'), blob('INIALL')),
         (site('DEVORDER'), zeros('DEVORDER'), blob('DEVORDER')),
         (site('PAD_INIKEYS'), zeros('PAD_INIKEYS'), blob('PAD_INIKEYS')),
         # window title, shared by both pages now
         (0x0026c88c, '4b6579626f617264206f6e6c79202853696d706c652074797065202d2025645020736964652900',
                      '42696e64696e6773202d20256450207369646500000000000000000000000000000000000000'
                      '00'),
         # the gamepad's default binds, 1P and 2P, twelve slots of stride
         # 2. Keyboard (Simple)'s shipped set moves to PAD_SIMPLEDEF.
         (0x0026c400, '11001f001e002000100012002e0022002d0013002f002100', 'e800e900ea00eb00ee00ef00e600e500e400e000e200e700'),
         (0x0026c418, 'c700cf00d300d100d200c900520051004f004c0053005000', 'e800e900ea00eb00ee00ef00e600e500e400e000e200e700'),
         (site('PAD_SIMPLEDEF'), zeros('PAD_SIMPLEDEF'), blob('PAD_SIMPLEDEF')),
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
          blob('PAD_DEVLIST')),
         # profile switch, both players: slot 2 gets the twin-stick stubs
         # and slot 3 the stubs Keyboard (Simple) always had, back from
         # slot 1. Slot 1 is the gamepad, repointed above; slot 0 is the
         # game's own keyboard handler and is left alone.
         (0x000422ac, '5a2e4400', abs32(('TWIN', 'stub1p'))),
         (0x001bc13f, 'edcc5b00', abs32(('TWIN', 'stub2p'))),
         (0x000422b0, '6b2e4400', abs32('SIMPLESTUB1')),
         (0x001bc143, 'fecc5b00', abs32('SIMPLESTUB2')),
         # The keyboard profile shared its twenty-four bind slots with
         # Simple, and the gamepad took those. Move it to the block owned by
         # the hidden Joystick + Keyboard profile, whose "Joy+Key Assign"
         # v_on.ini line keeps it persistent.
         (0x00095e46, '83c008', '83c020'),
         (0x00095ec7, '4068bf00', abs32('BLOCKS', 0x20)),
         (0x00096d37, '83c008', '83c020'),
         (0x00096d61, '83c008', '83c020'),
         (0x00096f19, '83c008', '83c020'),
         (0x00096c0a, '4068bf00', abs32('BLOCKS', 0x20)),
         (0x00096c40, '4068bf00', abs32('BLOCKS', 0x20)),
         (0x00096c6d, '4068bf00', abs32('BLOCKS', 0x20)),
         (0x00096de8, '4068bf00', abs32('BLOCKS', 0x20)),
         (0x00096e3f, '4068bf00', abs32('BLOCKS', 0x20)),
         (0x00096e9a, '4068bf00', abs32('BLOCKS', 0x20)),
         # 2P could not take a key 1P had bound, even with 1P on a pad and
         # those binds dormant. Gate that on 1P actually being on the
         # keyboard. Entry only; see asm/kbpage.asm for what that misses.
         (0x00096b61, '833dac6bbf00010f8558000000', jump(0x00096b61, ('KBPAGE', 'dupkey'), 8)),
         # and Default passed a hardcoded player 0, so on the 2P side it
         # reset 1P's binds. The other two pages pass ds:0xbf6bac here.
         (0x00096c8e, '6a016a00e87800000083c408', jump(0x00096c8e, ('KBPAGE', 'default_button'), 7)),
         (site('KBPAGE'), zeros('KBPAGE'), blob('KBPAGE')),
         # Startup defaults every block in a fixed order, and Joystick +
         # Keyboard writes that block after the keyboard profile does. It is
         # hidden, so drop its call; the pushes around it stay balanced.
         (0x00094ea0, 'e8592b0000', '9090909090'),
         # The call after it filled +0x38 with 2 Joysticks defaults; that
         # block is Keyboard (Simple)'s now, so it goes to the writer at
         # the end of asm/bindmap.asm instead.
         (0x00094eaf, 'ca330000', rel(0x00094eaf, ('BINDMAP', 'simple_defaults'))),
         # Startup validates the device saved in v_on.ini through a table
         # at 0x495e0f indexed by device - 2, so this entry is device 4,
         # a legacy joystick profile. It is hidden and unreachable, and
         # this only spares it a check it would fail.
         (0x00095217, '415d4900', abs32('JOYCHECKOK')),
         # and this one is device 3, Keyboard (Simple) now: a keyboard must
         # not fail the joystick-presence check, or the saved device resets
         # at every launch.
         (0x00095213, '185d4900', abs32('JOYCHECKOK')),
         # The device page's OK handler counts the joysticks enumerated at
         # startup and spends one per selection, refusing the page if a
         # counter goes negative. Twin-stick reads the pad through XInput
         # and spends nothing, so send its case straight to that check,
         # where the keyboard and gamepad selections already arrive.
         (0x00096731, 'e0724900', abs32('SPENDNONE')),
         # and device 3, Keyboard (Simple), spends nothing either
         (0x00096735, '06734900', abs32('SPENDNONE')),
         # F7 page table: twin-stick binds nothing, so it takes the case
         # that opens no dialog and reports success.
         (0x00095bdc, '28674900', abs32('NODIALOG')),
         # and slot 3 opens the twelve-bind page the gamepad shares
         (0x00095be0, 'a9674900', abs32('BINDPAGE12')),
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
         (site('TWIN'), zeros('TWIN'), blob('TWIN')),
         # the intro movie blocks the message loop in GetMessageA, where the
         # pump stub does not run. Poll from the call itself instead, so a
         # pad press reaches the window procedure and Space skips the movie.
         (site('INTROWAIT'), zeros('INTROWAIT'), blob('INTROWAIT')),
         (0x001c52ac, 'ff158cd56503', call(0x001c52ac, ('INTROWAIT', 'introwait'), 1)),
         # what each pad input is and what it is called
         (site('PAD_COND'), zeros('PAD_COND'), blob('PAD_COND')),
         (site('PAD_BINDS'), zeros('PAD_BINDS'), blob('PAD_BINDS')),
         (site('PAD_NAMES'), zeros('PAD_NAMES'), blob('PAD_NAMES')),
         (site('PAD_PROFILES'), zeros('PAD_PROFILES'), blob('PAD_PROFILES')),
         # The win and lose screens read the camera key, not the accept
         # key, which is why Select skips them and A does not. The tick
         # calls this to write the camera slot for A as well, on those
         # screens only. See asm/camskip.asm.
         (site('CAMSKIP'), zeros('CAMSKIP'), blob('CAMSKIP')),
         # the routine itself: entry stubs, pump stub, tick, blocks
         (site('PADX'), zeros('PADX'), blob('PADX')),
         # the tick ORs every active input together, but the game's
         # gestures are exclusive lever positions, so a held direction
         # contaminates jump and guard. Strip it back off at the end,
         # and only when a pad was actually read, so the keyboard path
         # is left exactly as it was.
         (At(('PADX', 'epilogue')), '5f5e5bc9c3',
          jump(At(('PADX', 'epilogue')), ('LEVERS', 0))),
         (site('LEVERS'), zeros('LEVERS'), blob('LEVERS')),
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

def features(build):
    """The site table as this build needs it: each site at its own offset,
    expecting its own original bytes, writing what its symbols resolve to
    here. A site at a cave the build does not have is left out - the patch
    that owns it puts the blob somewhere else at apply time."""
    if build is RETAIL:
        return FEATURES
    out = []
    for key, label, tip, sites in FEATURES:
        rows = []
        for off, orig, new in sites:
            if isinstance(off, At):
                try:
                    off = off.for_build(build)
                except KeyError:
                    continue
                if isinstance(orig, Sym):
                    orig = orig.for_build(build)
            else:
                if off not in build.sites:
                    raise KeyError('site 0x%08x has no place in build %s'
                                   % (off, build.md5))
                off, orig = build.sites[off]
            if isinstance(new, Sym):
                new = new.for_build(build)
            rows.append((off, orig, new))
        out.append((key, label, tip, rows))
    return out


def by_key(build):
    table = {key: (label, tip, sites)
             for key, label, tip, sites in features(build)}
    table['dinput'] = BY_KEY['dinput']
    return table


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
# The patches with code in .rdata, per build: ticking any of them sets the
# flag.
RETAIL.rdata_exec = ('padxinput', 'movie', 'credits')
JAPAN.rdata_exec = ()                  # nothing of ours is in its .rdata

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


def _check_table(build=RETAIL):
    """Fail at import, not half way through somebody's executable.

    Three things go wrong silently otherwise: a length mismatch patches the
    wrong bytes, an offset past the end of the file patches nothing, and two
    features writing the same byte make the result depend on the tick boxes.

    Sites inside one feature *may* overlap - the XInput routine is written
    whole and then has its epilogue rewritten - but only where the later site
    expects exactly what the earlier one left there. Anything else means the
    list has been reordered and the patch would fail against a real file."""
    table = by_key(build)
    if set(table) != set(ESSENTIAL) | set(EXTRA) | set(ABOUT):
        raise AssertionError('patch list and display order disagree')
    if apply_order()[-1] != 'nodisc':
        raise AssertionError('nodisc must be applied last')

    # RDATA_EXEC is not in any feature's list, so seed it here or the four
    # bytes it writes are the one place two patches could collide unnoticed.
    owner = dict.fromkeys(range(RDATA_EXEC[0],
                                RDATA_EXEC[0] + len(RDATA_EXEC[1]) // 2),
                          'the .rdata executable flag')
    for key in table:
        written = {}
        for off, old, new in table[key][2] or ():
            if len(old) != len(new):
                raise AssertionError('%s at 0x%08x: %d bytes replaced by %d'
                                     % (key, off, len(old) // 2,
                                        len(new) // 2))
            old_b, new_b = bytes.fromhex(old), bytes.fromhex(new)
            end = build.size
            if build.annex:
                end = build.annex[1] + annex_layout(build)[1]
            if off + len(old_b) > end:
                raise AssertionError('%s at 0x%08x runs %d bytes past the end'
                                     % (key, off, off + len(old_b) - end))
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
    return sum(len(v[2] or ()) for v in table.values()), len(owner)


for _build in BUILDS.values():
    if _build is RETAIL or _build.sites:
        _check_table(_build)


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
            build = BUILDS.get(digest)
            return {
                'size': size, 'md5': digest,
                'supported': build is not None,
                'name': (build.name if build
                         else known[1] if known else 'unrecognised'),
                'why': ('' if build else
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
NETPLAY_SRC_SHA = '78e1bc8fc50bbc1e3c975f1af36c2d9b373dd65ab252595dfc0d74fc9fb55816'
# sha256 of the compiled DLL, so the patcher can tell its own build
# from an older one already installed.
NETPLAY_DLL_SHA = 'b83a8df635f4177ba09c993b1ebfb4c2b36f10352870158342c5327515f77b6c'
NETPLAY_DLL_Z = (
    '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'
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
SYNC_SITES = {                                           # divisor, continuefix
    RETAIL.md5: ((0x0010afc4, 0x01), (0x00077f5a, 0x90)),
    JAPAN.md5: ((0x0010774a, 0x01), (0x00076b0a, 0x90)),
}


def netplay_sync_ready(gamedir):
    """True if v_on.exe here has both simulation-affecting patches, False if
    either is missing, None if the exe cannot be read (so: do not warn)."""
    try:
        with open(os.path.join(gamedir, 'v_on.exe'), 'rb') as fh:
            data = fh.read()
    except OSError:
        return None
    for sites in SYNC_SITES.values():
        if all(off < len(data) and data[off] == patched
               for off, patched in sites):
            return True
    return False


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


def apply_extras_template(buf, build=RETAIL):
    """Append the template-and-annex section, point f11pause at the
    template and the dialog procedure's call at the annex."""
    pe = _PE(buf)
    gap = (-len(EXTRAS_TPL)) % 16
    # linked for this build: it names the build's IAT slots and scratch
    voxt = link('VOXT', build)
    rva = pe.add_section('.voxt', EXTRAS_TPL + b'\0' * gap + voxt,
                         chars=0x60000040)

    f11pause_at = build.off(cave_va('F11PAUSE', build))
    f11pause = link('F11PAUSE', build)
    pattern = struct.pack('<I', MAGIC_TEMPLATE)
    if f11pause.count(pattern) != 1:
        raise ValueError('the TEMPLATE placeholder should appear exactly '
                         'once in the f11pause blob')
    at = f11pause_at + f11pause.index(pattern)
    if pe.d[at:at + 4] != pattern:
        raise ValueError('the TEMPLATE placeholder is not where the '
                         'f11pause site put it')
    struct.pack_into('<I', pe.d, at, pe.base + rva)

    dbgproc_va = cave_va('DEBUGBOX', build) + DEBUGBOX_SPLIT + 1
    proc = link('DEBUGBOX', build)[DEBUGBOX_SPLIT + 1:]
    pattern = struct.pack('<I', MAGIC_ANNEXREL)
    if proc.count(pattern) != 1:
        raise ValueError('the ANNEXREL placeholder should appear exactly '
                         'once in the dialog procedure blob')
    idx = proc.index(pattern)
    at = build.off(dbgproc_va) + idx
    if pe.d[at:at + 4] != pattern:
        raise ValueError('the ANNEXREL placeholder is not where the '
                         'dialog procedure site put it')
    annex = pe.base + rva + len(EXTRAS_TPL) + gap
    struct.pack_into('<i', pe.d, at, annex - (dbgproc_va + idx + 4))
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

    def next_rva(self):
        """Where add_section will put the next section."""
        last = max(self.sections, key=lambda x: x['vaddr'])
        unit = self.salign
        return (last['vaddr'] + last['vsize'] + unit - 1) // unit * unit

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


def apply_annex(buf, build):
    """Append the build's annex, empty: the site table writes the blobs into
    it like any cave. It has to land where the tables say, which the file's
    own headers guarantee for the first section appended."""
    va, raw, _names = build.annex
    _layout, length = annex_layout(build)
    pe = _PE(buf)
    if len(pe.d) != raw or pe.base + pe.next_rva() != va:
        raise ValueError('the annex would not land at 0x%08x' % va)
    pe.add_section('.vojp', b'\0' * length, chars=0xE0000040)
    return pe.d


def apply_selected(buf, wanted, build=RETAIL):
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
    table = by_key(build)
    if build.annex:
        buf = apply_annex(buf, build)
    shared = [key for key in build.rdata_exec if wanted.get(key)]
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
        sites = table[key][2]
        try:
            if sites is not None:
                apply_feature(buf, sites)
            else:
                apply_dinput(buf)
            if key == 'debugbox':        # bytes first, then the template
                buf = apply_extras_template(buf, build)
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
        if os.path.getsize(path) not in [b.size for b in BUILDS.values()]:
            return False
        with open(path, 'rb') as fh:
            return hashlib.md5(fh.read()).hexdigest() in BUILDS
    except OSError:
        return False


def compare_report(size, digest, why, hint, level):
    """What the patcher wants against what it was given.

    Two rows so the difference is visible rather than described: the
    supported build nearest in size, and the file. The short hash is what
    goes on screen; the log gets every build in full, because that is what
    ends up in a bug report."""
    nearest = min(BUILDS.values(), key=lambda b: abs(b.size - size))
    return {
        'rows': [('SUPPORTED', nearest.size, nearest.md5),
                 ('YOURS', size, digest)],
        'why': why,
        'hint': hint,
        'level': level,
        'log': ['supported: %d bytes  %s  (%s)' % (b.size, b.md5, b.name)
                for b in BUILDS.values()]
               + ['yours:     %d bytes  %s' % (size, digest), why, hint],
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
        self.build = RETAIL

    def load(self, path):
        """Return (description, accepted). Raises OSError.

        The old path is dropped first. Keeping it meant a failed read left
        can_restore() answering for the file before this one, so Restore
        original stayed lit and rewrote an executable the window was no
        longer showing."""
        self.exe_path = None
        self.compare = None
        self.build = RETAIL

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
        if digest in BUILDS:
            self.build = BUILDS[digest]
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
            buf, applied, skipped = apply_selected(buf, wanted, self.build)
        except PatchFailed as exc:
            return False, [_note(str(exc)), NOTHING]
        except Exception as exc:             # a bug in here, not a bad file
            return False, ['patch: failed - %s' % exc, NOTHING]
        if 'credits' in applied:
            stamp_version(buf, self.build)
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
                   (os.path.join(folder, self.build.art[0]), False),
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
        path = os.path.join(os.path.dirname(self.exe_path), self.build.art[0])
        if not os.path.exists(path):
            return False, ('%s is missing. XInput gamepad support renames the '
                           'title prompt, which is artwork in that file.'
                           % self.build.art[0])
        try:
            with open(path, 'rb') as fh:
                data = fh.read()
        except OSError as exc:
            return False, 'Could not read %s: %s' % (self.build.art[0], exc)
        if len(data) != self.build.art[1]:
            return False, ('%s is %d bytes, expected %d.'
                           % (self.build.art[0], len(data), self.build.art[1]))
        digest = hashlib.md5(data).hexdigest()
        if self.build.art[2] and digest != self.build.art[2]:
            if os.path.exists(path + '.bak'):
                return True, ''          # ours from a previous run
            return False, ('%s has been modified and there is no %s.bak '
                           'beside it. It holds the title screen artwork, so '
                           'reinstall the game to get the original back. '
                           '(MD5 %s, expected %s)'
                           % (self.build.art[0], self.build.art[0], digest,
                              self.build.art[2]))
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
        path = os.path.join(os.path.dirname(self.exe_path), self.build.art[0])
        try:
            with open(path, 'rb') as fh:
                data = bytearray(fh.read())
        except OSError as exc:
            log.append('patch: could not read %s - %s' % (self.build.art[0], exc))
            return
        if len(data) != self.build.art[1]:
            log.append('patch: %s is %d bytes, expected %d - left alone'
                       % (self.build.art[0], len(data), self.build.art[1]))
            return
        if not self._backup(path, log):
            log.append('patch: %s left alone' % self.build.art[0])
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
            log.append('patch: could not write %s - %s' % (self.build.art[0], exc))
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
               'Manual\tWhich readme and help file is copied; the game '
               'itself is the same on every pressing of a release.')

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
                    'a build the patcher has tables for.',
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
    lines = ['vo_patch.py %s' % VERSION]
    for build in BUILDS.values():
        sites, byte_count = _check_table(build)
        lines.append('%s: %d bytes, MD5 %s; %d patches, %d sites, %d bytes '
                     'of the executable touched'
                     % (build.name, build.size, build.md5, len(BY_KEY),
                        sites, byte_count))
    lines += [
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
              'It installs, but the patcher has no tables for it.'
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
