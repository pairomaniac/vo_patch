#!/usr/bin/env python3
"""Higher resolution for Virtual-On.

    python3 hires.py v_on.exe                    1280x960
    python3 hires.py v_on.exe --width 1600 --height 1200
    python3 hires.py v_on.exe --width 1920 --height 1080
    python3 hires.py v_on.exe --restore

Run vo_patch first and point this at the patched v_on.exe. Nothing here
overlaps vo_patch's sites, and a stock exe is accepted too. The exe is
patched in place; the original goes to v_on.exe.prehires and --restore
puts it back.

What it changes:

  -  Mode, window, the places that refuse any mode but 640x480 and
     320x240 (including a mode-availability check that hangs on a black
     screen if the display does not enumerate the new mode), viewport
     sizes and the projection scale.
  -  The perspective-subdivision thresholds, which are in pixels.
  -  The renderer's coverage mask: one bit per pixel, 80 bytes a row, 480
     rows, in .data with other globals right behind it. It moves to a new
     section sized for the new width and height, and the row stride
     changes everywhere the renderer has it.
  -  The 2D layer (HUD, fonts, backdrops, menus) is drawn at its designed
     size into an offscreen buffer and scaled onto its viewport each time
     the game calls it, so it keeps its layout and art (nearest, or
     bilinear with --hud-filter linear). In a wide mode
     backdrops cover the full width and the HUD stays 4:3, centred.
  -  HUD polygons (bars, frames, timer box, reticle, weapon strips,
     machine select, cursors) are projected at 640x480 and scaled at
     insert to the same 4:3 frame as the 2D layer, so both layers share
     one grid whatever the field of view. What counts as HUD is decided
     by where it is drawn from: the functions that own the HUD
     projection setups (UI_PASS_FUNCS) are wrapped, and everything
     submitted while one of them is running is HUD.
  -  The machine-select hangar draws a platform mech while it is within
     an angle window sized for 4:3; the window is widened to the view
     (--hangar). The renderer's polygon cap is raised (--polys).
  -  Split screen: side by side is two W/2 x H viewports, top/bottom two
     W x H/2 (instead of the game's staggered 320x240 boxes). Each gets
     a field of view between the 4:3 frame that fits inside it and the
     one that covers it (--split-fov, --split-fov-tb); the HUD always
     fits, drawn at its own scale.

The exe grows by one section: 6 KB of code and data plus a header; the
buffers are zero-filled by the loader.

Width must be a multiple of 32 and at most 2040 (the coverage-mask
stride is an 8-bit immediate in ten places). Nothing else is tied to a
particular size: scales, offsets and the split factors are computed
from the width and height. Tested at 1280x720, 1280x960 and 1920x1080,
1P and both split layouts. In a wide mode the 3D is Hor+ (same
vertical field of view, more at the sides); the sky dome was built for
4:3 and may not reach the edges.

Not touched: the "Screen=Normal" window size beyond scaling it, and a GDI
text wrap at 640 pixels.
"""

import argparse
import math
import os
import shutil
import struct
import sys

