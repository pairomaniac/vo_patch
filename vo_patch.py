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
import math
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


# ==========================================================================
# The resolution patch: the game at a size other than 640x480. docs/HIRES.md
# describes it; this is the short form.
#
# hires_install takes any size the sites can carry, and the window applies
# 1920x1080. Nothing here overlaps the other patches' sites, and it goes on
# last, after nodisc.
#
# What it changes:
#
#   -  Mode, window, the places that refuse any mode but 640x480 and
#      320x240 (including a mode-availability check that hangs on a black
#      screen if the display does not enumerate the new mode), viewport
#      sizes and the projection scale.
#   -  The perspective-subdivision thresholds, which are in pixels.
#   -  The renderer's coverage mask: one bit per pixel, 80 bytes a row, 480
#      rows, in .data with other globals right behind it. It moves to a new
#      section sized for the new width and height, and the row stride
#      changes everywhere the renderer has it.
#   -  The 2D layer (HUD, fonts, backdrops, menus) is drawn at its designed
#      size into an offscreen buffer and scaled onto its viewport each time
#      the game calls it, so it keeps its layout and art
#      (nearest-neighbour). In a wide mode backdrops cover the
#      full width and the HUD stays 4:3, centred.
#   -  HUD polygons (bars, frames, timer box, reticle, weapon strips,
#      machine select, cursors) are projected at 640x480 and scaled at
#      insert to the same 4:3 frame as the 2D layer, so both layers share
#      one grid whatever the field of view. What counts as HUD is decided
#      by where it is drawn from: the functions that own the HUD
#      projection setups (UI_PASS_FUNCS) are wrapped, and everything
#      submitted while one of them is running is HUD.
#   -  The machine-select hangar draws a platform mech while it is within
#      an angle window sized for 4:3; the window is widened to the view,
#      as is the enemy marker's on-screen test, which decides when the
#      edge arrow takes over. The renderer's polygon cap is raised to
#      HIRES_POLYS.
#   -  The ending roll loses its black bands: the tile window enters at
#      the bottom and leaves through the top, and the driver's cut moves
#      so the last line leaves too.
#   -  Split screen: side by side is two W/2 x H viewports, top/bottom two
#      W x H/2 (instead of the game's staggered 320x240 boxes). Each gets
#      a field of view between the 4:3 frame that fits inside it and the
#      one that covers it (HIRES_SPLIT_FOV); the HUD always fits, drawn at
#      its own scale. In side by side, where the 4:3 HUD frame sits
#      centred in a taller viewport, the timer and health bars (frame
#      rows above HIRES_HUD_BAND) are pinned to the viewport top during
#      a match; the 2D layer and the HUD polygons move together. The
#      split is drawn only in the sub-states that draw a round
#      (HIRES_SPLIT_STATES); every other frame is one full-screen
#      viewport, so the machine select is no longer the same grid twice.
#   -  F4 and the F5 Screen row (720p / 1080p) switch between 1920x1080
#      and 1280x720 (HIRES_ALT): the sites are written for the first, and
#      a table in the section holds both sets for the blob to copy over
#      them at runtime, then the surfaces are recreated. The choice is
#      saved as bit 0 of ScrSize. The 320x240 menu command is defused.
#
# The exe grows by one section: 15 KB on disk - 5 KB of code, the data
# block and the F4 site table - plus a header; the canvas, mask,
# row-table and pool buffers are zero-filled by the loader.
#
# Width must be a multiple of 32 and at most 2040 (the coverage-mask
# stride is an 8-bit immediate in ten places). Nothing else is tied to a
# particular size: scales, offsets and the split factors are computed
# from the width and height. Tested at 1280x720, 1280x960 and 1920x1080,
# 1P and both split layouts. In a wide mode the 3D is Hor+ (same
# vertical field of view, more at the sides); the sky dome was built for
# 4:3 and may not reach the edges.
#
# The PORT table below carries the other builds: every site and address
# translated by tools/hiresport.py from a vomap map of each, and
# port_sites redoes the handful of rewrites that depend on the build's
# own bytes. docs/HIRES.md, Porting to other builds.
# ==========================================================================




BASE_W, BASE_H = 640, 480

# Each site is (file offset, old bytes, new bytes) built from W and H.
# Offsets are of the immediate, not the instruction, unless noted.


def u32(v):
    return struct.pack('<I', v & 0xffffffff)


def f32(v):
    return struct.pack('<f', v)


def imm_sites(offsets, old, new):
    return [(o, u32(old), u32(new)) for o in offsets]


# --- 2D layer ---------------------------------------------------------
# The 2D code (HUD, fonts, backdrops, menus) draws in pixel coordinates
# through the frame globals and the row table. 0x5c80df calls it four
# times a frame: 0x4800d0/0x4804f0 for viewport 1 before and after the
# 3D flush, 0x5670c0/0x5674f0 for viewport 2 in split screen. Each call
# is redirected to a stub that points the viewport's globals at a
# 640x480 offscreen canvas, pre-filled with the viewport's own pixels
# sampled down (a copy is kept), hides the split flag so split viewports
# get the full-size layout, calls the original, then composites every
# pixel that differs from the copy back onto the viewport with uniform
# nearest-neighbour scaling, centred, and restores the globals and row
# table. Translucent elements therefore blend against the real
# background. In the pre-3D phase (backdrops) the canvas has the
# viewport's own aspect, unless nothing was painted beyond the 4:3
# width, in which case it is treated as 4:3 (logos, title, menus); in
# split it is always 4:3. The post phase (HUD) is 4:3. When the last 3D
# flush drew nothing, the margins take the picture's top-row colour
# when that row is all one colour, else black. The canvas has 480 guard
# rows above and below, since the 2D code draws outside the
# viewport in split screen. Source: asm/ui.asm; nasm like the rest of
# asm/, but built by tools/uibuild.py since it is position independent
# apart from the four calls fixed up here.

def _pe_stamp(buf):
    pe = struct.unpack_from('<I', buf, 0x3c)[0]
    return struct.unpack_from('<I', buf, pe + 8)[0]


RETAIL_STAMP = 0x334d33fc

