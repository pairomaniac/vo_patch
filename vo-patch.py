#!/usr/bin/env python3
"""Virtual-On (PC, 1997) patcher. See README.md.

    python3 vo-patch.py                 patch a copy of v_on.exe
    python3 vo-patch.py --rip SRC DIR   rip the soundtrack, no window needed
    python3 vo-patch.py --selfcheck     validate the patch tables and exit
    python3 vo-patch.py --version

GTK4 4.10 or newer where it is available, Tk otherwise. VOPATCH_UI=gtk or
=tk forces one.

Version 0.7.0
https://github.com/pairomaniac/vo_patch
"""

import ctypes
import hashlib
import os
import re
import shutil
import struct
import sys
import threading

VERSION = re.search(r'^Version (\S+)', __doc__, re.M).group(1)

EXE_SIZE = 6650880

ORIGINAL_MD5 = 'a464b0ff32d5bab499f265e45658504e'

# GENERATED - do not edit the hex by hand.
#
# Assembled from asm/levers.asm and asm/twinstick.asm. To change either:
# edit the source and run
#     python3 asm/build.py
# which rewrites everything between the markers below. CI runs
# `asm/build.py --check` on every push and fails if the two have drifted, so a
# hand edit here will be caught but only after it has wasted your afternoon.
#
# The routine replaces the input tick's epilogue, so its site sits inside the
# XInput routine rather than in untouched padding: the table entry below
# expects the bytes the previous site wrote, not the original file's. Its
# length is taken from LEVERS_CODE, so the routine may change size freely.

# LEVERS BLOB BEGIN
LEVERS_CODE = bytes.fromhex(
    '837dfc0074288b53088b4b0cf60280750df601407508800a708009b0eb10f602'
    '40750bf601807506800ab08009705f5e5bc9c3'
)
# LEVERS BLOB END

# twinstick.asm is assembled at a fixed org, so it only works at the cave
# its site names. asm/build.py checks the two against each other.

# TWIN BLOB BEGIN
TWIN_CODE = bytes.fromhex(
    '68184a6200e88b37feff83c404e9eee4e1ff68404a6200e87937feff83c404e9'
    '6f83f9ffe800e900ea00eb00ec00ed00ee00ef00e600e700e400e50020108040'
    '000000000100020000000000201080400001000200000000e8496200c414cb01'
    'c614cb01004a62000c4a62008104bf0060cb6503743044005704bf0001000000'
    'e8496200e43eee01e63eee01004a62000c4a6200b10dad0161cb6503edce5b00'
    '940dad01'
)
# TWIN BLOB END

# KBPAGE BLOB BEGIN
KBPAGE_CODE = bytes.fromhex(
    '833dac6bbf0001750e833d40156503007505e91f8ee5ffe9728ee5ff90909090'
    '6a01ff35ac6bbf00e8aa8fe5ff83c408e92d8fe5ff'
)
# KBPAGE BLOB END

# Each site: (offset, original, patched).