BASE_W, BASE_H = 640, 480
KEEP = '.prehires'

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
# split it is always 4:3. The post phase (HUD) is 4:3. Margins are
# blacked only when the last 3D flush drew nothing. The canvas has 480
# guard rows above and below, since the 2D code draws outside the
# viewport in split screen. Source: ui.asm; assembled with keystone,
# position independent apart from the four calls fixed up here.

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
            0x0042cda6: 0x0042cac8,
            0x00432fbe: 0x0043270e,
            0x00433141: 0x00432891,
            0x00460b70: 0x0045fbf0,
            0x00460cf3: 0x0045fd73,
            0x004800d0: 0x0047ec80,
            0x004804f0: 0x0047f0a0,
            0x004b6030: 0x004b43a0,
            0x004b981f: 0x004b7a61,
            0x004c468e: 0x004c24d6,
            0x004d0280: 0x004cde03,
            0x004d9c3d: 0x004d715b,
            0x0051444d: 0x0051097d,
            0x0051448e: 0x005109be,
            0x00514576: 0x00510aa6,
            0x00531f6a: 0x0052e2ad,
            0x005495b1: 0x00544c21,
            0x0055d221: 0x00558711,
            0x005670c0: 0x00562400,
            0x005674f0: 0x0047f0a0,
            0x0057f1b0: 0x0057a480,
            0x005829c3: 0x0057db65,
            0x0058881e: 0x0058390a,
            0x00588d85: 0x00583e71,
            0x005a1f3c: 0x0059ccd4,
            0x005a251b: 0x0059d2b3,
            0x005b5f2e: 0x005b0bb6,
            0x005c79aa: 0x005c2279,
            0x005c7dfe: 0x005c26cd,
            0x005c813a: 0x005c2a09,
            0x005c814c: 0x005c2a1b,
            0x005c8188: 0x005c2a57,
            0x005c819a: 0x005c2a69,
            0x005cc39d: 0x005c6c3d,
            0x005cc3de: 0x005c6c7e,
            0x005cc4c6: 0x005c6d66,
            0x00624728: 0x0061f4b0,
            0x0066c17c: 0x00668104,
            0x006bc1e4: 0x006b7f44,
            0x006bc1e8: 0x006b7f48,
            0x006bc948: 0x006b86a8,
            0x006bf598: 0x006bb2b0,
            0x006bf5ac: 0x006bb2c4,
            0x006bf5b8: 0x006bb2d0,
            0x006bf5bc: 0x006bb2d4,
            0x006c8b24: 0x006c48b4,
            0x006c8b28: 0x006c48b8,
            0x006c8ce8: 0x006c4a78,
            0x006d0dc4: 0x006ccb54,
            0x006db4c8: 0x006d7258,
            0x006db530: 0x006d72c0,
            0x006db534: 0x006d72c4,
            0x007001d0: 0x006fbf60,
            0x00708818: 0x007045a8,
            0x00708870: 0x00704600,
            0x00708874: 0x00704604,
            0x00725f50: 0x00721ce0,
            0x033cd5f4: 0x033c8280,
        },
        'off': {
            0x02c1a6: (0x02bec8, '558bec81eca8000000'),
            0x0323be: (0x031b0e, '558bec83ec04'),
            0x032541: (0x031c91, '558bec5356'),
            0x05ff70: (0x05eff0, '558bec83ec04'),
            0x0600f3: (0x05f173, '558bec5356'),
            0x07e504: (0x07d0b4, '8b0da8866b003bc80f84bd010000'),
            0x07f890: (0x07e440, 'e0326c00'),
            0x07f8e0: (0x07e490, 'e0326c00'),
            0x07fd71: (0x07e921, 'e0326c00'),
            0x07fe3c: (0x07e9ec, 'e0326c00'),
            0x07fea1: (0x07ea51, 'e0326c00'),
            0x0802fc: (0x07eebc, 'e0326c00'),
            0x0b5430: (0x0b37a0, '558bec83ec34'),
            0x0b8c1f: (0x0b6e61, '558bec83ec34'),
            0x0c3a8e: (0x0c18d6, '558bec81ec8c000000'),
            0x0cf680: (0x0cd203, '558bec535657'),
            0x0d903d: (0x0d655b, '558bec81eca8000000'),
            0x11384d: (0x10fd7d, '558bec535657'),
            0x11388e: (0x10fdbe, '558bec535657'),
            0x113976: (0x10fea6, '558bec535657'),
            0x13136a: (0x12d6ad, '558bec535657'),
            0x1489b1: (0x144021, '558bec83ec10'),
            0x15c621: (0x157b11, '558bec83ec10'),
            0x166888: (0x161bc8, 'e0326c00'),
            0x1668e0: (0x161c20, 'e0326c00'),
            0x166d79: (0x1620b9, 'e0326c00'),
            0x166e44: (0x162184, 'e0326c00'),
            0x166eb1: (0x1621f1, 'e0326c00'),
            0x167324: (0x162664, 'e0326c00'),
            0x17e5b0: (0x179880, '558bec83ec34'),
            0x181dc3: (0x17cf65, '558bec83ec34'),
            0x187c1e: (0x182d0a, '558bec83ec0c'),
            0x188185: (0x183271, '558bec83ec0c'),
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
            0x1c68e1: (0x1c11b0, '0f8505000000'),
            0x1c68fc: (0x1c11cb, 'e0010000'),
            0x1c6901: (0x1c11d0, '80020000'),
            0x1c6d3d: (0x1c160c, 'e0010000'),
            0x1c6d42: (0x1c1611, '80020000'),
            0x1c6daa: (0x1c1679, '6a1068f0000000'),
            0x1c753a: (0x1c1e09, 'e872c2ebff'),
            0x1c754c: (0x1c1e1b, 'e8e0f9f9ff'),
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
            0x1ce988: (0x1d40e8, 'a1784a6c004683c0505ba3784a6c00'),
            0x1ce9de: (0x1d413e, 'a1784a6c0083c05046a3784a6c00'),
            0x1cf028: (0x1d4788, 'a1784a6c004683c0505ba3784a6c00'),
            0x1cf07e: (0x1d47de, 'a1784a6c0083c05046a3784a6c00'),
            0x1cf6d8: (0x1d4e38, 'a1784a6c004683c0505ba3784a6c00'),
            0x1cf72e: (0x1d4e8e, 'a1784a6c0083c05046a3784a6c00'),
            0x1cfd88: (0x1d54e8, 'a1784a6c004683c0505ba3784a6c00'),
            0x1cfdde: (0x1d553e, 'a1784a6c0083c05046a3784a6c00'),
            0x1cfe32: (0x1d5592, '50000000'),
            0x1cfe90: (0x1d55f0, '50000000'),
            0x1d0eac: (0x1cb73c, 'c4090000'),
            0x1d0ed0: (0x1cb760, '4c986f00'),
            0x1d11d8: (0x1cba78, '83e10783'),
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
            0x1dc4f8: (0x1d6d88, '70da6c00'),
            0x1dc502: (0x1d6d92, '83c850'),
            0x1dc919: (0x1d71a9, '70da6c00'),
            0x1dc923: (0x1d71b3, '83c850'),
            0x1dcccb: (0x1d755b, 'a1784a6c000ffefe83c0505ea3784a6c00'),
            0x1dcd02: (0x1d7592, 'a1784a6c000ffefe83c0508b0dccb26b00a3784a6c00'),
            0x1dcff1: (0x1d7881, 'a1784a6c000ffefe83c0505ea3784a6c00'),
            0x1dd032: (0x1d78c2, 'a1784a6c000ffefe83c0508b0dccb26b00a3784a6c00'),
            0x1de938: (0x1d91c8, '8b349de01c7200'),
            0x1df6b0: (0x1d9f40, '8b349de01c7200'),
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
            0x0042cda6: 0x0042cd06,
            0x00432fbe: 0x00432f1e,
            0x00433141: 0x004330a1,
            0x00460b70: 0x00460ad0,
            0x00460cf3: 0x00460c53,
            0x004800d0: 0x0047ffe0,
            0x004804f0: 0x00480400,
            0x004b6030: 0x004b5ed0,
            0x004b981f: 0x004b96bf,
            0x004c468e: 0x004c452e,
            0x004d0280: 0x004d0120,
            0x004d9c3d: 0x004d9add,
            0x0051444d: 0x00513fbd,
            0x0051448e: 0x00513ffe,
            0x00514576: 0x005140e6,
            0x00531f6a: 0x00531ada,
            0x005495b1: 0x00549121,
            0x0055d221: 0x0055cd91,
            0x005670c0: 0x00566c30,
            0x005674f0: 0x00567060,
            0x0057f1b0: 0x0057ec80,
            0x005829c3: 0x00582493,
            0x0058881e: 0x005882ee,
            0x00588d85: 0x00588855,
            0x005a1f3c: 0x005a1a0c,
            0x005a251b: 0x005a1feb,
            0x005b5f2e: 0x005b59fe,
            0x005c79aa: 0x005c74c3,
            0x005c7dfe: 0x005c7917,
            0x005c813a: 0x005c7c53,
            0x005c814c: 0x005c7c65,
            0x005c8188: 0x005c7ca1,
            0x005c819a: 0x005c7cb3,
            0x005cc39d: 0x005cbedd,
            0x005cc3de: 0x005cbf1e,
            0x005cc4c6: 0x005cc006,
            0x00624728: 0x00624718,
            0x0066c17c: 0x0066c174,
            0x006bc1e4: 0x006bc17c,
            0x006bc1e8: 0x006bc180,
            0x006bc948: 0x006bc8e0,
            0x006bf598: 0x006bf530,
            0x006bf5ac: 0x006bf544,
            0x006bf5b8: 0x006bf550,
            0x006bf5bc: 0x006bf554,
            0x006c8b24: 0x006c8aec,
            0x006c8b28: 0x006c8af0,
            0x006c8ce8: 0x006c8ca8,
            0x006d0dc4: 0x006d0d84,
            0x006db4c8: 0x006db488,
            0x006db530: 0x006db4f0,
            0x006db534: 0x006db4f4,
            0x007001d0: 0x00700190,
            0x00708818: 0x007087d8,
            0x00708870: 0x00708830,
            0x00708874: 0x00708834,
            0x00725f50: 0x00725f10,
            0x033cd5f4: 0x033cd584,
        },
        'off': {
            0x02c1a6: (0x02c106, '558bec81eca8000000'),
            0x0323be: (0x03231e, '558bec83ec04'),
            0x032541: (0x0324a1, '558bec5356'),
            0x05ff70: (0x05fed0, '558bec83ec04'),
            0x0600f3: (0x060053, '558bec5356'),
            0x07e504: (0x07e404, '8b0de0c86b003bc80f84bd010000'),
            0x07f890: (0x07f7a0, '60756c00'),
            0x07f8e0: (0x07f7f0, '60756c00'),
            0x07fd71: (0x07fc81, '60756c00'),
            0x07fe3c: (0x07fd4c, '60756c00'),
            0x07fea1: (0x07fdb1, '60756c00'),
            0x0802fc: (0x08020c, '60756c00'),
            0x0b5430: (0x0b52d0, '558bec83ec34'),
            0x0b8c1f: (0x0b8abf, '558bec83ec34'),
            0x0c3a8e: (0x0c392e, '558bec81ec8c000000'),
            0x0cf680: (0x0cf520, '558bec83ec08'),
            0x0d903d: (0x0d8edd, '558bec81eca8000000'),
            0x11384d: (0x1133bd, '558bec535657'),
            0x11388e: (0x1133fe, '558bec535657'),
            0x113976: (0x1134e6, '558bec535657'),
            0x13136a: (0x130eda, '558bec83ec08'),
            0x1489b1: (0x148521, '558bec83ec10'),
            0x15c621: (0x15c191, '558bec83ec10'),
            0x166888: (0x1663f8, '60756c00'),
            0x1668e0: (0x166450, '60756c00'),
            0x166d79: (0x1668e9, '60756c00'),
            0x166e44: (0x1669b4, '60756c00'),
            0x166eb1: (0x166a21, '60756c00'),
            0x167324: (0x166e94, '60756c00'),
            0x17e5b0: (0x17e080, '558bec83ec34'),
            0x181dc3: (0x181893, '558bec83ec34'),
            0x187c1e: (0x1876ee, '558bec83ec0c'),
            0x188185: (0x187c55, '558bec83ec0c'),
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
            0x1c68e1: (0x1c63fa, '0f8505000000'),
            0x1c68fc: (0x1c6415, 'e0010000'),
            0x1c6901: (0x1c641a, '80020000'),
            0x1c6d3d: (0x1c6856, 'e0010000'),
            0x1c6d42: (0x1c685b, '80020000'),
            0x1c6daa: (0x1c68c3, '6a1068f0000000'),
            0x1c753a: (0x1c7053, 'e88883ebff'),
            0x1c754c: (0x1c7065, 'e8c6eff9ff'),
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
            0x1dc4f8: (0x1dc038, 'a01c6d00'),
            0x1dc502: (0x1dc042, '83c850'),
            0x1dc919: (0x1dc459, 'a01c6d00'),
            0x1dc923: (0x1dc463, '83c850'),
            0x1dcccb: (0x1dc80b, 'a1a88c6c000ffefe83c0505ea3a88c6c00'),
            0x1dcd02: (0x1dc842, 'a1a88c6c000ffefe83c0508b0d4cf56b00a3a88c6c00'),
            0x1dcff1: (0x1dcb31, 'a1a88c6c000ffefe83c0505ea3a88c6c00'),
            0x1dd032: (0x1dcb72, 'a1a88c6c000ffefe83c0508b0d4cf56b00a3a88c6c00'),
            0x1de938: (0x1de478, '8b349d105f7200'),
            0x1df6b0: (0x1df1f0, '8b349d105f7200'),
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

UI_CODE = bytes.fromhex(
    'b80000adde53e8000000005b81eb0b0000008b442408ff35e8c16b00ff35e4c16b00ff74'
    '2410ff7424106a02e84811000083c4148b4424088983e8180000c783ec18000000000000'
    'd905e4c16b00d84c2408d80de8c16b00d99bdc180000d905e4c16b00d80de8c16b00d99b'
    'e0180000eb69b80100adde53e8000000005b81eb7d0000008b442408ff35e8c16b00ff35'
    'e4c16b00ff742410ff7424106a02e8d610000083c4148b4424088983e8180000c783ec18'
    '000001000000d905e4c16b00d84c2408d80de8c16b00d99bdc180000a1e4c16b008983e0'
    '180000d905e4c16b00d84c2408d99be41800008b83dc180000a3c8b46d008b83e0180000'
    'a3d0b46d008b83e4180000a3d4b46d0083bb08190000007438a1e8c16b0083bbec180000'
    '007405b80000803fa3d0b46d008b83c8190000a330b56d008b83cc190000a334b56d00c7'
    '83c0180000010000005bc3b80200adde53e8000000005b81eb5a0100008b442408ff3528'
    '8b6c00ff35248b6c00ff742410ff7424106a06e8f90f000083c4148b4424088983fc1800'
    '00c7830019000000000000d905288b6c00d84c2408d80d248b6c00d99bf0180000d90528'
    '8b6c00d80d248b6c00d99bf4180000eb69b80300adde53e8000000005b81ebcc0100008b'
    '442408ff35288b6c00ff35248b6c00ff742410ff7424106a06e8870f000083c4148b4424'
    '088983fc180000c7830019000001000000d9442408d80d288b6c00d80d248b6c00d99bf0'
    '180000a1248b6c008983f4180000d9442408d80d248b6c00d99bf81800008b83f0180000'
    'a3188870008b83f4180000a31c8870008b83f8180000a32088700083bb08190000007438'
    'a1288b6c0083bb00190000007405b80000803fa31c8870008b83c8190000a3708870008b'
    '83cc190000a374887000c783c4180000010000005bc3b80400adde5350e8000000005b81'
    'ebaa020000ff742414ff742414ff742414ff7424148b8308190000c1e00883c80150e8a6'
    '0e000083c41483bb08190000007457d983e8180000d80de8c16b00d91dc8b46d00a1e8c1'
    '6b0083bbec180000007405b80000803fa3d0b46d008b83e8180000a3d4b46d008b83c819'
    '0000a330b56d008b83cc190000a334b56d00c783c018000001000000eb418b83dc180000'
    'a3c8b46d008b83e0180000a3d0b46d008b83e4180000a3d4b46d008b83b8180000a330b5'
    '6d008b839c180000a334b56d00c783c018000000000000585b5589e5535657687c455100'
    'c3b80500adde5350e8000000005b81eb91030000ff742414ff742414ff742414ff742414'
    '8b8308190000c1e00883c80550e8bf0d000083c41483bb08190000007457d983fc180000'
    'd80d288b6c00d91d18887000a1288b6c0083bb00190000007405b80000803fa31c887000'
    '8b83fc180000a3208870008b83c8190000a3708870008b83cc190000a374887000c783c4'
    '18000001000000eb418b83f0180000a3188870008b83f4180000a31c8870008b83f81800'
    '00a3208870008b83bc180000a3708870008b83a0180000a374887000c783c41800000000'
    '0000585b5589e553565768ccc45c00c3b80c00adde535051e8000000005b81eb79040000'
    '8b83d819000083f820733f8b8b08190000898c83dc19000041898b08190000ff83d81900'
    '008b4c2410898c830c1900006a006a0050516a03e8bc0c000083c4148d8bce040000894c'
    '241059585bc35350e8000000005b81ebd5040000ff8bd81900008b83d8190000508b8483'
    'dc19000089830819000085c07505e812000000588b84830c1900008704248b5c2404c204'
    '0050c783c018000000000000c783c4180000000000008b8308180000a3b8f56b008b830c'
    '180000a3bcf56b008b83dc180000a3c8b46d008b83e0180000a3d0b46d008b83e4180000'
    'a3d4b46d008b83f0180000a3188870008b83f4180000a31c8870008b83f8180000a32088'
    '70008b83b8180000a330b56d008b839c180000a334b56d008b83bc180000a3708870008b'
    '83a0180000a37488700058c3b80600addee85e000000e81e020000e810fb4700e8040500'
    '00c3b80700addee89a000000e804020000e816ff4700e8ea040000c3b80800addee8b000'
    '0000e8ea010000e8cc6a5600e8d0040000c3b80900addee8b9000000e8d0010000e8e26e'
    '5600e8b6040000c35350e8000000005b81eb1b060000c7831c180000a8f56b00c7833818'
    '000001000000e87c0b0000a134b56d0089839c180000a1748870008983a0180000a130b5'
    '6d008983b8180000a1708870008983bc180000585bc35350e8000000005b81eb71060000'
    'c7831c180000a8f56b00c7833818000000000000a1c40d6d00898350180000585bc353e8'
    '000000005b81eba0060000c7831c180000b0f56b00c78338180000010000005bc353e800'
    '0000005b81ebc3060000c7831c180000b0f56b00c78338180000000000005bc38b836818'
    '000083bb541800000074158b836c180000f60598f56b000374068b837018000089835c18'
    '0000b800000100c1e00831d2f7b35c180000c1e00885d274014089831018000089831418'
    '00008b8b281800000faf8b5c180000c1e9108b830818000029c8d1f8c783601800000000'
    '000085c0791301c1f7d80faf831018000089836018000031c08983341800008b93081800'
    '0029c239d17e0289d1898b301800008b8b2c1800000faf8b5c180000c1e9108b830c1800'
    '0029c8d1f8c783641800000000000085c0791301c1f7d80faf8314180000898364180000'
    '31c08983441800008b930c18000029c239d17e0289d1898b40180000c360e8000000005b'
    '81ebdf0700008bb31c1800008b06898300180000a1acf56b00898304180000a1b8f56b00'
    '898308180000a1bcf56b0089830c180000a148c96b00898354180000c70548c96b000000'
    '00008b8b7418000085c074158b8b78180000f60598f56b000374068b8b7c180000898b80'
    '180000d98380180000d88ba8180000db9bc8180000db8308180000d98380180000d88bac'
    '180000dee9d88bb4180000db9bcc1800008b83b819000083bb541800000074158b83bc19'
    '0000f60598f56b000374068b83c01900000183cc180000db830c180000d98380180000d8'
    '8bb0180000dee9d88bb4180000db9bd01800008b83c41900000183d0180000db83081800'
    '00d88bb4180000d8b380180000db9bc8190000db830c180000d88bb4180000d8b3801800'
    '00db9bcc1900008b83c81900002d400100000faf83c81800008b8bcc180000c1e11029c1'
    '898bd01900008b83cc1900002df00000000faf83c81800008b8bd0180000c1e11029c189'
    '8bd4190000c7832c180000e0010000b8e001000083bb3818000000742683bb5418000000'
    '751d0faf830818000031d2f7b30c1800003d000400007e13b800040000eb0cc1e00231d2'
    'b903000000f7f1898328180000e83efdffffc78318180000000000008dbb001b0f008bab'
    '44180000c1e5108b8364180000c1e8100faf835c18000029c589e8c1f81078683b830c18'
    '00007d600faf830418000003830018000089c68b9334180000c1e2108b8360180000c1e8'
    '100faf835c18000029c231c989d0c1f810780e3b83081800007d06668b0446eb0231c066'
    '89044f6689844f00001e0003935c180000413b8b281800007cceeb1e5731c08b8b281800'
    '00f366ab5f5781c700001e008b8b28180000f366ab5f81c70008000003ab5c180000ff83'
    '181800008b83181800003b832c1800000f8c4fffffff8d83001b0f008bb31c1800008906'
    'c705acf56b00000800008b8328180000a3b8f56b008b832c180000a3bcf56b008bbb3c18'
    '000031c031c989048f05000800004181f9e00100007cef61c360e8000000005b81ebcf0a'
    '00008b8354180000a348c96b008bb31c1800008b830018000089068b8304180000a3acf5'
    '6b008b8308180000a3b8f56b008b830c180000a3bcf56b008bbb3c18000031c031c98904'
    '8f038304180000413b8b0c1800007cee83bb381800000074678b832c180000c1e00231d2'
    'b903000000f7f13b83281800007d4d89c18db3001b0f0031d251668b044e663b844e0000'
    '1e007533413b8b281800007ce95981c600080000423b932c1800007cd88b832c180000c1'
    'e00231d2b903000000f7f1898328180000eb0159e83ffbffff8b83441800000faf830418'
    '00000383001800008bbb341800008d3c788b8364180000898358180000c7831818000000'
    '0000008bb35818000089f025ffff0000c1e80889838c180000c1ee1089f0403b832c1800'
    '007c014869c0000800008d8403001b0f0089838818000069f6000800008db433001b0f00'
    '8b936018000031c989d5c1ed1083bb8418000000751b668b046e663b846e00001e000f84'
    '610100006689044fe958010000668b046e663b846e00001e00753a668b446e02663b846e'
    '02001e00752b568bb388180000668b046e663b846e00001e007515668b446e02663b846e'
    '02001e0075065ee9110100005e515289e8403b83281800007c01485089d181e1ffff0000'
    'c1e9080fb7046ee89c010000ba0001000029cae8fb010000ffb390180000ffb394180000'
    'ffb3981800008b44240c0fb70446e87101000089cae8d501000058018398180000580183'
    '9418000058018390180000ba000100002b938c180000e8b0010000ffb390180000ffb394'
    '180000ffb398180000568bb3881800000fb7046ee823010000ba0001000029cae8820100'
    '00ffb390180000ffb394180000ffb3981800008b44241c0fb70446e8f800000089cae85c'
    '0100005801839818000058018394180000580183901800008b938c180000e83c0100005e'
    '580183981800005801839418000058018390180000585a59e84e0100006689044f039310'
    '180000413b8b301800000f8c6cfeffff03bb041800008b8314180000018358180000ff83'
    '181800008b83181800003b83401800000f8cf5fdffff83bb3818000000746783bb501800'
    '0000755e83bb341800000074558bbb00180000c783181800000000000031c0578b8b3418'
    '0000f366ab8b8b301800008d3c4f8b8b081800002b8b301800002b8b34180000f366ab5f'
    '03bb04180000ff83181800008b8b181800003b8b0c1800007cbd61c3515289c2813df4d5'
    '3c032b020000742cc1ea0bc1e20389939018000089c2c1ea0583e23fc1e2028993941800'
    '0083e01fc1e0038983981800005a59c3c1ea0a83e21fc1e20389939018000089c2c1ea05'
    '83e21fc1e20389939418000083e01fc1e0038983981800005a59c3508b83901800000faf'
    'c28983901800008b83941800000fafc28983941800008b83981800000fafc28983981800'
    '0058c3528b8390180000c1e8108b9394180000c1ea10813df4d53c032b020000741ec1e8'
    '03c1e00bc1ea02c1e20509d08b9398180000c1ea10c1ea0309d05ac3c1e803c1e00ac1ea'
    '03c1e20509d08b9398180000c1ea10c1ea0309d05ac3b80a00adde60e8000000005b81eb'
    '510f000083bb601a0000007422ff7218ff7214ff7210ff74241c8b83c0180000c1e00883'
    'c80a50e8f901000083c4148b83641a000085c0740a0faf420cc1e81089420c83bbc01800'
    '00007424e89b00000083bb601a0000007416ff7218ff7214ff7210ff721c6a0be8b80100'
    '0083c414618b349dd0017000c3b80b00adde60e8000000005b81ebd80f000083bb601a00'
    '00007422ff7218ff7214ff7210ff74241c8b83c4180000c1e00883c80a50e87201000083'
    'c41483bbc4180000007424e82800000083bb601a0000007416ff7218ff7214ff7210ff72'
    '1c6a0be84501000083c414618b349d505f7200c3c783d4180000ffffff7fc783d8180000'
    'ffffff7fc783b019000001000080c783b4190000010000808d7210b9040000000fbf063b'
    '83d41800007d068983d41800003b83b01900007e068983b01900000fbf46023b83d81800'
    '007d068983d81800003b83b41900007e068983b419000083c6044975bb8d721031c90fbf'
    '063b83d418000075168bbbb01900003bbbd418000075218d79ff83ff027319400faf83c8'
    '1800000383d01900000500800000c1f81048eb150faf83c81800000383d0190000050080'
    '0000c1f8100fbf7e023bbbd8180000751a508b83b41900003b83d8180000587524508d41'
    'ff83f80258731a470fafbbc818000003bbd419000081c700800000c1ff104feb160fafbb'
    'c818000003bbd419000081c700800000c1ff1025ffff0000c1e71009f8890683c6044183'
    'f9040f8c42ffffffc360e8000000005b81eb7f11000083bb981900000074268bbb941900'
    '0085ff741c8d47143b839019000077118983941900008d742424b905000000f3a561c360'
    'e8000000005b81ebbd11000083bb8c190000000f84c40000008b839c1900004089839c19'
    '000031d2b978000000f7f183bb601a000000740783fa037278eb0d85d2757283bb981900'
    '0000746983bb98190000000f8484000000c78398190000000000006a0068800000006a02'
    '6a006a0068000000408d83a419000050ff15ccd4650383f8ff745689c66a008d83a01900'
    '00508b83941900002b838c19000050ffb38c19000056ff1590d4650356ff15b8d46503eb'
    '28c7839819000001000000ff3598f56b00ff3548c96b00ffb39c1900006a006a04e8e7fe'
    'ffff83c41461c3b80d00adde53e8000000005b81eba212000050528b45088d04808b5508'
    '8d0482d9048558bd45038b450c8d04808b550c8d0482d82485c8b24503d8835e130000d8'
    '8b62130000d89366130000dfe0f6c4017408ddd8d98366130000d9e8d8d9dfe0f6c40175'
    '0ed88ba8180000db9b641a0000eb0cddd8c783641a0000000000005a58508b4424088983'
    '681a00008d833613000089442408585b6893cb5900c353e8000000005b81eb3c130000c7'
    '83641a000000000000ffb3681a00008b5c240483c408ff6424f8a470e341abaaaa3d0000'
    '8037')
UI_CALLS = [(0x5bb, 0x4800d0), (0x5d5, 0x4804f0),   # rel32 at offset+1
            (0x5ef, 0x5670c0), (0x609, 0x5674f0)]
UI_STUBS = [(0x1c753a, 0x5c813a, 0x5b1),           # file offset, VA, stub
            (0x1c7588, 0x5c8188, 0x5cb),
            (0x1c754c, 0x5c814c, 0x5e5),
            (0x1c759a, 0x5c819a, 0x5ff)]
# Projection setups: the originals' entries jump to these, and the HUD
# passes' setup calls go there too (the HUD/world decision is made per
# submission, by the pass depth).
UI_WORLD = ((0x51444d, 0x11384d, 0x5), (0x51448e, 0x11388e, 0x77),
            (0x5cc39d, 0x1cb79d, 0x154), (0x5cc3de, 0x1cb7de, 0x1c6))
UI_SUBMIT = ((0x514576, 0x113976, 0x2a3), (0x5cc4c6, 0x1cb8c6, 0x38a))
UI_INSERT_A, UI_INSERT_B = 0xf4b, 0xfd2              # render-list insert hooks
UI_HUD_ENTER = 0x471                              # called by the pass stubs
UI_HANGAR_DRAW = 0x129c                            # hangar platform mech draw
UI_PASS_STUBS = 0x1600                      # 20 bytes per wrapped function
UI_MODEW = 0x1820                           # mode size, written by the patcher
UI_ROWTAB = 0x183c                          # row table address, likewise
UI_KSBS = 0x1848                            # split FOV factors, likewise
UI_SCALE = 0x1868                           # 2D scale per layout, likewise
UI_HUD = 0x1874                             # same as floats
UI_HUDF = 0x1880                            # the one in use, read by the game
UI_FILTER = 0x1884                          # 1: bilinear composite
UI_CONST = 0x18a8                           # 65536, 640, 480, 0.5
UI_SHIFT = 0x19b8                           # HUD polygon x shift per layout, y
UI_LOGQUADS = 0x1a60                        # log polygons at insert too
UI_LOG = 0x198c                             # log base, end, ptr; name at +0x18
LOG_SIZE = 48 << 20                          # about 90 s of every frame
assert len(UI_CODE) <= UI_PASS_STUBS        # data block starts at 0x1800
UI_OFF = 0x1b00                             # offscreen: guard, canvas, guard
# Functions that draw HUD elements: everything they submit, directly or
# through callees, is HUD (see hud_enter in ui.asm). They are the
# functions that call the projection setup with the HUD focal lengths
# (600 in game, 128 in the machine select). The first two rows are the
# ones seen running in the --log traces (1P, machine select, split in
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


def build_sites(w, h, sec_va, span_va, rowtab_va, hangar_all=True, A=None,
                pool=None):
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
    # the new section, H entries each.
    sites += imm_sites([0x07f890, 0x07f8e0, 0x07fd71, 0x07fe3c, 0x07fea1,
                        0x0802fc, 0x166888, 0x1668e0, 0x166d79, 0x166e44,
                        0x166eb1, 0x167324, 0x1c4f73, 0x1c90b8, 0x1c90e9],
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
    # The 320x240 mode has no place at this size: F4's toggle at 0x5c74da
    # falls through to its exit, and the menu command that picked 320x240
    # outright (0x5c79aa) jumps there too. Both would set a mode this
    # patch's scales do not cover.
    sites += [(0x1c68e1, bytes.fromhex('0f8505000000'), b'\x90' * 6),
              (0x1c6daa, bytes.fromhex('6a1068f0000000'),
               b'\xe9' + u32(A('F4EXIT') - (A('F4CASE') + 5)) + b'\x90\x90')]
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
    if hangar_all:
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
    # Renderer A's polygon record pool: 2500 records of 0x30 bytes at
    # 0x6db7e0, a side array of 8 per record at 0x6f8ca0 and the flush
    # list at 0x6fdabc, with the cap in the two insert paths and the
    # flush. Moved to the section and enlarged (--polys); the stock
    # cap drops the last polygons of the machine-select hangar once all
    # three mechs are drawn.
    if pool:
        pool_va, n = pool
        sites += imm_sites([0x1d3967, 0x1d46ba], 0x6db7e0, pool_va)
        sites += imm_sites([0x1d3970, 0x1d46c3], 0x6f8ca0, pool_va + n * 0x30)
        sites += imm_sites([0x1d0ed0], 0x6fdabc, pool_va + n * 0x38)
        sites += imm_sites([0x1d11d8], 0x6fdac0, pool_va + n * 0x38 + 4)
        sites += imm_sites([0x1d395b, 0x1d46ae, 0x1d0eac], 2500, n)
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
    it is hidden. Located by string: the framerate patch grows this
    template, so fixed offsets would miss."""
    lo, hi = 0x602c00, min(len(buf), 0x60e000)
    for old, new, hide in (('Type1', 'Ver  ', False),
                           ('Type2', None, True),
                           ('Type3', 'Hor  ', False)):
        pat = old.encode('utf-16-le')
        i = buf.find(pat, lo, hi)
        if i < 0 or buf.find(pat, i + 1, hi) >= 0:
            raise ValueError('Screen Split radio %s not found once' % old)
        if new:
            buf[i:i + 10] = new.encode('utf-16-le')
        if hide:
            so = i - 22                     # the item's style dword
            style = struct.unpack_from('<I', buf, so)[0]
            if style != 0x50010009:         # visible radio, as shipped
                raise ValueError('Type2 style is not where expected')
            struct.pack_into('<I', buf, so, style & ~0x10000000)


def install(buf, width, height, split_fov='mean', split_fov_tb=None,
            hud_shift=None, hangar='wide', polys=8000, hud_filter='nearest',
            log=False, log_quads=False):
    """Patch buf (a bytearray of v_on.exe, stock or vo_patch'd) in place
    for width x height. Raises ValueError on a size or byte mismatch.
    Returns the number of sites written."""
    if width % 32 or height % 8 or width > 2040:
        raise ValueError('width must be a multiple of 32 and at most 2040, '
                         'height a multiple of 8')
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
    mask_off = UI_OFF + UI_OFF_SIZE
    rowtab_off = mask_off + hh * (w // 8 + 4)
    size = rowtab_off + 2 * hh * 4
    log_off = size
    if log:
        size += LOG_SIZE
    pool_off = size
    if polys:
        if polys < 2500:
            raise ValueError('polys must be at least 2500')
        size += polys * 0x3c + 8       # records, side array, list
    code = UI_CODE
    if port is not None:
        code = bytearray(code)
        for name, va in ADDR.items():
            p, q = struct.pack('<I', va), struct.pack('<I', port['va'][va])
            j = 0
            while True:
                j = code.find(p, j)
                if j < 0:
                    break
                code[j:j + 4] = q
                j += 4
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
    struct.pack_into('<II', buf, rawptr + UI_MODEW, w, hh)
    struct.pack_into('<I', buf, rawptr + UI_ROWTAB, sec_va + rowtab_off)
    if log:
        struct.pack_into('<III', buf, rawptr + UI_LOG, sec_va + log_off,
                         sec_va + log_off + LOG_SIZE, sec_va + log_off)
        buf[rawptr + UI_LOG + 0x18:rawptr + UI_LOG + 0x22] = b'hires.log\0'
        struct.pack_into('<I', buf, rawptr + UI_LOGQUADS, int(log_quads))
    # 3D scale: the height (vertical FOV kept, Hor+). Split viewports
    # take a scale between the 4:3 that fits inside them and the one
    # that fills them; the game's split block multiplies the 1P scale
    # by K, and the compositor uses the same scale for the 2D layer.
    full = hh / 480
    def split_scale(vw, vh, mode):
        fit, fill = min(vw / 640, vh / 480), max(vw / 640, vh / 480)
        return {'fit': fit, 'fill': fill,
                'mean': math.sqrt(fit * fill)}[mode]
    s_sbs = split_scale(w / 2, hh, split_fov)
    s_tb = split_scale(w, hh / 2, split_fov_tb or split_fov)
    struct.pack_into('<ff', buf, rawptr + UI_KSBS,
                     s_sbs / full, s_tb / full)
    # HUD scale: the 4:3 frame that fits inside the viewport, for the
    # HUD passes (through the compositor's float) and the 2D layer.
    h_1p = min(w / 640, hh / 480)
    h_sbs = min(w / 2 / 640, hh / 480)
    h_tb = min(w / 640, hh / 2 / 480)
    struct.pack_into('<III', buf, rawptr + UI_SCALE,
                     int(h_1p * 65536), int(h_sbs * 65536),
                     int(h_tb * 65536))
    struct.pack_into('<ffff', buf, rawptr + UI_HUD,
                     h_1p, h_sbs, h_tb, h_1p)
    linear = hud_filter == 'linear'
    struct.pack_into('<I', buf, rawptr + UI_FILTER, int(linear))
    if hud_shift is None:
        sx = [math.ceil(v) for v in (h_1p, h_sbs, h_tb)]
        sy = 0
    else:
        sx = [int(hud_shift.split(',')[0])] * 3
        sy = int((hud_shift.split(',') + ['0'])[1])
    struct.pack_into('<iiii', buf, rawptr + UI_SHIFT, sx[0], sx[1], sx[2], sy)
    struct.pack_into('<ffff', buf, rawptr + UI_CONST, 65536.0, 640.0,
                     480.0, 0.5)
    sites = build_sites(w, hh, sec_va, sec_va + mask_off,
                        sec_va + rowtab_off, hangar_all=hangar == 'wide',
                        pool=(sec_va + pool_off, polys) if polys
                        else None, A=A)
    if port is not None:
        moved = []
        lens = port.get('passlen', {})
        pass_offs = {s - 0x400c00: n for n, (s, _l)
                     in enumerate(UI_PASS_FUNCS)}
        for off, old_, new_ in sites:
            if off in port.get('absent', ()):
                continue
            boff, bold = port['off'][off]
            if off in pass_offs and 0x400c00 + off in lens:
                # the build's shorter prologue: fewer displaced bytes
                new_ = new_[:5] + b'\x90' * (lens[0x400c00 + off] - 5)
            bold = None if old_ is None else bytes.fromhex(bold)
            if new_ and new_[0] in (0xe8, 0xe9):
                # a jump into the section: same target, moved site
                tgt = (0x400c00 + off + 5
                       + struct.unpack_from('<i', new_, 1)[0])
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
        sites = moved
        masks = tuple(struct.pack('<I', A('MASKPTR')).join(
            m.split(struct.pack('<I', ADDR['MASKPTR'])))
            for m in (MASK_LOAD, MASK_STORE))
        apply(buf, sites, mask_load=masks[0], mask_store=masks[1])
    else:
        apply(buf, sites)
    _split_dialog(buf)
    return len(sites)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('exe')
    ap.add_argument('--width', type=int, default=1280)
    ap.add_argument('--height', type=int, default=960)
    ap.add_argument('--restore', action='store_true')
    ap.add_argument('--split-fov', choices=('fit', 'mean', 'fill'),
                    default='mean',
                    help='side-by-side split field of view: fit is the 4:3 '
                         'frame that fits inside the viewport (widest), '
                         'fill the one that covers it (narrowest), mean '
                         '(default) between; the HUD always fits')
    ap.add_argument('--split-fov-tb', choices=('fit', 'mean', 'fill'),
                    default=None,
                    help='the same for the top/bottom layout (default: as '
                         '--split-fov)')
    ap.add_argument('--hud-shift', default=None,
                    help='shift the HUD polygons (bars, frames, reticle) '
                         'against the 2D layer by X,Y viewport pixels; the '
                         'default is the HUD scale rounded up, 0 (stock '
                         'has the text shadow, the frame, then the fill)')
    ap.add_argument('--hangar', choices=('wide', 'stock'), default='wide',
                    help='machine-select hangar: widen the angle window '
                         'in which a platform mech is drawn to the wider '
                         'view on the left (default; the right neighbour '
                         'enters during the switch), or keep the 4:3 '
                         'window, where mechs pop in at both edges')
    ap.add_argument('--polys', type=int, default=8000,
                    help='polygons per frame the renderer keeps (stock '
                         '2500; 0 leaves it)')
    ap.add_argument('--hud-filter', choices=('nearest', 'linear'),
                    default='nearest',
                    help='2D layer scaling (default nearest; linear blends '
                         'edges into neighbouring HUD elements)')
    ap.add_argument('--log', action='store_true',
                    help='diagnostic: write submissions and projection '
                         'setups to hires.log in the game folder')
    ap.add_argument('--log-quads', action='store_true',
                    help='diagnostic: with --log, every polygon at insert '
                         'too (three frames in 120 instead of all)')
    a = ap.parse_args()

    keep = a.exe + KEEP
    if a.restore:
        if not os.path.exists(keep):
            sys.exit('no %s to restore' % keep)
        shutil.copy2(keep, a.exe)
        os.remove(keep)
        print('restored', a.exe)
        return

    with open(a.exe, 'rb') as fh:
        buf = bytearray(fh.read())
    if len(buf) < 0x1f4400:
        sys.exit('not v_on.exe')

    try:
        n = install(buf, a.width, a.height, split_fov=a.split_fov,
                    split_fov_tb=a.split_fov_tb, hud_shift=a.hud_shift,
                    hangar=a.hangar, polys=a.polys, hud_filter=a.hud_filter,
                    log=a.log, log_quads=a.log_quads)
    except ValueError as exc:
        sys.exit('%s - already patched by this tool, or not the retail '
                 'v_on.exe' % exc)

    if not os.path.exists(keep):
        shutil.copy2(a.exe, keep)
    with open(a.exe, 'wb') as fh:
        fh.write(buf)
    print('%dx%d, %d sites, original kept as %s'
          % (a.width, a.height, n, keep))


if __name__ == '__main__':
    main()