# GENERATED - do not edit by hand. tools/hiresport.py writes these from a
# vomap.py map of each build; regenerate rather than editing.
# PORT TABLES BEGIN
PORT = {
    '345107fa': {                       # jp.exe
        'va': {
            0x00408790: 0x00408700,
            0x00427ec9: 0x00427b29,
            0x00428241: 0x00427ea1,
            0x0042cda6: 0x0042cac8,
            0x00432fbe: 0x0043270e,
            0x00433141: 0x00432891,
            0x00460b70: 0x0045fbf0,
            0x00460cf3: 0x0045fd73,
            0x0047f2e0: 0x0047de90,
            0x004800d0: 0x0047ec80,
            0x004803d0: 0x0047ef80,
            0x00480410: 0x0047efc0,
            0x004804f0: 0x0047f0a0,
            0x004b6030: 0x004b43a0,
            0x004b981f: 0x004b7a61,
            0x004c468e: 0x004c24d6,
            0x004d0280: 0x004cde03,
            0x004d9c3d: 0x004d715b,
            0x0050bcc6: 0x0050844c,
            0x0050c0cb: 0x00508851,
            0x00514430: 0x00510960,
            0x0051444d: 0x0051097d,
            0x0051448e: 0x005109be,
            0x00514576: 0x00510aa6,
            0x0051457c: 0x00510aac,
            0x00531f6a: 0x0052e2ad,
            0x005495b1: 0x00544c21,
            0x0055d221: 0x00558711,
            0x005670c0: 0x00562400,
            0x005673c0: 0x00562700,
            0x00567400: 0x00562740,
            0x005674f0: 0x00562830,
            0x0057f1b0: 0x0057a480,
            0x005829c3: 0x0057db65,
            0x0058881e: 0x0058390a,
            0x00588d85: 0x00583e71,
            0x0059cb93: 0x00597993,
            0x005a1f3c: 0x0059ccd4,
            0x005a251b: 0x0059d2b3,
            0x005b5f2e: 0x005b0bb6,
            0x005c56a2: 0x005bff42,
            0x005c680b: 0x005c10da,
            0x005c755a: 0x005c1e29,
            0x005c79aa: 0x005c2279,
            0x005c7dfe: 0x005c26cd,
            0x005c813a: 0x005c2a09,
            0x005c814c: 0x005c2a1b,
            0x005c8188: 0x005c2a57,
            0x005c819a: 0x005c2a69,
            0x005c8317: 0x005c2be6,
            0x005c8ca0: 0x005c34e6,
            0x005c991c: 0x005c4198,
            0x005cc39d: 0x005c6c3d,
            0x005cc3de: 0x005c6c7e,
            0x005cc4c6: 0x005c6d66,
            0x005cc4cc: 0x005c6d6c,
            0x005ce180: 0x005c8a10,
            0x005d1db0: 0x005cc640,
            0x005dcc80: 0x005d7510,
            0x00624728: 0x0061f4b0,
            0x0066c17c: 0x00668104,
            0x0066c180: 0x00668108,
            0x0066c190: 0x00668118,
            0x006a0240: 0x0069bfa0,
            0x006bc1e4: 0x006b7f44,
            0x006bc1e8: 0x006b7f48,
            0x006bc948: 0x006b86a8,
            0x006bc94c: 0x006b86ac,
            0x006bf598: 0x006bb2b0,
            0x006bf5a8: 0x006bb2c0,
            0x006bf5ac: 0x006bb2c4,
            0x006bf5b0: 0x006bb2c8,
            0x006bf5b8: 0x006bb2d0,
            0x006bf5bc: 0x006bb2d4,
            0x006c84c8: 0x006c41e0,
            0x006c84cc: 0x006c41e4,
            0x006c866c: 0x006c4384,
            0x006c8b24: 0x006c48b4,
            0x006c8b28: 0x006c48b8,
            0x006c8ce8: 0x006c4a78,
            0x006d0dc4: 0x006ccb54,
            0x006db4c8: 0x006d7258,
            0x006db4d0: 0x006d7260,
            0x006db4d4: 0x006d7264,
            0x006db530: 0x006d72c0,
            0x006db534: 0x006d72c4,
            0x007001d0: 0x006fbf60,
            0x007087a0: 0x00704530,
            0x00708818: 0x007045a8,
            0x0070881c: 0x007045ac,
            0x00708820: 0x007045b0,
            0x00708870: 0x00704600,
            0x00708874: 0x00704604,
            0x00725f50: 0x00721ce0,
            0x00791ad0: 0x0078d860,
            0x00be4300: 0x00bdef40,
            0x00bf5f78: 0x00bf0bd8,
            0x00bf5f7c: 0x00bf0bdc,
            0x01ad0030: 0x01acacb0,
            0x01ad0034: 0x01acacb4,
            0x01ae3594: 0x01adcdf0,
            0x01ae3690: 0x01addc40,
            0x01ae5f40: 0x01ae0c34,
            0x01ae5f5c: 0x01ae0c18,
            0x01cc6700: 0x01cc13b0,
            0x01ef1140: 0x01edfd90,
            0x01ef8a90: 0x01ef4230,
            0x01ef9eb0: 0x01ef5ac8,
            0x01efb728: 0x01ef63c8,
            0x01efb730: 0x01ef63d0,
            0x033cd5f4: 0x033c8280,
            0x034155c8: 0x0340ffc8,
            0x034155d0: 0x0340ffd0,
            0x0345b2c8: 0x03455d88,
            0x0345bd58: 0x034569a8,
        },
        'off': {
            0x0272c4: (0x026f24, 'a340efbd00'),
            0x02763c: (0x02729c, 'a3b0b26b00'),
            0x02c1a6: (0x02bec8, '558bec81eca8000000'),
            0x0323be: (0x031b0e, '558bec83ec04'),
            0x032541: (0x031c91, '558bec5356'),
            0x05ff70: (0x05eff0, '558bec83ec04'),
            0x0600f3: (0x05f173, '558bec5356'),
            0x074ed0: (0x073eba, '00150000'),
            0x074edf: (0x073ec9, '00ebffff'),
            0x075038: (0x074022, '00150000'),
            0x075047: (0x074031, '00ebffff'),
            0x07e504: (0x07d0b4, '8b0da8866b003bc80f84bd010000'),
            0x07f2e0: (0x07de90, '56a108816600'),
            0x07f88d: (0x07e43d, '8b1495e0326c00'),
            0x07f8dd: (0x07e48d, '8b1495e0326c00'),
            0x07f96c: (0x07e51c, '32'),
            0x07f99b: (0x07e54b, '81e1ffff0000'),
            0x07fa63: (0x07e613, 'b8080000002bc350'),
            0x07fd37: (0x07e8e7, '31'),
            0x07fd71: (0x07e921, 'e0326c00'),
            0x07fe39: (0x07e9e9, '8b1495e0326c00'),
            0x07fe9e: (0x07ea4e, '8b1495e0326c00'),
            0x080074: (0x07ec24, '0f85a6000000'),
            0x0802f9: (0x07eeb9, '8b1495e0326c00'),
            0x0804e7: (0x07f0a7, '83e27f'),
            0x080565: (0x07f125, '83e27f'),
            0x0808f8: (0x07f4b6, '83e27f'),
            0x080a0d: (0x07f5cb, '83e27f'),
            0x080c74: (0x07f828, '83e27f'),
            0x080e17: (0x07f9d6, '83e27f'),
            0x081046: (0x07fcb6, '83e27f'),
            0x0811ec: (0x07fe87, '83e27f'),
            0x081498: (0x08012a, '83e27f'),
            0x081611: (0x080295, '83e27f'),
            0x081bf6: (0x08086d, '83e27f'),
            0x081ea6: (0x080ae7, '83e27f'),
            0x082174: (0x080d93, '83e27f'),
            0x0823b3: (0x080fdd, '83e27f'),
            0x0827d4: (0x081400, '83e27f'),
            0x082944: (0x08156f, '83e27f'),
            0x082c82: (0x0818a6, '83e27f'),
            0x082d06: (0x08192a, '83e27f'),
            0x082f07: (0x081b3a, '83e27f'),
            0x082f8b: (0x081bc8, '83e27f'),
            0x08325d: (0x081ec2, '83e27f'),
            0x0832ef: (0x081f5d, '83e27f'),
            0x0834f3: (0x082187, '83e27f'),
            0x083577: (0x08220b, '83e27f'),
            0x0b5430: (0x0b37a0, '558bec83ec34'),
            0x0b8c1f: (0x0b6e61, '558bec83ec34'),
            0x0c3a8e: (0x0c18d6, '558bec81ec8c000000'),
            0x0cf680: (0x0cd203, '558bec535657'),
            0x0d903d: (0x0d655b, '558bec81eca8000000'),
            0x10b0c1: (0x107847, '68a0bf6900'),
            0x10b4c6: (0x107c4c, 'a1b0b26b00'),
            0x11384d: (0x10fd7d, '558bec535657'),
            0x11388e: (0x10fdbe, '558bec535657'),
            0x113976: (0x10fea6, '558bec535657'),
            0x13136a: (0x12d6ad, '558bec535657'),
            0x147b60: (0x1432da, '00150000'),
            0x147b6f: (0x1432e9, '00ebffff'),
            0x147cc8: (0x143442, '00150000'),
            0x147cd7: (0x143451, '00ebffff'),
            0x1489b1: (0x144021, '558bec83ec10'),
            0x15c621: (0x157b11, '558bec83ec10'),
            0x166885: (0x161bc5, '8b1495e0326c00'),
            0x1668dd: (0x161c1d, '8b1495e0326c00'),
            0x16696c: (0x161cac, '32'),
            0x16699b: (0x161cdb, '81e1ffff0000'),
            0x166a63: (0x161da3, 'b8080000002bc750'),
            0x166d37: (0x162077, '31'),
            0x166d79: (0x1620b9, 'e0326c00'),
            0x166e41: (0x162181, '8b1495e0326c00'),
            0x166eae: (0x1621ee, '8b1495e0326c00'),
            0x167084: (0x1623c4, '0f85a6000000'),
            0x167321: (0x162661, '8b1495e0326c00'),
            0x17e5b0: (0x179880, '558bec83ec34'),
            0x181dc3: (0x17cf65, '558bec83ec34'),
            0x187c1e: (0x182d0a, '558bec83ec0c'),
            0x188185: (0x183271, '558bec83ec0c'),
            0x18e341: (0x1894ee, 'e2100000'),
            0x18e7b9: (0x189893, 'e3100000'),
            0x18e949: (0x189a23, 'e83863f8ff'),
            0x18ea14: (0x189aee, 'd8dcffff'),
            0x19d8ea: (0x1986ea, 'e8a4e6ffff'),
            0x1a133c: (0x19c0d4, '558bec83ec0c'),
            0x1a191b: (0x19c6b3, '558bec83ec0c'),
            0x1b098b: (0x1ab647, 'e0010000'),
            0x1b0990: (0x1ab64c, '80020000'),
            0x1b532e: (0x1affb6, '558bec81ec8c000000'),
            0x1c4dd3: (0x1bf6b8, 'e0010000'),
            0x1c4dd8: (0x1bf6bd, '80020000'),
            0x1c4f73: (0x1bf842, 'e0326c00'),
            0x1c4fc9: (0x1bf898, '603a6c00'),
            0x1c5011: (0x1bf8e0, '80020000'),
            0x1c501e: (0x1bf8ed, 'e0010000'),
            0x1c617a: (0x1c0a49, 'e0010000'),
            0x1c617f: (0x1c0a4e, '80020000'),
            0x1c6894: (0x1c1163, 'e872f3ffff'),
            0x1c68ec: (0x1c11bb, 'f605b0b26b0004'),
            0x1c68fc: (0x1c11cb, 'e0010000'),
            0x1c6901: (0x1c11d0, '80020000'),
            0x1c6d3d: (0x1c160c, 'e0010000'),
            0x1c6d42: (0x1c1611, '80020000'),
            0x1c6daa: (0x1c1679, '6a1068f0000000'),
            0x1c751b: (0x1c1dea, 'e8f7010000'),
            0x1c753a: (0x1c1e09, 'e872c2ebff'),
            0x1c754c: (0x1c1e1b, 'e8e0f9f9ff'),
            0x1c7566: (0x1c1e35, 'e8069c0000'),
            0x1c7578: (0x1c1e47, 'e8c44a0100'),
            0x1c7588: (0x1c1e57, 'e844c6ebff'),
            0x1c759a: (0x1c1e69, 'e8c2fdf9ff'),
            0x1c7726: (0x1c1ff5, '0000803f'),
            0x1c7730: (0x1c1fff, '0000003f'),
            0x1c782d: (0x1c2093, 'd905447f6b00dc35a8f46100d91d447f6b00d905487f6b00dc0db0f46100d91d487f6b00d905b8486c00dc0db0f46100d91db8486c008b45088b40248945fc8b45088b40108945f88b45fca3300cae018b45'),
            0x1c78f0: (0x1c2138, '80020000'),
            0x1c7907: (0x1c214f, '28000000'),
            0x1c794a: (0x1c2190, '80020000'),
            0x1c7961: (0x1c21a7, '28000000'),
            0x1c799b: (0x1c21e1, '8d0c498d0c89c1e104'),
            0x1c79bd: (0x1c2203, '004b0000'),
            0x1c7a2c: (0x1c2272, '28000000'),
            0x1c7a8e: (0x1c22d4, '28000000'),
            0x1c7ad2: (0x1c2318, 'c1e1038d0c498d0c89'),
            0x1c7af4: (0x1c233a, '80250000'),
            0x1c7b72: (0x1c23b8, '80020000'),
            0x1c7b89: (0x1c23cf, '28000000'),
            0x1c7bb1: (0x1c23f7, '8d04408d0480c1e004'),
            0x1c7bd0: (0x1c2416, '004b0000'),
            0x1c7c0e: (0x1c2454, '28000000'),
            0x1c7c36: (0x1c247c, 'c1e0038d04408d0480'),
            0x1c7c55: (0x1c249b, '80250000'),
            0x1c7cb8: (0x1c24fe, '02'),
            0x1c7cc5: (0x1c250b, '40010000'),
            0x1c7ccf: (0x1c2515, 'f0000000'),
            0x1c7cf2: (0x1c2538, '40010000'),
            0x1c7cfc: (0x1c2542, 'f0000000'),
            0x1c7d68: (0x1c25ae, 'f0010000'),
            0x1c7d72: (0x1c25b8, '80010000'),
            0x1c7d77: (0x1c25bd, '80020000'),
            0x1c7d89: (0x1c25cf, 'e0010000'),
            0x1c7da5: (0x1c25eb, '80020000'),
            0x1c7daf: (0x1c25f5, 'e0010000'),
            0x1c7e3a: (0x1c2680, '02'),
            0x1c824e: (0x1c2ab4, 'e0010000'),
            0x1c834b: (0x1c2bdb, '80020000'),
            0x1c8435: (0x1c2ce9, '50010000'),
            0x1c843c: (0x1c2cf0, '80020000'),
            0x1c8443: (0x1c2cf7, 'e0010000'),
            0x1c8491: (0x1c2d45, 'b0010000'),
            0x1c8498: (0x1c2d4c, '80020000'),
            0x1c849f: (0x1c2d53, 'e0010000'),
            0x1c84d6: (0x1c2d8a, 'b0010000'),
            0x1c84dd: (0x1c2d91, '80020000'),
            0x1c84e4: (0x1c2d98, 'e0010000'),
            0x1c8810: (0x1c308c, '80020000'),
            0x1c881d: (0x1c3099, 'e0010000'),
            0x1c898b: (0x1c3207, 'e0010000'),
            0x1c8990: (0x1c320c, '80020000'),
            0x1c8a88: (0x1c3304, 'a1a0436c00'),
            0x1c8ab9: (0x1c3335, 'e0010000'),
            0x1c8abe: (0x1c333a, '80020000'),
            0x1c8b4d: (0x1c33c9, 'e0010000'),
            0x1c8b52: (0x1c33ce, '80020000'),
            0x1c8e8d: (0x1c3709, '40010000'),
            0x1c8e94: (0x1c3710, 'a0000000'),
            0x1c8f1d: (0x1c3799, '40010000'),
            0x1c8f24: (0x1c37a0, 'a0000000'),
            0x1c90b8: (0x1c3934, 'e0326c00'),
            0x1c90e9: (0x1c3965, 'e0326c00'),
            0x1cb79d: (0x1c603d, '558bec535657'),
            0x1cb7de: (0x1c607e, '558bec535657'),
            0x1cb8c6: (0x1c6166, '558bec535657'),
            0x1cd5d4: (0x1c7e64, '70cb6c008d0570da6c00b9e0010000890783c05089470483c05083c70883e9027fed'),
            0x1cd5da: (0x1c7e6a, '70da6c00'),
            0x1cd605: (0x1c7e95, '70da6c00'),
            0x1cd60c: (0x1c7e9c, '80250000'),
            0x1cd8a5: (0x1c8135, '70da6c00'),
            0x1cd8b8: (0x1c8148, '83c850'),
            0x1cd9db: (0x1c826b, '70da6c00'),
            0x1cd9ee: (0x1c827e, '83c850'),
            0x1ce0f9: (0x1c8989, '70da6c00'),
            0x1ce11b: (0x1c89ab, '83c850'),
            0x1ce988: (0x1c9218, 'a1784a6c004683c0505ba3784a6c00'),
            0x1ce9de: (0x1c926e, 'a1784a6c0083c05046a3784a6c00'),
            0x1cf028: (0x1c98b8, 'a1784a6c004683c0505ba3784a6c00'),
            0x1cf07e: (0x1c990e, 'a1784a6c0083c05046a3784a6c00'),
            0x1cf6d8: (0x1c9f68, 'a1784a6c004683c0505ba3784a6c00'),
            0x1cf72e: (0x1c9fbe, 'a1784a6c0083c05046a3784a6c00'),
            0x1cfd88: (0x1ca618, 'a1784a6c004683c0505ba3784a6c00'),
            0x1cfdde: (0x1ca66e, 'a1784a6c0083c05046a3784a6c00'),
            0x1cfe32: (0x1ca6c2, '50000000'),
            0x1cfe90: (0x1ca720, '50000000'),
            0x1d0eac: (0x1cb73c, 'c4090000'),
            0x1d0ed0: (0x1cb760, '4c986f00'),
            0x1d11d8: (0x1cba68, '50986f00'),
            0x1d161f: (0x1cbeaf, '70da6c00'),
            0x1d162c: (0x1cbebc, '83c850'),
            0x1d1a30: (0x1cc2c0, '70da6c00'),
            0x1d1a3d: (0x1cc2cd, '83c850'),
            0x1d1deb: (0x1cc67b, 'a1784a6c000ffefe83c0505ea3784a6c00'),
            0x1d1e22: (0x1cc6b2, 'a1784a6c000ffefe83c0508b0dc4b26b00a3784a6c00'),
            0x1d2111: (0x1cc9a1, 'a1784a6c000ffefe83c0505ea3784a6c00'),
            0x1d2152: (0x1cc9e2, 'a1784a6c000ffefe83c0508b0dc4b26b00a3784a6c00'),
            0x1d395b: (0x1ce1eb, 'c4090000'),
            0x1d3967: (0x1ce1f7, '70756d00'),
            0x1d3970: (0x1ce200, '304a6f00'),
            0x1d3a28: (0x1ce2b8, '8b349d60bf6f00'),
            0x1d46ae: (0x1cef3e, 'c4090000'),
            0x1d46ba: (0x1cef4a, '70756d00'),
            0x1d46c3: (0x1cef53, '304a6f00'),
            0x1d4760: (0x1ceff0, '8b349d60bf6f00'),
            0x1d876b: (0x1d2ffb, '70da6c00'),
            0x1d877e: (0x1d300e, '83c850'),
            0x1d88a7: (0x1d3137, '70da6c00'),
            0x1d88ba: (0x1d314a, '83c850'),
            0x1d8fcf: (0x1d385f, '70da6c00'),
            0x1d8ff1: (0x1d3881, '83c850'),
            0x1d9858: (0x1d40e8, 'a1784a6c004683c0505ba3784a6c00'),
            0x1d98ae: (0x1d413e, 'a1784a6c0083c05046a3784a6c00'),
            0x1d9ef8: (0x1d4788, 'a1784a6c004683c0505ba3784a6c00'),
            0x1d9f4e: (0x1d47de, 'a1784a6c0083c05046a3784a6c00'),
            0x1da5a8: (0x1d4e38, 'a1784a6c004683c0505ba3784a6c00'),
            0x1da5fe: (0x1d4e8e, 'a1784a6c0083c05046a3784a6c00'),
            0x1dac58: (0x1d54e8, 'a1784a6c004683c0505ba3784a6c00'),
            0x1dacae: (0x1d553e, 'a1784a6c0083c05046a3784a6c00'),
            0x1dad02: (0x1d5592, '50000000'),
            0x1dad60: (0x1d55f0, '50000000'),
            0x1dbd7c: (0x1d660c, 'c4090000'),
            0x1dbda0: (0x1d6630, '9cfd7100'),
            0x1dc0a8: (0x1d6938, 'a0fd7100'),
            0x1dc4f8: (0x1d6d88, '70da6c00'),
            0x1dc502: (0x1d6d92, '83c850'),
            0x1dc919: (0x1d71a9, '70da6c00'),
            0x1dc923: (0x1d71b3, '83c850'),
            0x1dcccb: (0x1d755b, 'a1784a6c000ffefe83c0505ea3784a6c00'),
            0x1dcd02: (0x1d7592, 'a1784a6c000ffefe83c0508b0dccb26b00a3784a6c00'),
            0x1dcff1: (0x1d7881, 'a1784a6c000ffefe83c0505ea3784a6c00'),
            0x1dd032: (0x1d78c2, 'a1784a6c000ffefe83c0508b0dccb26b00a3784a6c00'),
            0x1de86b: (0x1d90fb, 'd0070000'),
            0x1de877: (0x1d9107, '20487000'),
            0x1de880: (0x1d9110, '20bf7100'),
            0x1de938: (0x1d91c8, '8b349de01c7200'),
            0x1df5fe: (0x1d9e8e, 'd0070000'),
            0x1df60a: (0x1d9e9a, '20487000'),
            0x1df613: (0x1d9ea3, '20bf7100'),
            0x1df6b0: (0x1d9f40, '8b349de01c7200'),
            0x1fb4a0: (0x1f5ca0, '000080c3'),
            0x1fb4a4: (0x1f5ca4, '00008043'),
            0x205d38: (0x2002c8, '000080c3'),
            0x205d3c: (0x2002cc, '00008043'),
            0x2213f0: (0x21b978, '52b81e85eb913f40'),
            0x2213f8: (0x21b980, 'ae47e17a146e3c40'),
            0x2baff4: (0x2b6154, '00000047'),
            0x2baffc: (0x2b615c, '00000048'),
            0x2c734c: (0x2c2464, '40000000'),
            0x2c7350: (0x2c2468, '40000000'),
            0x2c7354: (0x2c246c, 'b0000000'),
            0x2c7358: (0x2c2470, '00010000'),
            0x2c735c: (0x2c2474, '78000000'),
            0x2c7360: (0x2c2478, '78000000'),
            0x2c7370: (0x2c2488, '18000000'),
            0x2c73f0: (0x2c2508, '18000000'),
        },
        'passlen': {
            0x004d0280: 5,
            0x00531f6a: 5,
        },
        'absent': (0x1c7775,),
    },
    '3317246a': {                       # oem.exe
        'va': {
            0x00408790: 0x00408790,
            0x00427ec9: 0x00427e29,
            0x00428241: 0x004281a1,
            0x0042cda6: 0x0042cd06,
            0x00432fbe: 0x00432f1e,
            0x00433141: 0x004330a1,
            0x00460b70: 0x00460ad0,
            0x00460cf3: 0x00460c53,
            0x0047f2e0: 0x0047f1e0,
            0x004800d0: 0x0047ffe0,
            0x004803d0: 0x004802e0,
            0x00480410: 0x00480320,
            0x004804f0: 0x00480400,
            0x004b6030: 0x004b5ed0,
            0x004b981f: 0x004b96bf,
            0x004c468e: 0x004c452e,
            0x004d0280: 0x004d0120,
            0x004d9c3d: 0x004d9add,
            0x0050bcc6: 0x0050b836,
            0x0050c0cb: 0x0050bc3b,
            0x00514430: 0x00513fa0,
            0x0051444d: 0x00513fbd,
            0x0051448e: 0x00513ffe,
            0x00514576: 0x005140e6,
            0x0051457c: 0x005140ec,
            0x00531f6a: 0x00531ada,
            0x005495b1: 0x00549121,
            0x0055d221: 0x0055cd91,
            0x005670c0: 0x00566c30,
            0x005673c0: 0x00566f30,
            0x00567400: 0x00566f70,
            0x005674f0: 0x00567060,
            0x0057f1b0: 0x0057ec80,
            0x005829c3: 0x00582493,
            0x0058881e: 0x005882ee,
            0x00588d85: 0x00588855,
            0x0059cb93: 0x0059c663,
            0x005a1f3c: 0x005a1a0c,
            0x005a251b: 0x005a1feb,
            0x005b5f2e: 0x005b59fe,
            0x005c56a2: 0x005c5172,
            0x005c680b: 0x005c6324,
            0x005c755a: 0x005c7073,
            0x005c79aa: 0x005c74c3,
            0x005c7dfe: 0x005c7917,
            0x005c813a: 0x005c7c53,
            0x005c814c: 0x005c7c65,
            0x005c8188: 0x005c7ca1,
            0x005c819a: 0x005c7cb3,
            0x005c8317: 0x005c7e52,
            0x005c8ca0: 0x005c87d8,
            0x005c991c: 0x005c9454,
            0x005cc39d: 0x005cbedd,
            0x005cc3de: 0x005cbf1e,
            0x005cc4c6: 0x005cc006,
            0x005cc4cc: 0x005cc00c,
            0x005ce180: 0x005cdcc0,
            0x005d1db0: 0x005d18f0,
            0x005dcc80: 0x005dc7c0,
            0x00624728: 0x00624718,
            0x0066c17c: 0x0066c174,
            0x0066c180: 0x0066c178,
            0x0066c190: 0x0066c188,
            0x006a0240: 0x006a01d8,
            0x006bc1e4: 0x006bc17c,
            0x006bc1e8: 0x006bc180,
            0x006bc948: 0x006bc8e0,
            0x006bc94c: 0x006bc8e4,
            0x006bf598: 0x006bf530,
            0x006bf5a8: 0x006bf540,
            0x006bf5ac: 0x006bf544,
            0x006bf5b0: 0x006bf548,
            0x006bf5b8: 0x006bf550,
            0x006bf5bc: 0x006bf554,
            0x006c84c8: 0x006c8460,
            0x006c84cc: 0x006c8464,
            0x006c866c: 0x006c8604,
            0x006c8b24: 0x006c8aec,
            0x006c8b28: 0x006c8af0,
            0x006c8ce8: 0x006c8ca8,
            0x006d0dc4: 0x006d0d84,
            0x006db4c8: 0x006db488,
            0x006db4d0: 0x006db490,
            0x006db4d4: 0x006db494,
            0x006db530: 0x006db4f0,
            0x006db534: 0x006db4f4,
            0x007001d0: 0x00700190,
            0x007087a0: 0x00708760,
            0x00708818: 0x007087d8,
            0x0070881c: 0x007087dc,
            0x00708820: 0x007087e0,
            0x00708870: 0x00708830,
            0x00708874: 0x00708834,
            0x00725f50: 0x00725f10,
            0x00791ad0: 0x00791a90,
            0x00be4300: 0x00be42c0,
            0x00bf5f78: 0x00bf5f38,
            0x00bf5f7c: 0x00bf5f3c,
            0x01ad0030: 0x01acffc8,
            0x01ad0034: 0x01acffcc,
            0x01ae3594: 0x01ae3524,
            0x01ae3690: 0x01ae3620,
            0x01ae5f40: 0x01ae5ed0,
            0x01ae5f5c: 0x01ae5eec,
            0x01cc6700: 0x01cc6690,
            0x01ef1140: 0x01ef10d0,
            0x01ef8a90: 0x01ef8a20,
            0x01ef9eb0: 0x01ef9e40,
            0x01efb728: 0x01efb6b8,
            0x01efb730: 0x01efb6c0,
            0x033cd5f4: 0x033cd584,
            0x034155c8: 0x03415558,
            0x034155d0: 0x03415560,
            0x0345b2c8: 0x0345b258,
            0x0345bd58: 0x0345bce8,
        },
        'off': {
            0x0272c4: (0x027224, 'a3c042be00'),
            0x02763c: (0x02759c, 'a330f56b00'),
            0x02c1a6: (0x02c106, '558bec81eca8000000'),
            0x0323be: (0x03231e, '558bec83ec04'),
            0x032541: (0x0324a1, '558bec5356'),
            0x05ff70: (0x05fed0, '558bec83ec04'),
            0x0600f3: (0x060053, '558bec5356'),
            0x074ed0: (0x074dd0, '00150000'),
            0x074edf: (0x074ddf, '00ebffff'),
            0x075038: (0x074f38, '00150000'),
            0x075047: (0x074f47, '00ebffff'),
            0x07e504: (0x07e404, '8b0de0c86b003bc80f84bd010000'),
            0x07f2e0: (0x07f1f0, '56a178c16600'),
            0x07f88d: (0x07f79d, '8b149560756c00'),
            0x07f8dd: (0x07f7ed, '8b149560756c00'),
            0x07f96c: (0x07f87c, '32'),
            0x07f99b: (0x07f8ab, '81e1ffff0000'),
            0x07fa63: (0x07f973, 'b8080000002bc350'),
            0x07fd37: (0x07fc47, '31'),
            0x07fd71: (0x07fc81, '60756c00'),
            0x07fe39: (0x07fd49, '8b149560756c00'),
            0x07fe9e: (0x07fdae, '8b149560756c00'),
            0x080074: (0x07ff84, '0f859e000000'),
            0x0802f9: (0x080209, '8b149560756c00'),
            0x0804e7: (0x0803f7, '83e27f'),
            0x080565: (0x080475, '83e27f'),
            0x0808f8: (0x080813, '83e27f'),
            0x080a0d: (0x080924, '83e27f'),
            0x080c74: (0x080b96, '83e27f'),
            0x080e17: (0x080d36, '83e27f'),
            0x081046: (0x080f66, '83e27f'),
            0x0811ec: (0x08110c, '83e27f'),
            0x081498: (0x0813b9, '83e27f'),
            0x081611: (0x081530, '83e27f'),
            0x081bf6: (0x081b0f, '83e27f'),
            0x081ea6: (0x081da3, '83e27f'),
            0x082174: (0x082059, '83e27f'),
            0x0823b3: (0x08228c, '83e27f'),
            0x0827d4: (0x0826ae, '83e27f'),
            0x082944: (0x08281d, '83e27f'),
            0x082c82: (0x082b58, '83e27f'),
            0x082d06: (0x082bdc, '83e27f'),
            0x082f07: (0x082de0, '83e27f'),
            0x082f8b: (0x082e64, '83e27f'),
            0x08325d: (0x083146, '83e27f'),
            0x0832ef: (0x0831d8, '83e27f'),
            0x0834f3: (0x0833e9, '83e27f'),
            0x083577: (0x08346d, '83e27f'),
            0x0b5430: (0x0b52d0, '558bec83ec34'),
            0x0b8c1f: (0x0b8abf, '558bec83ec34'),
            0x0c3a8e: (0x0c392e, '558bec81ec8c000000'),
            0x0cf680: (0x0cf520, '558bec83ec08'),
            0x0d903d: (0x0d8edd, '558bec81eca8000000'),
            0x10b0c1: (0x10ac31, '68d8016a00'),
            0x10b4c6: (0x10b036, 'a130f56b00'),
            0x11384d: (0x1133bd, '558bec535657'),
            0x11388e: (0x1133fe, '558bec535657'),
            0x113976: (0x1134e6, '558bec535657'),
            0x13136a: (0x130eda, '558bec83ec08'),
            0x147b60: (0x1476d0, '00150000'),
            0x147b6f: (0x1476df, '00ebffff'),
            0x147cc8: (0x147838, '00150000'),
            0x147cd7: (0x147847, '00ebffff'),
            0x1489b1: (0x148521, '558bec83ec10'),
            0x15c621: (0x15c191, '558bec83ec10'),
            0x166885: (0x1663f5, '8b149560756c00'),
            0x1668dd: (0x16644d, '8b149560756c00'),
            0x16696c: (0x1664dc, '32'),
            0x16699b: (0x16650b, '81e1ffff0000'),
            0x166a63: (0x1665d3, 'b8080000002bc350'),
            0x166d37: (0x1668a7, '31'),
            0x166d79: (0x1668e9, '60756c00'),
            0x166e41: (0x1669b1, '8b149560756c00'),
            0x166eae: (0x166a1e, '8b149560756c00'),
            0x167084: (0x166bf4, '0f85a6000000'),
            0x167321: (0x166e91, '8b149560756c00'),
            0x17e5b0: (0x17e080, '558bec83ec34'),
            0x181dc3: (0x181893, '558bec83ec34'),
            0x187c1e: (0x1876ee, '558bec83ec0c'),
            0x188185: (0x187c55, '558bec83ec0c'),
            0x18e341: (0x18de11, 'e2100000'),
            0x18e7b9: (0x18e289, 'e3100000'),
            0x18e949: (0x18e419, 'e8824ff8ff'),
            0x18ea14: (0x18e4e4, 'd8dcffff'),
            0x19d8ea: (0x19d3ba, 'e8a4e6ffff'),
            0x1a133c: (0x1a0e0c, '558bec83ec0c'),
            0x1a191b: (0x1a13eb, '558bec83ec0c'),
            0x1b098b: (0x1b045b, 'e0010000'),
            0x1b0990: (0x1b0460, '80020000'),
            0x1b532e: (0x1b4dfe, '558bec81ec8c000000'),
            0x1c4dd3: (0x1c48a3, 'e0010000'),
            0x1c4dd8: (0x1c48a8, '80020000'),
            0x1c4f73: (0x1c4a42, '60756c00'),
            0x1c4fc9: (0x1c4a98, 'e07c6c00'),
            0x1c5011: (0x1c4ae0, '80020000'),
            0x1c501e: (0x1c4aed, 'e0010000'),
            0x1c617a: (0x1c5c93, 'e0010000'),
            0x1c617f: (0x1c5c98, '80020000'),
            0x1c6894: (0x1c63ad, 'e872f3ffff'),
            0x1c68ec: (0x1c6405, 'f60530f56b0004'),
            0x1c68fc: (0x1c6415, 'e0010000'),
            0x1c6901: (0x1c641a, '80020000'),
            0x1c6d3d: (0x1c6856, 'e0010000'),
            0x1c6d42: (0x1c685b, '80020000'),
            0x1c6daa: (0x1c68c3, '6a1068f0000000'),
            0x1c751b: (0x1c7034, 'e819020000'),
            0x1c753a: (0x1c7053, 'e88883ebff'),
            0x1c754c: (0x1c7065, 'e8c6eff9ff'),
            0x1c7566: (0x1c707f, 'e86c9c0000'),
            0x1c7578: (0x1c7091, 'e82a4b0100'),
            0x1c7588: (0x1c70a1, 'e85a87ebff'),
            0x1c759a: (0x1c70b3, 'e8a8f3f9ff'),
            0x1c7726: (0x1c7261, '0000803f'),
            0x1c7730: (0x1c726b, '0000003f'),
            0x1c7775: (0x1c72b0, '3333733f'),
            0x1c782d: (0x1c7368, 'd9057cc16b00833ddc09a000007508dc3510476200eb11ff3514476200ff3510476200e828d30100d91d7cc16b00d90580c16b00dc0d18476200d91d80c16b00d905f08a6c00dc0d18476200d91df08a6c00'),
            0x1c78f0: (0x1c7429, '80020000'),
            0x1c7907: (0x1c7440, '28000000'),
            0x1c794a: (0x1c7481, '80020000'),
            0x1c7961: (0x1c7498, '28000000'),
            0x1c799b: (0x1c74cd, '8d04408d0480c1e004'),
            0x1c79bd: (0x1c74f5, '004b0000'),
            0x1c7a2c: (0x1c7564, '28000000'),
            0x1c7a8e: (0x1c75c6, '28000000'),
            0x1c7ad2: (0x1c760a, 'c1e1038d0c498d0c89'),
            0x1c7af4: (0x1c762c, '80250000'),
            0x1c7b72: (0x1c76aa, '80020000'),
            0x1c7b89: (0x1c76c1, '28000000'),
            0x1c7bb1: (0x1c76e9, '8d04408d0480c1e004'),
            0x1c7bd0: (0x1c7708, '004b0000'),
            0x1c7c0e: (0x1c7746, '28000000'),
            0x1c7c36: (0x1c776e, 'c1e0038d04408d0480'),
            0x1c7c55: (0x1c778d, '80250000'),
            0x1c7cb8: (0x1c77f0, '02'),
            0x1c7cc5: (0x1c77fd, '40010000'),
            0x1c7ccf: (0x1c7807, 'f0000000'),
            0x1c7cf2: (0x1c782a, '40010000'),
            0x1c7cfc: (0x1c7834, 'f0000000'),
            0x1c7d68: (0x1c78a0, 'f0010000'),
            0x1c7d72: (0x1c78aa, '80010000'),
            0x1c7d77: (0x1c78af, '80020000'),
            0x1c7d89: (0x1c78c1, 'e0010000'),
            0x1c7da5: (0x1c78dd, '80020000'),
            0x1c7daf: (0x1c78e7, 'e0010000'),
            0x1c7e3a: (0x1c7972, '02'),
            0x1c824e: (0x1c7d86, 'e0010000'),
            0x1c834b: (0x1c7e83, '80020000'),
            0x1c8435: (0x1c7f6d, '50010000'),
            0x1c843c: (0x1c7f74, '80020000'),
            0x1c8443: (0x1c7f7b, 'e0010000'),
            0x1c8491: (0x1c7fc9, 'b0010000'),
            0x1c8498: (0x1c7fd0, '80020000'),
            0x1c849f: (0x1c7fd7, 'e0010000'),
            0x1c84d6: (0x1c800e, 'b0010000'),
            0x1c84dd: (0x1c8015, '80020000'),
            0x1c84e4: (0x1c801c, 'e0010000'),
            0x1c8810: (0x1c8348, '80020000'),
            0x1c881d: (0x1c8355, 'e0010000'),
            0x1c898b: (0x1c84c3, 'e0010000'),
            0x1c8990: (0x1c84c8, '80020000'),
            0x1c8a88: (0x1c85c0, 'a120866c00'),
            0x1c8ab9: (0x1c85f1, 'e0010000'),
            0x1c8abe: (0x1c85f6, '80020000'),
            0x1c8b4d: (0x1c8685, 'e0010000'),
            0x1c8b52: (0x1c868a, '80020000'),
            0x1c8e8d: (0x1c89c5, '40010000'),
            0x1c8e94: (0x1c89cc, 'a0000000'),
            0x1c8f1d: (0x1c8a55, '40010000'),
            0x1c8f24: (0x1c8a5c, 'a0000000'),
            0x1c90b8: (0x1c8bf0, '60756c00'),
            0x1c90e9: (0x1c8c21, '60756c00'),
            0x1cb79d: (0x1cb2dd, '558bec535657'),
            0x1cb7de: (0x1cb31e, '558bec535657'),
            0x1cb8c6: (0x1cb406, '558bec535657'),
            0x1cd5d4: (0x1cd114, 'a00d6d008d05a01c6d00b9e0010000890783c05089470483c05083c70883e9027fed'),
            0x1cd5da: (0x1cd11a, 'a01c6d00'),
            0x1cd605: (0x1cd145, 'a01c6d00'),
            0x1cd60c: (0x1cd14c, '80250000'),
            0x1cd8a5: (0x1cd3e5, 'a01c6d00'),
            0x1cd8b8: (0x1cd3f8, '83c850'),
            0x1cd9db: (0x1cd51b, 'a01c6d00'),
            0x1cd9ee: (0x1cd52e, '83c850'),
            0x1ce0f9: (0x1cdc39, 'a01c6d00'),
            0x1ce11b: (0x1cdc5b, '83c850'),
            0x1ce988: (0x1ce4c8, 'a1a88c6c004683c0505ba3a88c6c00'),
            0x1ce9de: (0x1ce51e, 'a1a88c6c0083c05046a3a88c6c00'),
            0x1cf028: (0x1ceb68, 'a1a88c6c004683c0505ba3a88c6c00'),
            0x1cf07e: (0x1cebbe, 'a1a88c6c0083c05046a3a88c6c00'),
            0x1cf6d8: (0x1cf218, 'a1a88c6c004683c0505ba3a88c6c00'),
            0x1cf72e: (0x1cf26e, 'a1a88c6c0083c05046a3a88c6c00'),
            0x1cfd88: (0x1cf8c8, 'a1a88c6c004683c0505ba3a88c6c00'),
            0x1cfdde: (0x1cf91e, 'a1a88c6c0083c05046a3a88c6c00'),
            0x1cfe32: (0x1cf972, '50000000'),
            0x1cfe90: (0x1cf9d0, '50000000'),
            0x1d0eac: (0x1d09ec, 'c4090000'),
            0x1d0ed0: (0x1d0a10, '7cda6f00'),
            0x1d11d8: (0x1d0d18, '80da6f00'),
            0x1d161f: (0x1d115f, 'a01c6d00'),
            0x1d162c: (0x1d116c, '83c850'),
            0x1d1a30: (0x1d1570, 'a01c6d00'),
            0x1d1a3d: (0x1d157d, '83c850'),
            0x1d1deb: (0x1d192b, 'a1a88c6c000ffefe83c0505ea3a88c6c00'),
            0x1d1e22: (0x1d1962, 'a1a88c6c000ffefe83c0508b0d44f56b00a3a88c6c00'),
            0x1d2111: (0x1d1c51, 'a1a88c6c000ffefe83c0505ea3a88c6c00'),
            0x1d2152: (0x1d1c92, 'a1a88c6c000ffefe83c0508b0d44f56b00a3a88c6c00'),
            0x1d395b: (0x1d349b, 'c4090000'),
            0x1d3967: (0x1d34a7, 'a0b76d00'),
            0x1d3970: (0x1d34b0, '608c6f00'),
            0x1d3a28: (0x1d3568, '8b349d90017000'),
            0x1d46ae: (0x1d41ee, 'c4090000'),
            0x1d46ba: (0x1d41fa, 'a0b76d00'),
            0x1d46c3: (0x1d4203, '608c6f00'),
            0x1d4760: (0x1d42a0, '8b349d90017000'),
            0x1d876b: (0x1d82ab, 'a01c6d00'),
            0x1d877e: (0x1d82be, '83c850'),
            0x1d88a7: (0x1d83e7, 'a01c6d00'),
            0x1d88ba: (0x1d83fa, '83c850'),
            0x1d8fcf: (0x1d8b0f, 'a01c6d00'),
            0x1d8ff1: (0x1d8b31, '83c850'),
            0x1d9858: (0x1d9398, 'a1a88c6c004683c0505ba3a88c6c00'),
            0x1d98ae: (0x1d93ee, 'a1a88c6c0083c05046a3a88c6c00'),
            0x1d9ef8: (0x1d9a38, 'a1a88c6c004683c0505ba3a88c6c00'),
            0x1d9f4e: (0x1d9a8e, 'a1a88c6c0083c05046a3a88c6c00'),
            0x1da5a8: (0x1da0e8, 'a1a88c6c004683c0505ba3a88c6c00'),
            0x1da5fe: (0x1da13e, 'a1a88c6c0083c05046a3a88c6c00'),
            0x1dac58: (0x1da798, 'a1a88c6c004683c0505ba3a88c6c00'),
            0x1dacae: (0x1da7ee, 'a1a88c6c0083c05046a3a88c6c00'),
            0x1dad02: (0x1da842, '50000000'),
            0x1dad60: (0x1da8a0, '50000000'),
            0x1dbd7c: (0x1db8bc, 'c4090000'),
            0x1dbda0: (0x1db8e0, 'cc3f7200'),
            0x1dc0a8: (0x1dbbe8, 'd03f7200'),
            0x1dc4f8: (0x1dc038, 'a01c6d00'),
            0x1dc502: (0x1dc042, '83c850'),
            0x1dc919: (0x1dc459, 'a01c6d00'),
            0x1dc923: (0x1dc463, '83c850'),
            0x1dcccb: (0x1dc80b, 'a1a88c6c000ffefe83c0505ea3a88c6c00'),
            0x1dcd02: (0x1dc842, 'a1a88c6c000ffefe83c0508b0d4cf56b00a3a88c6c00'),
            0x1dcff1: (0x1dcb31, 'a1a88c6c000ffefe83c0505ea3a88c6c00'),
            0x1dd032: (0x1dcb72, 'a1a88c6c000ffefe83c0508b0d4cf56b00a3a88c6c00'),
            0x1de86b: (0x1de3ab, 'd0070000'),
            0x1de877: (0x1de3b7, '508a7000'),
            0x1de880: (0x1de3c0, '50017200'),
            0x1de938: (0x1de478, '8b349d105f7200'),
            0x1df5fe: (0x1df13e, 'd0070000'),
            0x1df60a: (0x1df14a, '508a7000'),
            0x1df613: (0x1df153, '50017200'),
            0x1df6b0: (0x1df1f0, '8b349d105f7200'),
            0x1fb4a0: (0x1faea0, '000080c3'),
            0x1fb4a4: (0x1faea4, '00008043'),
            0x205d38: (0x205728, '000080c3'),
            0x205d3c: (0x20572c, '00008043'),
            0x2213f0: (0x220de0, '52b81e85eb913f40'),
            0x2213f8: (0x220de8, 'ae47e17a146e3c40'),
            0x2baff4: (0x2ba98c, '00000047'),
            0x2baffc: (0x2ba994, '00000048'),
            0x2c734c: (0x2c6ce4, '40000000'),
            0x2c7350: (0x2c6ce8, '40000000'),
            0x2c7354: (0x2c6cec, 'b0000000'),
            0x2c7358: (0x2c6cf0, '00010000'),
            0x2c735c: (0x2c6cf4, '78000000'),
            0x2c7360: (0x2c6cf8, '78000000'),
            0x2c7370: (0x2c6d08, '18000000'),
            0x2c73f0: (0x2c6d88, '18000000'),
        },
    },
}
# PORT TABLES END



