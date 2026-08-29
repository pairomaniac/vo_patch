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
    def __init__(self, name, short, md5, size, sections, caves, symbols, art,
                 sites=None, annex=None):
        # the name on screen, and a word for a label or a log line
        self.name, self.short = name, short
        self.md5, self.size = md5, size
        # (file offset, virtual address) of each section, in file order
        self.sections = sections
        self.caves, self.symbols = caves, symbols
        # The blobs live in a section appended before any patch is written,
        # in this order, 16-aligned. Where it lands is fixed by the file's
        # own headers - the first appended section can only go in one
        # place - so they link at import: (virtual address, file offset,
        # names). The two caves are the places the game itself reaches.
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


# Every blob but the two the game reaches itself, in the order a build's
# annex lays them out. A build other than retail keeps them all there.
ANNEX_BLOBS = (
    'TIMER', 'DEBUGBOX', 'PADX', 'TWIN', 'INTROWAIT', 'KBPAGE',
    'BINDLIST', 'BINDMAP', 'BINDBLOCK', 'INISAVE', 'INILOAD', 'BLOCKCUR',
    'INIPARSE', 'PAGESEC', 'PAGESEL', 'COMMITDEV', 'INIALL', 'DEVORDER',
    'F11PAUSE', 'MOVIE', 'CREDITS', 'NAMEENTRY', 'CAMSKIP', 'OVERLAY',
    'TITLEVER', 'PAD_COND', 'PAD_BINDS', 'PAD_NAMES', 'PAD_PROFILES',
    'PAD_SIMPLEDEF', 'PAD_INIKEYS', 'EXTRAS_DATA', 'ACTIVATE')

RETAIL = Build('English retail', 'retail', ORIGINAL_MD5, EXE_SIZE, sections=(
    (0x00000400, 0x00401000),       # .text
    (0x001f4400, 0x005f5000),       # .rdata
    (0x0023de00, 0x0063f000),       # .data
    (0x00601a00, 0x0365d000),       # .idata
    (0x00602c00, 0x0365f000),       # .rsrc
    (0x0060c400, 0x03669000),       # .reloc
), caves={
    # Two places the game itself looks: the F7 device list's own run in
    # .data, and the levers tail inside the XInput routine. Everything else
    # is in the annex, a section appended before any patch is written.
    'PAD_DEVLIST': 0x0066d418,
    'LEVERS': ('PADX', 'end'),
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
    'GRESUME': 0x005c680b,
    'PENDING': 0x0365cb9c,         # a recreate owed
    'RETADDR': 0x0365cba0,         # where one returns to
    'RECREATE': 0x005c56a2,        # release and create the surfaces
    'IDLE': 0x005c63aa,            # the loop's pass while inactive
    'INACTIVE': 0x01add128,        # the flag it idles on
    'SETACTIVE': 0x005c6326,       # (pause): the loop stops on 1, runs on 0
    'FSFLAGS': 0x006bf598,         # bit 2: the low-resolution modes
    'FSMODE': 0x006bf560,          # and 320x240 among them
    'HAVESURF': 0x006bf570,        # the game's "surfaces exist" flag
    'ISICONIC': 0x0365d594,        # IAT: IsIconic
    'LOCKBACK': 0x005c8108,        # the frame's back buffer lock, its result test
    'FLIPBACK': 0x005c6510,        # and its flip of the primary, the same         # and their resume
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
}, art=('escrgame.bin', 4194304, 'f0c2b33c6d32e8e25cee840a0de65dc0'),
    annex=ANNEX_BLOBS)


# The Japanese rerelease: the same source through the same toolchain four
# months on, with every address moved. See docs/NOTES.md, and tools/vomap.py
# for how the addresses were found.
JAPAN_MD5 = 'd19320bdc3381a48228990907910a391'
JAPAN_SIZE = 6621696
JAPAN = Build('Japanese rerelease', 'jp', JAPAN_MD5, JAPAN_SIZE, sections=(
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
    'PENDING': 0x036577fc,
    'RETADDR': 0x03657800,
    'RECREATE': 0x005bff42,
    'IDLE': 0x005c0c79,
    'INACTIVE': 0x01ad7db0,
    'SETACTIVE': 0x005c0bf5,
    'FSFLAGS': 0x006bb2b0,
    'FSMODE': 0x006bb278,
    'HAVESURF': 0x006bb288,
    'ISICONIC': 0x036585a4,
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
}, art=('jscrgame.bin', 4194304, '1d892aeb30bb517f57e7d289ed2f4389'),
   sites=None, annex=ANNEX_BLOBS)