FEATURES = [
    ('sound', 'Miscellaneous sound fixes',
     'Three small fixes, applied together.\n'
     '\n'
     'Sound effects\tThe built-in delay before each one is removed.\n'
     'Output frequency\t22050 to 44100 Hz. The samples are 8-bit either way.\n'
     'Enemy Fei-Yen\tRestores the hypermode sound a bug left silent.', [
         (0x002bba60, '0f', '01'),
         (0x00189546, '2256', '44ac'),
         (0x00189552, '88580100', '10b10200'),
         (0x00058189, '01', '02'),
         (0x00170dc9, '01', '02')]),

    ('noloading', 'Hide loading screen text',
     'Hides "Now Loading . . .". Cosmetic: the loading it announced is\n'
     'already over by the time you read it.', [
         (0x002c7678, '4e', '00')]),


    ('defaults', 'Better defaults with no v_on.ini',
     'Changes what the game falls back on when a key is missing from\n'
     'v_on.ini, which on a first run is all of them. An existing key wins,\n'
     'and F5 overrides both.\n'
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
     'Disc check\tThe "Please insert VIRTUAL ON CD" prompt is skipped. The drive is still scanned, so a mounted image still works.\n'
     'Music\tRead from music\\trackNN.wav beside the game. Rip them in the CD MUSIC section below. With no files there, the game reads the drive.\n'
     'Section\tAdds a section to the executable instead of editing bytes in place. The file grows by about 3 KB.', [
         (0x001c76d4, '0f840a000000', '909090909090')]),

    ('nocpucheck', 'Skip processor check',
     'The game will not start on a modern CPU without this. Same as\n'
     'ProcessorCheck=Off in v_on.ini, but with no ini needed, and it takes\n'
     'the MMX, Pentium and vendor checks with it.', [
         (0x00107930, '830dc884bf0001', '90909090909090')]),
    ('framerate', 'Fix the frame rate (60 FPS)',
     'Three fixes, all for the game not running at full speed.\n'
     '\n'
     'Timer resolution\tWithout it the game runs at about 70 per cent speed on Windows 2000 and later. Not needed under Wine.\n'
     'Motion value\tMakes Motion= in v_on.ini work and stick. It is a divisor: 1 draws every frame, 2 draws half.\n'
     'Motion Type\tThe F5 radios wrote 3 and 2, so 60 fps was unreachable from the interface. They write 2 and 1 now, and read 30 FPS and 60 FPS.', [
         (0x001f423e, '00' * 62,
          '68624e5f00ff1504d56503686c4e5f0050ff1508d5650385c074046a01ff'
          'd0e9ce2affff77696e6d6d2e646c6c0074696d65426567696e506572696f'
          '6400'),
         (0x000000a8, '30791e00', '3e4e1f00'),
         (0x000273c1, '833d0843be0003', '833d0843be0002'),
         (0x000275d3, 'c7050843be0003000000', 'c7050843be0002000000'),
         (0x000275e2, 'c7050843be0002000000', 'c7050843be0001000000'),
         (0x006035ac, '2c040000', '30040000'),
         (0x0060c064, '230008002c04ffff800046006100730074000000000000000900015000'
                      '00000085005400290008002d04ffff800053006d006f006f0074006800'
                      '000000000000090003500000000044006200240008002e04ffff800054'
                      '0079007000650031000000000009000150000000006e00620024000800'
                      '2f04ffff80005400790070006500320000000000090001500000000098'
                      '006200240008003104ffff800054007900700065003300000000000000'
                      '0250000000000f00050022000800ffffffff8200530063007200650065'
                      '006e0000000000000000000250000000000f00540032000800ffffffff'
                      '82004d006f00740069006f006e00200054007900700065000000000000'
                      '000250000000000f00600032000a00ffffffff82005300630072006500'
                      '65006e002000530070006c006900740000000000000007000050000000'
                      '0010001900af001900ffffffff80005400650078007400750072006500'
                      '00000000070000500000000010003700af001800ffffffff8000440069'
                      '00730070006c006100790020004f0062006a0065006300740073000000'
                      '000000000250000000000f000f002b000900ffffffff82004600690065'
                      '006c006400200047007200610070006800690063000000000000000250'
                      '00000000160069001a000800ffffffff82002800320050002000560053'
                      '0029000000000000000000',
          '290008002c04ffff800033003000200046005000530000000000000009'
          '0001500000000085005400290008002d04ffff80003600300020004600'
          '50005300000000000000090003500000000044006200240008002e04ff'
          'ff8000540079007000650031000000000009000150000000006e006200'
          '240008002f04ffff800054007900700065003200000000000900015000'
          '00000098006200240008003104ffff8000540079007000650033000000'
          '000000000250000000000f00050022000800ffffffff82005300630072'
          '00650065006e0000000000000000000250000000000f00540032000800'
          'ffffffff82004d006f00740069006f006e002000540079007000650000'
          '00000000000250000000000f00600032000a00ffffffff820053006300'
          '7200650065006e002000530070006c0069007400000000000000070000'
          '500000000010001900af001900ffffffff800054006500780074007500'
          '7200650000000000070000500000000010003700af001800ffffffff80'
          '0044006900730070006c006100790020004f0062006a00650063007400'
          '73000000000000000250000000000f000f002b000900ffffffff820046'
          '00690065006c0064002000470072006100700068006900630000000000'
          '0000025000000000160069001a000800ffffffff820028003200500020'
          '0056005300290000000000'),
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
     'its place: No shot, SE, CD, Kill 1P, Kill 2P, Scorekeeping and Quit\n'
     'Program. Motion has moved to F5.\n'
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
         (0x001c4d7e, '57685c00', '7c4e5f00'),
         (0x001f427c, '0000000000000000000000000000000000000000000000000000000000000000'
          '0000000000000000000000000000000000000000000000000000000000000000'
          '000000000000000000000000000000000000000000000000000000',
          '558bec53817d0c000100007547817d107a000000753e68e8e86300ff1504d565'
          '0368f3e8630050ff1508d5650385c0741c8bd86a0068d84e5f00ff750868b0fa'
          '65036a00ff15a0d4650350ffd333c05b5dc210005b5de98019fdff'),
         (0x001f42d8, '0000000000000000000000000000000000000000000000000000000000000000'
          '0000000000000000000000000000000000000000000000000000000000000000'
          '0000000000000000000000000000000000000000000000000000000000000000'
          '0000000000000000000000000000000000000000000000000000000000000000'
          '0000000000000000000000000000000000000000000000000000000000000000'
          '0000000000000000000000000000000000000000000000000000000000000000'
          '0000000000000000000000000000000000000000000000000000000000000000'
          '0000000000000000000000000000000000000000000000',
          '558bec5356578b450c3d10010000756fbe0ce96300bf030000008b068b0033c9'
          '83f8010f94c151ff7604ff7508ff1544d5650383c6084f75e168e8030000ff75'
          '08ff154cd565038bd8be24e96300bf05000000566a00684301000053ff152cd5'
          '650383c6044f75eba1d0846c00486a0050684e01000053ff152cd56503eb683d'
          '1101000075688b45100fb7c8c1e81081f9e8030000752a48755468e8030000ff'
          '7508ff154cd565036a006a00684701000050ff152cd5650305559c00008bc8eb'
          '1283f90275316a00ff7508ff1538d56503eb146a00516811010000ff35585fae'
          '01ff156cd56503b801000000eb0233c05f5e5b5dc21000'),
         (0x001f43cf, '00000000000000000000000000000000000000000000000000',
          '81f9419c0000750f89cb6a00ff7508ff1538d5650389d9ebc3'),
         (0x0023dce8, '0000000000000000000000000000000000000000000000000000000000000000'
          '0000000000000000000000000000000000000000000000000000000000000000'
          '00000000000000000000000000000000',
          '5553455233322e444c4c004469616c6f67426f78496e64697265637450617261'
          '6d410000d82f6500479c00004ccc6b005b9c000030f463005c9c0000312f3100'
          '312f3200312f3300312f3400312f3500'),
         (0x006036b0, '0000000010002600470061006d00650000000000439c26005200650073'
                      '0074006100720074002000470061006d006500090041006c0074002b00'
                      '4600320000000000449c2600500061007500730065002000470061006d'
                      '00650009004600330000000000469c44006900730063006f006e006e00'
                      '650063007400200026004e006500740077006f0072006b000900460039'
                      '0000000000679c260042006f006f006b004b0065006500700069006e00'
                      '670020002e002e002e0000000000000000008000419c45002600780069'
                      '0074002000470061006d006500090041006c0074002b00460034000000'
                      '1000260044006500620075006700000010004400690073007000260046'
                      '006c006f006f00720000000000499c26004600690065006c0064000000'
                      '00004a9c260057006100740065007200000000004b9c260053006b0079'
                      '00000000004c9c26004f00750074005300690064006500000080004d9c'
                      '460069006c006c002600420047000000100026004d006f00740069006f'
                      '006e0000000000559c31002f002600310000000000569c31002f002600'
                      '320000000000579c31002f002600330000000000589c31002f00260034'
                      '0000008000599c31002f00260035000000100026004b006900',
          'c000c88000000000080000000000d40068000000000045007800740072'
          '0061007300000008004d0053002000530061006e007300200053006500'
          '72006900660000000000030001500000000010000e0038000c00479cff'
          'ff80004e006f002000730068006f007400000000000300015000000000'
          '50000e0024000c005b9cffff8000530045000000000000000300015000'
          '0000007c000e0024000c005c9cffff8000430044000000000000000000'
          '01500000000010002c0032000e00619cffff80004b0069006c006c0020'
          '003100500000000000000001500000000046002c0032000e00629cffff'
          '80004b0069006c006c002000320050000000000000000150000000007c'
          '002c0048000e00679cffff8000530063006f00720065006b0065006500'
          '700069006e00670000000000000000000150000000001000540048000e'
          '00419cffff800051007500690074002000500072006f00670072006100'
          '6d0000000000000000000150000000009200540032000e000200ffff80'
          '0043006c006f0073006500000000000000000000000000000000000000'
          '0000000000000000000000000000000000000000000000000000000000'
          '00000000000000000000000000000000000000000000000000')
]),
    ('continuefix', 'Fix crash on round loss',
     'Stops the crash when you lose a round as Temjin, Viper II, Apharmd or\n'
     'Raiden.', [
         (0x00077f5a, '8b45fcd94008d9e083ec04d91c248b45fc8b4004508b45fcd900d9e083ec04d91c24e8df63f9ff83c40c',
          '909090909090909090909090909090909090909090909090909090909090909090909090909090909090'),
         (0x00078b1c, '8b45fcd94008d9e083ec04d91c248b45fc8b4004508b45fcd900d9e083ec04d91c24e81d58f9ff83c40c',
          '909090909090909090909090909090909090909090909090909090909090909090909090909090909090'),
         (0x00079bb6, '8b45fcd94008d9e083ec04d91c248b45fc8b4004508b45fcd900d9e083ec04d91c24e88347f9ff83c40c',
          '909090909090909090909090909090909090909090909090909090909090909090909090909090909090'),
         (0x00079f3f, '8b45fcd94008d9e083ec04d91c248b45fc8b4004508b45fcd900d9e083ec04d91c24e8fa43f9ff83c40c',
          '909090909090909090909090909090909090909090909090909090909090909090909090909090909090'),
         (0x0007d04a, '8b45fcd94008d9e083ec04d91c248b45fc8b4004508b45fcd900d9e083ec04d91c24e8ef12f9ff83c40c',
          '909090909090909090909090909090909090909090909090909090909090909090909090909090909090'),
         (0x000bb9ea, '8b45fcd94008d9e083ec04d91c248b45fc8b4004508b45fcd900d9e083ec04d91c24e80fc1f4ff83c40c',
          '909090909090909090909090909090909090909090909090909090909090909090909090909090909090'),
         (0x000bc5ac, '8b45fcd94008d9e083ec04d91c248b45fc8b4004508b45fcd900d9e083ec04d91c24e84db5f4ff83c40c',
          '909090909090909090909090909090909090909090909090909090909090909090909090909090909090'),
         (0x000bd646, '8b45fcd94008d9e083ec04d91c248b45fc8b4004508b45fcd900d9e083ec04d91c24e8b3a4f4ff83c40c',
          '909090909090909090909090909090909090909090909090909090909090909090909090909090909090'),
         (0x000bd9cf, '8b45fcd94008d9e083ec04d91c248b45fc8b4004508b45fcd900d9e083ec04d91c24e82aa1f4ff83c40c',
          '909090909090909090909090909090909090909090909090909090909090909090909090909090909090'),
         (0x000c0ada, '8b45fcd94008d9e083ec04d91c248b45fc8b4004508b45fcd900d9e083ec04d91c24e81f70f4ff83c40c',
          '909090909090909090909090909090909090909090909090909090909090909090909090909090909090')]),

    ('padxinput', 'XInput gamepad support',
     'Three profiles on the F7 screen, for both players: pad 1 drives 1P,\n'
     'pad 2 drives 2P.\n'
     '\n'
     'Keyboard (Real)\tthe game\'s two-lever keyboard scheme\n'
     'Gamepad (XInput)\ttwelve named actions, bind them yourself\n'
     'Twin-stick (XInput)\tthe arcade levers, nothing to bind\n'
     '\n'
     'Disables Keyboard (Simple) for the time being: it is the only page\n'
     'that binds all twelve actions, so the gamepad has to take it.\n'
     '\n'
     'A\tAccept, and skips the intro\n'
     'Select\tCamera\n'
     'Start\tPause\n'
     '\n'
     'Twin-stick puts a thumbstick on each lever, the triggers on the\n'
     'triggers and LB/RB on the turbo buttons. Both sticks the same way\n'
     'walks, opposite ways turns, apart jumps, together crouches. On the\n'
     'bound profiles jump and guard are gestures too, and work while moving.\n'
     '\n'
     'Your v_on.ini is renamed to v_on.ini.bak and the game writes a fresh\n'
     'one, because binds saved by the unpatched game do not fit the new\n'
     'device list. Restore original puts it back.', [
         # the routine lives in .rdata padding, so mark it executable
         (0x000001c4, '40000040', '40000060'),
         # F7 page: drop the letter, digit and named-key sections
         (0x00097042, '1a', '00'),
         (0x00097082, '0a', '00'),
         (0x000970c2, '21', '10'),
         (0x000970d8, '38d46600', '43486200'),
         # same boundaries when a selection is stored
         (0x0009725a, '1a', '00'),
         (0x00097277, '1a', '00'),
         (0x0009727b, '0a', '00'),
         (0x00097298, '0a', '00'),
         (0x0009729f, '3cd46600', '47486200'),
         # and when one is restored - three index rebases, all or none
         (0x0009742b, '1a', '00'),
         (0x0009746b, '0a', '00'),
         (0x0009748d, '1a', '00'),
         (0x000974ce, '24', '00'),
         (0x000974af, '21', '10'),
         (0x000974c1, '3cd46600', '47486200'),
         # window title
         (0x0026c88c, '4b6579626f617264206f6e6c79202853696d706c652074797065202d2025645020736964652900',
                      '47616d65706164202858496e70757429202d202564502073696465000000000000000000000000'),
         # default binds, 1P and 2P, twelve slots of stride 2
         (0x0026c400, '11001f001e002000100012002e0022002d0013002f002100', 'e800e900ea00eb00ee00ef00e600e500e400e000e200e700'),
         (0x0026c418, 'c700cf00d300d100d200c900520051004f004c0053005000', 'e800e900ea00eb00ee00ef00e600e500e400e000e200e700'),
         # each player's profile 1 dispatches to the routine
         (0x000422a8, '502e4400', '60806000'),
         (0x001bc13b, 'e3cc5b00', '72806000'),
         # PeekMessage: Start and A must reach the game while it is paused
         # or on the intro, where the input tick does not run
         (0x001c530e, 'ff1590d56503', 'e88521040090'),
         # two pads are separate devices, so 2P may reuse 1P's inputs
         (0x000971bd, '0f8558000000', 'e95900000090'),
         # device list: Keyboard (Real), Gamepad (XInput), Twin-stick
         (0x0026c218, 'ecd36600d4d36600c0d36600b4d3660090d3660080d3660068d3660058d36600',
          'ed4b6200fd4b62000e4c62000000000000000000000000000000000000000000'),
         # profile switch, both players: slot 2 gets the twin-stick stubs.
         # Slot 1 is the gamepad, repointed above; slot 0 is the game's own
         # keyboard handler and is left alone.
         (0x000422ac, '5a2e4400', 'c4496200'),
         (0x001bc13f, 'edcc5b00', 'd6496200'),
         # The keyboard profile shared its twenty-four bind slots with
         # Simple, and the gamepad took those. Move it to the block owned by
         # the hidden Joystick + Keyboard profile, which is inside the
         # structure saved to v_on.ini, so it persists for free.
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
         (0x00096b61, '833dac6bbf00010f8558000000',
                      'e9d2711a009090909090909090'),
         # and Default passed a hardcoded player 0, so on the 2P side it
         # reset 1P's binds. The other two pages pass ds:0xbf6bac here.
         (0x00096c8e, '6a016a00e87800000083c408',
                      'e9c5701a0090909090909090'),
         (0x0023dd38, '00' * len(KBPAGE_CODE), KBPAGE_CODE.hex()),
         # Startup defaults every block in a fixed order, and Joystick +
         # Keyboard writes that block after the keyboard profile does. It is
         # hidden, so drop its call; the pushes around it stay balanced.
         (0x00094ea0, 'e8592b0000', '9090909090'),
         # the picker's Next handler, where slot 2 demanded two or three
         # joysticks. Send it to the case that requires nothing.
         (0x00095217, '415d4900', '235e4900'),
         # F7 page table: twin-stick binds nothing, so it takes the case
         # that opens no dialog and reports success.
         (0x00095bdc, '28674900', 'a9674900'),
         (0x00223dc4, '00' * len(TWIN_CODE), TWIN_CODE.hex()),
         # condition table, input list, strings, then the routine itself
         (0x0022411b, '0000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000',
                      '02000000001000000200000000200000020000000040000002000000008000000200000000010000020000000002000003000200400000000300030040000000010006001027000000000600f0d8ffff00000400f0d8ffff010004001027000001000a001027000000000a00f0d8ffff00000800f0d8ffff0100080010270000'),
         (0x00223c43, '0000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000',
                      '9b4b6200e00000009d4b6200e10000009f4b6200e2000000a14b6200e3000000a34b6200e4000000a64b6200e5000000a94b6200e6000000ac4b6200e7000000af4b6200e8000000b54b6200e9000000bd4b6200ea000000c54b6200eb000000ce4b6200ec000000d44b6200ed000000dc4b6200ee000000e44b6200ef000000'),
         (0x00223f9b, '000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000',
          '41004200580059004c42005242004c54005254004c53205570004c5320446f776e004c53204c656674004c5320526967687400525320557000525320446f776e005253204c656674005253205269676874004b6579626f61726420285265616c290047616d65706164202858496e70757429005477696e2d737469636b202858496e7075742900'),
         (0x00207460, '0000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000',
                      '6822836000e91a00000083c404e952aee3ff684a836000e91200000083c404e9d34cfbffe8d0000000e9dcffffffe8c6000000e9e4ffffff609ca140cb650383f8010f86a900000031f683fe020f839e0000006870cb650356ff1540cb650385c00f85840000000fb70574cb6503a9100000000f95c00fb6c08d9684cb65030fb60a39c80f841f000000880285c00f84150000006a006a726800010000a1585fae0150ff156cd565030fb70574cb6503a9001000000f95c00fb6c08d9688cb65030fb60a39c80f841f000000880285c00f84150000006a006a206800010000a1585fae0150ff156cd5650346e959ffffff9d61ff2590d565035589e583ec045356578b5d08c745fc00000000a140cb650385c00f840e00000083f8010f84a1000000e95400000031f683fe030f833a0000008b04b50783600050ff1504d5650385c00f850600000046e9dbffffff681383600050ff1508d5650385c00f840a000000a340cb6503e90f000000c70540cb650301000000e9480000006844cb6503ff33ffd085c00f8537000000c745fc010000000fb70548cb6503a9001000000f84060000008b5318c602800fb70548cb6503a9200000000f84060000008b5324c602808b4320ffd0837dfc000f84d000000031f683fe0c0f83c50000008b53040fb604722de00000000f82ad00000083f8100f83a40000008d3cc51b4d62000fb6070fb757028b4f0483f8000f842600000083f8010f843100000083f8020f843c0000000fb68248cb650339c80f8741000000e9640000000fbf8248cb650339c80f8c2d000000e9500000000fbf8248cb650339c80f8f19000000e93c0000000fb70548cb650385c80f8505000000e9280000008b53100fb60c32f7d18b53080fb70221c86689028b53140fb60c32f7d18b530c0fb70221c866890246e932ffffff5f5e5bc9c372836000808360008e83600058496e7075744765745374617465000000000070146503c414cb01c614cb01903665009d3665008104bf0060cb6503743044005704bf000100000088146503e43eee01e63eee0108eb6b0015eb6b00b10dad0161cb6503edce5b00940dad0178696e707574315f342e646c6c0078696e707574315f332e646c6c0078696e707574395f315f302e646c6c00'),
         # the tick ORs every active input together, but the game's
         # gestures are exclusive lever positions, so a held direction
         # contaminates jump and guard. Strip it back off at the end,
         # and only when a pad was actually read, so the keyboard path
         # is left exactly as it was.
         (0x00207702, '5f5e5bc9c3', 'e997000000'),
         (0x0020779e, '00' * len(LEVERS_CODE), LEVERS_CODE.hex())]),
]