# GENERATED - every game-address dword in UI_CODE, by blob offset.
# tools/hiresport.py translates the targets; install() swaps them
# in place for non-retail builds. Regenerate if ui.asm changes.
# UI REFS BEGIN
UI_REFS = (
    (0x0023, 0x06bc1e4),
    (0x002d, 0x06bc1e8),
    (0x0039, 0x06bc1e4),
    (0x003f, 0x06bc1e8),
    (0x006e, 0x06bc1e4),
    (0x0078, 0x06bc1e8),
    (0x0083, 0x06bc1e4),
    (0x008f, 0x06bc1e4),
    (0x00a4, 0x06db4c8),
    (0x00af, 0x06db4d0),
    (0x00ba, 0x06db4d4),
    (0x00c8, 0x06bc1e8),
    (0x00db, 0x06db4d0),
    (0x00e6, 0x06db530),
    (0x00f1, 0x06db534),
    (0x0124, 0x06c8b28),
    (0x012e, 0x06c8b24),
    (0x013a, 0x06c8b28),
    (0x0140, 0x06c8b24),
    (0x0173, 0x06c8b28),
    (0x0179, 0x06c8b24),
    (0x0184, 0x06c8b24),
    (0x0194, 0x06c8b24),
    (0x01a5, 0x0708818),
    (0x01b0, 0x070881c),
    (0x01bb, 0x0708820),
    (0x01c9, 0x06c8b28),
    (0x01dc, 0x070881c),
    (0x01e7, 0x0708870),
    (0x01f2, 0x0708874),
    (0x0221, 0x06bc1e8),
    (0x0227, 0x06db4c8),
    (0x022c, 0x06bc1e8),
    (0x023f, 0x06db4d0),
    (0x024a, 0x06db4d4),
    (0x0255, 0x06db530),
    (0x0260, 0x06db534),
    (0x0277, 0x06db4c8),
    (0x0282, 0x06db4d0),
    (0x028d, 0x06db4d4),
    (0x0298, 0x06db530),
    (0x02a3, 0x06db534),
    (0x02ba, 0x051457c),
    (0x02de, 0x06c8b28),
    (0x02e4, 0x0708818),
    (0x02e9, 0x06c8b28),
    (0x02fc, 0x070881c),
    (0x0307, 0x0708820),
    (0x0312, 0x0708870),
    (0x031d, 0x0708874),
    (0x0334, 0x0708818),
    (0x033f, 0x070881c),
    (0x034a, 0x0708820),
    (0x0355, 0x0708870),
    (0x0360, 0x0708874),
    (0x0377, 0x05cc4cc),
    (0x042c, 0x06bf5b8),
    (0x0437, 0x06bf5bc),
    (0x0442, 0x06db4c8),
    (0x044d, 0x06db4d0),
    (0x0458, 0x06db4d4),
    (0x0463, 0x0708818),
    (0x046e, 0x070881c),
    (0x0479, 0x0708820),
    (0x0484, 0x06db530),
    (0x048f, 0x06db534),
    (0x049a, 0x0708870),
    (0x04a5, 0x0708874),
    (0x0513, 0x06bf5a8),
    (0x0522, 0x06db534),
    (0x052d, 0x0708874),
    (0x0538, 0x06db530),
    (0x0543, 0x0708870),
    (0x0564, 0x06bf5a8),
    (0x0573, 0x06d0dc4),
    (0x0593, 0x06bf5b0),
    (0x05b6, 0x06bf5b0),
    (0x06c4, 0x06bf5ac),
    (0x06cf, 0x06bf5b8),
    (0x06da, 0x06bf5bc),
    (0x06e5, 0x06bc948),
    (0x06f1, 0x06bc948),
    (0x070b, 0x06bf598),
    (0x086c, 0x1ae3594),
    (0x0872, 0x1ae3690),
    (0x087c, 0x06bf5a8),
    (0x0884, 0x1ef8a90),
    (0x088a, 0x1ef9eb0),
    (0x0a43, 0x06bf5ac),
    (0x0a52, 0x06bf5b8),
    (0x0a5d, 0x06bf5bc),
    (0x0a92, 0x06bc948),
    (0x0ab6, 0x06bf5ac),
    (0x0ac1, 0x06bf5b8),
    (0x0acc, 0x06bf5bc),
    (0x0afc, 0x06bf5a8),
    (0x0c2d, 0x06bf5a8),
    (0x0c72, 0x06bf5a8),
    (0x0c8d, 0x06bc948),
    (0x0c99, 0x06bc948),
    (0x0ca7, 0x1cc6700),
    (0x0cb1, 0x34155c8),
    (0x0cbb, 0x34155d0),
    (0x0cc5, 0x0480410),
    (0x0ccf, 0x04803d0),
    (0x0cd9, 0x0bf5f7c),
    (0x0ce3, 0x0bf5f78),
    (0x0ced, 0x06bf5a8),
    (0x0cf9, 0x1ef1140),
    (0x0d03, 0x1efb728),
    (0x0d0d, 0x1efb730),
    (0x0d17, 0x0567400),
    (0x0d21, 0x05673c0),
    (0x0d2b, 0x1ad0034),
    (0x0d35, 0x1ad0030),
    (0x0f1a, 0x06bc948),
    (0x0f27, 0x1ae3594),
    (0x0f35, 0x1ae3690),
    (0x0f47, 0x1ef8a90),
    (0x0f55, 0x1ef9eb0),
    (0x0f76, 0x1ae5f40),
    (0x0f7d, 0x1ae5f5c),
    (0x0f83, 0x1ae5f40),
    (0x0f9d, 0x05c991c),
    (0x0fa9, 0x1ae5f40),
    (0x0fff, 0x07001d0),
    (0x1028, 0x0725f50),
    (0x1039, 0x0791ad0),
    (0x1211, 0x06bc948),
    (0x121f, 0x1ae3594),
    (0x1228, 0x1ae3690),
    (0x1235, 0x1ef8a90),
    (0x123e, 0x1ef9eb0),
    (0x124b, 0x1ae3690),
    (0x125e, 0x06bc948),
    (0x1264, 0x06bc948),
    (0x1271, 0x05c8317),
    (0x127c, 0x06bc948),
    (0x1281, 0x06bf5a8),
    (0x1286, 0x06bf5b0),
    (0x128c, 0x07087a0),
    (0x129b, 0x05c8317),
    (0x12da, 0x05d1db0),
    (0x12e9, 0x06c84c8),
    (0x12ef, 0x06c84c8),
    (0x12fb, 0x06c84c8),
    (0x1316, 0x05dcc80),
    (0x1325, 0x06c84cc),
    (0x132b, 0x06c84cc),
    (0x1337, 0x06c84cc),
    (0x1361, 0x345bd58),
    (0x1374, 0x345b2c8),
    (0x13d8, 0x059cb93),
    (0x1425, 0x05c755a),
    (0x143e, 0x05c56a2),
    (0x1484, 0x05ce180),
    (0x148c, 0x06c866c),
    (0x1497, 0x05c8ca0),
    (0x14b5, 0x0be4300),
    (0x14c7, 0x0427ec9),
    (0x14ea, 0x06bf598),
    (0x14f0, 0x0428241),
    (0x1504, 0x06bc94c),
    (0x1520, 0x05c680b),
    (0x1533, 0x06bf598),
    (0x153f, 0x06bf598),
    (0x1553, 0x06a0240),
    (0x1558, 0x050bcc6),
    (0x156b, 0x06bf598),
    (0x1577, 0x050c0cb),
    (0x158a, 0x066c180),
    (0x15b3, 0x047f2e0),
    (0x15bd, 0x06bf5ac),
    (0x15d9, 0x06bf5bc),
    (0x15e3, 0x066c190),
    (0x1629, 0x0408790),
    (0x1634, 0x0514430),
)
# UI REFS END

# Every game address this tool bakes into written bytes, the ui blob, or
# its hook tables. PORT (generated by tools/hiresport.py) carries these
# per build; retail is the identity.
ADDR = {
    # ui.asm globals compiled into UI_CODE
    'FB_PITCH': 0x6bf5ac, 'FB_W': 0x6bf5b8, 'FB_H': 0x6bf5bc,
    'SPLIT': 0x6bc948, 'FLAGS': 0x6bf598, 'DRAWN': 0x6d0dc4,
    'PROJ_A': 0x6db4c8, 'ASPECT_A': 0x6bc1e8, 'PROJ_B': 0x708818,
    'ASPECT_B': 0x6c8b28, 'SCALE_A': 0x6bc1e4, 'SCALE_B': 0x6c8b24,
    'CENTRE_AX': 0x6db530, 'CENTRE_BX': 0x708870, 'CENTRE_A': 0x6db534,
    'CENTRE_B': 0x708874, 'LIST_A': 0x7001d0, 'LIST_B': 0x725f50,
    'PIXFMT': 0x33cd5f4,
    # plane B of each 2D engine, for the margins (see ui.asm)
    'RING1': 0x1cc6700, 'SCRX1': 0x34155c8, 'SCRY1': 0x34155d0,
    'DEST1': 0x480410, 'BLIT1': 0x4803d0, 'WMA1': 0xbf5f7c,
    'WMB1': 0xbf5f78,
    'RING2': 0x1ef1140, 'SCRX2': 0x1efb728, 'SCRY2': 0x1efb730,
    'DEST2': 0x567400, 'BLIT2': 0x5673c0, 'WMA2': 0x1ad0034,
    'WMB2': 0x1ad0030,
    # the four 2D calls and their stubs' call sites
    'CALL_PRE1': 0x4800d0, 'CALL_POST1': 0x4804f0,
    'CALL_PRE2': 0x5670c0, 'CALL_POST2': 0x5674f0,
    'STUB1': 0x5c813a, 'STUB2': 0x5c8188, 'STUB3': 0x5c814c,
    'STUB4': 0x5c819a,
    # projection setups and submit hooks
    'WORLD1': 0x51444d, 'WORLD2': 0x51448e, 'WORLD3': 0x5cc39d,
    'WORLD4': 0x5cc3de, 'SUBMIT_A': 0x514576, 'SUBMIT_B': 0x5cc4c6,
    # the wrapped pass prologues
    'PASS0': 0x5b5f2e, 'PASS1': 0x4c468e, 'PASS2': 0x55d221,
    'PASS3': 0x5495b1, 'PASS4': 0x5a1f3c, 'PASS5': 0x5a251b,
    'PASS6': 0x58881e, 'PASS7': 0x588d85, 'PASS8': 0x4d0280,
    'PASS9': 0x531f6a, 'PASS10': 0x4d9c3d, 'PASS11': 0x42cda6,
    'PASS12': 0x460b70, 'PASS13': 0x460cf3, 'PASS14': 0x432fbe,
    'PASS15': 0x433141, 'PASS16': 0x57f1b0, 'PASS17': 0x5829c3,
    'PASS18': 0x4b6030, 'PASS19': 0x4b981f,
    # addresses in written bytes: the coverage mask pointer, the FOV
    # block, and the F4 fall-through
    'MASKPTR': 0x6c8ce8, 'SPRITEMODE': 0x66c17c,
    'F4EXIT': 0x5c7dfe, 'F4CASE': 0x5c79aa,
    'FCONST': 0x624728,
}

# UI CODE BEGIN
UI_ASM_SHA = 'd2b7d6ee0c3c1bb901eb1fc74dc49148e9f030836eae82625f3ac26fd25d3bb5'
UI_CODE = bytes.fromhex(
    '53e8000000005b81eb060000008b4424088983e8180000c783ec18000000000000d905e4'
    'c16b00d84c2408d80de8c16b00d99bdc180000d905e4c16b00d80de8c16b00d99be01800'
    '00eb4253e8000000005b81eb510000008b4424088983e8180000c783ec18000001000000'
    'd905e4c16b00d84c2408d80de8c16b00d99bdc180000a1e4c16b008983e0180000d905e4'
    'c16b00d84c2408d99be41800008b83dc180000a3c8b46d008b83e0180000a3d0b46d008b'
    '83e4180000a3d4b46d0083bb08190000007438a1e8c16b0083bbec180000007405b80000'
    '803fa3d0b46d008b83c8190000a330b56d008b83cc190000a334b56d00c783c018000001'
    '0000005bc353e8000000005b81eb070100008b4424088983fc180000c783001900000000'
    '0000d905288b6c00d84c2408d80d248b6c00d99bf0180000d905288b6c00d80d248b6c00'
    'd99bf4180000eb4253e8000000005b81eb520100008b4424088983fc180000c783001900'
    '0001000000d9442408d80d288b6c00d80d248b6c00d99bf0180000a1248b6c008983f418'
    '0000d9442408d80d248b6c00d99bf81800008b83f0180000a3188870008b83f4180000a3'
    '1c8870008b83f8180000a32088700083bb08190000007438a1288b6c0083bb0019000000'
    '7405b80000803fa31c8870008b83c8190000a3708870008b83cc190000a374887000c783'
    'c4180000010000005bc35350e8000000005b81eb0902000083bb08190000007457d983e8'
    '180000d80de8c16b00d91dc8b46d00a1e8c16b0083bbec180000007405b80000803fa3d0'
    'b46d008b83e8180000a3d4b46d008b83c8190000a330b56d008b83cc190000a334b56d00'
    'c783c018000001000000eb418b83dc180000a3c8b46d008b83e0180000a3d0b46d008b83'
    'e4180000a3d4b46d008b83b8180000a330b56d008b839c180000a334b56d00c783c01800'
    '0000000000585b5589e5535657687c455100c35350e8000000005b81ebc602000083bb08'
    '190000007457d983fc180000d80d288b6c00d91d18887000a1288b6c0083bb0019000000'
    '7405b80000803fa31c8870008b83fc180000a3208870008b83c8190000a3708870008b83'
    'cc190000a374887000c783c418000001000000eb418b83f0180000a3188870008b83f418'
    '0000a31c8870008b83f8180000a3208870008b83bc180000a3708870008b83a0180000a3'
    '74887000c783c418000000000000585b5589e553565768ccc45c00c3535051e800000000'
    '5b81eb840300008b83d819000083f820732f8b8b08190000898c83dc19000041898b0819'
    '0000ff83d81900008b4c2410898c830c1900008d8bc9030000894c241059585bc35350e8'
    '000000005b81ebd0030000ff8bd81900008b83d8190000508b8483dc1900008983081900'
    '0085c07505e812000000588b84830c1900008704248b5c2404c2040050c783c018000000'
    '000000c783c4180000000000008b830818000085c07410a3b8f56b008b830c180000a3bc'
    'f56b008b83dc180000a3c8b46d008b83e0180000a3d0b46d008b83e4180000a3d4b46d00'
    '8b83f0180000a3188870008b83f4180000a31c8870008b83f8180000a3208870008b83b8'
    '180000a330b56d008b839c180000a334b56d008b83bc180000a3708870008b83a0180000'
    'a37488700058c3e84f000000e8ed010000e816fc4700e8bf050000c3e88b000000e8d801'
    '0000e821004800e8aa050000c3e8a6000000e8c3010000e8dc6b5600e895050000c3e8b4'
    '000000e8ae010000e8f76f5600e880050000c35350e8000000005b81eb06050000c7831c'
    '180000a8f56b00c7833818000001000000a134b56d0089839c180000a1748870008983a0'
    '180000a130b56d008983b8180000a1708870008983bc180000585bc35350e8000000005b'
    '81eb57050000c7831c180000a8f56b00c7833818000000000000a1c40d6d008983501800'
    '00585bc353e8000000005b81eb86050000c7831c180000b0f56b00c78338180000010000'
    '005bc353e8000000005b81eba9050000c7831c180000b0f56b00c7833818000000000000'
    '5bc38b838c1a00008b84836818000089835c18000031c0ba01000000f7b35c18000085d2'
    '7401408983101800008983141800008b8b281800000faf8b5c180000c1e9108b83081800'
    '0029c8d1f8c783601800000000000085c0791301c1f7d80faf8310180000898360180000'
    '31c08983341800008b930818000029c239d17e0289d1898b301800008b8b2c1800000faf'
    '8b5c180000c1e9108b830c18000029c8d1f8c783641800000000000085c0791301c1f7d8'
    '0faf831418000089836418000031c08983441800008b930c18000029c239d17e0289d189'
    '8b40180000c360e8000000005b81eba80600008bb31c1800008b0689830018000085c075'
    '0261c3a1acf56b00898304180000a1b8f56b00898308180000a1bcf56b0089830c180000'
    'a148c96b00898354180000c70548c96b000000000031c985c0741483bb881a000000750b'
    '41f60598f56b0003740141898b8c1a00008b8c8b74180000898b80180000d98380180000'
    'd88ba8180000db9bc8180000db8308180000d98380180000d88bac180000dee9d88bb418'
    '0000db9bcc180000db830c180000d98380180000d88bb0180000dee9d88bb4180000db9b'
    'd0180000db8308180000d88bb4180000d8b380180000db9bc8190000db830c180000d88b'
    'b4180000d8b380180000db9bcc1900008b83c81900002d400100000faf83c81800008b8b'
    'cc180000c1e11029c1898bd01900008b83cc1900002df00000000faf83c81800008b8bd0'
    '180000c1e11029c1898bd4190000c7832c180000e0010000b8e001000083bb3818000000'
    '742a83bb8c1a00000075210faf830818000031d2f7b30c1800004083e0fe3d000400007e'
    '13b800040000eb0cc1e00231d2b903000000f7f18983281800002d80020000d1f88983cc'
    '1a000083c007c1f8038983d01a0000e866fdffffc78388180000000000008b0d9435ae01'
    '8b159036ae0181bb1c180000a8f56b00740c8b0d908aef018b15b09eef0183f9047511e8'
    '100a0000740ac7838818000001000000c783701a000000000000c783781a000000000000'
    '8b836c1a000085c0745783bb8c1a000000744e83bbd0180000007e4583bb881800000074'
    '3c83fa0d743783fa0e7432c783701a0000010000008b8bd0180000c1e110898b741a0000'
    '83bb381800000075100faf835c180000c1e8108983781a0000c78318180000000000008d'
    'bb003b0f008bab44180000c1e5108b8364180000c1e8100faf835c18000029c589ab841a'
    '000083bb781a000000740231ed89e8c1f81078683b830c1800007d600faf830418000003'
    '830018000089c68b9334180000c1e2108b8360180000c1e8100faf835c18000029c231c9'
    '89d0c1f810780e3b83081800007d06668b0446eb0231c06689044f6689844f00001e0003'
    '935c180000413b8b281800007cceeb1e5731c08b8b28180000f366ab5f5781c700001e00'
    '8b8b28180000f366ab5f81c70008000003ab5c180000ff83181800008b831818000083bb'
    '781a000000741d3b836c1a000075158bab841a00000faf835c18000001c58b8318180000'
    '3b832c1800000f8c29ffffff8b83cc1a00008d8443003b0f008bb31c1800008906c705ac'
    'f56b00000800008b8328180000a3b8f56b008b832c180000a3bcf56b008bbb3c18000031'
    'c031c989048f05000800004181f9e00100007cef61c360e8000000005b81eb840a00008b'
    '8354180000a348c96b008bb31c1800008b830018000085c0750261c3e88d01000089068b'
    '8304180000a3acf56b008b8308180000a3b8f56b008b830c180000a3bcf56b008bbb3c18'
    '000031c031c989048f038304180000413b8b0c1800007cee8b83881a000085c0742081bb'
    '1c180000a8f56b00750b83f8020f840a010000eb0983f8010f84ff000000e8abfaffff8b'
    '83401800008983801a00008b834418000083bb781a000000740e8b83781a00008983801a'
    '000031c00faf83041800000383001800008bbb341800008d3c788b836418000089835818'
    '0000c78318180000000000008bb358180000c1ee1069f6000800008db433003b0f008b93'
    '6018000031c989d5c1ed10668b046e663b846e00001e0074046689044f03931018000041'
    '3b8b301800007cda03bb041800008b8314180000018358180000ff83181800008b831818'
    '00003b83801a00007c963b83401800007d338b83401800008983801a00008b8318180000'
    '0383441800000faf83041800000383001800008bbb341800008d3c78e95bffffff83bb98'
    '1a000000741a83bb3818000000751181bb1c180000a8f56b007505e8e802000061c36083'
    'bbcc1a0000000f84d602000083bb38180000000f84c902000083bb8c1a0000000f85bc02'
    '00008b83881a000085c0742081bb1c180000a8f56b00750b83f8020f849d020000eb0983'
    'f8010f8492020000a148c96b008983601a0000c70548c96b0000000000c783d41a000000'
    '67cc01c783d81a0000c8554103c783dc1a0000d0554103c783e01a000010044800c783e4'
    '1a0000d0034800c783e81a00007c5fbf00c783ec1a0000785fbf0081bb1c180000a8f56b'
    '007446c783d41a00004011ef01c783d81a000028b7ef01c783dc1a000030b7ef01c783e0'
    '1a000000745600c783e41a0000c0735600c783e81a00003400ad01c783ec1a00003000ad'
    '018b83cc1a00008db443003b0f000fb706b980020000663b06750883c6024975f5eb0231'
    'c08983fc1a00008b83dc1a00000fb70089c183e107898b5c1a0000c1e80383e07f83f83e'
    '720383e83e8983f81a00008b83d81a00000fb700c1e80383e07f8983f41a0000c783f01a'
    '0000000000008b83f01a00002b83f81a0000790383c03e69c0a40000000383d41a000089'
    'c631ff83bbf01a00003b750f8bbb5c1a000085ff7405f7df83c7088b83dc1a0000f64001'
    '800f859c000000b95000000089f266f702ff3f0f848a00000083c2024975ef8babd01a00'
    '00f7dd89e985c9790583c150eb0383e9508b83f41a000039c1730383c15229c10fb70c4e'
    'f7c1ff3f0000743c89c825ff3f00008b93e81a0000f7c10040000074068b93ec1a00003b'
    '02731d5189e98b93f01a0000ff93e01a00005985c0740989c257ff93e41a0000457505bd'
    '500000008b83d01a000083c05039c57c86eb7183bb501800000075688b83f01a0000c1e0'
    '0303835c1a0000b9080000003de00100007c052de0010000505169c0000800008d940300'
    '3b0f008b83fc1a000089d78b8bcc1a0000f366ab8b8bcc1a00008dbc4a000500008b8b28'
    '1800002b8bcc1a000081e980020000f366ab5958404975acff83f01a000083bbf01a0000'
    '3c0f8c97feffff8b83601a0000a348c96b0061c38dbba01a0000a19435ae01e87e000000'
    'c6072047a19036ae01e87000000066c707202083c702a1908aef01e85e000000c6072047'
    'a1b09eef01e85000000066c707202083c7028b83881a0000e83d000000c607008b0d405f'
    'ae01518b0d5c5fae01890d405fae016a016800ff00006a28682c0100008d83a01a000050'
    'b81c995c00ffd083c41459890d405fae01c350c1e804e8010000005883e00f04303c3976'
    '020407880747c360e8000000005b81ebcd0f00008b83641a000085c0740a0faf420cc1e8'
    '1089420c83bbc0180000007405e802010000e832000000618b349dd0017000c360e80000'
    '00005b81eb0a10000083bbc4180000007405e8d9000000e809000000618b349d505f7200'
    'c383bb8818000000744b817a04d01a790075428b83cc18000083c0088b8b081800002b8b'
    'cc18000083e9098d7210bf040000000fbf2e39c57f0766c7060000eb0e39cd7c0a8bab08'
    '1800004d66892e83c6044f75dec3c783d4180000ffffff7fc783d8180000ffffff7fc783'
    'b019000001000080c783b4190000010000808d7210b9040000000fbf063b83d41800007d'
    '068983d41800003b83b01900007e068983b01900000fbf46023b83d81800007d068983d8'
    '1800003b83b41900007e068983b419000083c6044975bbc3e885ffffff8b83d419000083'
    'bb701a00000074208bbbb41900002bbbcc19000081c7f00000003bbb6c1a00007d062b83'
    '741a000089837c1a00008d721031c90fbf063b83d418000075168bbbb01900003bbbd418'
    '000075218d79ff83ff027319400faf83c81800000383d01900000500800000c1f81048eb'
    '150faf83c81800000383d01900000500800000c1f8100fbf7e023bbbd8180000751a508b'
    '83b41900003b83d8180000587524508d41ff83f80258731a470fafbbc818000003bb7c1a'
    '000081c700800000c1ff104feb160fafbbc818000003bb7c1a000081c700800000c1ff10'
    '25ffff0000c1e71009f8890683c6044183f9040f8c42ffffffc3535152e8000000005b81'
    'ebfe110000c783881a000000000000833d48c96b0000747eb901000000833d9435ae0104'
    '75308b159036ae01e8770000007563833d908aef0104751a8b15b09eef01e86100000075'
    '4d3b159036ae017305b902000000898b881a0000ff3548c96b00c70548c96b0000000000'
    'ff742414b817835c00ffd083c4048f0548c96b00a1a8f56b00a3b0f56b00c705a0877000'
    '00000000eb0eff742410b817835c00ffd083c4045a595bc383fa40731b5189d183e11fb8'
    '01000000d3e089d1c1e90585848b901a000059c331c0c35350e8000000005b81ebd21200'
    '00b8b01d5d0083bb881a000002751bff35c8846c00c705c8846c0001000000ffd08f05c8'
    '846c00585bc3ffd0585bc35350e8000000005b81eb0e130000b880cc5d0083bb881a0000'
    '01751bff35cc846c00c705cc846c0001000000ffd08f05cc846c00585bc3ffd0585bc353'
    'e8000000005b81eb4913000050528b45088d04808b55088d0482d9048558bd45038b450c'
    '8d04808b550c8d0482d82485c8b24503d88305140000d88b09140000d8930d140000dfe0'
    'f6c4017408ddd8d9830d140000d9e8d8d9dfe0f6c401750ed88ba8180000db9b641a0000'
    'eb0cddd8c783641a0000000000005a58508b4424088983681a00008d83dd130000894424'
    '08585b6893cb5900c353e8000000005b81ebe3130000c783641a000000000000ffb3681a'
    '00008b5c240483c408ff6424f8a470e341abaaaa3d0000803760e8000000005b81eb1714'
    '0000e80700000061685a755c00c3e8220000006a10ffb324180000ffb320180000b8a256'
    '5c00ffd083c40c85c07505e801000000c38b83c01a000083f0018983c01a00008bb3c41a'
    '00008b3e85ff74178b4e0483c6085685c0740201ce89caf3a45e8d3456ebe3b880e15c00'
    'ffd0c7056c866c00000000006a01b8a08c5c00ffd083c404c353e8000000005b81eba714'
    '00000b83c01a0000a30043be00ffb3c01a00008f83c81a00005b68c97e4200c353e80000'
    '00005b81ebd21400008983c81a000083a3c81a00000183e0fea398f56b005b6841824200'
    'c360e8000000005b81ebfb140000833d4cc96b000274138b83c81a00003b83c01a000074'
    '05e80cffffff61680b685c00c360e8000000005b81eb2b150000a198f56b00a801741683'
    'e0fea398f56b0083bbc01a0000007505e800ffffff616840026a0068c6bc5000c353e800'
    '0000005b81eb63150000a198f56b000b83c01a00005b68cbc05000c356575389ce8b7c24'
    '1085ff750ba180c1660085c07432eb1a83ff0876138d4ff8c1e10401cab81000000029f8'
    '741aeb0289f889c389f1b8e0f24700ffd089c20335acf56b004b75ec5b5f5ec204005350'
    'e8000000005b81ebd1150000a1bcf56b0039c2721c833d90c1660000750c85d2780429c2'
    'ebeb01c2ebe7ba00000f00eb098b833c1800008b1490585bc353e8000000005b81eb0f16'
    '0000ffb384180000ffb384180000ffb384180000b890874000ffd083c40c5bb830445100'
    'ffe0')