# The USA OEM pressing: the same toolchain a month before retail, and the
# closest of the builds to it - 7852 of 7934 functions match, 7593
# identically, every frame is laid out the same. It has no vendor check
# to remove: its processor check is an MMX test through cpuid32.dll.
OEM_MD5 = '4c70f780a7f0d98d74be62304fb99021'
OEM_SIZE = 6649344
OEM = Build('USA OEM', 'oem', OEM_MD5, OEM_SIZE, sections=(
    (0x00000400, 0x00401000),       # .text
    (0x001f3e00, 0x005f5000),       # .rdata
    (0x0023d800, 0x0063f000),       # .data
    (0x00601400, 0x0365d000),       # .idata
    (0x00602600, 0x0365f000),       # .rsrc
    (0x0060be00, 0x03669000),       # .reloc
), caves={
    'PAD_DEVLIST': 0x0066d3d4,      # the F7 device list's own run, in .data
    'LEVERS': ('PADX', 'end'),
}, symbols={
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
    'FILLIDX': -0x8,
    'STOREIDX': -0x14,
    'SELIDX': -0xc,
    'DEVSEL': -0xc,
    'DEVNUM': -0x14,
    'SAVEPLAYER': -0xc8,
    'SAVELINE': -0xcc,
    'F_X': -0xc,
    'F_Y': -0x10,
    'GAMEPADDEF': 0x0066d5f8,   # data 1 votes
    'SPEEDSEL': 0x00be42c8,
    'FRAMEDIV': 0x006c8468,
    'SIMPLESTUB1': 0x00442db0,   # func
    'SIMPLESTUB2': 0x005bc7b3,   # func
    'JOYCHECKOK': 0x00495cc3,   # insn
    'SPENDNONE': 0x004971e8,   # insn
    'NODIALOG': 0x00496648,   # func
    'BINDPAGE12': 0x00496588,   # func
    'EXIT1P': 0x00442e24,   # insn
    'KBD1P': 0x00442fd4,   # func
    'KBHANDLER1': 0x00442fd4,   # func
    'CASEB': 0x004969c2,   # func
    'CROSSCHECK': 0x0049760c,   # insn
    'KBACCEPT': 0x00497664,
    'RESUME': 0x00497737,   # insn
    'DEFAULTS': 0x004977ac,   # func
    'DIGITLOOP': 0x00497b0d,   # insn
    'LISTLOOP': 0x00497b4d,   # insn
    'FILLDONE': 0x00497b94,   # insn
    'STORESHIFT': 0x00497d0f,   # insn
    'STORELIST': 0x00497d34,   # insn
    'SELDIGITS': 0x00497ef4,   # insn
    'SELLIST': 0x00497f38,   # insn
    'MAPDONE': 0x00497f74,   # insn
    'SELSET': 0x00497f74,   # insn
    'CURSOR': 0x004cd763,   # func
    'PRINT': 0x004ced8b,   # func
    'WRITELINE': 0x005b1303,   # func
    'FINDLINE': 0x005b1341,   # func
    'EXIT2P': 0x005bc827,   # insn
    'KBD2P': 0x005bc9bd,   # func
    'KBHANDLER2': 0x005bc9bd,   # func
    'GPAUSE': 0x005c62de,   # func
    'GRESUME': 0x005c6324,   # func
    'PENDING': 0x0365cb1c,
    'RETADDR': 0x0365cb20,
    'RECREATE': 0x005c5172,
    'IDLE': 0x005c5ec3,
    'INACTIVE': 0x01add0c0,
    'SETACTIVE': 0x005c5e3f,
    'FSFLAGS': 0x006bf530,
    'FSMODE': 0x006bf4f8,
    'HAVESURF': 0x006bf508,
    'ISICONIC': 0x0365d5c8,
    'ORIGWNDPROC': 0x005c6370,   # func
    'ORIG': 0x005c7bf8,   # func
    'DRAW': 0x005c9454,   # func
    'MEMCPY': 0x005e5b70,   # func
    'ORIGENTRY': 0x005e7470,   # func
    'CDMUTE': 0x0063f430,
    'NOSHOT': 0x00652fd0,
    'MASK1A': 0x00653688,
    'MASK1B': 0x00653695,
    'KEYLIST': 0x0066d430,
    'SEMUTE': 0x006bcbe4,
    'MASK2A': 0x006beaa0,
    'MASK2B': 0x006beaad,
    'HALF': 0x006bf4f8,
    'WIDE': 0x006bf530,
    'PREV': 0x006c3ce0,
    'HELD': 0x006c3ce1,
    'CAMERA1': 0x00bf0417,
    'ACCEPT1': 0x00bf0441,   # data 1 votes
    'BLOCKS': 0x00bf67f8,
    'CURPLAYER': 0x00bf6b6c,
    'PHASE': 0x01ad08fc,
    'CAM2': 0x01ad0d2c,
    'CAMERA2': 0x01ad0d2c,
    'ACC2': 0x01ad0d49,   # data 1 votes
    'ACCEPT2': 0x01ad0d49,   # data 1 votes
    'FLAG': 0x01ae1bac,
    'MODE': 0x01ae3524,
    'SUBMODE': 0x01ae3620,
    'MOVIEX': 0x01ae5ec4,
    'MOVIEY': 0x01ae5ec8,
    'PRIMARY': 0x01ae5ed0,
    'HWND': 0x01ae5ee8,
    'BACK': 0x01ae5eec,
    'LEV1A': 0x01cb1454,
    'LEV1B': 0x01cb1456,
    'EDGEA': 0x01ed5e55,
    'EDGEB': 0x01ed5e56,
    'LEV2A': 0x01ee3e74,
    'LEV2B': 0x01ee3e76,
    'MOVIEHWND': 0x01ef8858,
    'MOVIEDEV': 0x01ef8880,
    'LIVE': 0x03651400,
    'BINDS1': 0x03651400,
    'BINDS2': 0x03651418,   # data 1 votes
    'DEVICES': 0x036514d0,
    'XIFN': 0x0365cac0,
    'STATE': 0x0365cac4,
    'BTN': 0x0365cac8,
    'SCR1': 0x0365cae0,
    'SCR2': 0x0365cae1,
    'PSTATE': 0x0365caf0,
    'PBTN': 0x0365caf4,
    'SLEEPFN': 0x0365cb00,
    'PADPREV': 0x0365cb04,
    'DZTHR1': 0x0365cb0c,
    'DZSTR1': 0x0365cb14,
    'GETMODULE': 0x0365d4c8,   # iat GetModuleHandleA
    'LOADLIB': 0x0365d544,   # iat LoadLibraryA
    'GETPROC': 0x0365d548,   # iat GetProcAddress
    'SENDMSG': 0x0365d560,   # iat SendMessageA
    'ENDDIALOG': 0x0365d574,   # iat EndDialog
    'CHECKDLGBTN': 0x0365d580,   # iat CheckDlgButton
    'GETDLGITEM': 0x0365d57c,   # iat GetDlgItem
    'POSTMSG': 0x0365d5a0,   # iat PostMessageA
    'GETMSG': 0x0365d5c0,   # iat GetMessageA
    'PEEKMSG': 0x0365d5c4,   # iat PeekMessageA
    'GETCLIENT': 0x0365d608,   # iat GetClientRect
    'MOVEWINDOW': 0x0365d614,   # iat MoveWindow
    'MCISEND': 0x0365d67c,   # iat mciSendCommandA
}, art=RETAIL.art, sites=None, annex=ANNEX_BLOBS)   # retail's art, byte for byte