# Found by signature rather than offset, so it cannot live in FEATURES.
# SetCooperativeLevel: DISCL_FOREGROUND -> DISCL_BACKGROUND.
DI_FIND = re.compile(
    rb'\x6a\x06[\s\S]{0,20}?\xff(?:[\x50-\x57]\x34|[\x90-\x97]\x34\x00\x00\x00)')

# key -> (label, description, sites), with sites None meaning DI_FIND.
BY_KEY = {key: (label, tip, sites) for key, label, tip, sites in FEATURES}
BY_KEY['dinput'] = (
    'Fix keyboard input after ALT+TAB',
    'Without this, alt-tabbing away or opening an F-key dialog kills\n'
    'keyboard input until the game is restarted.', None)

# Display order only; see apply_order for the write order. Essential fixes
# what is broken on modern systems, extra is taste. Both start ticked, extra
# running from the biggest change down to the smallest.
ESSENTIAL = ('nocpucheck', 'framerate', 'continuefix', 'dinput')
EXTRA = ('padxinput', 'nodisc', 'debugbox', 'defaults', 'sound',
         'noloading')


def apply_order():
    """Display order, except that nodisc has to be last: it appends a
    section and chains the entry point, so it must see every other edit."""
    keys = [k for k in ESSENTIAL + EXTRA if k != 'nodisc']
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
    if set(BY_KEY) != set(ESSENTIAL) | set(EXTRA):
        raise AssertionError('patch list and display order disagree')
    if apply_order()[-1] != 'nodisc':
        raise AssertionError('nodisc must be applied last')

    owner = {}
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


class WavWriter(object):
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
    m, s, f = (int(x) for x in _MSF.match(text).groups())
    return (m * 60 + s) * 75 + f


def parse_cue(path):
    """Return [(track_no, mode, binpath, start_sector, index0_sector)].

    index0_sector is the pregap start of the *next* track where present, which
    is where this track's audio should stop.
    """
    base = os.path.dirname(os.path.abspath(path))
    tracks, curbin, cur = [], None, None

    with open(path, 'r', errors='replace') as fh:
        for line in fh:
            line = line.strip()
            up = line.upper()

            if up.startswith('FILE'):
                name = line.split('"')[1] if '"' in line else line.split()[1]
                curbin = os.path.join(base, name)
                if not os.path.exists(curbin):
                    # Cue sheets are often wrong about case.
                    for entry in os.listdir(base):
                        if entry.lower() == os.path.basename(name).lower():
                            curbin = os.path.join(base, entry)
                            break

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


def rip_cue(cue_path, outdir, progress=None):
    """Extract every AUDIO track from a bin/cue pair."""
    tracks = parse_cue(cue_path)
    os.makedirs(outdir, exist_ok=True)
    written = []

    for i, t in enumerate(tracks):
        if 'AUDIO' not in t['mode']:
            continue

        nxt = tracks[i + 1] if i + 1 < len(tracks) else None
        size = os.path.getsize(t['bin'])

        if nxt is None or nxt['bin'] != t['bin']:
            end = size // RAW
        else:
            # Stop at the next track's pregap so trailing silence is dropped.
            end = nxt['pregap'] if nxt['pregap'] is not None else nxt['start']

        start = t['start']
        if end <= start:
            continue

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


# ---------------------------------------------------------- Windows device

IOCTL_CDROM_READ_TOC = 0x00024000
IOCTL_CDROM_RAW_READ = 0x0002403E
TRACK_MODE_CDDA = 2
INVALID_HANDLE = ctypes.c_void_p(-1).value


def _kernel32():
    """kernel32 with prototypes. Without them ctypes assumes every argument
    and return value is a 32-bit int, which truncates handles on 64-bit
    Windows."""
    k = ctypes.WinDLL('kernel32', use_last_error=True)
    u32, ptr = ctypes.c_uint32, ctypes.c_void_p
    k.CreateFileW.restype = ptr
    k.CreateFileW.argtypes = [ctypes.c_wchar_p, u32, u32, ptr, u32, u32, ptr]
    k.DeviceIoControl.restype = ctypes.c_int
    k.DeviceIoControl.argtypes = [ptr, u32, ptr, u32, ptr, u32,
                                  ctypes.POINTER(ctypes.c_ulong), ptr]
    k.CloseHandle.restype = ctypes.c_int
    k.CloseHandle.argtypes = [ptr]
    k.GetDriveTypeW.restype = ctypes.c_uint
    k.GetDriveTypeW.argtypes = [ctypes.c_wchar_p]
    k.GetLogicalDrives.restype = ctypes.c_uint32
    return k


def _win_ioctl(k, h, code, inbuf, outlen):
    out = ctypes.create_string_buffer(outlen)
    ret = ctypes.c_ulong(0)
    ok = k.DeviceIoControl(h, code, inbuf, len(inbuf) if inbuf else 0,
                           out, outlen, ctypes.byref(ret), None)
    if not ok:
        raise OSError('DeviceIoControl 0x%x failed: %d'
                      % (code, ctypes.get_last_error()))
    return out.raw[:ret.value]