# UI CODE END
UI_CALLS = [(0x4b5, 0x4800d0), (0x4ca, 0x4804f0),   # rel32 at offset+1
            (0x4df, 0x5670c0), (0x4f4, 0x5674f0)]
UI_STUBS = [(0x1c753a, 0x5c813a, 0x4ab),
            (0x1c7588, 0x5c8188, 0x4c0),
            (0x1c754c, 0x5c814c, 0x4d5),
            (0x1c759a, 0x5c819a, 0x4ea)]
# Projection setups: the originals' entries jump to these, and the HUD
# passes' setup calls go there too (the HUD/world decision is made per
# submission, by the pass depth).
UI_WORLD = ((0x51444d, 0x11384d, 0x0),
(0x51448e, 0x11388e, 0x4b),
(0x5cc39d, 0x1cb79d, 0x101),
(0x5cc3de, 0x1cb7de, 0x14c))
UI_SUBMIT = ((0x514576, 0x113976, 0x202),
(0x5cc4c6, 0x1cb8c6, 0x2bf))
UI_INSERT_A, UI_INSERT_B = 0xfc7, 0x1004
UI_HUD_ENTER = 0x37c
UI_HANGAR_DRAW = 0x1343
UI_FRAME, UI_FLUSH_A, UI_FLUSH_B = 0x11f6, 0x12cb, 0x1307
UI_F4 = 0x1411
UI_ROLLBLIT = 0x157c
UI_ROWSAFE = 0x15ca
UI_CREDITS_MOON = 0x1609
UI_DLG_INIT, UI_DLG_OK, UI_DLG_DONE, UI_INI_LOAD, UI_INI_SAVE = 0x14a1, 0x14cc, 0x14f5, 0x1525, 0x155d
UI_PASS_STUBS = 0x1670                      # 20 bytes per wrapped function, to 0x1800
UI_MODEW = 0x1820                           # mode size, written by the patcher
UI_ROWTAB = 0x183c                          # row table address, likewise
UI_KSBS = 0x1848                            # split FOV factors, likewise
UI_SCALE = 0x1868                           # 2D scale per layout, likewise
UI_HUD = 0x1874                             # same as floats
UI_CONST = 0x18a8                           # 65536, 640, 480, 0.5
UI_PINTH = 0x1a6c                           # split HUD band threshold, 0 off
UI_SPLITST = 0x1a90                         # sub-states drawn split
UI_DEBUG = 0x1a98                           # 1: state readout on the frame
UI_F4MODE = 0x1ac0                          # 0: first size in place, 1: second
UI_F4TAB = 0x1ac4                           # the F4 site table's address
UI_CMOON = 0x1884                           # credits moon card scale, float
assert len(UI_CODE) <= UI_PASS_STUBS        # data block starts at 0x1800
UI_F4TAB_OFF = 0x1b00                       # the F4 site table, in the file
UI_F4TAB_SIZE = 0x1fa0
UI_FOV = 0x3aa0                             # a ported build's FOV block, when
                                            # its own is too short to hold it
UI_OFF = 0x3b00                             # offscreen: guard, canvas, guard
# Functions that draw HUD elements: everything they submit, directly or
# through callees, is HUD (see hud_enter in ui.asm). They are the
# functions that call the projection setup with the HUD focal lengths
# (600 in game, 128 in the machine select). The first two rows are the
# ones seen running in the diagnostic traces (1P, machine select, split in
# both layouts): bars, frames, timer box, reticle and lock-on for each
# renderer; weapon strips; machine-select portraits and cursors. The
# rest have the same shape and are wrapped for the modes not traced.
# VA and the length of the prologue displaced into the stub (push ebp;
# mov ebp, esp; then sub esp, imm8/imm32 or push ebx; push esi).
UI_PASS_FUNCS = [
    (0x5b5f2e, 9), (0x4c468e, 9),           # in-game HUD, renderer A/B
    (0x55d221, 6), (0x5495b1, 6),           # weapon strips
    (0x5a1f3c, 6), (0x5a251b, 6),           # machine select, cursors
    (0x58881e, 6), (0x588d85, 6),           # the same, renderer B
    (0x4d0280, 6), (0x531f6a, 6),           # untraced: other HUD builds
    (0x4d9c3d, 9), (0x42cda6, 9),
    (0x460b70, 6), (0x460cf3, 5),
    (0x432fbe, 6), (0x433141, 5),
    (0x57f1b0, 6), (0x5829c3, 6),
    (0x4b6030, 6), (0x4b981f, 6),
]

UI_OFF_SIZE = 4 * 1024 * 480 * 2            # guard, canvas, guard, copy

MASK_ROW = 0x50                       # coverage mask bytes per row, 640 px
MASK_LOAD = bytes.fromhex('a1e88c6c00')      # mov eax, [0x6c8ce8]
MASK_ADD = bytes.fromhex('83c050')           # add eax, 0x50
MASK_STORE = bytes.fromhex('a3e88c6c00')     # mov [0x6c8ce8], eax

# Row advances in the span fillers: load, add 0x50, store, with one or
# two unrelated instructions interleaved (inc esi, pop ebx, pop esi, a
# paddd, a mov ecx). eax is reloaded right after every one, so the whole
# span becomes the interleaved instructions, add dword [0x6c8ce8], imm32,
# and nops. (file offset, span length, interleaved bytes)
MASK_ADVANCE = [
    (0x1ce988, 15, '465b'), (0x1ce9de, 14, '46'),
    (0x1cf028, 15, '465b'), (0x1cf07e, 14, '46'),
    (0x1cf6d8, 15, '465b'), (0x1cf72e, 14, '46'),
    (0x1cfd88, 15, '465b'), (0x1cfdde, 14, '46'),
    (0x1d1deb, 17, '0ffefe5e'), (0x1d1e22, 22, '0ffefe8b0dacf56b00'),
    (0x1d2111, 17, '0ffefe5e'), (0x1d2152, 22, '0ffefe8b0dacf56b00'),
    (0x1d9858, 15, '465b'), (0x1d98ae, 14, '46'),
    (0x1d9ef8, 15, '465b'), (0x1d9f4e, 14, '46'),
    (0x1da5a8, 15, '465b'), (0x1da5fe, 14, '46'),
    (0x1dac58, 15, '465b'), (0x1dacae, 14, '46'),
    (0x1dcccb, 17, '0ffefe5e'), (0x1dcd02, 22, '0ffefe8b0db4f56b00'),
    (0x1dcff1, 17, '0ffefe5e'), (0x1dd032, 22, '0ffefe8b0db4f56b00'),
]
# or eax, 0x50 - packs the stride into the low word of eax, whose low byte
# is zero at that point. Becomes mov al, stride; nop.
MASK_PACK = [0x1cd8b8, 0x1cd9ee, 0x1ce11b, 0x1d162c, 0x1d1a3d, 0x1d877e,
             0x1d88ba, 0x1d8ff1, 0x1dc502, 0x1dc923]
# mov edx, 0x50 with a 32-bit immediate.
MASK_EDX = [0x1cfe31, 0x1cfe8f, 0x1dad01, 0x1dad5f]


def add_section(buf, size, raw=b'', name=b'.vohr'):
    """Append a section: `raw` bytes in the file, zero-filled to `size` in
    memory. Returns its VA and the file offset of the raw bytes."""
    pe = struct.unpack_from('<I', buf, 0x3c)[0]
    nsec = struct.unpack_from('<H', buf, pe + 6)[0]
    optsz = struct.unpack_from('<H', buf, pe + 20)[0]
    opt = pe + 24
    base = struct.unpack_from('<I', buf, opt + 28)[0]
    salign = struct.unpack_from('<I', buf, opt + 32)[0]
    hdrs = struct.unpack_from('<I', buf, opt + 60)[0]
    tab = opt + optsz
    if tab + 40 * (nsec + 1) > hdrs:
        raise ValueError('no room in the section table')
    end = 0
    for i in range(nsec):
        o = tab + 40 * i
        if buf[o:o + 8].rstrip(b'\0') == name:
            raise ValueError('section %s already present' % name.decode())
        vs, va = struct.unpack_from('<II', buf, o + 8)
        end = max(end, va + vs)
    rva = (end + salign - 1) // salign * salign
    vsize = (size + salign - 1) // salign * salign
    falign = struct.unpack_from('<I', buf, opt + 36)[0]
    rawsize = (len(raw) + falign - 1) // falign * falign
    rawptr = (len(buf) + falign - 1) // falign * falign
    buf += b'\0' * (rawptr - len(buf))
    buf += raw.ljust(rawsize, b'\0')
    o = tab + 40 * nsec
    buf[o:o + 40] = struct.pack('<8sIIIIIIHHI', name.ljust(8, b'\0'), size,
                                rva, rawsize, rawptr, 0, 0, 0, 0, 0xE00000E0)
    struct.pack_into('<H', buf, pe + 6, nsec + 1)
    struct.pack_into('<I', buf, opt + 56, rva + vsize)      # SizeOfImage
    return base + rva, rawptr