BUILDS = {RETAIL.md5: RETAIL, JAPAN.md5: JAPAN, OEM.md5: OEM}

# GENERATED by tools/buildsites.py - do not edit by hand.
#
# For each build but retail: where every site the table names by retail
# offset is in that build, and what it holds there; None for a site the
# build has no code for. Sites at a blob's cave or in the annex are not
# here; those come from the build's tables.

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
    0x001b0920: (0x001ab5dc, '0f850f000000'),
    0x001c4aa2: (0x001bf342, '558bec81ece0000000'),
    0x001c5726: (0x001bfff5, '558bec5356'),
    0x001c5412: (0x001bfce1, 'e893030000'),
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

# SITES OEM BEGIN
OEM.sites = {
    0x002bba60: (0x002bb3f8, '0f'),
    0x00189546: (0x00189016, '2256'),
    0x00189552: (0x00189022, '88580100'),
    0x00058189: (0x000580e9, '01'),
    0x00170dc9: (0x00170899, '01'),
    0x001fcec8: (0x001fc8c8, '80340200'),
    0x001fcecc: (0x001fc8cc, '48240000'),
    0x002bbb54: (0x002bb4ec,
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
    0x001c5900: (0x001c5419, 'a1d05eae01'),
    0x0014dc42: (0x0014d7b2,
        'c745f400000000c745f028000000a1c45eae018945f4a1c85eae018945f0'
    ),
    0x002c7678: (0x002c7034, '4e'),
    0x0000023f: (0x0000023f, '40'),
    0x0018fc25: (0x0018f6f5, 'c705ac1bae0100000000'),
    0x000d60c8: (0x000d5f68,
        'f605555eed01010f850d000000f605565eed01010f84f2010000'
    ),
    0x001c58e7: (0x001c5400, 'e8f31b0000'),
    0x0010acd7: (0x0010a847, '00000000'),
    0x0010b088: (0x0010abf8, '01000000'),
    0x0010b131: (0x0010aca1, 'c705c017680000000000e950000000'),
    0x0010b1b0: (0x0010ad20, '00000000'),
    0x0010b1ba: (0x0010ad2a, '00000000'),
    0x0010b1c4: (0x0010ad34, '00000000'),
    0x001c76d4: (0x001c71ed, '0f840a000000'),
    0x00107930: None,  # nocpucheck
    0x001b0920: (0x001b03f0, '0f850f000000'),
    0x001c4aa2: (0x001c4572, '558bec81ece0000000'),
    0x001c5726: (0x001c523f, '558bec5356'),
    0x001c5412: (0x001c4ee1, 'e8dd030000'),
    0x000000a8: (0x000000a8, '70741e00'),
    0x000273c1: (0x00027321, '833dc842be0003'),
    0x000275d3: (0x00027533, 'c705c842be0003000000'),
    0x000275e2: (0x00027542, 'c705c842be0002000000'),
    0x006035ac: (0x00602fac, '2c040000'),
    0x0060c064: (0x0060ba64,
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
    0x0010afbe: (0x0010ab2e, 'c70568846c0003000000'),
    0x0010afeb: (0x0010ab5b, 'c70568846c0003000000'),
    0x0010b002: (0x0010ab72, 'c70568846c0003000000'),
    0x001c6941: (0x001c645a, 'c70568846c0002000000'),
    0x001c6950: (0x001c6469, 'c70568846c0003000000'),
    0x001c6d8c: (0x001c68a5, 'c70568846c0002000000'),
    0x001c6d9b: (0x001c68b4, 'c70568846c0003000000'),
    0x001c6dfc: (0x001c6915, 'c70568846c0002000000'),
    0x001c6e0b: (0x001c6924, 'c70568846c0003000000'),
    0x001c8bc4: (0x001c86fc, 'c70568846c0002000000'),
    0x001c8bd3: (0x001c870b, 'c70568846c0003000000'),
    0x001c4d42: (0x001c4812, '0f850c000000'),
    0x001c4d4b: (0x001c481b, '65000000'),
    0x001c4d7e: (0x001c484e, '70635c00'),
    0x00077f5a: (0x00077e5a,
        '8b45fcd94008d9e083ec04d91c248b45fc8b4004508b45fcd900d9e083ec04d9'
        '1c24e83f64f9ff83c40c'
    ),
    0x00078b1c: (0x00078a1c,
        '8b45fcd94008d9e083ec04d91c248b45fc8b4004508b45fcd900d9e083ec04d9'
        '1c24e87d58f9ff83c40c'
    ),
    0x00079bb6: (0x00079ab6,
        '8b45fcd94008d9e083ec04d91c248b45fc8b4004508b45fcd900d9e083ec04d9'
        '1c24e8e347f9ff83c40c'
    ),
    0x00079f3f: (0x00079e3f,
        '8b45fcd94008d9e083ec04d91c248b45fc8b4004508b45fcd900d9e083ec04d9'
        '1c24e85a44f9ff83c40c'
    ),
    0x0007d04a: (0x0007cf4a,
        '8b45fcd94008d9e083ec04d91c248b45fc8b4004508b45fcd900d9e083ec04d9'
        '1c24e84f13f9ff83c40c'
    ),
    0x000bb9ea: (0x000bb88a,
        '8b45fcd94008d9e083ec04d91c248b45fc8b4004508b45fcd900d9e083ec04d9'
        '1c24e86fc2f4ff83c40c'
    ),
    0x000bc5ac: (0x000bc44c,
        '8b45fcd94008d9e083ec04d91c248b45fc8b4004508b45fcd900d9e083ec04d9'
        '1c24e8adb6f4ff83c40c'
    ),
    0x000bd646: (0x000bd4e6,
        '8b45fcd94008d9e083ec04d91c248b45fc8b4004508b45fcd900d9e083ec04d9'
        '1c24e813a6f4ff83c40c'
    ),
    0x000bd9cf: (0x000bd86f,
        '8b45fcd94008d9e083ec04d91c248b45fc8b4004508b45fcd900d9e083ec04d9'
        '1c24e88aa2f4ff83c40c'
    ),
    0x000c0ada: (0x000c097a,
        '8b45fcd94008d9e083ec04d91c248b45fc8b4004508b45fcd900d9e083ec04d9'
        '1c24e87f71f4ff83c40c'
    ),
    0x000970bf: (0x00096f5c, '837df8210f8d2e000000'),
    0x000970d5: (0x00096f72, '8b04c530d46600'),
    0x0009729c: (0x00097137, '8a04c534d46600'),
    0x000974ac: (0x00097347, '837df4210f8d23000000'),
    0x000974be: (0x00097359, '390cc534d46600'),
    0x00095f35: (0x00095dd4, '05f867bf0083c008'),
    0x0009724c: (0x000970e7, '05f867bf0083c008'),
    0x0009736d: (0x00097208, '05f867bf0083c008'),
    0x00097355: (0x000971f0, '8d04408d04c5f8d56600'),
    0x00097397: (0x00097232, '05f867bf0083c008'),
    0x00097531: (0x000973cc, '05f867bf0083c008'),
    0x0009740f: (0x000972aa, '8a84410068bf00'),
    0x0026c88c: (0x0026c284,
        '4b6579626f617264206f6e6c79202853696d706c652074797065202d20256450'
        '20736964652900'
    ),
    0x0026c400: (0x0026bdf8,
        '11001f001e002000100012002e0022002d0013002f002100'
    ),
    0x0026c418: (0x0026be10,
        'c700cf00d300d100d200c900520051004f004c0053005000'
    ),
    0x000422a8: (0x00042208, 'b02d4400'),
    0x001bc13b: (0x001bbc0b, 'b3c75b00'),
    0x001c530e: (0x001c4ddd, 'ff15c4d56503'),
    0x000971bd: (0x00097059, '0f8558000000'),
    0x0026c218: (0x0026bc10,
        'e4d36600ccd36600b8d36600acd3660088d3660078d3660060d3660050d36600'
    ),
    0x000422ac: (0x0004220c, 'ba2d4400'),
    0x001bc13f: (0x001bbc0f, 'bdc75b00'),
    0x000422b0: (0x00042210, 'cb2d4400'),
    0x001bc143: (0x001bbc13, 'cec75b00'),
    0x00095e46: (0x00095ce5, '83c008'),
    0x00095ec7: (0x00095d66, '0068bf00'),
    0x00096d37: (0x00096bd4, '83c008'),
    0x00096d61: (0x00096bfe, '83c008'),
    0x00096f19: (0x00096db6, '83c008'),
    0x00096c0a: (0x00096aa7, '0068bf00'),
    0x00096c40: (0x00096add, '0068bf00'),
    0x00096c6d: (0x00096b0a, '0068bf00'),
    0x00096de8: (0x00096c85, '0068bf00'),
    0x00096e3f: (0x00096cdc, '0068bf00'),
    0x00096e9a: (0x00096d37, '0068bf00'),
    0x00096b61: (0x000969ff, '833d6c6bbf00010f8558000000'),
    0x00096c8e: (0x00096b2b, '6a016a00e87800000083c408'),
    0x00094ea0: (0x00094d40, 'e8542b0000'),
    0x00094eaf: (0x00094d4f, 'c5330000'),
    0x00095217: (0x000950b7, 'e15b4900'),
    0x00095213: (0x000950b3, 'b85b4900'),
    0x00096731: (0x000965d0, '7f714900'),
    0x00096735: (0x000965d4, 'a5714900'),
    0x00095bdc: (0x00095a7b, 'c7654900'),
    0x00095be0: (0x00095a7f, '48664900'),
    0x00096253: (0x000960f2, 'c2694900'),
    0x0009625b: (0x000960fa, 'ca6c4900'),
    0x00095604: (0x000954a4, 'e86f2c0000'),
    0x000958a1: (0x00095741, '03'),
    0x000958aa: (0x0009574a, 'e900000000'),
    0x0009522e: (0x000950ce, '03'),
    0x00095248: (0x000950e8, '03'),
    0x0009651a: (0x000963b9, '8b80f867bf00'),
    0x00096784: (0x00096623, '8b45f48b4dec'),
    0x000959f7: (0x00095896, '89048dd0146503'),
    0x0060b34e: (0x0060ad4e, '3100500020007300690064006500'),
    0x0009703f: (0x00096edc, '837df81a0f8d27000000'),
    0x00097257: (0x000970f2, '837dec1a0f8d13000000'),
    0x00097428: (0x000972c3, '837df41a0f8d27000000'),
    0x000974cb: (0x00097366, '8345f424e905000000'),
    0x0009753a: (0x000973d5, 'e896db1400'),
    0x001c52ac: (0x001c4d7b, 'ff15c0d56503'),
    0x00285e04: (0x002857fc, '20505245535320535041434520424152'),
    0x002c7654: (0x002c7010,
        '546f20526573756d652047616d652c20507265737320463300000000'
    ),
    0x00269b60: (0x00269558,
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
# SITES OEM END

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
    'ACTIVATE': (bytes.fromhex(
        '58a30000000068190000005589e581ece0000000e90500000085c0751ec70500'
        '00000001000000c7050000000001000000c7050000000001000000ff25000000'
        '00837c240400751e833d00000000007515c7050000000001000000c705000000'
        '0001000000c35589e55356e901000000e8fcffffff833d00000000007455ff35'
        '00000000ff150000000085c075456a10f60500000000047415833d0000000000'
        '740c68f00000006840010000eb0a68e00100006880020000e8fcffffff83c40c'
        '85c0740fc7050000000000000000e8fcffffffb801000000c3'
    ), (
        (0x2, 'abs', 'RETADDR', 0),
        (0x7, 'abs', '.', 25),
        (0x15, 'rel', 'RECREATE', 5),
        (0x1f, 'abs', 'PENDING', 0),
        (0x29, 'abs', 'INACTIVE', 0),
        (0x33, 'abs', 'HAVESURF', 0),
        (0x3d, 'abs', 'RETADDR', 0),
        (0x4a, 'abs', 'BACK', 0),
        (0x53, 'abs', 'PENDING', 0),
        (0x5d, 'abs', 'INACTIVE', 0),
        (0x6c, 'rel', 'SETACTIVE', 1),
        (0x71, 'rel', 'IDLE', -4),
        (0x77, 'abs', 'PENDING', 0),
        (0x80, 'abs', 'HWND', 0),
        (0x86, 'abs', 'ISICONIC', 0),
        (0x92, 'abs', 'FSFLAGS', 0),
        (0x9b, 'abs', 'FSMODE', 0),
        (0xb9, 'rel', 'RECREATE', -4),
        (0xc6, 'abs', 'PENDING', 0),
        (0xcf, 'rel', 'GRESUME', -4),
    ), {
        'recreate': 0x0,
        'made': 0x19,
        'resume': 0x41,
        'idle': 0x70,
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


# Set by asm/build.py and tools/buildsites.py while they import this module
# to regenerate it: a blob, label or site they are about to write does not
# exist yet, and reads as empty rather than failing the import.
BOOTSTRAP = bool(os.environ.get('VO_PATCH_BOOTSTRAP'))
EMPTY = (b'', (), {})


def blob_of(name, blobs=None):
    blobs = BLOBS if blobs is None else blobs
    if BOOTSTRAP:
        return blobs.get(name, EMPTY)
    return blobs[name]


def annex_layout(build, blobs=None):
    """name -> offset into the annex, and the annex's padded length."""
    out, at = {}, 0
    for name in build.annex[2]:
        out[name] = at
        length = len(blob_of(name, blobs)[0])
        if name == 'PADX':
            length += len(blob_of('LEVERS', blobs)[0])   # written after it
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
    code, _fixups, labels = blob_of(name, blobs)
    if label == 'end':
        return len(code)
    if isinstance(label, int):
        return label
    return labels.get(label, 0) if BOOTSTRAP else labels[label]


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
    code, fixups, _labels = blob_of(name, blobs)
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
    if isinstance(off, In):
        return int(off)
    if build.sites is not None:
        # a hook that starts a byte or more into its site names that spot
        for back in range(16):
            if off - back in build.sites:
                return build.sites[off - back][0] + back
        raise KeyError(off)
    return off


class In(int):
    """A file offset in the site table that one build has and retail has
    not - code the other compile grew. The offset is that build's own, so
    a site written this way needs no entry in its map."""
    def __new__(cls, md5, off):
        obj = int.__new__(cls, off)
        obj.md5 = md5
        return obj


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
    return Sym([lambda build: '00' * len(blob_of(name)[0])])


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
ACTIVATE_CODE = link('ACTIVATE', RETAIL)
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

DEBUGBOX_CODE = link('DEBUGBOX', RETAIL)

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
         (0x00107930, '830dc884bf0001', '90909090909090'),
         # The OEM has no vendor check; its test is a CPU class from the
         # cpuid32.dll it ships, and only two answers pass. Take any, and
         # set the MMX flag it would set for one of them.
         (In(OEM_MD5, 0x001c4b85), '0f8425000000', '90e925000000'),
         (In(OEM_MD5, 0x001c4bb4), '0f850a000000', '909090909090')]),
    ('activate', 'Fix crash on ALT+TAB',
     'Switching away during the intro movie stops it, and the game ends it\n'
     'as stopped - without rebuilding the screen it had handed to the\n'
     'player, and if it tried while still in the background the rebuild\n'
     'came back half done. The exit rebuilds it now, and a rebuild that\n'
     'fails pauses the game until one succeeds.', [
         (site('ACTIVATE'), zeros('ACTIVATE'), blob('ACTIVATE')),
         # the movie's exit: if "stopped by deactivation", clear that and
         # return without recreating the surfaces. jne -> jmp: always try.
         (0x001b0920, '0f850f000000', '90e90f000000'),
         # the recreate's entry: push ebp; mov ebp,esp; sub esp,0xe0
         (0x001c4aa2, '558bec81ece0000000',
          jump(0x001c4aa2, ('ACTIVATE', 'recreate'), 4)),
         # setactive's entry: push ebp; mov ebp,esp; push ebx; push esi
         (0x001c5726, '558bec5356', jump(0x001c5726, ('ACTIVATE', 'resume'))),
         # the loop's idle pass while inactive
         (0x001c5412, 'e893030000', call(0x001c5412, ('ACTIVATE', 'idle')))]),
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
         (site('DEBUGBOX'), zeros('DEBUGBOX'), blob('DEBUGBOX')),
         # the pause-and-resume wrapper the hook runs the dialog through,
         # matching the built-in F-key dialogs; see asm/f11pause.asm
         (site('F11PAUSE'), zeros('F11PAUSE'), blob('F11PAUSE')),
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
    out = []
    for key, label, tip, sites in FEATURES:
        rows = []
        for off, orig, new in sites:
            if isinstance(off, In):
                if off.md5 != build.md5:
                    continue
            elif build is RETAIL:
                pass
            elif isinstance(off, At):
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
                if build.sites[off] is None:
                    continue                # the build has no such code
                off, orig = build.sites[off]
            if isinstance(new, Sym) and build is not RETAIL:
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
BY_KEY = {key: (label, tip, sites)
          for key, label, tip, sites in features(RETAIL)}

# The patches a lockstep match cannot differ on are the frame rate and the
# round-loss fix: both change what the simulation computes. Both are Essential
# and always applied, so this patcher cannot produce a build missing them -
# SYNC_SITES reads them back out of a file an older release may have written
# without, and net/dpctrl.c fingerprints the same two bytes.
BY_KEY['dinput'] = (
    'Fix keyboard input after ALT+TAB',
    'Without this, alt-tabbing away or opening an F-key dialog kills\n'
    'keyboard input until the game is restarted.', None)

# Display order only; see apply_order for the write order. Essential fixes
# what is broken on modern systems, extra is taste. Both start ticked, extra
# running from the biggest change down to the smallest.
ESSENTIAL = ('nocpucheck', 'framerate', 'continuefix', 'dinput', 'activate')
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

    owner = {}
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
        try:
            _check_table(_build)
        except KeyError:
            # A site the build's map has no entry for yet: tools/buildsites.py
            # is about to write one, and imports this module to do it.
            if not os.environ.get('VO_PATCH_BOOTSTRAP'):
                raise


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
            build = BUILDS.get(digest)
            return {
                'size': size, 'md5': digest,
                'supported': build is not None,
                'name': build.name if build else 'unrecognised',
                'why': ('' if build else
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
NETPLAY_SRC_SHA = 'c8a40e0fc5d5b4de73067c5d4c81a9d28c4a477dd0b767375eb7721186f975d1'
# sha256 of the compiled DLL, so the patcher can tell its own build
# from an older one already installed.
NETPLAY_DLL_SHA = 'c39459fde4e5f7430a11dd820afcfe4f398e75b96578c35f4db5be32f20c5de0'
NETPLAY_DLL_Z = (
    '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'
    'wY3hxnL/dfPvPwC9FTx9'
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
# The frame divisor's immediate, six bytes into its site, and the first
# byte of a continuefix site - in every build, through its site map.
SYNC_SITES = {build.md5: ((site_in(0x0010afbe, build) + 6, 0x01),
                          (site_in(0x00077f5a, build), 0x90))
              for build in BUILDS.values()}


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

    dbgproc_va = symbol_va(('DEBUGBOX', 'dlgproc'), build)
    proc = link('DEBUGBOX', build)[label_at('DEBUGBOX', 'dlgproc'):]
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
    own headers guarantee for the first section appended. Always applied,
    so the essentials can rely on it - the timer stub is there."""
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
    buf = apply_annex(buf, build)
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
            self.compare = None
            return READY_TAG, True

        if backup_is_original(path + '.bak'):
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
# What a good disc looks like, in one line: which build, and enough to tell
# a full rip from a data-only one before anything is written.
INSTALL_FOUND = '%s. %d files, %d MB.'
# The copy is the same work whichever build is on the disc, so it runs; only
# the patches need a build with tables, which the card below spells out.
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
READY = 'READY - %s. %d patches selected. Press Apply patches.'
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
                self._disc_note(INSTALL_FOUND % (build['name'], info['count'],
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
                note = READY % (self.core.build.name, self._selected())
                self._log('file: %s, the %s build'
                          % (os.path.basename(path), self.core.build.name))
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
                self._set_status(READY % (self.core.build.name,
                                          self._selected()), True)

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