def _rip_windows(letter, outdir, progress=None, chunk=16):
    GENERIC_READ = 0x80000000
    FILE_SHARE_READ = 1
    OPEN_EXISTING = 3

    k = _kernel32()
    path = '\\\\.\\%s:' % letter.rstrip(':\\/')
    h = k.CreateFileW(path, GENERIC_READ, FILE_SHARE_READ, None,
                      OPEN_EXISTING, 0, None)
    if not h or h == INVALID_HANDLE:
        raise OSError('cannot open %s: %d' % (path, ctypes.get_last_error()))

    try:
        toc = _win_ioctl(k, h, IOCTL_CDROM_READ_TOC, None, 4 + 100 * 8)
        first, last = toc[2], toc[3]
        entries = []
        for i in range((len(toc) - 4) // 8):
            # TRACK_DATA declares Control before Adr, so on this side Control
            # is the *low* nibble - the opposite of Linux's cdrom_tocentry.
            # Reading the wrong one makes the data track look like audio.
            no, ctrl = toc[4 + i * 8 + 2], toc[4 + i * 8 + 1] & 0x0F
            m, s, f = toc[4 + i * 8 + 5:4 + i * 8 + 8]
            # MSF counts from the start of the lead-in, LBA from the start
            # of track 1, and the gap between them is 2 seconds.
            lba = (m * 60 + s) * 75 + f - 150
            entries.append({'no': no, 'lba': lba, 'audio': not (ctrl & 4)})
            if no == 0xAA:
                break

        os.makedirs(outdir, exist_ok=True)
        written = []
        for i, t in enumerate(entries):
            if t['no'] == 0xAA or not t['audio'] or t['no'] < first \
                    or t['no'] > last:
                continue
            start, end = t['lba'], entries[i + 1]['lba']
            total = (end - start) * RAW
            out = os.path.join(outdir, 'track%02d.wav' % t['no'])

            with WavWriter(out) as dst:
                lba = start
                while lba < end:
                    n = min(chunk, end - lba)
                    # DiskOffset counts in 2048-byte units even for CDDA.
                    req = struct.pack('<qII', lba * 2048, n, TRACK_MODE_CDDA)
                    dst.write(_win_ioctl(k, h, IOCTL_CDROM_RAW_READ, req,
                                         n * RAW))
                    lba += n
                    if progress:
                        progress(t['no'], (lba - start) * RAW, total)
            written.append(out)
        if not written:
            raise ValueError('no audio tracks in %s - data-only disc?' % path)
        return written
    finally:
        k.CloseHandle(h)


# ------------------------------------------------------------------ public

def rip_device(device, outdir, progress=None):
    """Rip audio tracks from a CD drive, real or cdemu-backed."""
    if os.name == 'nt':
        return _rip_windows(device, outdir, progress)
    return _rip_linux(device, outdir, progress)


def list_devices():
    """Candidate optical devices, best-effort."""
    if os.name == 'nt':
        DRIVE_CDROM = 5
        k = _kernel32()
        out = []
        mask = k.GetLogicalDrives()
        for i in range(26):
            if mask & (1 << i):
                letter = chr(ord('A') + i)
                if k.GetDriveTypeW(letter + ':\\') == DRIVE_CDROM:
                    out.append(letter + ':')
        return out
    return [os.path.join('/dev', d) for d in sorted(os.listdir('/dev'))
            if re.match(r'^sr\d+$', d)]


MUSIC_SUBDIR = 'music'


def outdir_for(gamedir):
    """Where the tracks live, given the directory holding v_on.exe."""
    return os.path.join(gamedir, MUSIC_SUBDIR)


def rip(source, outdir, progress=None):
    """Dispatch on what the source looks like."""
    if source.lower().endswith('.cue'):
        return rip_cue(source, outdir, progress)
    return rip_device(source, outdir, progress)


# Shown until a file is picked. The GUI highlights this one line, because a
# user who has not picked one yet reads the CD MUSIC section first and finds
# the Rip button does nothing.
MUSIC_NEEDS_EXE = 'Select v_on.exe first - the tracks go beside it.'


def music_status(gamedir):
    """One line on what is in the music folder, for the GUI."""
    if not gamedir:
        return MUSIC_NEEDS_EXE
    out = outdir_for(gamedir)
    if not os.path.isdir(out):
        return 'No music folder. The game will read the drive.'
    found = [f for f in os.listdir(out)
             if re.match(r'track\d+\.wav$', f, re.I)]
    if not found:
        return 'Music folder is empty. The game will read the drive.'
    mb = sum(os.path.getsize(os.path.join(out, f)) for f in found) // (1 << 20)
    return '%d tracks in music (%d MB).' % (len(found), mb)


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


# --- CD audio ------------------------------------------------------------
# GENERATED - do not edit the hex by hand.
#
# Assembled from asm/vocd.asm and asm/layout.py. To change it: edit those and
# run
#     python3 asm/build.py
# which rewrites everything between the markers below, including VOCD_MAGICS.
# CI runs `asm/build.py --check` on every push and fails if the two have
# drifted. Editing the hex directly would pass that check only until the next
# regeneration silently threw the edit away.
#
# The only absolute addresses in VOCD_CODE are the placeholders below, which
# apply_cdaudio fills in once it has read the executable. VOCD_DATA is the
# string table and the scratch space.

# VOCD BLOB BEGIN
VOCD_MAGICS = {
    'MAGIC_ORIGENTRY': 0xE1E1E1E1,     # VA of the entry point we chain to
    'MAGIC_IATMCI': 0xE2E2E2E2,        # VA of the mciSendCommandA IAT slot
    'MAGIC_LOADLIB': 0xE3E3E3E3,       # VA of the LoadLibraryA IAT slot
    'MAGIC_GETPROC': 0xE4E4E4E4,       # VA of the GetProcAddress IAT slot
    'MAGIC_DATA': 0xE5E5E5E5,          # VA the data blob lands at
    'MAGIC_HOOK': 0xE6E6E6E6,          # VA of the hook thunk
}

VOCD_CODE = bytes.fromhex(
    'e913020000eb1aacaa84c075fa4fc331d2b90a000000f7f10430aa88d00430aa'
    'c360bbe5e5e5e5837b2c000f8581010000c7432c010000008d83c005000050ff'
    '15e3e3e3e389c68d83e60500005056ff15e4e4e4e48943148d83f90500005056'
    'ff15e4e4e4e48943188d83050600005056ff15e4e4e4e489431c8d8311060000'
    '5056ff15e4e4e4e48943208d831d0600005056ff15e4e4e4e48943248d832c06'
    '00005056ff15e4e4e4e48943288d83cd05000050ff15e3e3e3e385c00f84f000'
    '000089c68d83d70500005056ff15e4e4e4e489431085c00f84d50000008d83d0'
    '0100006808010000506a00ff531485c00f84bc0000008dbbd001000001c74f80'
    '3f5c740a8d83d001000039c777f0c6470100be02000000e89d0000006a006880'
    '0000006a036a006a0168000000808d83e002000050ff531883f8ff744489c76a'
    '0057ff531c89c557ff532083ed2c763189e831d2b930090000f7f131d2b99411'
    '0000f7f189c589d031d2b94b000000f7f1c1e00809c5c1e21009d5896cb34089'
    '334683fe647290833b0074268d4330506a046a0468e2e2e2e2ff532485c07412'
    'a1e2e2e2e289430cc705e2e2e2e2e6e6e6e66168e1e1e1e1c3568dbbe0020000'
    '8db3d0010000e83cfeffff8db33e060000e831feffff8b0424e831feffff8db3'
    '4a060000e81efeffff5ec36a006a006a008d040350ff5310c3837b04007418b8'
    'd1060000e8e2ffffffc7430400000000c7430800000000c3fc5589e5535657bb'
    'e5e5e5e5833b000f84900000008b450c3d0308000075408b5510f7c200200000'
    '747bf7c20010000075738b751485f6746c8b460885c074658d93360600005250'
    'ff532885c07556e88dffffffc74604cefa000031c0eb55817d08cefa0000753d'
    '3d140800000f84520100003d060800000f84ad0000003d0808000074513d0908'
    '000074633d55080000747d3d0408000074333d0b080000741a31c0eb0fff7514'
    'ff7510ff750cff7508ff530c5f5e5b5dc210008b751485f67407c74604010000'
    '0031c0ebe7e80fffffff31c0ebde837b0400740fb8a7060000e8edfeffffe8f6'
    'feffff31c0ebc5837b04007417837b08007511b8b4060000e8cefeffffc74308'
    '0100000031c0eba4837b08007411b8c2060000e8b3feffffc743080000000031'
    'c0eb898b5510f7c2040000000f84840000008b751485f6747d8b46040fb6f083'
    'fe02727283fe64736d8b44b34085c07465e883feffffe83efeffff8dbb000400'
    '008db34f060000e87bfcffff8db3e0020000e870fcffff8db356060000e865fc'
    'ffff6a006a006a008d830004000050ff531085c075208b45148b40040fb6c089'
    '4304b875060000e81ffeffffb89a060000e815feffff31c0e9effeffff8b7514'
    '85f6750731c0e9e1feffff8b460883f80375078b13e9e300000083f801752d8b'
    '5510f7c2100000000f84cd0000008b4e0c83f9010f82c100000083f9640f83b8'
    '0000008b548b40e9b100000083f8047556ba0d020000837b08007544837b0400'
    '0f84970000006a006a408d8300040000508d83df06000050ff531085c0757e8d'
    '83f3060000508d830004000050ff5328ba0d02000085c07564ba0e020000eb5d'
    'ba11020000eb5683f808750e8b530485d2754aba01000000eb4383f805743583'
    'f807743083f8067507ba0a000000eb2d3d014000007524ba400400008b4d10f7'
    'c1100000007416837e0c017510ba41040000eb09ba01000000eb0231d2895604'
    '31c0e9e5fdffff'
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
    '696c65410047657446696c6553697a6500436c6f736548616e646c6500566972'
    '7475616c50726f74656374006c737472636d706941006364617564696f006d75'
    '7369635c747261636b002e776176006f70656e20220022207479706520776176'
    '65617564696f20616c69617320766f636462676d0073657420766f636462676d'
    '2074696d6520666f726d6174206d696c6c697365636f6e647300706c61792076'
    '6f636462676d0073746f7020766f636462676d00706175736520766f63646267'
    '6d00726573756d6520766f636462676d00636c6f736520766f636462676d0073'
    '746174757320766f636462676d206d6f646500706c6179696e6700'
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
    values = {
        'MAGIC_ORIGENTRY': pe.base + pe.entry_rva,
        'MAGIC_IATMCI':    pe.iat_slot('winmm.dll', 'mciSendCommandA'),
        'MAGIC_LOADLIB':   pe.iat_slot('kernel32.dll', 'LoadLibraryA'),
        'MAGIC_GETPROC':   pe.iat_slot('kernel32.dll', 'GetProcAddress'),
        'MAGIC_DATA':      pe.base + code_rva + len(VOCD_CODE) + gap,
        'MAGIC_HOOK':      pe.base + code_rva,
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
    return pe.d


class Patcher:
    """All the file handling, with no reference to any toolkit."""

    def __init__(self):
        self.exe_path = None

    def load(self, path):
        """Return (description, accepted). Raises OSError."""
        with open(path, 'rb') as fh:
            data = fh.read()
        self.exe_path = path
        if hashlib.md5(data).hexdigest() == ORIGINAL_MD5:
            return 'READY \u2014 unmodified disc original', True
        note = 'CANNOT PATCH \u2014 this is not the original v_on.exe.'
        if len(data) != EXE_SIZE:
            note += '  Expected %d bytes, got %d.' % (EXE_SIZE, len(data))
        elif os.path.exists(path + '.bak'):
            note += '  Already patched - restore the backup first.'
        return note, False

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

        applied, skipped = [], []
        for key in apply_order():
            if not wanted.get(key):
                continue
            label, _tip, sites = BY_KEY[key]
            try:
                if sites is not None:
                    apply_feature(buf, sites)
                elif key == 'dinput':
                    apply_dinput(buf)
                if key == 'nodisc':          # bytes first, then the section
                    buf = apply_cdaudio(buf)
            except ValueError as exc:
                if key == 'dinput':          # signature miss, not fatal
                    log.append('Skipped %s: %s' % (label, exc))
                    skipped.append(label)
                    continue
                return False, ['%s: %s' % (label, exc), 'Nothing written.']
            applied.append(label)

        if not applied:
            if skipped:
                log.append('%s was the only patch selected and its call site '
                           'was not found. Nothing written.' % skipped[0])
            else:
                log.append('No patches selected. Nothing written.')
            return False, log

        self._backup(self.exe_path, log)
        try:
            with open(self.exe_path, 'wb') as fh:
                fh.write(buf)
        except OSError as exc:
            return False, log + ['Write failed: %s' % exc]
        log += ['  %s' % name for name in applied]
        log.append('Wrote %s' % self.exe_path)
        if wanted.get('padxinput'):
            self._retire_ini(log)
        return True, log

    def can_restore(self):
        return bool(self.exe_path) and os.path.exists(self.exe_path + '.bak')

    def restore(self):
        """Copy the .bak back over the exe. Returns a list of log lines."""
        bak = self.exe_path + '.bak'
        try:
            shutil.copy(bak, self.exe_path)
        except OSError as exc:
            return ['Restore failed: %s' % exc]
        log = ['Restored %s from the backup'
               % os.path.basename(self.exe_path)]

        # The patched game rebuilds v_on.ini on its first run, so by now
        # there is almost always one in the way. It holds pad binds the
        # restored game cannot read, so it is the one to move aside.
        ini = os.path.join(os.path.dirname(self.exe_path), 'v_on.ini')
        if os.path.exists(ini + '.bak'):
            try:
                if os.path.exists(ini):
                    os.replace(ini, ini + '.patched')
                    log.append('Kept the patched settings as v_on.ini.patched')
                shutil.move(ini + '.bak', ini)
                log.append('Put the original v_on.ini back')
            except OSError as exc:
                log.append('Could not restore v_on.ini: %s' % exc)
        return log

    def _retire_ini(self, log):
        """Binds written by the unpatched game crash the gamepad profile.

        The game rebuilds the file, so moving it aside is enough."""
        ini = os.path.join(os.path.dirname(self.exe_path), 'v_on.ini')
        if not os.path.exists(ini):
            return
        try:
            shutil.move(ini, ini + '.bak')
            log.append('Moved v_on.ini aside - the game will rebuild it')
        except OSError as exc:
            log.append('Could not move v_on.ini: %s - delete it by hand' % exc)

    @staticmethod
    def _backup(path, log):
        bak = path + '.bak'
        if not os.path.exists(bak):
            try:
                shutil.copy(path, bak)
                log.append('Backup: %s' % bak)
            except OSError as exc:
                log.append('Backup failed for %s: %s' % (path, exc))


def describe(text):
    """Split a description into prose and any 'key<TAB>meaning' rows.

    A blank line starts a paragraph; the breaks stay in the prose so either
    toolkit can drop it into one wrapped label."""
    paragraphs, para, rows = [], [], []
    for line in text.split('\n'):
        if '\t' in line:
            key, _, meaning = line.partition('\t')
            rows.append((key.strip(), meaning.strip()))
        elif line.strip():
            para.append(line.strip())
        elif para:
            paragraphs.append(' '.join(para))
            para = []
    if para:
        paragraphs.append(' '.join(para))
    return '\n\n'.join(paragraphs), rows


# From the game's artwork. Both toolkits paint themselves with this rather
# than following the desktop theme.
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
    'mag': '#ff5aa8',       # the info buttons
    'amber': '#ffa62b',     # the key column of a description
    'ok': '#42e08a',
    'bad': '#ff6b6b',
}

# The version is in the title because it is the only place a Windows
# user who double-clicked the exe can see it, and it is the first
# thing worth knowing about a bug report.
TITLE = 'Virtual-On patcher %s' % VERSION
INTRO = 'Select an unmodified v_on.exe.'
NO_FILE = 'No file selected'
ESSENTIAL_HINT = ('Fixes for what is broken on modern systems. Leave these '
                  'on unless you have a reason not to.')
EXTRA_HINT = 'Optional. Untick what you do not want.'

MUSIC_HINT = ('Rips the soundtrack to music\\ beside the game, which is '
              'where No disc required reads it from. Source: a cue sheet or '
              'a CD drive. About 320 MB, 26 tracks. You can rip before or '
              'after patching, and restoring the original leaves the tracks '
              'alone.')

MUSIC_PLACEHOLDER = 'VIRTUAL-ON.cue, or a CD drive'
DONE = 'Done. Restore the original to change your selection.'
FAILED = 'Nothing was written - see the log.'


# ---------------------------------------------------------------- GTK4 front


GTK_CSS = ("""
window { background-color: %(ink)s; color: %(text)s; }

.vp-card {
    background-color: %(card)s;
    border: 1px solid %(line)s;
    border-radius: 10px;
}
.vp-headbtn {
    background-color: %(head)s;
    border: none;
    box-shadow: none;
    outline: none;
    min-height: 0;
    padding: 9px 12px;
    border-radius: 9px;
}
.vp-headbtn:hover { background-color: %(line)s; }
.vp-head {
    color: %(cyan)s;
    font-size: 0.80em;
    font-weight: 800;
    letter-spacing: 0.10em;
}
.vp-arrow { color: %(cyan)s; }
.vp-dim { color: %(dim)s; }
.vp-hint { color: %(dim)s; font-size: 0.87em; padding-bottom: 4px; }
.vp-cue { color: %(cyan)s; font-size: 0.87em; padding-bottom: 4px; }
.vp-key { color: %(amber)s; font-weight: bold; }
.vp-info {
    color: %(mag)s;
    background: none;
    border: none;
    box-shadow: none;
    padding: 0 4px;
    min-height: 0;
    min-width: 0;
}
.vp-info:hover { color: %(text)s; }
.vp-statusbar {
    background-color: %(head)s;
    border-top: 1px solid %(line)s;
    padding: 8px 12px;
}
.vp-log { font-size: 0.88em; color: %(dim)s; }
.vp-ok  { color: %(ok)s; font-weight: bold; }
.vp-bad { color: %(bad)s; font-weight: bold; }

entry {
    background-color: %(ink)s;
    color: %(text)s;
    border: 1px solid %(line)s;
    border-radius: 6px;
    caret-color: %(cyan)s;
}
entry:disabled { color: %(dim)s; background-color: %(ink)s; }

button {
    background-image: none;
    background-color: %(head)s;
    color: %(text)s;
    border: 1px solid %(line)s;
    border-radius: 6px;
}
button:hover { background-color: %(line)s; }
button:disabled, button:disabled label {
    background-color: %(card)s;
    color: %(dim)s;
}
button.suggested-action, button.suggested-action label {
    color: %(ink)s;
    font-weight: bold;
}
button.suggested-action {
    background-color: %(cyan)s;
    border-color: %(cyan)s;
}
button.suggested-action:hover {
    background-color: %(cyan_hi)s;
    border-color: %(cyan_hi)s;
}
button.suggested-action:active {
    background-color: %(cyan_lo)s;
    border-color: %(cyan_lo)s;
}
button.suggested-action:disabled, button.suggested-action:disabled label {
    background-color: %(card)s;
    color: %(dim)s;
    border-color: %(line)s;
}

checkbutton, checkbutton label { color: %(text)s; }
checkbutton:disabled, checkbutton:disabled label { color: %(dim)s; }
checkbutton check {
    background-image: none;
    background-color: %(ink)s;
    border: 1px solid %(line)s;
    border-radius: 4px;
    color: %(ink)s;
}
checkbutton check:checked {
    background-color: %(cyan)s;
    border-color: %(cyan)s;
    color: %(ink)s;
}

textview, textview text { background-color: %(ink)s; color: %(dim)s; }
scrolledwindow { border: 1px solid %(line)s; border-radius: 6px; }
scrollbar { background-color: %(card)s; border: none; }
scrollbar slider { background-color: %(line)s; border-radius: 6px; }
scrollbar slider:hover { background-color: %(dim)s; }

popover > contents {
    background-color: %(card)s;
    color: %(text)s;
    border: 1px solid %(cyan)s;
    border-radius: 8px;
}
popover > arrow { background-color: %(card)s; border: 1px solid %(cyan)s; }
tooltip { background-color: %(card)s; color: %(text)s; }
""" % PALETTE).encode()


def run_gtk():
    import gi
    gi.require_version('Gtk', '4.0')
    from gi.repository import Gtk, Gdk, Gio, GLib, Pango

    VERTICAL = Gtk.Orientation.VERTICAL
    HORIZONTAL = Gtk.Orientation.HORIZONTAL

    def margins(widget, value):
        for side in ('top', 'bottom', 'start', 'end'):
            getattr(widget, 'set_margin_' + side)(value)

    class Window(Gtk.ApplicationWindow):

        def __init__(self, app):
            super().__init__(application=app, title=TITLE)
            self.set_default_size(470, -1)      # height follows the content
            self.core = Patcher()
            self.boxes = {}

            root = Gtk.Box(orientation=VERTICAL)
            self.set_child(root)

            content = Gtk.Box(orientation=VERTICAL, spacing=10)
            margins(content, 12)
            scroll = Gtk.ScrolledWindow(vexpand=True)
            scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            scroll.set_propagate_natural_height(True)
            scroll.set_max_content_height(760)
            scroll.set_child(content)
            root.append(scroll)

            content.append(self._section('GAME EXECUTABLE', self._file_body()))
            content.append(self._section(
                'ESSENTIAL PATCHES',
                self._patch_body(ESSENTIAL, ESSENTIAL_HINT), expanded=False))
            content.append(self._section(
                'EXTRA PATCHES',
                self._patch_body(EXTRA, EXTRA_HINT), expanded=False))
            content.append(self._section('CD MUSIC', self._music_body(),
                                        expanded=False))
            content.append(self._section('LOG', self._log_body()))

            root.append(self._statusbar())
            self._log(INTRO)

        # -- building blocks

        def _section(self, title, child, expanded=True):
            card = Gtk.Box(orientation=VERTICAL)
            card.add_css_class('vp-card')

            arrow = Gtk.Label(label='\u25be' if expanded else '\u25b8')
            arrow.add_css_class('vp-arrow')
            name = Gtk.Label(label=title, xalign=0, hexpand=True)
            name.add_css_class('vp-head')
            head_box = Gtk.Box(orientation=HORIZONTAL, spacing=8)
            head_box.append(arrow)
            head_box.append(name)
            head = Gtk.Button(child=head_box)
            head.add_css_class('vp-headbtn')

            margins(child, 12)
            child.set_margin_start(14)
            revealer = Gtk.Revealer(reveal_child=expanded)
            revealer.set_child(child)

            def toggled(_button):
                shown = not revealer.get_reveal_child()
                revealer.set_reveal_child(shown)
                arrow.set_label('\u25be' if shown else '\u25b8')
            head.connect('clicked', toggled)

            card.append(head)
            card.append(revealer)
            return card

        def _file_body(self):
            box = Gtk.Box(orientation=VERTICAL, spacing=8)
            row = Gtk.Box(orientation=HORIZONTAL, spacing=8)
            self.entry = Gtk.Entry(hexpand=True, editable=False,
                                   placeholder_text='not selected')
            row.append(self.entry)
            browse = Gtk.Button(label='Browse\u2026')
            browse.connect('clicked', self._pick)
            row.append(browse)
            box.append(row)

            self.file_note = Gtk.Label(label=NO_FILE, xalign=0, wrap=True)
            self.file_note.add_css_class('vp-hint')
            box.append(self.file_note)
            return box

        def _music_body(self):
            box = Gtk.Box(orientation=VERTICAL, spacing=8)
            note = Gtk.Label(label=MUSIC_HINT, xalign=0, wrap=True)
            note.add_css_class('vp-hint')
            box.append(note)

            row = Gtk.Box(orientation=HORIZONTAL, spacing=8)
            self.rip_entry = Gtk.Entry(hexpand=True,
                                       placeholder_text=MUSIC_PLACEHOLDER)
            drives = list_devices()
            if drives:
                self.rip_entry.set_text(drives[0])
            row.append(self.rip_entry)
            browse = Gtk.Button(label='Cue\u2026')
            browse.connect('clicked', self._pick_cue)
            row.append(browse)
            self.rip_btn = Gtk.Button(label='Rip tracks')
            self.rip_btn.set_sensitive(False)
            self.rip_btn.connect('clicked', self._rip)
            row.append(self.rip_btn)
            box.append(row)

            self.music_note = Gtk.Label(xalign=0, wrap=True)
            box.append(self.music_note)
            self._music(music_status(None))
            return box

        def _music(self, text):
            """The prompt to pick a file is the one line people miss, so it
            gets the accent colour; everything else is a quiet hint."""
            for old in ('vp-hint', 'vp-cue'):
                self.music_note.remove_css_class(old)
            self.music_note.add_css_class(
                'vp-cue' if text == MUSIC_NEEDS_EXE else 'vp-hint')
            self.music_note.set_text(text)

        def _pick_cue(self, _btn):
            dlg = Gtk.FileDialog(title='Select the cue sheet')
            filt = Gtk.FileFilter()
            filt.set_name('Cue sheets')
            filt.add_pattern('*.cue')
            filt.add_pattern('*.CUE')
            store = Gio.ListStore.new(Gtk.FileFilter)
            store.append(filt)
            dlg.set_filters(store)

            def chosen(dialog, result):
                try:
                    self.rip_entry.set_text(dialog.open_finish(result).get_path())
                except GLib.Error:
                    pass
            dlg.open(self, None, chosen)

        def _rip(self, _btn):
            source = self.rip_entry.get_text().strip()
            if not source or not self.core.exe_path:
                return
            gamedir = os.path.dirname(self.core.exe_path)
            self.rip_btn.set_sensitive(False)
            self._log('Ripping from %s' % source)
            last = [-1]

            def progress(track, done, total):
                pct = done * 100 // max(total, 1)
                if pct == last[0]:
                    return
                last[0] = pct
                GLib.idle_add(self._music,
                              'track %02d  %d%%' % (track, pct))

            def finished(error, files):
                GLib.idle_add(self._ripped, error, files)

            rip_in_background(source, gamedir, progress, finished)

        def _ripped(self, error, files):
            if error is None:
                self._log('Ripped %d tracks' % len(files))
            else:
                self._log('Ripping failed: %s' % error)
            self._music(music_status(os.path.dirname(self.core.exe_path)))
            self.rip_btn.set_sensitive(True)

        def _patch_body(self, keys, hint):
            box = Gtk.Box(orientation=VERTICAL, spacing=4)
            note = Gtk.Label(label=hint, xalign=0, wrap=True)
            note.add_css_class('vp-hint')
            box.append(note)
            for key in keys:
                box.append(self._check(key))
            return box

        def _check(self, key):
            label, tip, _sites = BY_KEY[key]
            row = Gtk.Box(orientation=HORIZONTAL, spacing=4)
            check = Gtk.CheckButton(label=label, hexpand=True)
            check.set_active(default_state()[key])
            check.set_sensitive(False)
            row.append(check)
            row.append(self._info(label, tip))
            self.boxes[key] = check
            return row

        def _info(self, title, text):
            prose, rows = describe(text)
            box = Gtk.Box(orientation=VERTICAL, spacing=8)
            margins(box, 12)
            head = Gtk.Label(xalign=0)
            head.set_markup('<b>%s</b>' % GLib.markup_escape_text(title))
            box.append(head)
            if prose:
                body = Gtk.Label(label=prose, xalign=0, wrap=True)
                body.set_max_width_chars(46)
                box.append(body)
            if rows:
                grid = Gtk.Grid(column_spacing=14, row_spacing=2)
                for line, (key, meaning) in enumerate(rows):
                    bold = Gtk.Label(label=key, xalign=0)
                    bold.add_css_class('vp-key')
                    grid.attach(bold, 0, line, 1, 1)
                    grid.attach(Gtk.Label(label=meaning, xalign=0),
                                1, line, 1, 1)
                box.append(grid)

            btn = Gtk.MenuButton(popover=Gtk.Popover(child=box))
            btn.add_css_class('flat')
            btn.add_css_class('vp-info')
            btn.set_valign(Gtk.Align.CENTER)
            btn.set_tooltip_text('What this does')
            theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
            icon = 'help-about-symbolic'
            if theme is not None and theme.has_icon(icon):
                btn.set_icon_name(icon)
            else:
                btn.set_label('\u24d8')
            return btn

        def _log_body(self):
            self.log_view = Gtk.TextView(editable=False, monospace=True,
                                         cursor_visible=False)
            self.log_view.add_css_class('vp-log')
            scroll = Gtk.ScrolledWindow()
            scroll.set_child(self.log_view)
            scroll.set_size_request(-1, 88)
            return scroll

        def _statusbar(self):
            bar = Gtk.Box(orientation=HORIZONTAL, spacing=8)
            bar.add_css_class('vp-statusbar')
            self.status = Gtk.Label(label=NO_FILE, xalign=0, hexpand=True)
            self.status.set_ellipsize(Pango.EllipsizeMode.END)
            self.status.add_css_class('vp-dim')
            bar.append(self.status)

            self.restore_btn = Gtk.Button(label='Restore original')
            self.restore_btn.set_sensitive(False)
            self.restore_btn.connect('clicked', self._restore)
            bar.append(self.restore_btn)

            self.apply_btn = Gtk.Button(label='Apply patches')
            self.apply_btn.add_css_class('suggested-action')
            self.apply_btn.set_sensitive(False)
            self.apply_btn.connect('clicked', self._apply)
            bar.append(self.apply_btn)
            return bar

        # -- behaviour

        def _set_status(self, text, ok=None):
            css = 'vp-dim' if ok is None else 'vp-ok' if ok else 'vp-bad'
            for label in (self.status, self.file_note):
                for old in ('vp-dim', 'vp-ok', 'vp-bad'):
                    label.remove_css_class(old)
                label.add_css_class(css)
                label.set_text(text)

        def _pick(self, _btn):
            dlg = Gtk.FileDialog(title='Select v_on.exe')
            filt = Gtk.FileFilter()
            filt.set_name('Executables')
            filt.add_pattern('*.exe')
            filt.add_pattern('*.EXE')
            store = Gio.ListStore.new(Gtk.FileFilter)
            store.append(filt)
            dlg.set_filters(store)
            dlg.open(self, None, self._picked)

        def _picked(self, dialog, result):
            try:
                path = dialog.open_finish(result).get_path()
            except GLib.Error:
                return
            self.entry.set_text(path)
            self._check_file(path)

        def _check_file(self, path):
            try:
                note, ok = self.core.load(path)
            except OSError as exc:
                self._set_status('Could not read it: %s' % exc, False)
                for check in self.boxes.values():
                    check.set_active(False)
                    check.set_sensitive(False)
                self.apply_btn.set_sensitive(False)
                self.rip_btn.set_sensitive(False)
                return
            self._set_status(note, ok)
            state = default_state()
            for key, check in self.boxes.items():
                check.set_active(state[key] if ok else False)
                check.set_sensitive(ok)
            self.apply_btn.set_sensitive(ok)
            self.restore_btn.set_sensitive(self.core.can_restore())
            # Ripping only needs a folder, so it stays available for a file
            # that cannot be patched - already patched, most likely.
            self.rip_btn.set_sensitive(True)
            self._music(music_status(os.path.dirname(path)))
            if not ok:
                self._log(note)

        def _apply(self, _btn):
            wanted = {k: cb.get_active() for k, cb in self.boxes.items()}
            ok, lines = self.core.apply(wanted)
            for line in lines:
                self._log(line)
            self.restore_btn.set_sensitive(self.core.can_restore())
            if not ok:                      # leave everything as it was
                self._set_status(FAILED, False)
                return
            self.apply_btn.set_sensitive(False)
            for check in self.boxes.values():
                check.set_sensitive(False)
            self._set_status(DONE)

        def _restore(self, _btn):
            for line in self.core.restore():
                self._log(line)
            self._check_file(self.core.exe_path)

        def _log(self, text):
            buf = self.log_view.get_buffer()
            buf.insert(buf.get_end_iter(), text + '\n')
            mark = buf.create_mark(None, buf.get_end_iter(), False)
            self.log_view.scroll_mark_onscreen(mark)
            buf.delete_mark(mark)

    def stylesheet():
        """Cosmetic only, so a failure here must not stop the window."""
        display = Gdk.Display.get_default()
        if display is None:
            return
        provider = Gtk.CssProvider()
        try:
            provider.load_from_data(GTK_CSS)
        except TypeError:                       # older PyGObject signature
            provider.load_from_data(GTK_CSS, len(GTK_CSS))
        add = getattr(Gtk, 'style_context_add_provider_for_display', None) \
            or Gtk.StyleContext.add_provider_for_display
        add(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def start(app):
        try:
            stylesheet()
        except Exception as exc:                # noqa: BLE001
            print('stylesheet skipped: %s' % exc, file=sys.stderr)
        Window(app).present()

    app = Gtk.Application(application_id='org.local.vopatch',
                          flags=Gio.ApplicationFlags.FLAGS_NONE)
    app.connect('activate', start)
    return app.run(None)


# ------------------------------------------------------------------ Tk front


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
            self.btn = ttk.Label(parent, text='\u24d8', style='Card.TLabel',
                                 foreground=PALETTE['mag'],
                                 cursor='question_arrow')
            self.btn.bind('<Button-1>', self.toggle)
            self.btn.bind('<Enter>', lambda _e: self.btn.config(
                foreground=PALETTE['text']))
            self.btn.bind('<Leave>', lambda _e: self.btn.config(
                foreground=PALETTE['mag']))

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
            self._line(frame, self.title, self.app.bold,
                       colour=PALETTE['cyan']).pack(anchor='w', padx=9,
                                                    pady=(7, 0))
            if prose:
                self._line(frame, prose, self.app.small, wrap=320).pack(
                    anchor='w', padx=9, pady=(3, 0))
            if rows:
                table = tk.Frame(frame, background=PALETTE['card'])
                table.pack(anchor='w', padx=9, pady=(7, 0))
                for line, (key, meaning) in enumerate(rows):
                    self._line(table, key, self.app.bold,
                               colour=PALETTE['amber']).grid(
                                   row=line, column=0, sticky='w',
                                   padx=(0, 12))
                    self._line(table, meaning, self.app.small).grid(
                        row=line, column=1, sticky='w')
            tk.Frame(frame, background=PALETTE['card'], height=8).pack()
            win.update_idletasks()
            wide = win.winfo_reqwidth()
            x = self.btn.winfo_rootx() + self.btn.winfo_width() - wide
            x = max(4, min(x, self.btn.winfo_screenwidth() - wide - 4))
            win.wm_geometry('+%d+%d' % (
                x, self.btn.winfo_rooty() + self.btn.winfo_height() + 3))
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

    class App:

        def __init__(self, root):
            self.root = root
            self.core = Patcher()
            self.vars, self.checks = {}, {}
            root.title(TITLE)
            root.minsize(430, 0)
            root.maxsize(1100, root.winfo_screenheight() - 60)
            root.bind_all('<Button-1>', close_info, add='+')
            root.bind_all('<Escape>', close_info, add='+')

            self._styles()

            outer = ttk.Frame(root, style='Ink.TFrame')
            outer.pack(fill='both', expand=True)
            self._statusbar(outer)                  # pinned before the body
            body = self._body(outer)

            self._section(body, 'GAME EXECUTABLE', self._file_body)
            self._section(body, 'ESSENTIAL PATCHES',
                          lambda p: self._patch_body(p, ESSENTIAL,
                                                     ESSENTIAL_HINT),
                          expanded=False)
            self._section(body, 'EXTRA PATCHES',
                          lambda p: self._patch_body(p, EXTRA, EXTRA_HINT),
                          expanded=False)
            self._section(body, 'CD MUSIC', self._music_body,
                          expanded=False)
            self._section(body, 'LOG', self._log_body)
            self._log(INTRO)

        def _body(self, parent):
            """Size to the content, scrolling only if it outgrows the
            screen."""
            holder = ttk.Frame(parent, style='Ink.TFrame')
            holder.pack(fill='both', expand=True)
            self.canvas = tk.Canvas(holder, highlightthickness=0,
                                    borderwidth=0,
                                    background=PALETTE['ink'])
            self.vbar = ttk.Scrollbar(holder, orient='vertical',
                                      style='Vo.Vertical.TScrollbar',
                                      command=self.canvas.yview)
            self.canvas.pack(side='left', fill='both', expand=True)
            self.canvas.configure(yscrollcommand=self.vbar.set)

            self.inner = ttk.Frame(self.canvas, padding=12,
                                   style='Ink.TFrame')
            self.window = self.canvas.create_window((0, 0), window=self.inner,
                                                    anchor='nw')
            self.cap = max(300, parent.winfo_screenheight() - 200)
            self.sized = False
            self.inner.bind('<Configure>', self._fit)
            self.canvas.bind('<Configure>', self._fit)
            for seq in ('<MouseWheel>', '<Button-4>', '<Button-5>'):
                self.canvas.bind_all(seq, self._wheel)
            return self.inner

        def _fit(self, _event=None):
            need = self.inner.winfo_reqheight()
            wide = self.canvas.winfo_width()
            if wide > 1:
                self.canvas.itemconfigure(self.window, width=wide)
            if not self.sized:                      # settle the width once
                self.canvas.configure(width=self.inner.winfo_reqwidth())
                self.sized = True
            self.canvas.configure(
                scrollregion=(0, 0, self.inner.winfo_reqwidth(), need),
                height=min(need, self.cap))
            if need > self.cap:
                self.vbar.pack(side='right', fill='y')
            else:
                self.vbar.pack_forget()
                self.canvas.yview_moveto(0)

        def _wheel(self, event):
            log = getattr(self, 'log_box', None)
            if log is not None and str(event.widget) == str(log):
                return                              # let the log scroll itself
            if self.inner.winfo_reqheight() <= self.cap:
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
            style.configure('Card.TLabel', background=p['card'],
                            foreground=p['text'])
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
                indicatorbackground=[('selected', p['cyan']),
                                     ('!selected', p['ink'])],
                indicatorforeground=[('selected', p['ink'])])

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
            self.dim = p['dim']

        def _section(self, parent, title, build, expanded=True):
            card = ttk.Frame(parent, style='Card.TFrame')
            card.pack(fill='x', pady=(0, 10))

            head = ttk.Frame(card, style='Head.TFrame', padding=(10, 7))
            head.pack(fill='x')
            arrow = ttk.Label(head, style='Head.TLabel',
                              text='\u25be' if expanded else '\u25b8')
            arrow.pack(side='left', padx=(0, 8))
            name = ttk.Label(head, text=title, style='Head.TLabel',
                             font=self.head_font)
            name.pack(side='left')

            inner = ttk.Frame(card, style='Card.TFrame',
                              padding=(14, 10, 12, 12))
            if expanded:
                inner.pack(fill='x')
            build(inner)

            state = {'open': expanded}

            def toggle(_event=None):
                state['open'] = not state['open']
                arrow.config(text='\u25be' if state['open'] else '\u25b8')
                if state['open']:
                    inner.pack(fill='x')
                else:
                    inner.pack_forget()
            for widget in (head, arrow, name):
                widget.bind('<Button-1>', toggle)
            return inner

        def _file_body(self, parent):
            row = ttk.Frame(parent, style='Card.TFrame')
            row.pack(fill='x')
            self.path_var = tk.StringVar()
            ttk.Entry(row, textvariable=self.path_var, state='readonly',
                      style='Vo.TEntry', width=30).pack(
                          side='left', fill='x', expand=True)
            ttk.Button(row, text='Browse\u2026', style='Vo.TButton',
                       command=self._pick).pack(side='left', padx=(8, 0))
            self.file_note = ttk.Label(parent, text=NO_FILE, style='Card.TLabel',
                                       foreground=self.dim, font=self.small,
                                       wraplength=380, justify='left')
            self.file_note.pack(anchor='w', pady=(8, 0))

        def _music_body(self, parent):
            ttk.Label(parent, text=MUSIC_HINT, style='Card.TLabel',
                      foreground=self.dim, font=self.small,
                      wraplength=380, justify='left').pack(anchor='w',
                                                           pady=(0, 8))
            row = ttk.Frame(parent, style='Card.TFrame')
            row.pack(fill='x')
            self.rip_var = tk.StringVar()
            drives = list_devices()
            if drives:
                self.rip_var.set(drives[0])
            ttk.Entry(row, textvariable=self.rip_var, style='Vo.TEntry',
                      width=22).pack(side='left', fill='x', expand=True)
            ttk.Button(row, text='Cue\u2026', style='Vo.TButton',
                       command=self._pick_cue).pack(side='left', padx=(8, 0))
            self.rip_btn = ttk.Button(row, text='Rip tracks',
                                      style='Vo.TButton', state='disabled',
                                      command=self._rip)
            self.rip_btn.pack(side='left', padx=(8, 0))

            self.music_note = ttk.Label(parent, style='Card.TLabel',
                                        font=self.small,
                                        wraplength=380, justify='left')
            self.music_note.pack(anchor='w', pady=(8, 0))
            self._music(music_status(None))

        def _music(self, text):
            """The prompt to pick a file is the one line people miss, so it
            gets the accent colour; everything else is a quiet hint."""
            colour = PALETTE['cyan'] if text == MUSIC_NEEDS_EXE else self.dim
            self.music_note.config(text=text, foreground=colour)

        def _pick_cue(self):
            path = filedialog.askopenfilename(
                title='Select the cue sheet',
                filetypes=[('Cue sheets', '*.cue *.CUE'), ('All files', '*')])
            if path:
                self.rip_var.set(path)

        def _rip(self):
            source = self.rip_var.get().strip()
            if not source or not self.core.exe_path:
                return
            gamedir = os.path.dirname(self.core.exe_path)
            self.rip_btn.state(['disabled'])
            self._log('Ripping from %s' % source)
            last = [-1]

            def progress(track, done, total):
                pct = done * 100 // max(total, 1)
                if pct == last[0]:
                    return
                last[0] = pct
                self.root.after(
                    0, lambda: self._music('track %02d  %d%%' % (track, pct)))

            def finished(error, files):
                self.root.after(0, lambda: self._ripped(error, files))

            rip_in_background(source, gamedir, progress, finished)

        def _ripped(self, error, files):
            if error is None:
                self._log('Ripped %d tracks' % len(files))
            else:
                self._log('Ripping failed: %s' % error)
            self._music(music_status(os.path.dirname(self.core.exe_path)))
            self.rip_btn.state(['!disabled'])

        def _patch_body(self, parent, keys, hint):
            ttk.Label(parent, text=hint, style='Card.TLabel',
                      foreground=self.dim, font=self.small,
                      wraplength=380, justify='left').pack(anchor='w',
                                                           pady=(0, 8))
            for key in keys:
                label, tip, _sites = BY_KEY[key]
                row = ttk.Frame(parent, style='Card.TFrame')
                row.pack(fill='x', pady=1)
                var = tk.BooleanVar(value=default_state()[key])
                check = ttk.Checkbutton(row, text=label, variable=var,
                                        style='Card.TCheckbutton')
                check.state(['disabled'])
                check.pack(side='left')
                Info(row, label, tip, self).btn.pack(side='right',
                                                     padx=(6, 2))
                self.vars[key], self.checks[key] = var, check

        def _log_body(self, parent):
            wrap = tk.Frame(parent, background=PALETTE['line'],
                            borderwidth=0, highlightthickness=0)
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
            bar = ttk.Frame(parent, style='Bar.TFrame', padding=(12, 8))
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

        # -- behaviour

        def _set_status(self, text, ok=None):
            colour = self.dim if ok is None else \
                PALETTE['ok'] if ok else PALETTE['bad']
            font = self.small if ok is None else self.bold
            # the card above shows it in full
            short = text if len(text) <= 52 else text[:51] + '\u2026'
            self.status.config(text=short, foreground=colour, font=font)
            self.file_note.config(text=text, foreground=colour, font=font)

        def _pick(self):
            path = filedialog.askopenfilename(
                title='Select v_on.exe',
                filetypes=[('Executables', '*.exe'), ('All files', '*.*')])
            if path:
                self.path_var.set(path)
                self._check_file(path)

        def _check_file(self, path):
            try:
                note, ok = self.core.load(path)
            except OSError as exc:
                self._set_status('Could not read it: %s' % exc, False)
                for key, check in self.checks.items():
                    self.vars[key].set(False)
                    check.state(['disabled'])
                self.apply_btn.state(['disabled'])
                self.rip_btn.state(['disabled'])
                return
            self._set_status(note, ok)
            state = default_state()
            for key, check in self.checks.items():
                self.vars[key].set(state[key] if ok else False)
                check.state(['!disabled'] if ok else ['disabled'])
            self.apply_btn.state(['!disabled'] if ok else ['disabled'])
            self.restore_btn.state(
                ['!disabled'] if self.core.can_restore() else ['disabled'])
            # Ripping only needs a folder, so it stays available for a file
            # that cannot be patched - already patched, most likely.
            self.rip_btn.state(['!disabled'])
            self._music(music_status(os.path.dirname(path)))
            if not ok:
                self._log(note)

        def _apply(self):
            wanted = {k: v.get() for k, v in self.vars.items()}
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
            self._set_status(DONE)

        def _restore(self):
            for line in self.core.restore():
                self._log(line)
            self._check_file(self.core.exe_path)

        def _log(self, text):
            self.log_box.config(state='normal')
            self.log_box.insert('end', text + '\n')
            self.log_box.see('end')
            self.log_box.config(state='disabled')

    _root = tk.Tk()
    App(_root)
    _root.mainloop()
    return 0


def probe_gtk():
    """None if GTK4 is usable, otherwise why not.

    A missing typelib raises ValueError from require_version, not
    ImportError."""
    try:
        import gi
        gi.require_version('Gtk', '4.0')
        from gi.repository import Gtk
        return None if Gtk else 'gi.repository.Gtk did not load'
    except (ImportError, ValueError) as exc:
        return str(exc)


def probe_tk():
    try:
        import tkinter
        return None if tkinter else 'tkinter did not load'
    except ImportError as exc:
        return str(exc)


USAGE = """vo-patch.py %s - Virtual-On (PC, 1997) patcher

  vo-patch.py                     open the patcher
  vo-patch.py --rip SOURCE DIR    rip the soundtrack; SOURCE is a .cue sheet
                                  or a CD drive, DIR holds v_on.exe
  vo-patch.py --rip               list the drives it can see
  vo-patch.py --selfcheck         validate the patch tables and exit
  vo-patch.py --version

VOPATCH_UI=gtk or =tk picks a toolkit instead of preferring GTK4."""


def selfcheck():
    """Run the import-time table checks and say what they covered.

    The tables are the whole patcher, and nothing else exercises them without
    a copy of the game, so this is what to run after editing one."""
    sites, byte_count = _check_table()
    lines = ['vo-patch.py %s' % VERSION,
             '%d patches, %d sites, %d bytes of the executable touched'
             % (len(BY_KEY), sites, byte_count),
             'expects %d bytes, MD5 %s' % (EXE_SIZE, ORIGINAL_MD5),
             'CD audio blob: %d bytes of code, %d of data, %d placeholders'
             % (len(VOCD_CODE), len(VOCD_DATA), len(VOCD_MAGICS)),
             'lever routine: %d bytes' % len(LEVERS_CODE),
             'write order: %s' % ' '.join(apply_order()),
             'tables OK']
    print('\n'.join(lines))
    return None


def rip_cli(argv):
    """--rip SOURCE GAMEDIR, for scripting or a machine with no display."""
    if len(argv) == 0:
        found = list_devices()
        print('Drives visible here: %s' % (', '.join(found) or 'none'))
        print('Rip one with: vo-patch.py --rip SOURCE GAMEDIR')
        return None
    if len(argv) != 2:
        return 'Usage: vo-patch.py --rip SOURCE GAMEDIR'

    source, gamedir = argv
    seen = [None]

    def progress(track, done, total):
        if seen[0] != track:
            seen[0] = track
            sys.stderr.write('\n')
        sys.stderr.write('\rtrack %02d  %5.1f%%  '
                         % (track, 100.0 * done / max(total, 1)))

    try:
        files = rip(source, outdir_for(gamedir), progress)
    except Exception as exc:
        return '\nRipping failed: %s' % exc
    sys.stderr.write('\n')
    print('%d tracks written to %s' % (len(files), outdir_for(gamedir)))
    return None


def main():
    """GTK4 if it is there, Tk if not. VOPATCH_UI=gtk or =tk forces one."""
    args = sys.argv[1:]
    if '--help' in args or '-h' in args:
        print(USAGE % VERSION)
        return None
    if '--version' in args:
        print(VERSION)
        return None
    if '--selfcheck' in args:
        return selfcheck()
    if '--rip' in args:
        return rip_cli(sys.argv[sys.argv.index('--rip') + 1:])

    forced = os.environ.get('VOPATCH_UI', '').lower()
    gtk_why = 'skipped, VOPATCH_UI=tk' if forced == 'tk' else probe_gtk()
    if gtk_why is None:
        return run_gtk()
    tk_why = 'skipped, VOPATCH_UI=gtk' if forced == 'gtk' else probe_tk()
    if tk_why is None:
        if forced != 'tk':
            print('GTK4 unavailable (%s), using Tk' % gtk_why, file=sys.stderr)
        return run_tk()
    return ('No usable toolkit.\n'
            '  GTK4: %s\n'
            '  Tk:   %s\n'
            'Install either one. Tk is the smaller of the two: python3-tk on '
            'Debian, Ubuntu and Mint, python3-tkinter on Fedora, tk on Arch.'
            % (gtk_why, tk_why))


if __name__ == '__main__':
    sys.exit(main())