def build_sites(w, h, sec_va, span_va, rowtab_va, pool, A=None):
    sx, sy = w / BASE_W, h / BASE_H
    if A is None:
        A = ADDR.__getitem__
    sites = []
    stride = w // 8
    # Coverage mask at 0x6d1ce0 (twelve references), its row pointer
    # table at 0x6d0de0 (one). Buffer first, table after it.
    sites += imm_sites([0x1cd5da, 0x1cd605, 0x1cd8a5, 0x1cd9db, 0x1ce0f9,
                        0x1d161f, 0x1d1a30, 0x1d876b, 0x1d88a7, 0x1d8fcf,
                        0x1dc4f8, 0x1dc919], 0x6d1ce0, span_va)
    # Row pointer table init at 0x5ce1d2: two rows per iteration with an
    # 8-bit stride. Rewritten as one row per iteration, 32-bit stride.
    sites += [(0x1cd5d4,
               bytes.fromhex('e00d6d00' '8d05e01c6d00' 'b9e0010000'
                             '8907' '83c050' '894704' '83c050' '83c708'
                             '83e902' '7fed'),
               u32(span_va + h * stride)
               + b'\x8d\x05' + u32(span_va)          # lea eax, [buf]
               + b'\xb9' + u32(h)                    # mov ecx, h
               + b'\x89\x07'                        # mov [edi], eax
               + b'\x05' + u32(stride)               # add eax, stride
               + b'\x83\xc7\x04'                   # add edi, 4
               + b'\x49'                             # dec ecx
               + b'\x7f\xf3'                        # jg
               + b'\x90' * 6)]
    sites += imm_sites([0x1cd60c], 480 * MASK_ROW // 4,   # dwords to clear
                       h * stride // 4)
    # The stride itself, in the three forms the renderer has it.
    for off, n, between in MASK_ADVANCE:
        old = MASK_LOAD + bytes.fromhex(between) + MASK_ADD + MASK_STORE
        assert len(old) == n
        new = (bytes.fromhex(between) + b'\x81\x05' + u32(A('MASKPTR'))
               + u32(stride))
        sites.append((off, None, new.ljust(n, b'\x90')))
    sites += [(o, bytes.fromhex('83c850'), bytes([0xb0, stride, 0x90]))
              for o in MASK_PACK]
    sites += imm_sites([o + 1 for o in MASK_EDX], MASK_ROW, stride)
    # Sprite blit at 0x47f0c0: split screen always goes through the
    # downscaling sampler with the size-derived mode (8 full, 4 half, 2
    # quarter) as the factor. Stock split viewports are 320 wide, so the
    # sampler only ever ran at factor 2; at 640 it runs at factor 1, a
    # path that draws the transparent key and miscolours 565. Route mode
    # 8 to the plain blit whether split or not; the 320x240 low-res mode
    # is tested just before and still takes the sampler.
    sites += [(0x7e504, bytes.fromhex('8b0d48c96b003bc80f84bd010000'), bytes.fromhex('833d7cc16600080f84be01000090'))]
    # The pause text and one more message are drawn centred on 320,160,
    # halved for the low-res mode (0x5c9a81, 0x5c9afd).
    sites += imm_sites([0x1c8e8d, 0x1c8f1d], 320, w // 2)
    sites += imm_sites([0x1c8e94, 0x1c8f24], 160, 160 * h // 480)
    # The two 24px GDI LOGFONTs (lfHeight, .data) behind the pause,
    # loading and credits-prompt text.
    sites += imm_sites([0x2c7370, 0x2c73f0], 24, 24 * h // 480)
    # GDI text (pause, messages) and the loading strip use 640x480
    # rectangles: wrap width, height, and the strip rows 336/432 to 480.
    sites += imm_sites([0x1c834b, 0x1c843c, 0x1c8498, 0x1c84dd], 640, w)
    sites += imm_sites([0x1c824e, 0x1c8443, 0x1c849f, 0x1c84e4], 480, h)
    sites += imm_sites([0x1c8435], 336, 336 * h // 480)
    sites += imm_sites([0x1c8491, 0x1c84d6], 432, 432 * h // 480)
    # 2D row table: 480+480 entries in .data with the frame divisor right
    # behind them; a tall split viewport overflows it. Both tables move to
    # the new section, H entries each. The tile planes' destination
    # helpers index the first table with rows past either end - stock's
    # deliberate vertical wrap into the second table, which relocation
    # breaks (the neighbours are the mask now) - so those ten loads go
    # through ui.asm rowsafe, which wraps by the frame height, or parks
    # on the canvas guard during the credits roll, where the second
    # plane's window pokes past both edges every frame (the top-of-roll
    # corruption). The two loads with their own bounds and the three
    # outside the tile planes keep the plain rebased reference.
    for off in (0x07f88d, 0x07f8dd, 0x07fe39, 0x07fe9e, 0x0802f9,
                0x166885, 0x1668dd, 0x166e41, 0x166eae, 0x167321):
        site = 0x400c00 + off
        sites.append((off, bytes.fromhex('8b1495c8756c00'),
                      b'\xe8' + u32(sec_va + UI_ROWSAFE - (site + 5))
                      + b'\x90\x90'))
    sites += imm_sites([0x07fd71, 0x166d79, 0x1c4f73, 0x1c90b8, 0x1c90e9],
                       0x6c75c8, rowtab_va)
    sites += imm_sites([0x1c4fc9], 0x6c7d48, rowtab_va + h * 4)
    # 2D layer: the four calls in 0x5c80df go to the stubs.
    for (off, site, stub), (_, target) in zip(UI_STUBS, UI_CALLS):
        sites.append((off, b'\xe8' + u32(target - (site + 5)),
                      b'\xe8' + u32(sec_va + stub - (site + 5))))
    # --- mode ----------------------------------------------------------
    # push 640 / push 480 into the SetDisplayMode wrapper (0x5c56a2) and
    # the mode selector (0x5c9404), plus CreateWindowExA at 0x5c59d2.
    sites += imm_sites([0x1b0990, 0x1c617f, 0x1c6901, 0x1c6d42, 0x1c8990,
                        0x1c8abe, 0x1c8b52, 0x1c4dd8], 640, w)
    sites += imm_sites([0x1b098b, 0x1c617a, 0x1c68fc, 0x1c6d3d, 0x1c898b,
                        0x1c8ab9, 0x1c8b4d, 0x1c4dd3], 480, h)
    # F4: past the network-game guard, the handler (0x5c74da) toggled
    # 320x240. It goes to the section instead (ui.asm f4_toggle), which
    # switches between the two sizes the patcher wrote. The menu command
    # that picked 320x240 outright (0x5c79aa) jumps to the handler exit.
    sites += [(0x1c68ec, bytes.fromhex('f60598f56b0004'),
               b'\xe9' + u32(sec_va + UI_F4 - (0x5c74ec + 5)) + b'\x90\x90'),
              (0x1c6daa, bytes.fromhex('6a1068f0000000'),
               b'\xe9' + u32(A('F4EXIT') - (A('F4CASE') + 5)) + b'\x90\x90')]
    # The F5 Screen row (720p / 1080p, see _split_dialog) drives the
    # same switch through four hooks in the section, and the ini load
    # and save carry the choice as bit 0 of ScrSize: the dialog staging
    # FLAGS (0x427ec4), OK writing it back (0x42823c), the resume after
    # the dialog (0x5c7494), the ini load's join (0x50bcc1) and the ini
    # save's read (0x50c0c6).
    for site, old, routine, op in (
            (0x427ec4, 'a30043be00', UI_DLG_INIT, b'\xe9'),
            (0x42823c, 'a398f56b00', UI_DLG_OK, b'\xe9'),
            (0x5c7494, 'e872f3ffff', UI_DLG_DONE, b'\xe8'),
            (0x50bcc1, '6840026a00', UI_INI_LOAD, b'\xe9'),
            (0x50c0c6, 'a198f56b00', UI_INI_SAVE, b'\xe9')):
        sites.append((site - 0x400c00, bytes.fromhex(old),
                      op + u32(sec_va + routine - (site + 5))))
    # 0x5c9404 returns failure for any mode but the two it knows. This is
    # the crash: the caller then runs on without a surface.
    sites += imm_sites([0x1c8810], 640, w)
    sites += imm_sites([0x1c881d], 480, h)
    # EnumDisplayModes callback at 0x5c5be4 flags 640x480 as available.
    sites += imm_sites([0x1c5011], 640, w)
    sites += imm_sites([0x1c501e], 480, h)
    # If the enumeration does not list the mode but does list 320x240,
    # 0x5c9661 loops forever trying to pick one: black screen, no crash.
    # Treat the mode as available and let SetDisplayMode be the judge.
    sites += [(0x1c8a88, bytes.fromhex('a188866c00'),      # mov eax,[flag]
               bytes.fromhex('b801000000'))]               # mov eax,1
    # Viewport in 0x5c8317: width/height globals, the screen size the
    # Screen=Normal window is centred in, and that window's size.
    sites += imm_sites([0x1c7da5, 0x1c7d77], 640, w)
    sites += imm_sites([0x1c7daf, 0x1c7d89], 480, h)
    sites += imm_sites([0x1c7d68], 496, int(496 * sx))
    sites += imm_sites([0x1c7d72], 384, int(384 * sy))
    # Projection scale. 0x51444d builds X as sx*f and Y as sx*sy*f, so the
    # first float carries the resolution and the second is the aspect
    # ratio relative to it. Only the first pair changes; the 0.95 is one
    # hardware-specific case of the same value. The scale comes from the
    # height, so a wide mode keeps the vertical field of view and sees
    # more at the sides.
    # The second renderer's copy (0x6c8b24) is set up pre-halved for split
    # screen; the split block below applies the per-layout factor to both
    # copies, so it gets the full scale here.
    sites += [(0x1c7726, f32(1.0), f32(sy)),
              (0x1c7730, f32(0.5), f32(sy)),
              (0x1c7775, f32(0.95), f32(0.95 * sy))]
    # The projection setups jump to versions in the section that also
    # keep a copy of the result; the submit functions get an entry hook
    # that installs the HUD projection inside a HUD pass and the saved
    # world projection for everything else, on every submission.
    for site, off, routine in UI_WORLD + UI_SUBMIT:
        sites.append((off, bytes.fromhex('558bec535657'),
                      b'\xe9' + u32(sec_va + routine - (site + 5)) + b'\x90'))
    # HUD pass functions: the prologue jumps to a stub in the section
    # (built in main) that counts the pass in and out.
    for n, (site, ln) in enumerate(UI_PASS_FUNCS):
        stub = sec_va + UI_PASS_STUBS + 20 * n
        sites.append((site - 0x400c00, None,
                      b'\xe9' + u32(stub - (site + 5)) + b'\x90' * (ln - 5)))
    # Render-list inserts (quads and triangles, both renderers): in a HUD
    # pass the hook scales the whole-pixel 640x480 vertex positions to
    # the HUD scale, then runs the displaced instruction.
    for site, off, routine in ((0x5d4628, 0x1d3a28, UI_INSERT_A),
                               (0x5d5360, 0x1d4760, UI_INSERT_A),
                               (0x5e02b0, 0x1df6b0, UI_INSERT_B),
                               (0x5df538, 0x1de938, UI_INSERT_B)):
        old = bytes.fromhex('8b349dd0017000' if routine == UI_INSERT_A
                            else '8b349d505f7200')
        sites.append((off, old, b'\xe8' + u32(sec_va + routine - (site + 5))
                      + b'\x90\x90'))
    # Single viewport outside the rounds of a split game: the
    # viewport setup call and the two flush calls in the frame loop go
    # through the section (ui.asm frame_setup, flush_a, flush_b).
    for site, routine, target in ((0x5c811b, UI_FRAME, 0x5c8317),
                                  (0x5c8166, UI_FLUSH_A, 0x5d1db0),
                                  (0x5c8178, UI_FLUSH_B, 0x5dcc80)):
        sites.append((site - 0x400c00, b'\xe8' + u32(target - (site + 5)),
                      b'\xe8' + u32(sec_va + routine - (site + 5))))
    # Credits roll: during the ending (sub-state 0x20) the tail of each
    # engine's 2D post draw blacked rows 0..96 and 384..480 of the
    # frame, one memset (0x47e580) per row - the letterbox. Both bands
    # go (the jne past each block becomes a jmp: 0x480c74 engine 1,
    # 0x567c84 engine 2), which works because the edge clipping the
    # roll's tile walkers always asked for is wired up at the same
    # time: the walkers push a visible row count for the window's top
    # tile and the entering tile at its foot, but the shared blit
    # (0x47fee0, both engines) discarded it and drew all 8 glyph rows -
    # the top tile redrew unscrolled at row 0 and the entering line
    # popped in whole, which the bands existed to hide. The blit's
    # entry jumps to ui.asm roll_blit, which honours the count, and the
    # top edge's push is re-encoded as fine+8 (was 8-fine) so the two
    # edges are distinguishable. Lines now slide in at the window's
    # foot (row 400: the writer feeds the ring on a hand-timed
    # schedule, so nothing exists below - see docs/HIRES.md) and out
    # through the real row 0, with the scenery clean behind both.
    # The roll starts from the bottom: the writer feeds the ring about
    # two rows inside the old window (at its 0x31 draw cap, measured on
    # video: a fed line lands ~0.6s after its ring row would enter),
    # so instead of stretching the window down into the writer's
    # workspace, the whole window moves down 13 rows - it begins 13
    # ring rows earlier, showing the rows that scrolled past, draws 60
    # rows, and the destination helper's cap rises from 0x31 to 0x3d.
    # The bottom row (start+47) then trails the feed (start+48) by a
    # full row, so a line is complete before it slides in at line 480,
    # and lines leave through line 0 as before. 13 exactly: the writer
    # composes an entering line over ring rows cursor..cursor+2, which
    # sit at start-16..start-14 mod 64 - at 14 rows of shift the third
    # compose row was the top display row and its glyph bottoms
    # flashed at the screen's top edge (seen at 60 fps on video). At
    # 13 the window excludes all three scratch rows, and a history row
    # still has 3 rows of margin before the feed comes around to
    # rewrite it. Plane B is untouched: the roll's text is all on
    # plane A (B's uncapped 60-row window never shows a line below 400
    # on screen).
    sites += [(0x07f99b, bytes.fromhex('81e1ffff0000'),      # coarse row
               bytes.fromhex('83e90d83e13f')),               # -13 mod 64
              (0x16699b, bytes.fromhex('81e1ffff0000'),
               bytes.fromhex('83e90d83e13f')),
              (0x07f96c, b'\x32', b'\x3c'),                  # 60 rows
              (0x16696c, b'\x32', b'\x3c'),
              (0x07fd37, b'\x31', b'\x3d'),                  # dest cap
              (0x166d37, b'\x31', b'\x3d')]
    # The strip loaders' availability watermarks round up: a tile with
    # only part of its 128 bytes read counts as loaded, and the walkers
    # draw it. Stock never saw that - the freshly fed rows sat below
    # the 400-line window and finished loading before they appeared -
    # but with the window reaching line 480 a half-read tile shows the
    # moment it is fed (the streamed name strips; the heading strip is
    # long since loaded). Floored, a partial tile stays culled for the
    # frame it takes to complete. All 24 stores (12 loader variants,
    # two banks, shared by both engines); the strips are 128-aligned,
    # so no final tile is stranded.
    for off in (0x0804e7, 0x0808f8, 0x080c74, 0x081046, 0x081498,
                0x081bf6, 0x082174, 0x0827d4, 0x082c82, 0x082f07,
                0x08325d, 0x0834f3, 0x080565, 0x080a0d, 0x080e17,
                0x0811ec, 0x081611, 0x081ea6, 0x0823b3, 0x082944,
                0x082d06, 0x082f8b, 0x0832ef, 0x083577):
        sites.append((off, bytes.fromhex('83e27f'), bytes.fromhex('83e200')))
    sites += [(0x080074, bytes.fromhex('0f859e000000'),
               bytes.fromhex('e99f00000090')),
              (0x167084, bytes.fromhex('0f85a6000000'),
               bytes.fromhex('e9a700000090')),
              (0x07f2e0, bytes.fromhex('56a180c16600'),
               b'\xe9' + u32(sec_va + UI_ROLLBLIT - (0x47fee0 + 5)) + b'\x90'),
              (0x07fa63, bytes.fromhex('b8080000002bc350'),
               bytes.fromhex('8d430850') + b'\x90' * 4),
              (0x166a63, bytes.fromhex('b8080000002bc350'),
               bytes.fromhex('8d430850') + b'\x90' * 4)]
    # The ending driver (0x58ecd0) scrolls the roll one line a frame
    # from frame 0x116 and cuts it at 0x10e2, timed for the last line
    # to be under the top band. Its last ring row (53, fed at 0x0f2e)
    # is off the top at 0x112e; the same rows come round to the foot
    # at 0x1136, and the blank feed only clears columns 9..59, so
    # their right-hand tiles would show. The cut moves to 0x1132.
    sites += imm_sites([0x18e341], 0x10e2, 0x10e2 + 80)
    sites += imm_sites([0x18e7b9], 0x10e3, 0x10e3 + 80)
    # Machine-select hangar: a platform mech is drawn while its angle is
    # within a window of the camera's (0x59e3a1: 31.57 degrees to the
    # left, 28.43 to the right, .data doubles), sized for a 4:3 view;
    # in a wider one the next mech pops in at the edge. Both bounds are
    # widened by the extra half field of view plus eight degrees for the
    # mech's own width (stock's margin over its view). The game keeps
    # palettes for the selection and the previous selection only (rows
    # 1/3/5/7 and 9/11 of the colour planes) and loads them
    # asynchronously, so a mech far enough right can come out in someone
    # else's colours; past 28.43 degrees right of the camera - stock's
    # draw bound, where colours stop being guaranteed - its shade is
    # scaled to black instead, fading over the twelve degrees inside that
    # edge, so the outermost mech is a silhouette that lights up as it
    # turns in (ui.asm hangar_draw, wrapping the platform draw at
    # 0x59e4ea).
    f = 600 * 1.21875                    # focal times the 1P aspect
    margin = math.degrees(math.atan(w / (h / 480) / 2 / f)
                          - math.atan(320 / f))
    if margin > 0:
        sites += [(0x2213f0, struct.pack('<d', 31.57),
                   struct.pack('<d', 31.57 + margin + 8)),
                  (0x2213f8, struct.pack('<d', 28.43),
                   struct.pack('<d', 28.43 + margin + 8)),
                  (0x59e4ea - 0x400c00, b'\xe8' + u32(0x59cb93 - (0x59e4ea + 5)),
                   b'\xe8' + u32(sec_va + UI_HANGAR_DRAW - (0x59e4ea + 5)))]
    # Enemy marker and its off-screen arrow. 0x5485c0 (renderer A) and
    # 0x475930 (B) draw the lock brackets while the enemy's projected
    # position is inside a 4:3 window (x within 256 in focal-600 space)
    # and switch to the edge arrow once the bearing is more than 0x1500
    # (29.5 degrees) off the camera. Both are sized for the stock view:
    # in a wider one the arrow points at an enemy still on screen. The
    # x bounds grow with the visible width and the bearing window by
    # the extra half field of view; the y bounds stay, since the
    # vertical view is unchanged. Both renderers keep their own copy of
    # the float pool; the compare immediates are in the code.
    wide = w / (h / 480) / 640           # visible width over 4:3's
    win = 0x1500 + round(margin * 65536 / 360)
    sites += [(0x205d38, f32(-256.0), f32(-256.0 * wide)),
              (0x205d3c, f32(256.0), f32(256.0 * wide)),
              (0x1fb4a0, f32(-256.0), f32(-256.0 * wide)),
              (0x1fb4a4, f32(256.0), f32(256.0 * wide))]
    sites += [(off, u32(0x1500), u32(win))
              for off in (0x147b60, 0x147cc8, 0x074ed0, 0x075038)]
    sites += [(off, u32(0xffffeb00), u32(-win))
              for off in (0x147b6f, 0x147cd7, 0x074edf, 0x075047)]
    # Renderer A's polygon record pool: 2500 records of 0x30 bytes at
    # 0x6db7e0, a side array of 8 per record at 0x6f8ca0 and the flush
    # list at 0x6fdabc, with the cap in the two insert paths and the
    # flush. Moved to the section and enlarged; the stock
    # cap drops the last polygons of the machine-select hangar once all
    # three mechs are drawn.
    pool_va, n = pool
    sites += imm_sites([0x1d3967, 0x1d46ba], 0x6db7e0, pool_va)
    sites += imm_sites([0x1d3970, 0x1d46c3], 0x6f8ca0, pool_va + n * 0x30)
    sites += imm_sites([0x1d0ed0], 0x6fdabc, pool_va + n * 0x38)
    sites += imm_sites([0x1d11d8], 0x6fdac0, pool_va + n * 0x38 + 4)
    sites += imm_sites([0x1d395b, 0x1d46ae, 0x1d0eac], 2500, n)
    # Renderer B's, the same shape behind it: 2000 records at 0x708a90,
    # sides at 0x720190, list at 0x72400c; its flush cap is 2500.
    pool_b = pool_va + n * 0x3c + 8
    sites += imm_sites([0x1de877, 0x1df60a], 0x708a90, pool_b)
    sites += imm_sites([0x1de880, 0x1df613], 0x720190, pool_b + n * 0x30)
    sites += imm_sites([0x1dbda0], 0x72400c, pool_b + n * 0x38)
    sites += imm_sites([0x1dc0a8], 0x724010, pool_b + n * 0x38 + 4)
    sites += imm_sites([0x1de86b, 0x1df5fe], 2000, n)
    sites += imm_sites([0x1dbd7c], 2500, n)
    # Perspective subdivision. 0x5d3430 splits a polygon while any edge is
    # longer than a threshold, squared, in pixels, with a fixed recursion
    # budget per polygon. Twice the pixels per edge means four times the
    # splits, and big polygons run out of budget and vanish. Scale the two
    # thresholds (.data initial values) by sx*sx to keep the split count
    # what it was.
    sites += [(0x2baff4, f32(32768.0), f32(32768.0 * sy * sy)),
              (0x2baffc, f32(131072.0), f32(131072.0 * sy * sy))]
    # --- split screen --------------------------------------------------
    # Side by side: two W/2 x H viewports at (0,0) and (W/2,0). Top and
    # bottom (split flag bit 1, or Screen=Normal): two W x H/2 at (0,0)
    # and (0,H/2). The game's own layouts were 320x240 boxes, staggered.
    sites += imm_sites([0x1c7cc5], 320, w)            # top/bottom size
    sites += imm_sites([0x1c7ccf], 240, h // 2)
    sites += imm_sites([0x1c7cf2], 320, w // 2)       # side by side size
    sites += imm_sites([0x1c7cfc], 240, h)
    # The two layout tests look at bit 1 only, while the origin case
    # table treats Screen=Normal (bit 0) as top/bottom too; make them agree.
    sites += [(0x1c7cb8, b'\x02', b'\x03'), (0x1c7e3a, b'\x02', b'\x03')]
    # Origins (.data at 0x6c854c: y, x, y, x, y, y) all become zero.
    sites += imm_sites([0x2c734c, 0x2c7350], 64, 0)
    sites += imm_sites([0x2c7354], 176, 0)
    sites += imm_sites([0x2c7358], 256, 0)
    sites += imm_sites([0x2c735c, 0x2c7360], 120, 0)
    # Second viewport: half a row in (640 bytes at 16bpp), its coverage
    # mask offset (0x7087a0) half a row, 240 rows or 120 rows, and the
    # row offsets pitch*240 and pitch*120 as lea/shl chains replaced with
    # imul reg, reg, imm32 and nops.
    sites += imm_sites([0x1c78f0, 0x1c794a, 0x1c7b72], 640, w)
    sites += imm_sites([0x1c7907, 0x1c7961, 0x1c7a2c, 0x1c7a8e, 0x1c7b89,
                        0x1c7c0e], 0x28, stride // 2)
    sites += imm_sites([0x1c79bd, 0x1c7bd0], 0x4b00, h // 2 * stride)
    sites += imm_sites([0x1c7af4, 0x1c7c55], 0x2580, h // 4 * stride)
    nop = b'\x90' * 3
    sites += [
        (0x1c7bb1, bytes.fromhex('8d04408d0480c1e004'),
         b'\x69\xc0' + u32(h // 2) + nop),
        (0x1c7c36, bytes.fromhex('c1e0038d04408d0480'),
         b'\x69\xc0' + u32(h // 4) + nop),
        (0x1c799b, bytes.fromhex('8d0c498d0c89c1e104'),
         b'\x69\xc9' + u32(h // 2) + nop),
        (0x1c7ad2, bytes.fromhex('c1e1038d0c498d0c89'),
         b'\x69\xc9' + u32(h // 4) + nop),
    ]
    # Field of view. In split mode 0x5c8317 halves the projection scale
    # because its viewports were half-size both ways. Replaced with a
    # per-layout factor from the section data, so each viewport gets the
    # scale of the 4:3 screen that fits inside it; the aspect multiplies
    # that follow are kept. Fits the original block.
    ksbs, ktb = sec_va + UI_KSBS, sec_va + UI_KSBS + 4
    block = (b'\xf6\x05' + u32(A('FLAGS')) + b'\x03'   # test byte [flags], 3
             + b'\x75\x08'                             # jne top/bottom
             + b'\xd9\x05' + u32(ksbs)                 # fld [ksbs]
             + b'\xeb\x06'                             # jmp join
             + b'\xd9\x05' + u32(ktb)                  # fld [ktb]
             + b'\xd9\x05' + u32(A('SCALE_A'))             # fld [xscale]
             + b'\xd8\xc9'                             # fmul st, st(1)
             + b'\xd9\x1d' + u32(A('SCALE_A'))             # fstp [xscale]
             + b'\xd8\x0d' + u32(A('SCALE_B'))             # fmul [xscale B]
             + b'\xd9\x1d' + u32(A('SCALE_B'))             # fstp [xscale B]
             + b'\xdd\x05' + u32(A('FCONST'))             # fld qword [1.21875]
             + b'\xd9\x05' + u32(A('ASPECT_A'))             # fld [aspect]
             + b'\xd8\xc9'                             # fmul st, st(1)
             + b'\xd9\x1d' + u32(A('ASPECT_A'))             # fstp [aspect]
             + b'\xd8\x0d' + u32(A('ASPECT_B'))             # fmul [aspect B]
             + b'\xd9\x1d' + u32(A('ASPECT_B')))            # fstp [aspect B]
    old = bytes.fromhex(
        'd905e4c16b00833d1c0aa000007508dc3520476200eb11ff3524476200ff35204762'
        '00e823d30100d91de4c16b00d905e8c16b00dc0d28476200d91de8c16b00d905288b'
        '6c00dc0d28476200d91d288b6c00')
    assert len(block) <= len(old)
    sites.append((0x1c782d, old, block.ljust(len(old), b'\x90')))
    # --- credits -------------------------------------------------------
    # Two ending assets are sized for the 640x288 band the stock roll
    # sits behind (see roll_blit). The star field is a grid of tiles
    # (0x58f59c, 1800-unit columns from -9000, drifting 0.9 a frame) that
    # runs out on the left before the roll ends once the frame is wider
    # than 4:3: it starts further left, by the extra width in columns
    # plus one. The moon card's commit (0x58f549) goes through ui.asm
    # credits_moon, which scales it to the full height (UI_CMOON).
    cols = int(math.ceil(max(0.0, w / h / (4 / 3) - 1) * 5)) + 1
    sites += imm_sites([0x18ea14], -9000, -9000 - cols * 1800)
    sites.append((0x18e949, b'\xe8' + u32(0x514430 - (0x58f549 + 5)),
                  b'\xe8' + u32(sec_va + UI_CREDITS_MOON - (0x58f549 + 5))))
    return sites


def apply(buf, sites, mask_load=MASK_LOAD, mask_store=MASK_STORE):
    for off, old, new in sites:
        if old is None:                  # a rewritten span: check its parts
            cur = buf[off:off + len(new)]
            if cur[:3] == bytes.fromhex('558bec'):   # a pass prologue
                continue
            if mask_load not in cur or MASK_ADD not in cur \
                    or mask_store not in cur:
                raise ValueError('unexpected bytes at 0x%06x: %s'
                                 % (off, cur.hex()))
            continue
        if buf[off:off + len(old)] != old:
            raise ValueError('unexpected bytes at 0x%06x: %s'
                             % (off, buf[off:off + len(old)].hex()))
    for off, old, new in sites:
        buf[off:off + len(new)] = new


def _split_dialog(buf):
    """Relabel the F5 Screen Split radios for the two layouts this patch
    keeps: Type1 -> Ver (side by side), Type3 -> Hor (top/bottom). Type2
    differed from Type1 only by a stagger the new layouts do not have, so
    it is hidden. The Screen radios become 720p (was Normal) and 1080p
    (was Large). Located by string and control id: the framerate patch
    grows this template, so fixed offsets would miss."""
    lo, hi = 0x602c00, min(len(buf), 0x60e000)
    for old, new, hide, ident in (('Type1', 'Ver  ', False, 0x42e),
                                  ('Type2', None, True, 0x42f),
                                  ('Type3', 'Hor  ', False, 0x431),
                                  ('Normal', '720p  ', False, 0x420),
                                  ('Large', '1080p', False, 0x421)):
        pat = old.encode('utf-16-le') + b'\0\0'
        hits = []
        i = buf.find(pat, lo, hi)
        while i >= 0:
            if struct.unpack_from('<H', buf, i - 6)[0] == ident:
                hits.append(i)
            i = buf.find(pat, i + 1, hi)
        if len(hits) != 1:
            raise ValueError('F5 radio %s not found once' % old)
        i = hits[0]
        if new:
            enc = new.encode('utf-16-le')
            assert len(enc) == len(pat) - 2
            buf[i:i + len(enc)] = enc
        if hide:
            so = i - 22                     # the item's style dword
            style = struct.unpack_from('<I', buf, so)[0]
            if style != 0x50010009:         # visible radio, as shipped
                raise ValueError('Type2 style is not where expected')
            struct.pack_into('<I', buf, so, style & ~0x10000000)


def hires_supported_stamp(stamp):
    """Whether a build (by PE timestamp) has a full site table."""
    return stamp == RETAIL_STAMP or '%08x' % stamp in PORT


def hires_supported(buf):
    """Whether this executable's build has a full site table."""
    return hires_supported_stamp(_pe_stamp(buf))


# What the CLI-era knobs settled on: the split FOV between the 4:3 that
# fits a viewport and the one that fills it, nearest-neighbour compositing,
# the widened hangar window, and a polygon pool of 8000 per renderer.
HIRES_SPLIT_FOV = 'mean'
HIRES_POLYS = 8000
# Side-by-side split: rows of the 640x480 HUD frame above this go to the
# top of the viewport instead of the centred frame. The timer and the
# health bars end by 95; the weapon strips start near 300. 0 turns it off.
HIRES_HUD_BAND = 110
# In a split game the split is drawn only while either player's machine is
# in one of these sub-states: the rounds (9..0x0c), the result and continue
# screens (0xd, 0xe), the win and lose screens (0x14, 0x15) and 0x1b. Every
# other frame - the machine select, the waiting card, the wipe, the encounter
# screen - is one full-screen viewport from the player whose sub-state is
# lower (P1 on a tie), and so is everything outside a match (MODE not 4).
HIRES_SPLIT_STATES = (9, 10, 11, 12, 0xd, 0xe, 0x14, 0x15, 0x1b)
# Diagnostic: print "MODE SUBMODE  MODE2 SUBMODE2  SHOW" in hex at the top
# of every frame. For reading the sub-state numbers off a screen.
HIRES_DEBUG_STATES = False


# The size F4 switches to and back from. Both sizes are written: the
# first into the code, the second into a table in the section that the
# blob copies over the sites at runtime (ui.asm f4_toggle).
HIRES_ALT = (1280, 720)


def _ui_words(w, hh):
    """The size-derived words of the data block, by section offset."""
    # 3D scale: the height (vertical FOV kept, Hor+). Split viewports
    # take a scale between the 4:3 that fits inside them and the one
    # that fills them; the game's split block multiplies the 1P scale
    # by K, and the compositor uses the same scale for the 2D layer.
    full = hh / 480

    def split_scale(vw, vh, mode):
        fit, fill = min(vw / 640, vh / 480), max(vw / 640, vh / 480)
        return {'fit': fit, 'fill': fill,
                'mean': math.sqrt(fit * fill)}[mode]
    s_sbs = split_scale(w / 2, hh, HIRES_SPLIT_FOV)
    s_tb = split_scale(w, hh / 2, HIRES_SPLIT_FOV)
    # HUD scale: the 4:3 frame that fits inside the viewport, for the
    # HUD passes (through the compositor's float) and the 2D layer.
    h_1p = min(w / 640, hh / 480)
    h_sbs = min(w / 2 / 640, hh / 480)
    h_tb = min(w / 640, hh / 2 / 480)
    return {
        UI_MODEW: struct.pack('<II', w, hh),
        UI_KSBS: struct.pack('<ff', s_sbs / full, s_tb / full),
        UI_SCALE: struct.pack('<III', int(h_1p * 65536),
                              int(h_sbs * 65536), int(h_tb * 65536)),
        UI_HUD: struct.pack('<ffff', h_1p, h_sbs, h_tb, h_1p),
        # The credits moon card: 26 units high at z=80, 0.325 focal
        # lengths, against the frame's half height of 0.399 - the same at
        # any size, since the focal length follows the height. 1.25 keeps
        # a margin over the 1.227 that just covers it.
        UI_CMOON: f32(1.25),
    }


def _hires_check_size(width, height):
    if width % 32 or height % 8 or width > 2040:
        raise ValueError('width must be a multiple of 32 and at most 2040, '
                         'height a multiple of 8')


def _va_at(pe, off):
    """A file offset's virtual address."""
    for x in pe.sections:
        if x['raddr'] <= off < x['raddr'] + x['rsize']:
            return pe.base + x['vaddr'] + off - x['raddr']
    raise ValueError('offset 0x%x is outside every section' % off)


def _make_writable(buf, pe, off):
    """Set the writable flag on the section holding file offset off.

    For the F4 switch: f4_toggle copies the other size's bytes over live
    code and data at runtime, so every section the table touches - .text
    and .rdata included - stays writable for the life of the process."""
    tab = pe.opt + pe.optsz
    for i, x in enumerate(pe.sections):
        if x['raddr'] <= off < x['raddr'] + x['rsize']:
            o = tab + 40 * i + 36
            chars = struct.unpack_from('<I', buf, o)[0]
            struct.pack_into('<I', buf, o, chars | 0x80000000)
            return
    raise ValueError('offset 0x%x is outside every section' % off)


def _annex_pushes(buf, stamp):
    """The (height, width) push immediates of the idle-pass recreate in
    asm/activate.asm, as file offsets, when the annex is present."""
    build = BY_STAMP.get(stamp)
    if build is None or not build.annex or len(buf) <= build.annex[1]:
        return None
    off = build.annex[1] + annex_layout(build)[0]['ACTIVATE']
    if buf[off + 0xae:off + 0xb8] != bytes.fromhex('68e00100006880020000'):
        return None
    return off + 0xae + 1, off + 0xb3 + 1


def port_sites(sites, port, A, sec_va=None):
    """Retail sites translated onto another build: each moved to the
    build's own offset, its old bytes replaced by the build's, and the
    handful of shape-dependent rewrites redone from those bytes. Split
    out so tools/selftest.py can exercise it with an identity port.

    Returns (sites, fov): fov is the FOV block to write at UI_FOV when
    the build's own block is too short to hold it and the site calls
    it there instead, else None. sec_va is the section's address; the
    identity port needs none."""
    moved, fov = [], None
    lens = port.get('passlen', {})
    pass_offs = {s - 0x400c00: n for n, (s, _l)
                 in enumerate(UI_PASS_FUNCS)}
    mask_offs = {o: len(b) // 2 for o, _n, b in MASK_ADVANCE}
    masks = [struct.pack('<I', A('MASKPTR')).join(
        m.split(struct.pack('<I', ADDR['MASKPTR'])))
        for m in (MASK_LOAD, MASK_STORE)]
    for off, old_, new_ in sites:
        if off in port.get('absent', ()):
            continue
        boff, bold = port['off'][off]
        if off in pass_offs and 0x400c00 + off in lens:
            # the build's shorter prologue: fewer displaced bytes
            new_ = new_[:5] + b'\x90' * (lens[0x400c00 + off] - 5)
        if off in mask_offs:
            # The interleaved instructions are the build's own, not
            # retail's: four of them load a frame global by address.
            span = bytes.fromhex(bold)
            between = span
            for part in (masks[0], MASK_ADD, masks[1]):
                i = between.find(part)
                if i < 0:
                    raise ValueError('mask span at 0x%06x: %s'
                                     % (boff, span.hex()))
                between = between[:i] + between[i + len(part):]
            i = mask_offs[off]
            new_ = (between + new_[i:i + 10]).ljust(len(span), b'\x90')
            moved.append((boff, None, new_))
            continue
        bold = None if old_ is None else bytes.fromhex(bold)
        if off == 0x1c782d:
            # the FOV block: the build's ends at its last fstp
            end = bold.find(b'\xd9\x1d' + u32(A('ASPECT_B')))
            if end < 0:
                raise ValueError('FOV block not recognised at 0x%06x'
                                 % boff)
            n = end + 6
            block = new_.rstrip(b'\x90')
            bold = bold[:n]
            if len(block) <= n:
                new_ = block.ljust(n, b'\x90')
            elif sec_va is None:
                raise ValueError('FOV block does not fit at 0x%06x' % boff)
            else:
                fov = block + b'\xc3'
                new_ = (b'\xe8' + u32(sec_va + UI_FOV - (0x400c00 + boff + 5))
                        ).ljust(n, b'\x90')
            moved.append((boff, bold, new_))
            continue
        if off in (0x07fa63, 0x166a63):
            # 8 - n becomes n + 8, in whichever register the build
            # keeps n
            reg = bold[6] & 7
            new_ = (b'\x8d' + bytes([0x40 | reg, 8]) + b'\x50'
                    + b'\x90' * 4)
        if new_ and new_[0] == 0xe9 and bold and bold[:1] == b'\x0f' \
                and 0x80 <= bold[1] <= 0x8f:
            # a jcc made unconditional: the build's own target, one
            # byte nearer (six bytes of jcc to five of jmp)
            rel = struct.unpack_from('<i', bold, 2)[0]
            new_ = b'\xe9' + u32(rel + 1) + new_[5:]
        elif new_ and new_[0] in (0xe8, 0xe9) and sec_va is not None:
            # a jump into the section: same target, moved site. One
            # inside the image was built from A() and is already
            # relative to the build's site (the F4 exit).
            tgt = (0x400c00 + off + 5
                   + struct.unpack_from('<i', new_, 1)[0])
            if tgt >= sec_va:
                new_ = (new_[:1] + u32(tgt - (0x400c00 + boff + 5))
                        + new_[5:])
        if off in (0x1c799b, 0x1c7ad2, 0x1c7bb1, 0x1c7c36):
            # row maths rewritten as imul: use the register the
            # build's own lea/shl chain uses
            reg = None
            for i2 in range(len(bold) - 1):
                if bold[i2] == 0xc1 and 0xe0 <= bold[i2 + 1] <= 0xe7:
                    reg = bold[i2 + 1] & 7
                    break
            assert reg is not None, hex(off)
            new_ = (b'\x69' + bytes([0xc0 | reg << 3 | reg])
                    + new_[2:6] + b'\x90' * (len(bold) - 6))
        if off == 0x7e504:
            # cmp [mode], 8 / je: the je keeps the build's own
            # distance, one byte further in
            rel = struct.unpack_from('<i', bold, 10)[0]
            new_ = (b'\x83\x3d' + u32(A('SPRITEMODE')) + b'\x08'
                    + b'\x0f\x84' + u32(rel + 1) + b'\x90')
            assert len(new_) == len(bold)
        moved.append((boff, bold, new_))
    return moved, fov


def hires_install(buf, width, height, alt=HIRES_ALT):
    """Patch buf (a bytearray of v_on.exe, stock or vo_patch'd) in place
    for width x height, with alt as the size F4 switches to. Raises
    ValueError on a size or byte mismatch. Returns the number of sites
    written."""
    _hires_check_size(width, height)
    _hires_check_size(*alt)
    stamp = _pe_stamp(buf)
    port = None
    if stamp != RETAIL_STAMP:
        port = PORT.get('%08x' % stamp)
        if port is None:
            raise ValueError('not a build this tool knows')
    if port is None:
        A = ADDR.__getitem__
    else:
        A = lambda name: port['va'][ADDR[name]]     # noqa: E731
    w, hh = width, height
    aw, ah = alt
    mask_off = UI_OFF + UI_OFF_SIZE
    rowtab_off = mask_off + max(hh * (w // 8 + 4), ah * (aw // 8 + 4))
    size = rowtab_off + 2 * max(hh, ah) * 4
    pool_off = size
    size += 2 * (HIRES_POLYS * 0x3c + 8)   # pool, sides, list; both renderers
    code = UI_CODE
    if port is not None:
        code = bytearray(code)
        for o, va in UI_REFS:
            assert struct.unpack_from('<I', code, o)[0] == va
            struct.pack_into('<I', code, o, port['va'][va])
        code = bytes(code)
    raw = bytearray(code.ljust(UI_OFF, b'\0'))
    sec_va, rawptr = add_section(buf, size, raw=bytes(raw))
    targets = [A(n) for n in
               ('CALL_PRE1', 'CALL_POST1', 'CALL_PRE2', 'CALL_POST2')]
    for (off, _t), target in zip(UI_CALLS, targets):
        struct.pack_into('<i', buf, rawptr + off + 1,
                         target - (sec_va + off + 5))
    # pass stubs: call hud_enter, the displaced prologue, jump back
    lens = port.get('passlen', {}) if port else {}
    pass_funcs = [(A('PASS%d' % n), lens.get(s, ln))
                  for n, (s, ln) in enumerate(UI_PASS_FUNCS)]
    for n, (site, ln) in enumerate(pass_funcs):
        o = UI_PASS_STUBS + 20 * n
        va = sec_va + o
        stub = (b'\xe8' + u32(sec_va + UI_HUD_ENTER - (va + 5))
                + buf[site - 0x400c00:site - 0x400c00 + ln]
                + b'\xe9' + u32(site + ln - (va + 5 + ln + 5)))
        assert len(stub) <= 20 and o + 20 <= 0x1800
        buf[rawptr + o:rawptr + o + len(stub)] = stub
    words, alt_words = _ui_words(w, hh), _ui_words(aw, ah)
    for off, val in words.items():
        buf[rawptr + off:rawptr + off + len(val)] = val
    struct.pack_into('<I', buf, rawptr + UI_ROWTAB, sec_va + rowtab_off)
    struct.pack_into('<I', buf, rawptr + UI_PINTH, HIRES_HUD_BAND)
    struct.pack_into('<Q', buf, rawptr + UI_SPLITST,
                     sum(1 << n for n in HIRES_SPLIT_STATES))
    struct.pack_into('<I', buf, rawptr + UI_DEBUG, int(HIRES_DEBUG_STATES))
    struct.pack_into('<ffff', buf, rawptr + UI_CONST, 65536.0, 640.0,
                     480.0, 0.5)
    sites = build_sites(w, hh, sec_va, sec_va + mask_off,
                        sec_va + rowtab_off,
                        pool=(sec_va + pool_off, HIRES_POLYS), A=A)
    alt_sites = build_sites(aw, ah, sec_va, sec_va + mask_off,
                            sec_va + rowtab_off,
                            pool=(sec_va + pool_off, HIRES_POLYS), A=A)
    if port is not None:
        sites, fov = port_sites(sites, port, A, sec_va)
        alt_sites, _fov = port_sites(alt_sites, port, A, sec_va)
        if fov:                          # the same at either size
            assert UI_FOV + len(fov) <= UI_OFF
            buf[rawptr + UI_FOV:rawptr + UI_FOV + len(fov)] = fov
    # The idle-pass recreate in asm/activate.asm pushes its own size.
    pushes = _annex_pushes(buf, stamp)
    if pushes:
        sites += [(pushes[0], u32(480), u32(hh)), (pushes[1], u32(640), u32(w))]
        alt_sites += [(pushes[0], u32(480), u32(ah)),
                      (pushes[1], u32(640), u32(aw))]
    # The F4 table: every site whose bytes differ between the sizes, and
    # the data words above.
    pe = _PE(buf)
    table = []
    for (off, old_, new_), (aoff, aold, anew) in zip(sites, alt_sites):
        assert off == aoff and old_ == aold and len(new_) == len(anew)
        if new_ != anew:
            table.append((_va_at(pe, off), new_, anew))
    for off in sorted(words):
        if words[off] != alt_words[off]:
            table.append((sec_va + off, words[off], alt_words[off]))
    for va, _a, _b in table:
        if va < sec_va:
            _make_writable(buf, pe, pe.off(va - pe.base))
    blob = b''.join(u32(va) + u32(len(a)) + a + b for va, a, b in table)
    blob += u32(0)
    if len(blob) > UI_F4TAB_SIZE:
        raise ValueError('F4 table too large: %d bytes' % len(blob))
    buf[rawptr + UI_F4TAB_OFF:rawptr + UI_F4TAB_OFF + len(blob)] = blob
    struct.pack_into('<II', buf, rawptr + UI_F4MODE, 0,
                     sec_va + UI_F4TAB_OFF)
    if port is not None:
        masks = tuple(struct.pack('<I', A('MASKPTR')).join(
            m.split(struct.pack('<I', ADDR['MASKPTR'])))
            for m in (MASK_LOAD, MASK_STORE))
        apply(buf, sites, mask_load=masks[0], mask_store=masks[1])
    else:
        apply(buf, sites)
    _split_dialog(buf)
    return len(sites)

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
# own tables and the same BLOBS.
#
# Where a blob goes is the annex, a section appended before any patch is
# written, in ANNEX_BLOBS order; or a cave, the virtual address of a place
# the game itself reaches, or (blob, offset) for a blob that rides inside
# another. A symbol is a virtual address in the game, or (blob, label) for
# a place inside one of ours.

class Build(object):
    def __init__(self, name, short, md5, size, stamp, sections, caves,
                 symbols, art, sites=None, annex=None):
        # the name on screen, and a word for a label or a log line
        self.name, self.short = name, short
        self.md5, self.size = md5, size
        self.stamp = stamp                  # PE timestamp; keys PORT
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

RETAIL = Build('English retail', 'retail', ORIGINAL_MD5, EXE_SIZE,
               RETAIL_STAMP, sections=(
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
    'SELSET': 0x004980d9,          # search loop exit, then set the selection
    'CURSOR': 0x004cd8c3,          # (column, row), cdecl
    'PRINT': 0x004ceeeb,           # (text), cdecl, from the cursor
    'WRITELINE': 0x005b1833,       # (key, value): one v_on.ini line
    'FINDLINE': 0x005b1871,        # (key) -> value text, 0 if absent
    'EXIT2P': 0x005bcd57,          # and the 2P one
    'KBD2P': 0x005bceed,
    'GPAUSE': 0x005c67c5,          # the built-in dialogs' pause, arg 0
    'GRESUME': 0x005c680b,         # and their resume
    'GAMEMODE': 0x006bc94c,        # loop mode: 1 two players, 2 network
    'MODE2': 0x01ef8a90,           # 2P state machine's mode; ticks in mode 1
    'PENDING': 0x0365cb9c,         # a recreate owed
    'RETADDR': 0x0365cba0,         # where one returns to
    'RECREATE': 0x005c56a2,        # release and create the surfaces
    'IDLE': 0x005c63aa,            # the loop's pass while inactive
    'INACTIVE': 0x01add128,        # the flag it idles on
    'SETACTIVE': 0x005c6326,       # (pause): the loop stops on 1, runs on 0
    'FSFLAGS': 0x006bf598,         # bit 2: the low-resolution modes
    'FSMODE': 0x006bf560,          # low-res: 320x240 in the FSFLAGS modes
    'FBX': 0x006bf578,             # picture origin and size on the
    'FBY': 0x006bf57c,             # surface, set per mode at 0x5c88ac
    'FBW': 0x006bf5b8,
    'FBH': 0x006bf5bc,
    'HAVESURF': 0x006bf570,        # the game's "surfaces exist" flag
    'ISICONIC': 0x0365d594,        # IAT: IsIconic
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
    'PREV': 0x0365cba4,            # scratch: last frame's slot, credits+names
    'HELD': 0x0365cba5,            # and how long this press has lasted
    'CAMERA1': 0x00bf0457,         # 1P key slot for Select; win/lose read it
    'ACCEPT1': 0x00bf0481,         # 1P's key buffer slot for A and Space
    'BLOCKS': 0x00bf6838,          # saved bind blocks: + player * 0x70
    'CURPLAYER': 0x00bf6bac,       # the side being configured, 0 or 1
    'PHASE': 0x01ad0964,           # where the credits sequence is up to
    'CAMERA2': 0x01ad0d94,
    'ACCEPT2': 0x01ad0db1,         # and 2P
    'FLAG': 0x01ae1c1c,            # the displaced write
    'MODE': 0x01ae3594,            # game state; the tick gates on MODE+SUBMODE
    'SUBMODE': 0x01ae3690,         # game sub-state
    'MOVIEX': 0x01ae5f34,          # the offsets the replaced code read
    'MOVIEY': 0x01ae5f38,
    'PRIMARY': 0x01ae5f40,         # the surface DRAW paints on
    'HWND': 0x01ae5f58,            # the game's window
    'BACK': 0x01ae5f5c,            # back buffer, flipped over PRIMARY
    'LEV1A': 0x01cb14c4,           # 1P lever words, left then right
    'LEV1B': 0x01cb14c6,
    'EDGEA': 0x01ed5ec5,           # press edges, lever A byte: bit 0 is LT
    'EDGEB': 0x01ed5ec6,           # press edges, lever B byte: 2P's RT
    'LEV2A': 0x01ee3ee4,           # 2P
    'LEV2B': 0x01ee3ee6,
    'MOVIEHWND': 0x01ef88c8,       # the mciavi window (MCI_ANIM_STATUS_HWND)
    'MOVIEDEV': 0x01ef88f0,        # its device id
    'BINDS1': 0x03651470,          # 1P bind bytes
    'BINDS2': 0x03651488,          # 2P bind bytes
    'DEVICES': 0x03651540,         # 1P's profile, 0 being the keyboard
    'XIFN': 0x0365cb40,            # XInputGetState; 0 not yet, 1 failed
    'STATE': 0x0365cb44,           # the tick's XINPUT_STATE
    'BTN': 0x0365cb48,             # wButtons in STATE; the cond table reads it
    'SCR1': 0x0365cb60,            # scratch the tick keeps per player
    'SCR2': 0x0365cb61,
    'PADIDX': 0x0365cb64,          # XInput slot + 1 per side, 5 none, 0 unbuilt
    'PADRETRY': 0x0365cb66,        # misses of a side without a pad
    'PSTATE': 0x0365cb70,          # the pump's own XINPUT_STATE, apart from
    'PBTN': 0x0365cb74,            # the tick's; wButtons in PSTATE
    'SLEEPFN': 0x0365cb80,         # resolved Sleep: 0 not yet, 1 failed
    'PADPREV': 0x0365cb84,         # last polled buttons, one word per pad
    'DZTHR1': 0x0365cb8c,          # stick thresholds of 32767, 1P then 2P
    'DZSTR1': 0x0365cb94,          # deadzone digit pairs; see padxinput.asm
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
JAPAN = Build('Japanese rerelease', 'jp', JAPAN_MD5, JAPAN_SIZE, 0x345107fa,
              sections=(
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
    'SELSET': 0x00496c36,          # search loop exit, then set the selection
    'CURSOR': 0x004cb463,          # (column, row), cdecl
    'PRINT': 0x004cca8b,           # (text), cdecl, from the cursor
    'WRITELINE': 0x005ac4f3,       # (key, value): one v_on.ini line
    'FINDLINE': 0x005ac531,        # (key) -> value text, 0 if absent
    'EXIT2P': 0x005b76c7,          # and the 2P one
    'KBD2P': 0x005b785d,
    'GPAUSE': 0x005c1094,          # the built-in dialogs' pause, arg 0
    'GRESUME': 0x005c10da,         # and their resume
    'GAMEMODE': 0x006b86ac,        # loop mode: 1 two players, 2 network
    'MODE2': 0x01ef4230,           # 2P state machine's mode; ticks in mode 1
    'PENDING': 0x036577fc,
    'RETADDR': 0x03657800,
    'RECREATE': 0x005bff42,
    'IDLE': 0x005c0c79,
    'INACTIVE': 0x01ad7db0,
    'SETACTIVE': 0x005c0bf5,
    'FSFLAGS': 0x006bb2b0,
    'FSMODE': 0x006bb278,          # low-res: 320x240 in the FSFLAGS modes
    'FBX': 0x006bb290,             # picture origin and size, as retail
    'FBY': 0x006bb294,
    'FBW': 0x006bb2d0,
    'FBH': 0x006bb2d4,
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
    'PREV': 0x03657804,            # scratch: last frame's slot, credits+names
    'HELD': 0x03657805,            # and how long this press has lasted
    'CAMERA1': 0x00beb0af,         # 1P key slot for Select; win/lose read it
    'ACCEPT1': 0x00beb0d9,         # 1P's key buffer slot for A and Space
    'BLOCKS': 0x00bf1730,          # saved bind blocks: + player * 0x70
    'CURPLAYER': 0x00bf1590,       # the side being configured, 0 or 1
    'PHASE': 0x01acb68c,           # where the credits sequence is up to
    'CAMERA2': 0x01acba14,
    'ACCEPT2': 0x01acba31,         # and 2P
    'FLAG': 0x01adcdf8,            # the displaced write
    'MODE': 0x01adcdf0,            # game state; the tick gates on MODE+SUBMODE
    'SUBMODE': 0x01addc40,         # game sub-state
    'MOVIEX': 0x01ae0c1c,          # the offsets the replaced code read
    'MOVIEY': 0x01ae0c20,
    'PRIMARY': 0x01ae0c34,         # the surface DRAW paints on
    'HWND': 0x01ae0c38,            # the game's window
    'BACK': 0x01ae0c18,            # back buffer, flipped over PRIMARY
    'LEV1A': 0x01b13204,           # 1P lever words, left then right
    'LEV1B': 0x01b13206,
    'EDGEA': 0x01ed0b65,           # press edges, lever A byte: bit 0 is LT
    'EDGEB': 0x01ed0b66,           # press edges, lever B byte: 2P's RT
    'LEV2A': 0x01ef3534,           # 2P
    'LEV2B': 0x01ef3536,
    'MOVIEHWND': 0x01ef3570,       # the mciavi window (MCI_ANIM_STATUS_HWND)
    'MOVIEDEV': 0x01ef3590,        # its device id
    'BINDS1': 0x0364c170,          # 1P bind bytes
    'BINDS2': 0x0364c188,          # 2P bind bytes
    'DEVICES': 0x0364c580,         # 1P's profile, 0 being the keyboard
    # scratch in the page slack past .data, as retail's is
    'XIFN': 0x036577a0,            # XInputGetState; 0 not yet, 1 failed
    'STATE': 0x036577a4,           # the tick's XINPUT_STATE
    'BTN': 0x036577a8,             # wButtons in STATE; the cond table reads it
    'SCR1': 0x036577c0,            # scratch the tick keeps per player
    'SCR2': 0x036577c1,
    'PADIDX': 0x036577c4,          # XInput slot + 1 per side, 5 none, 0 unbuilt
    'PADRETRY': 0x036577c6,        # misses of a side without a pad
    'PSTATE': 0x036577d0,          # the pump's own XINPUT_STATE, apart from
    'PBTN': 0x036577d4,            # the tick's; wButtons in PSTATE
    'SLEEPFN': 0x036577e0,         # resolved Sleep: 0 not yet, 1 failed
    'PADPREV': 0x036577e4,         # last polled buttons, one word per pad
    'DZTHR1': 0x036577ec,          # stick thresholds of 32767, 1P then 2P
    'DZSTR1': 0x036577f4,          # deadzone digit pairs; see padxinput.asm
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
OEM = Build('USA OEM', 'oem', OEM_MD5, OEM_SIZE, 0x3317246a, sections=(
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
    'SELSET': 0x00497f74,          # search loop exit, then set the selection
    'CURSOR': 0x004cd763,   # func
    'PRINT': 0x004ced8b,   # func
    'WRITELINE': 0x005b1303,   # func
    'FINDLINE': 0x005b1341,   # func
    'EXIT2P': 0x005bc827,   # insn
    'KBD2P': 0x005bc9bd,   # func
    'GPAUSE': 0x005c62de,   # func
    'GRESUME': 0x005c6324,   # func
    'GAMEMODE': 0x006bc8e4,        # loop mode: 1 two players, 2 network
    'MODE2': 0x01ef8a20,           # 2P state machine's mode; ticks in mode 1
    'PENDING': 0x0365cb1c,
    'RETADDR': 0x0365cb20,
    'RECREATE': 0x005c5172,
    'IDLE': 0x005c5ec3,
    'INACTIVE': 0x01add0c0,
    'SETACTIVE': 0x005c5e3f,
    'FSFLAGS': 0x006bf530,
    'FSMODE': 0x006bf4f8,          # low-res: 320x240 in the FSFLAGS modes
    'FBX': 0x006bf510,             # picture origin and size, as retail
    'FBY': 0x006bf514,
    'FBW': 0x006bf550,
    'FBH': 0x006bf554,
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
    'PREV': 0x0365cb24,
    'HELD': 0x0365cb25,
    'CAMERA1': 0x00bf0417,         # 1P key slot for Select; win/lose read it
    'ACCEPT1': 0x00bf0441,   # data 1 votes
    'BLOCKS': 0x00bf67f8,          # saved bind blocks: + player * 0x70
    'CURPLAYER': 0x00bf6b6c,
    'PHASE': 0x01ad08fc,
    'CAMERA2': 0x01ad0d2c,
    'ACCEPT2': 0x01ad0d49,   # data 1 votes
    'FLAG': 0x01ae1bac,
    'MODE': 0x01ae3524,            # game state; the tick gates on MODE+SUBMODE
    'SUBMODE': 0x01ae3620,         # game sub-state
    'MOVIEX': 0x01ae5ec4,
    'MOVIEY': 0x01ae5ec8,
    'PRIMARY': 0x01ae5ed0,         # the surface DRAW paints on
    'HWND': 0x01ae5ee8,
    'BACK': 0x01ae5eec,            # back buffer, flipped over PRIMARY
    'LEV1A': 0x01cb1454,
    'LEV1B': 0x01cb1456,
    'EDGEA': 0x01ed5e55,           # press edges, lever A byte: bit 0 is LT
    'EDGEB': 0x01ed5e56,           # press edges, lever B byte: 2P's RT
    'LEV2A': 0x01ee3e74,
    'LEV2B': 0x01ee3e76,
    'MOVIEHWND': 0x01ef8858,
    'MOVIEDEV': 0x01ef8880,
    'BINDS1': 0x03651400,
    'BINDS2': 0x03651418,   # data 1 votes
    'DEVICES': 0x036514d0,
    'XIFN': 0x0365cac0,
    'STATE': 0x0365cac4,
    'BTN': 0x0365cac8,             # wButtons in STATE; the cond table reads it
    'SCR1': 0x0365cae0,            # scratch the tick keeps per player
    'SCR2': 0x0365cae1,
    'PADIDX': 0x0365cae4,          # XInput slot + 1 per side, 5 none, 0 unbuilt
    'PADRETRY': 0x0365cae6,        # misses of a side without a pad
    'PSTATE': 0x0365caf0,          # the pump's own XINPUT_STATE, apart from
    'PBTN': 0x0365caf4,            # the tick's; wButtons in PSTATE
    'SLEEPFN': 0x0365cb00,
    'PADPREV': 0x0365cb04,         # last polled buttons, one word per pad
    'DZTHR1': 0x0365cb0c,          # stick thresholds of 32767, 1P then 2P
    'DZSTR1': 0x0365cb14,          # deadzone digit pairs; see padxinput.asm
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
}, art=RETAIL.art, sites=None, annex=ANNEX_BLOBS)   # retail's art

BUILDS = {RETAIL.md5: RETAIL, JAPAN.md5: JAPAN, OEM.md5: OEM}
BY_STAMP = {b.stamp: b for b in BUILDS.values()}

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
        '5589e553817d0c00010000753b817d107a0000007532833d0000000002742968'
        '00000000ff1500000000680000000050ff150000000085c0740789c3e8fcffff'
        'ff31c05b5dc210005b5de9fcffffff5589e55356578b450c3d100100007532ff'
        '7508e8fcffffff31ff8d475250ff7508ff15000000008d14bd00000000526a00'
        '6a0c50ff15000000004783ff0272daeb453d1101000075450fb74d108d51ae83'
        'fa01763283f902740d83f954740881f9419c000075308b5508e8eaeaeaea85c0'
        '74146a00516811010000ff3500000000ff1500000000b801000000eb0231c05f'
        '5e5b5dc2100083f9517513833d000000000475086a1f8f0500000000ebd8ebc2'
    ), (
        (0x18, 'abs', 'GAMEMODE', 0),
        (0x20, 'abs', 'USER32', 0),
        (0x26, 'abs', 'LOADLIB', 0),
        (0x2b, 'abs', 'DLGBOXPROC', 0),
        (0x32, 'abs', 'GETPROC', 0),
        (0x3d, 'rel', 'F11WRAP', -4),
        (0x4b, 'rel', 'ORIGWNDPROC', -4),
        (0x63, 'rel', 'F11CHECKS', -4),
        (0x72, 'abs', 'GETDLGITEM', 0),
        (0x79, 'abs', 'DZSTR1', 0),
        (0x85, 'abs', 'SENDMSG', 0),
        (0xcc, 'abs', 'HWND', 0),
        (0xd2, 'abs', 'POSTMSG', 0),
        (0xed, 'abs', 'MODE', 0),
        (0xf8, 'abs', 'SUBMODE', 0),
    ), {
        'hook': 0x0,
        'dlgproc': 0x4f,
        'credits': 0xe6,
    }),
    'PADX': (bytes.fromhex(
        '6877030000e81501000083c404e9fcffffff689f030000e80301000083c404e9'
        'fcffffffe806000000ff2500000000609ce8df02000083f8010f86d500000031'
        'f683fe020f83ca000000680000000056ff150000000085c00f85b00000000fb7'
        '1d000000008d14b5000000000fb72a66891a89d825100300003d100300007548'
        '803d060000001e723f803d070000001e723689e825100300003d10030000746e'
        '833d00000000027465c70500000000ffffffff833d00000000017552c7050000'
        '0000ffffffffeb4631ff83ff02733f8d0cbd170100000fb70189da21c221e839'
        'c27428b80001000085d2750b807903007419b8010100006a000fb651025250ff'
        '3500000000ff150000000047ebbc46e92dffffff9d61c3100072000010200055'
        '89e583ec045356578b5d08c745fc00000000e8de01000083f80174448b03ba00'
        '000000e83b01000085c07534c745fc010000000fb70500000000a90010000074'
        '0b8b5318c60280e8fcffffff0fb70500000000a92000000074068b5324c60280'
        '8b4320ffd0837dfc000f84c801000031f683fe0c0f83a1000000833d00000000'
        '047512833d00000000087c09833d000000000c7e0f83fe04747b83fe05747683'
        'fe077f778b53040fb604722de0000000726383f810735e8d3cc5000000000fb6'
        '070fb757028b4f0483f800741783f801741d83f802742c0fb6820000000039c8'
        '772eeb310fbf8200000000f7d8eb070fbf82000000008b0b3b048d000000007f'
        '0feb120fb7050000000085c87502eb05e82500000046e956ffffff31f683fe04'
        '0f83110100000fb705000000000fa3f07305e80300000046ebe38b53100fb60c'
        '32f7d18b53080fb70221c86689028b53140fb60c32f7d18b530c0fb70221c866'
        '8902c35389c366833d00000000007505e83b0000000fb683000000003c057318'
        '485250ff150000000085c0742166c705000000000000eb11fe05000000007509'
        '66c705000000000000b8010000005bc356575389d366c70500000000050531f6'
        '31ff8b04bd000000004883f801771a83fe04731b5356ff15000000004685c075'
        'ee89f08887000000004783ff0272d389da5b5f5ec3a10000000085c075385631'
        'f683fe0373258b04b55c03000050ff150000000085c0750346ebe66868030000'
        '50ff150000000085c07505b801000000a3000000005ec35f5e5bc9c3c7030000'
        'd5030000e303000058496e707574476574537461746500000000000000000000'
        '0000000000000000000000000000000000000000000000000000000000000001'
        '0000000000000000000000000000000000000000000000000000000000000000'
        '0000000000000078696e707574315f342e646c6c0078696e707574315f332e64'
        '6c6c0078696e707574395f315f302e646c6c00'
    ), (
        (0x1, 'abs', '.', 887),
        (0xe, 'rel', 'EXIT1P', -4),
        (0x13, 'abs', '.', 927),
        (0x20, 'rel', 'EXIT2P', -4),
        (0x2b, 'abs', 'PEEKMSG', 0),
        (0x4b, 'abs', 'PSTATE', 0),
        (0x52, 'abs', 'XIFN', 0),
        (0x61, 'abs', 'PBTN', 0),
        (0x68, 'abs', 'PADPREV', 0),
        (0x82, 'abs', 'PSTATE', 6),
        (0x8b, 'abs', 'PSTATE', 7),
        (0xa2, 'abs', 'GAMEMODE', 0),
        (0xab, 'abs', 'MODE', 0),
        (0xb5, 'abs', 'GAMEMODE', 0),
        (0xbe, 'abs', 'MODE2', 0),
        (0xd2, 'abs', '.', 279),
        (0x101, 'abs', 'HWND', 0),
        (0x107, 'abs', 'POSTMSG', 0),
        (0x13f, 'abs', 'STATE', 0),
        (0x156, 'abs', 'BTN', 0),
        (0x168, 'rel', 'CAMSKIP', -4),
        (0x16f, 'abs', 'BTN', 0),
        (0x19c, 'abs', 'MODE', 0),
        (0x1a5, 'abs', 'SUBMODE', 0),
        (0x1ae, 'abs', 'SUBMODE', 0),
        (0x1da, 'abs', 'COND', 0),
        (0x1fa, 'abs', 'BTN', 0),
        (0x207, 'abs', 'BTN', 0),
        (0x212, 'abs', 'BTN', 0),
        (0x21b, 'abs', 'DZTHR1', 0),
        (0x226, 'abs', 'BTN', 0),
        (0x249, 'abs', 'BTN', 0),
        (0x289, 'abs', 'PADIDX', 0),
        (0x298, 'abs', 'PADIDX', 0),
        (0x2a5, 'abs', 'XIFN', 0),
        (0x2b0, 'abs', 'PADIDX', 0),
        (0x2ba, 'abs', 'PADRETRY', 0),
        (0x2c3, 'abs', 'PADIDX', 0),
        (0x2d8, 'abs', 'PADIDX', 0),
        (0x2e5, 'abs', 'DEVICES', 0),
        (0x2f8, 'abs', 'XIFN', 0),
        (0x305, 'abs', 'PADIDX', 0),
        (0x316, 'abs', 'XIFN', 0),
        (0x329, 'abs', '.', 860),
        (0x330, 'abs', 'LOADLIB', 0),
        (0x33c, 'abs', '.', 872),
        (0x343, 'abs', 'GETPROC', 0),
        (0x351, 'abs', 'XIFN', 0),
        (0x35c, 'abs', '.', 967),
        (0x360, 'abs', '.', 981),
        (0x364, 'abs', '.', 995),
        (0x37b, 'abs', 'BINDS1', 0),
        (0x37f, 'abs', 'LEV1A', 0),
        (0x383, 'abs', 'LEV1B', 0),
        (0x387, 'abs', 'MASK1A', 0),
        (0x38b, 'abs', 'MASK1B', 0),
        (0x38f, 'abs', 'ACCEPT1', 0),
        (0x393, 'abs', 'SCR1', 0),
        (0x397, 'abs', 'KBD1P', 0),
        (0x39b, 'abs', 'CAMERA1', 0),
        (0x3a3, 'abs', 'BINDS2', 0),
        (0x3a7, 'abs', 'LEV2A', 0),
        (0x3ab, 'abs', 'LEV2B', 0),
        (0x3af, 'abs', 'MASK2A', 0),
        (0x3b3, 'abs', 'MASK2B', 0),
        (0x3b7, 'abs', 'ACCEPT2', 0),
        (0x3bb, 'abs', 'SCR2', 0),
        (0x3bf, 'abs', 'KBD2P', 0),
        (0x3c3, 'abs', 'CAMERA2', 0),
    ), {
        'entry1p': 0x0,
        'entry2p': 0x12,
        'pump': 0x24,
        'pollpads': 0x2f,
        'keytab': 0x117,
        'keytab_end': 0x11f,
        'tick': 0x11f,
        'apply': 0x25a,
        'padpoll': 0x283,
        'resolve': 0x315,
        'epilogue': 0x357,
        'dlltab': 0x35c,
        'procname': 0x368,
        'block1': 0x377,
        'block2': 0x39f,
        'dll14': 0x3c7,
        'dll13': 0x3d5,
        'dll910': 0x3e3,
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
        (0x94, 'abs', 'ACCEPT2', 0),
        (0x98, 'abs', 'SCR2', 0),
        (0x9c, 'abs', 'KBD2P', 0),
        (0xa0, 'abs', 'CAMERA2', 0),
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
        '833d00000000017510a1000000004883f8017605e9fcffffffe9fcffffff6a01'
        'ff3500000000e8fcffffff83c408e9fcffffff'
    ), (
        (0x2, 'abs', 'CURPLAYER', 0),
        (0xa, 'abs', 'DEVICES', 0),
        (0x15, 'rel', 'CROSSCHECK', -4),
        (0x1a, 'rel', 'KBACCEPT', -4),
        (0x22, 'abs', 'CURPLAYER', 0),
        (0x27, 'rel', 'DEFAULTS', -4),
        (0x2f, 'rel', 'RESUME', -4),
    ), {
        'dupkey': 0x0,
        'default_button': 0x1e,
    }),
    'BINDLIST': (bytes.fromhex(
        '50a1000000006bc07083b8000000000358c3e8e9ffffff7406837df810eb0483'
        '7df8217c0883c404e9fcffffffc3e8cdffffff74088b04c500000000c38b04c5'
        '00000000c3e8b6ffffff74088a04c504000000c38a04c504000000c3'
    ), (
        (0x2, 'abs', 'CURPLAYER', 0),
        (0xb, 'abs', 'BLOCKS', 0),
        (0x1b, 'abs8', 'FILLIDX', 0),
        (0x21, 'abs8', 'FILLIDX', 0),
        (0x29, 'rel', 'FILLDONE', -4),
        (0x38, 'abs', 'PADLIST', 0),
        (0x40, 'abs', 'KEYLIST', 0),
        (0x4f, 'abs', 'PADLIST', 4),
        (0x57, 'abs', 'KEYLIST', 4),
    ), {
        'devcur': 0x0,
        'fillcount': 0x12,
        'fillname': 0x2e,
        'storeid': 0x45,
    }),
    'BINDMAP': (bytes.fromhex(
        '50a1000000006bc07083b8000000000358c3e8e9ffffff7406837df410eb0483'
        '7df4217c0883c404e9fcffffffc3e8cdffffff7408390cc504000000c3390cc5'
        '04000000c38b4424046bc87081c1380000006bc01805000000006a185051e8fc'
        'ffffff83c40cc3'
    ), (
        (0x2, 'abs', 'CURPLAYER', 0),
        (0xb, 'abs', 'BLOCKS', 0),
        (0x1b, 'abs8', 'SELIDX', 0),
        (0x21, 'abs8', 'SELIDX', 0),
        (0x29, 'rel', 'SELSET', -4),
        (0x38, 'abs', 'PADLIST', 4),
        (0x40, 'abs', 'KEYLIST', 4),
        (0x4e, 'abs', 'BLOCKS', 56),
        (0x56, 'abs', 'SIMPLEDEF', 0),
        (0x5f, 'rel', 'MEMCPY', -4),
    ), {
        'devcur': 0x0,
        'mapcount': 0x12,
        'mapid': 0x2e,
        'simple_defaults': 0x45,
    }),
    'BINDBLOCK': (bytes.fromhex(
        '83b8000000000374060508000000c30538000000c36bd07083ba00000000036b'
        'c01874060500000000c30500000000c383b9000000000374088a844108000000'
        'c38a844138000000c3240f3c0a720204270430880747c3'
    ), (
        (0x2, 'abs', 'BLOCKS', 0),
        (0xa, 'abs', 'BLOCKS', 8),
        (0x10, 'abs', 'BLOCKS', 56),
        (0x1a, 'abs', 'BLOCKS', 0),
        (0x25, 'abs', 'GAMEPADDEF', 0),
        (0x2b, 'abs', 'SIMPLEDEF', 0),
        (0x32, 'abs', 'BLOCKS', 0),
        (0x3c, 'abs', 'BLOCKS', 8),
        (0x44, 'abs', 'BLOCKS', 56),
    ), {
        'blockaddr': 0x0,
        'defsource': 0x15,
        'preselbind': 0x30,
        'hexchar': 0x49,
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
        (0x40, 'abs', 'BINDS1', 0),
    ), {
        'loadsimple': 0x0,
    }),
    'BLOCKCUR': (bytes.fromhex(
        '518b0d000000006bc97083b900000000035974078d8008000000c38d80380000'
        '00c350518b45086bc8708b04850000000039810000000059587505e9fcffffff'
        'c3'
    ), (
        (0x3, 'abs', 'CURPLAYER', 0),
        (0xc, 'abs', 'BLOCKS', 0),
        (0x16, 'abs', 'BLOCKS', 8),
        (0x1d, 'abs', 'BLOCKS', 56),
        (0x2d, 'abs', 'DEVICES', 0),
        (0x33, 'abs', 'BLOCKS', 0),
        (0x3c, 'rel', 'MEMCPY', -4),
    ), {
        'blockcur': 0x0,
        'syncshim': 0x22,
    }),
    'INIPARSE': (bytes.fromhex(
        '50e8fcffffff83c40485c0741e89c631c9e816000000c0e00488c2e80c000000'
        '08d088040f4183f9187ce6c30fb606460c202c303c0976022c27c36a015e8d04'
        'b500000000506bc60c050000000050e8fcffffff83c4084e79e4c3'
    ), (
        (0x2, 'rel', 'FINDLINE', -4),
        (0x41, 'abs', 'DZSTR1', 0),
        (0x4a, 'abs', 'DZKEYS', 0),
        (0x50, 'rel', 'WRITELINE', -4),
    ), {
        'parse12': 0x0,
        'nibble': 0x2c,
        'dzsave': 0x3b,
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
        'ffffff75048345f42483c404e9fcffffff69c14701000089049d00000000880c'
        '9d0300000088c8d40a6605303086c46689049d00000000c3'
    ), (
        (0x1, 'rel', 'DEVCUR', -4),
        (0x9, 'abs8', 'SELIDX', 0),
        (0x12, 'rel', 'SELDIGITS', -4),
        (0x1a, 'rel', 'SELLIST', -4),
        (0x1f, 'rel', 'DEVCUR', -4),
        (0x27, 'abs8', 'SELIDX', 0),
        (0x2d, 'rel', 'SELSET', -4),
        (0x3a, 'abs', 'DZTHR1', 0),
        (0x41, 'abs', 'DZSTR1', 3),
        (0x53, 'abs', 'DZSTR1', 0),
    ), {
        'selsec': 0x0,
        'selidx': 0x1e,
        'dzseed': 0x31,
    }),
    'COMMITDEV': (bytes.fromhex(
        '89048d0000000066c70500000000000083f801740583f80375275657516bf170'
        '8db60800000083f803750383c6306bf9188dbf00000000b906000000f3a5595f'
        '5ec3'
    ), (
        (0x3, 'abs', 'DEVICES', 0),
        (0xa, 'abs', 'PADIDX', 0),
        (0x22, 'abs', 'BLOCKS', 8),
        (0x33, 'abs', 'BINDS1', 0),
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
        '0050ffd3e8fcffffffc3be000000006a035f8b068b0031c983f8010f94c151ff'
        '7604ff74240cff150000000083c6084f75e0c20400'
    ), (
        (0x3, 'rel', 'GPAUSE', -4),
        (0xd, 'abs', 'DLGPROC', 0),
        (0x1d, 'abs', 'GETMODULE', 0),
        (0x25, 'rel', 'GRESUME', -4),
        (0x2b, 'abs', 'CHECKS', 0),
        (0x48, 'abs', 'CHECKDLGBTN', 0),
    ), {
        'f11wrap': 0x0,
        'f11checks': 0x2a,
    }),
    'VOXT': (bytes.fromhex(
        '89d381f9419c00000f84bb00000083f95474766a015f8d47525053ff15000000'
        '008d34bd00000000566a036a0d50ff15000000000fb60683e83083f80977190f'
        'b6560183ea3083fa0977056bc00a01d08d50fb83fa5a76040fb64603ba000000'
        '00803a0074085389c189fbffd25b4f79a5b8000000008038007402ffd06a0053'
        'ff150000000031c0c3ba00000000803a0074336a015f536a285989fbffd25b4f'
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
        '5589e583ec445356578b7d0831c08945bca1000000008947f4a1000000008947'
        'f031f668ea010000ff150000000085c0742b68f401000050ff150000000085c0'
        '741b89c36805020000ff150000000085c0740a681002000050ffd389c685f675'
        '068b35000000008d45f050ff7708ffd685c00f846d0100008b45f82b45f08945'
        'd885c00f8e5c0100008b45fc2b45f48945d485c00f8e4b0100000fbf47108945'
        'd085c00f8e3c0100000fbf47148945cc85c00f8e2d0100008d45dc5068000043'
        '006843080000ff3500000000a100000000ffd085c075328b45e885c07e2b8b4d'
        'ec85c97e248945d089c86bc01e99bbf0000000f7fb8945c089c869c0b4000000'
        '99f7fb8945bc8945cc8b45d8f76dcc89c38b45d4f76dd039c37f118b45d88945'
        'c8f76dccf77dd08945c4eb0f8b45d48945c4f76dd0f77dcc8945c88b45d82b45'
        'c8d1f88947f48b45d42b45c4d1f88947f08b45c88947108b45c48947146a01ff'
        '75c4ff75c8ff77f0ff77f4ff3500000000ff15000000008b45bc85c0743531c0'
        '8945dc8945e08b45c08945e48b45d08945e88b45bc8945ec8d45dc5068000003'
        '006842080000ff3500000000a100000000ffd031c08945dc8945e08945e48b45'
        'c88945e88b45c48945ec8d45dc5068000005006842080000ff3500000000a100'
        '000000ffd05f5e5bc9c364647261772e646c6c00444447657450726f63416464'
        '72657373007573657233322e646c6c00476574436c69656e745265637400'
    ), (
        (0x12, 'abs', 'MOVIEX', 0),
        (0x18, 'abs8', 'F_X', 0),
        (0x1a, 'abs', 'MOVIEY', 0),
        (0x20, 'abs8', 'F_Y', 0),
        (0x24, 'abs', '.', 490),
        (0x2a, 'abs', 'GETMODULE', 0),
        (0x33, 'abs', '.', 500),
        (0x3a, 'abs', 'GETPROC', 0),
        (0x45, 'abs', '.', 517),
        (0x4b, 'abs', 'GETMODULE', 0),
        (0x54, 'abs', '.', 528),
        (0x63, 'abs', 'GETCLIENT', 0),
        (0xc8, 'abs', 'MOVIEDEV', 0),
        (0xcd, 'abs', 'MCISEND', 0),
        (0x145, 'abs8', 'F_X', 0),
        (0x150, 'abs8', 'F_Y', 0),
        (0x167, 'abs8', 'F_Y', 0),
        (0x16a, 'abs8', 'F_X', 0),
        (0x16d, 'abs', 'MOVIEHWND', 0),
        (0x173, 'abs', 'MOVEWINDOW', 0),
        (0x1a8, 'abs', 'MOVIEDEV', 0),
        (0x1ad, 'abs', 'MCISEND', 0),
        (0x1da, 'abs', 'MOVIEDEV', 0),
        (0x1df, 'abs', 'MCISEND', 0),
    ), {
        'movie_place': 0x0,
        's_ddraw': 0x1ea,
        's_ddgpa': 0x1f4,
        's_user32': 0x205,
        's_getclient': 0x210,
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
        'ff742404e8fcffffff83c404833d00000000047570833d00000000207567803d'
        '0000000002755e803d00000000007455a1000000006bc00bb90c00000099f7f9'
        '03050000000089c2a100000000d1e80305000000008b0d00000000518b0d0000'
        '0000890d000000006a016800ff000052506886000000e8fcffffff83c4145989'
        '0d00000000c3484f4c4420544f20534b495000'
    ), (
        (0x5, 'rel', 'ORIG', -4),
        (0xe, 'abs', 'MODE', 0),
        (0x17, 'abs', 'SUBMODE', 0),
        (0x20, 'abs', 'PHASE', 0),
        (0x29, 'abs', 'HELD', 0),
        (0x31, 'abs', 'FBH', 0),
        (0x42, 'abs', 'FBY', 0),
        (0x49, 'abs', 'FBW', 0),
        (0x51, 'abs', 'FBX', 0),
        (0x57, 'abs', 'PRIMARY', 0),
        (0x5e, 'abs', 'BACK', 0),
        (0x64, 'abs', 'PRIMARY', 0),
        (0x72, 'abs', '.', 134),
        (0x77, 'rel', 'DRAW', -4),
        (0x81, 'abs', 'PRIMARY', 0),
    ), {
        'overlay': 0x0,
        'prompt': 0x86,
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
    if BOOTSTRAP and sym not in build.symbols:
        return 0                        # a symbol a stale blob still names
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


# Two blobs the checks read as bytes: the version stamp's tail, and the
# lever routine's length for --selfcheck. Everything else is linked for
# the build being patched, through the site table.
LEVERS_CODE = link('LEVERS', RETAIL)
TITLEVER_CODE = link('TITLEVER', RETAIL)

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
BANNER_SPILL = 24845            # a run of empty tiles further in
BANNER_SPARE = 116              # how many
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
    spill = len(BANNER_TILES) - BANNER_TILE_MAX
    if spill > BANNER_SPARE:
        raise AssertionError('banner needs %d tiles: %d of its own and %d '
                             'spare, but only %d spare are free'
                             % (len(BANNER_TILES), BANNER_TILE_MAX, spill,
                                BANNER_SPARE))
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
         (0x002bbb54, '000000000000000004000000000000000000000004000000000000000000000004000000000000000000000004000000000000000000000004000000ffffffff1800000003000000000000000000000004000000000000000000000004000000000000000000000002000000000000001700000003000000000000000000000003000000630000001d00000003000000000000000000000004000000000000000000000004000000000000000000000002000000000000001100000003000000000000000000000003000000630000001e00000003000000000000000000000001000000630000001500000003000000000000000000000001000000630000001c00000003000000000000000000000001000000630000002400000003000000000000000000000001000000630000001800000003000000000000000000000004000000000000000000000004000000000000000000000002000000000000001800000003000000000000000000000003000000630000002400000003000000000000000000000001000000630000002000000003000000000000000000000001000000630000001f00000003000000000000000000000001000000630000002000000003000000000000000000000004000000000000000000000004000000000000000000000002000000000000001a00000003000000000000000000000003000000630000001d00000003000000000000000000000004000000000000000000000004000000000000000000000002000000000000002700000003000000000000000000000003000000630000002100000003000000000000000000000004000000000000000000000004000000000000000000000002000000000000000c00000003000000000000000000000003000000630000001100000003000000000000000000000001000000630000001900000003000000000000000000000004000000000000000000000004000000000000000000000002000000000000000b00000003000000000000000000000003000000630000001b00000003000000000000000000000004000000000000000000000004000000000000000000000002000000000000000c00000003000000000000000000000003000000630000001600000003000000000000000000000001000000630000002200000003000000000000000000000001000000630000001700000003000000000000000000000004000000000000000000000004000000000000000000000004000000000000000000000002000000000000001600000003000000000000000000000003000000630000001800000003000000000000000000000004000000000000000000000004000000000000000000000002000000000000002200000003000000000000000000000003000000630000001f00000003000000000000000000000004000000000000000000000004000000000000000000000002000000000000002100000003000000000000000000000003000000630000001b00000003000000000000000000000001000000630000001800000003000000000000000000000001000000630000001f00000003000000000000000000000004000000000000000000000004000000000000000000000002000000000000001200000003000000000000000000000003000000630000001d00000003000000000000000000000004000000000000000000000004000000000000000000000002000000000000000e00000003000000000000000000000003000000630000001600000003000000000000000000000004000000000000000000000004000000000000000000000004000000000000000000000002000000000000000a00000003000000000000000000000003000000630000001c00000003000000000000000000000001000000630000002200000003000000000000000000000001000000630000002100000003000000000000000000000001000000630000001500000003000000000000000000000004000000000000000000000004000000000000000000000004000000000000000000000002000000000000001c00000003000000000000000000000003000000630000002700000003000000000000000000000001000000630000001900000003000000000000000000000001000000630000002f00000003000000000000000000000001000000630000003100000003000000000000000000000001000000630000002000000003000000000000000000000001000000630000003300000003000000000000000000000006000000630000001c00000003000000000000000000000001000000630000001800000003000000000000000000000004000000000000000000000004000000000000000000000004000000000000000000000002000000000000000000000004000000040000000500000003000000000000000000000004000000000000000000000004000000000000000000000002000000040000001900000003000000000000000000000001000000630000001500000003000000000000000000000002000000000000000000000002000000000000000000000002000000000000000000000002000000',
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
     'Intro movie\tFitted to the window. The film is letterboxed\n'
     '\tinside its frames; the black bars stay off screen and\n'
     '\tthe picture fills the height. The game sized it for\n'
     '\t640x480, so scaled up it sat small in a corner.\n'
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
     '\tthem under INSTALL; with none there, the game reads\n'
     '\tthe drive.', [
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
     'Switching away during the intro movie and back crashed the game: the\n'
     'screen it had handed to the movie was never rebuilt. It is rebuilt\n'
     'now, and the game waits until that has worked before carrying on.', [
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
         (0x001c5412, 'e893030000', call(0x001c5412, ('ACTIVATE', 'idle'))),
         # The rerelease alone reports a failed recreate with a message box
         # from inside it, three times over - the display mode, the primary,
         # the attached back buffer - which during the retry is a box per
         # attempt, and under Proton an invisible one. The three calls go;
         # the add esp after each takes the arguments they were pushed.
         (In(JAPAN_MD5, 0x001bf3de), 'e894440000', '9090909090'),
         (In(JAPAN_MD5, 0x001bf4ae), 'e8c4430000', '9090909090'),
         (In(JAPAN_MD5, 0x001bf4f9), 'e879430000', '9090909090')]),
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
     'F4\tHigh / low resolution (1080p / 720p with Widescreen)\n'
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
     'The players on a pad profile take the connected pads in order,\n'
     '1P first. A accepts, Select is the\n'
     'camera, Start pauses, and the D-pad works the menus. A or Select\n'
     'skips the win and lose screens between rounds; A skips the intro\n'
     'movie. On-screen prompts name the button rather than a key.\n'
     '\n'
     'Stick deadzone\tEach player has one, 40% to start, set in the F11\n'
     '\tExtras dialog.\n'
     'Soft reset\tLB + RB + LT + RT + Start held together returns to\n'
     '\tthe title screen, from either pad. Not during an\n'
     '\tinternet match.\n'
     '\n'
     'v_on.ini and the title artwork are moved aside and rewritten.\n'
     'Restore original puts both back.', [
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
         # The letter and digit sections belong to the keyboard profile;
         # the gamepad page lists only its pad inputs. Fill, store and
         # preselect skip them together: asm/pagesec.asm, asm/pagesel.asm.
         (0x0009703f, '837df81a0f8d27000000', call(0x0009703f, ('PAGESEC', 'fillsec'), 5)),
         (0x00097257, '837dec1a0f8d13000000', call(0x00097257, ('PAGESEC', 'storesec'), 5)),
         (0x00097428, '837df41a0f8d27000000', call(0x00097428, ('PAGESEL', 'selsec'), 5)),
         (0x000974cb, '8345f424e905000000', call(0x000974cb, ('PAGESEL', 'selidx'), 4)),
         # The bind page's seed of its block from the live table is only
         # right when the page's device is the committed one; see
         # asm/blockcur.asm.
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
    here. A site mapped to None is code the build does not have, and a
    site the build alone has (In) is included for it only."""
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
    table['hires'] = BY_KEY['hires']
    return table


# Found by signature rather than offset, so it cannot live in FEATURES.
# SetCooperativeLevel: DISCL_FOREGROUND -> DISCL_BACKGROUND.
DI_FIND = re.compile(
    rb'\x6a\x06[\s\S]{0,20}?\xff(?:[\x50-\x57]\x34|[\x90-\x97]\x34\x00\x00\x00)')

# key -> (label, description, sites), with sites None meaning DI_FIND.
BY_KEY = {key: (label, tip, sites)
          for key, label, tip, sites in features(RETAIL)}

# Sites computed at apply time from the chosen size, so it cannot live in
# FEATURES. Applied last, after nodisc, since it appends its own section.
BY_KEY['hires'] = (
    'Native widescreen',
    'The game renders at 1920x1080 itself, in place of 640x480, and the\n'
    'wider view shows more of the arena at the sides.\n'
    '\n'
    'Picture\tThe 3D view at the new size; menus, HUD and text\n'
    '\tredrawn to match, not stretched.\n'
    'F4\tSwitches to 1280x720 and back. Also Screen on F5;\n'
    '\tthe choice is saved with the settings.\n'
    'Split screen\tVer or Hor on F5. The machine select is drawn once,\n'
    '\tfull size.\n'
    'Ending credits\tThe black bands are gone; the roll uses the whole\n'
    '\tscreen.',
    None)

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
# Essential is shown without tick boxes and always applied. Unticking any
# of them produced a game broken in a way nobody chose: no start on a modern
# CPU, a crash on a lost round, a third of the frame rate, dead keys after
# ALT+TAB. Two of them are also what internet play needs.
EXTRA = ('hires', 'padxinput', 'nodisc', 'debugbox', 'defaults', 'sound',
         'movie')
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
    keys = [k for k in ESSENTIAL + EXTRA + ABOUT
            if k not in ('nodisc', 'hires')]
    return keys + ['nodisc', 'hires']


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
    if apply_order()[-2:] != ['nodisc', 'hires']:
        raise AssertionError('nodisc, then hires, must be applied last')

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
        # The name goes straight into a path under the install folder.
        if not name or name == '..' or any(c in name for c in '/\\\0'):
            raise DiscError('This image has a file name the patcher will '
                            'not write: %r.' % raw)
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
NETPLAY_SRC_SHA = '83e7656b2ee6acb417f6d6d872561a59e36bc127e2166ebe192aeae271b99eab'
# sha256 of the compiled DLL, so the patcher can tell its own build
# from an older one already installed.
NETPLAY_DLL_SHA = '697ba120c0d99abe877e758e639e48e9d87d2d30038cfa57e61c1bc1428270ad'
NETPLAY_DLL_Z = (
    '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'
    'U1zTXXNdV7iucq1y/dz1O9dB12cudZG2aGzRuKKLi35UVFK0oKgVuF1ddHPRlqIz3D90X+Qu'
    'dE92z3PXu/3uxe64+xr3Wvdt7kfcu93KomMf8H0JKG++rk3XpVuv+6nuId0m3dO6V3Vv6T7V'
    'naPX6e36cv1MfaM+qI/qr9J3sX75mf7n+k36p/Q9+j36vfrX9X/Qf6D3GqoNlxsWG8KGTsPV'
    'husNNxseMrxoeN3wjqHP8JHhiOG7Rsk4yWg1FhvrjM3oy3uMDxn/l3Gz8VnjHuNe4zvG94wf'
    'G48YVabzTR7TZaaQ6RrT9abbTQ+YHjE9aXoW/fgnU455jFln9pgrzD5zszlq7jB3oU/vN29E'
    'v24x95pfMn9g/tycYRltOcsy19Js6bRcDcreaHkSVH3QMsp6gbXIOs1aDXq+znq79X7rRuuj'
    '1setW6zPWZ+37rP+2fqx9Yj1DNtYm9O2xna/7Ze2F22v2t6w/d72pU1jH2M/xy7ZL7Jb7UV2'
    'r32W/XK73x6yt9uX29fbH7Q/ad9h32v/vf1PjCIucHQ4bnTc59joeBQUsdnxsuMtx1HHmU6b'
    'c65zifMK54+dDzh/7XzG+YrzPeenTq38Q9knB+Uu9Pwt8j3yY+j1V+S3wUnnu8a7HK5K1yzX'
    '5a5GV9x1petm132u51yvuQ64PkC/ZxedU7Si6Jaie8BDx4oE92h3vvtCd4l7qrvGPR89/lP3'
    'S+4/upUPS6Qkleuu032jWwu5sdagMa41aiAVlpgPmV+0TLNeYnvYttaucax13unc6HzUedR5'
    '3KmWL5QnylZ5jrxQvk3+hbxNflF+Q/6DfBB1E12nuc5wtbg2uvYUTQN9xdzr3XehxEfdW91f'
    'U5nVit3ID3QGnazz6Mp1Vbo1uj/qpoCWtul7QTX79TmGMYazDVcYlhquM9xkuNuwwfC44WnD'
    'HsNbhr8aRhu/ZzQaXcZy4yrj9cZHjU8bXzW+a/yz8RPjMePfjVkmg6kOtb/P9ITpGXD6q6bf'
    'mz41VZjvMz9q3mx+wfyeWQUqON9ihISbZqm1BCxLwe0bLNsteyz7LL+zfAr5Nsaab73YarS6'
    'rPOsPuti8P9y6y7rK9YMWwGsA1fabrA9ZtuMnr/QbrBPtlfbl9hX2G+w32N/3v6q/Xf2o/bj'
    'dpXD4LA5PI7rHbc5nnG84vjSoQLfn+e8xGlyFjnLnR3Om5x3O3c4dzl/5/zEecw5TtbLJfIs'
    'uV4OyDcBi5/J38iZrnNcP3Rd4tK73K4q1xzXetd21xeuM4vOLbqgaEKRr2hJ0TVFa9DHdxVt'
    'L9pX9IeiUe4L3D8Cvq9wX+t+2/0nN33Yout7MnSn6b6P/t0Dvj0To0UVuPVu/ZP6Hfrd+j79'
    '13qt4RLDZEOLocNwleFaw22G58CXY4Dji41mo9PYABzfZ3yYcePbxgPGQ8ZPjd8YTzeNMZWY'
    'akw3me4wbTF9Ajkqms80jzdPNVeb55gbzAHzneafmn9j/sT8hfkbc7blTMvZFr2lzFJlmWV5'
    'AOPJDstfLF9YvmudYDVYZesSawIYXmXtse62LrXdbLvT9nPbJtsWW4/tkC3TPtp+lv0Cu85e'
    'bC+3z7CvtN9ov83+gP1R+x7765Czn9v/bs92nOkodcxwzHY0OBY7VoLDbnM84Njk2ObY5fit'
    'I995oXOi0+Oscs5zNjpbnfc59zrPcCkbU2kL/pM6ZSMa+d+Wry4S1it7MLeZ/ZaJskWW5XK5'
    'UX5JnuJa6drpukrhnA3Kt5ka3U26MZB7hww3YfQcZ/Fa1rlfIRrfpPDVKP1EvUF/Gtqpt86w'
    'zrfWg5oi1iutK623WX9q3Wn9D+v7TLaMtU2w/cjmsnlsP7bdZ3vS9pbtLHu+fZzdaLfbnY4y'
    'R5XjCken42HH047nHG84dsoXgSrsrmngscdc21y9rs9dGUW0GY722n5g/pHtStsk+zpInvOd'
    'PudTzq3O3c5XnZ85M+RRch5GlotlAzhW5xLd2e4x7nMxFlzsNrgdGA2mumdBPizCeBB1Xwk6'
    'WuO+2X23+373r9yPube4d7j3uF8Dbb3n/qv7M7dwQMFBlk6nM8FsVtYV67zg5kpdta5Wp2yS'
    'pH1yRwzHDMcNglFtzDLmGHONecaxxnxI/HHGAuNEo85oMuZZxlryLZJlnKXAMtGis5gsNots'
    'KQY2yzEaV4NL51kWWuotjZYWS9ASscQtx22CXW0fCxxJ9qA9Yo/bt9l3Qc7utx+wvw9J+6H9'
    'sL3FGXRGnHHw2jLncoyx1zmvd6533gopdq9zg/NByLJHnJucTzg3Az/bnM+DF18CZbzu3I9R'
    '+IDzfWef80PnYecRcOdxpyCr5Sw5R84F/sbK+bIkj5MLIAF1skm2gUKKZS+opFKulmvleZCJ'
    '9aCYFkjuiByXO+Rl8nKM3tfJ18vr5VvlO+V75Q3yg/JG+RF5k/yEvFneit54Xt4FGtsrKx98'
    '6Rvc6+Zj5uPmU2rwv+rvPwHWVwZg'
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
# same two bytes from the running exe (fp_builds in net/dpctrl.c, one row
# per build, as virtual addresses); here the patcher reads them from disk
# so it can warn at install time rather than leaving it to a refused match.
# The frame divisor's immediate, six bytes into its site, and the first
# byte of a continuefix site - in every build, through its site map. Change
# both tables together.
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
            if section == 'ddraw':
                end = n                     # a section with no keys yet
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
# ride in their own appended section, which exists only once the patch is
# applied. f11pause.asm pushes MAGIC_TEMPLATE where the
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

        The span is max(vsize, rsize), not vsize: the frame rate patch grows
        the F5 dialog template into the raw padding past .rsrc's VirtualSize.
        Windows maps that padding, so the address is real, but anything that
        trusts VirtualSize cannot see it.
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
        if key == 'hires':
            # Computed sites, own section append; wanted['hires'] carries
            # (width, height) from the window, or True from a caller that
            # takes the default.
            size = wanted[key]
            w, hh = size if isinstance(size, tuple) else (1920, 1080)
            if not hires_supported(buf):
                skipped.append((key, 'no resolution table for this build'))
                continue
            try:
                hires_install(buf, w, hh)
            except Exception as exc:
                raise PatchFailed(key, exc) from exc
            applied.append(key)
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
    """The Build this .bak is an untouched original of, or None.

    Checking the backup rather than the exe is what makes "already patched"
    reliable: the patched file's own size and checksum depend on which boxes
    were ticked, but the backup is always the untouched original."""
    try:
        if os.path.getsize(path) not in [b.size for b in BUILDS.values()]:
            return None
        with open(path, 'rb') as fh:
            return BUILDS.get(hashlib.md5(fh.read()).hexdigest())
    except OSError:
        return None


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
        self.stamp = None

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
        pe = struct.unpack_from('<I', data, 0x3c)[0]
        self.stamp = struct.unpack_from('<I', data, pe + 8)[0]
        digest = hashlib.md5(data).hexdigest()
        if digest in BUILDS:
            self.build = BUILDS[digest]
            self.compare = None
            return READY_TAG, True

        build = backup_is_original(path + '.bak')
        if build:
            # The size cannot be part of this test: every patched file has
            # the annex appended, and up to two more sections.
            # The build comes from the backup, so Restore looks for this
            # build's artwork rather than retail's.
            self.build = build
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
DDRAW_LINK = ('Windowing and scaling', 'cnc-ddraw',
              'https://github.com/FunkyFr3sh/cnc-ddraw',
              'Windowed and borderless modes, and the game scaled to your '
              'monitor without stretching, whatever size it runs at. '
              'Install downloads it and puts it beside v_on.exe.')

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
            # Widgets whose text is written once and never touched again;
            # the ones left blank after a resize, see _nudge.
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

            # In the order the work is done: installing first, since with
            # only a disc image nothing else can happen until it is
            # unpacked. Two columns where there is room - getting the game
            # in place is one job, patching it another; on a narrow screen
            # _body gives back the same frame twice and it stacks instead.
            left, right, band, foot_left, foot_right = body
            self._section(left, '1  INSTALL', self._install_body)
            self._section(left, '2  GAME FILE', self._file_body)
            self._section(right, '3  ESSENTIAL PATCHES',
                          lambda p: self._patch_body(p, ESSENTIAL,
                                                     ESSENTIAL_HINT))
            self._section(right, '4  EXTRA PATCHES',
                          lambda p: self._patch_body(p, EXTRA, EXTRA_HINT))
            # Not patches: Apply never touches these and they write files
            # rather than bytes. Collapsed, so open they do not push Apply
            # below the fold; full width, because its rows are a title, a
            # paragraph and a button.
            self._section(band, '5  ADD-ONS', self._addons_body,
                          expanded=False)
            # Side by side at the foot, on the same split as the columns
            # above, so the two headings line up whatever is open.
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
                if key in ESSENTIAL:
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
                # ESSENTIAL keys have no widget to disable; _apply forces them.
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
            if ok and 'hires' in self.checks \
                    and (self.core.stamp is None
                         or not hires_supported_stamp(self.core.stamp)):
                self.vars['hires'].set(False)
                self.checks['hires'].state(['disabled'])
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
                       if key in ESSENTIAL or var.get())

        def _retally(self, *_args):
            """Keep the count honest as boxes are ticked."""
            if self.core.exe_path and not self.core.compare:
                self._set_status(READY % (self.core.build.name,
                                          self._selected()), True)

        def _apply(self):
            wanted = {k: v.get() for k, v in self.vars.items()}
            wanted.update({key: True for key in ESSENTIAL})
            if wanted.get('hires'):
                wanted['hires'] = (1920, 1080)
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
            # needs are in ESSENTIAL, so Apply cannot produce a file without
            # them. netplay_sync_ready still checks the file on disk when
            # the add-on is installed, which catches a copy patched by an
            # older release.
            self._set_status(DONE % sum(1 for v in wanted.values() if v), True)

        def _restore(self):
            # A selection is worth keeping across the reload only if there
            # was one to make - _chose says whether the boxes were ever
            # usable for this file. They are disabled after an apply as well
            # as after a refusal, so their own state cannot answer it.
            # Somebody who unticked two patches, applied, and restored
            # should get their two back rather than a fresh set of
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
    return None


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
