#!/usr/bin/env python3
"""Virtual-On (PC, 1997) patcher. See README.md.

    python3 vo-patch.py                 patch a copy of v_on.exe
    python3 vo-patch.py --rip SRC DIR   rip the soundtrack, no window needed
    python3 vo-patch.py --ddraw DIR     fetch cnc-ddraw into the game folder
    python3 vo-patch.py --netplay DIR   install the UDP netplay DLL
    python3 vo-patch.py --selfcheck     validate the patch tables and exit
    python3 vo-patch.py --version

Version 0.7.6

https://github.com/pairomaniac/vo_patch
"""

import base64
import ctypes
import hashlib
import io
import os
import queue
import re
import shutil
import ssl
import struct
import sys
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

EXE_SIZE = 6650880

ORIGINAL_MD5 = 'a464b0ff32d5bab499f265e45658504e'

# Other builds that turn up. None of them can be patched - they are listed
# only so the patcher can name what it was handed instead of saying "not the
# original". md5 -> (size, name, why).
OTHER_BUILDS = {
    '4c70f780a7f0d98d74be62304fb99021': (
        6649344, 'USA OEM',
        'A different release of the same game. Everything here is written '
        'for the retail disc build.'),
}

RETAIL_HINT = 'Use v_on.exe from a standard retail VIRTUAL-ON CD.'

# GENERATED - do not edit the hex by hand.
#
# Assembled from the sources in asm/. To change any of them: edit the source
# and run
#     python3 asm/build.py
# which rewrites everything between the markers below. CI runs
# `asm/build.py --check` on every push and fails if the two have drifted.
#
# padxinput.asm is assembled at a fixed org and padded to a fixed length: five
# addresses inside it are named from outside, and levers.asm is written at the
# site immediately after it. The source pins all of that.

# PADX BLOB BEGIN
PADX_CODE = bytes.fromhex(
    '6822836000e8ef00000083c404e952aee3ff684a836000e8dd00000083c404e9'
    'd34cfbff0000000000000000000000000000000000000000e807000000ff2590'
    'd5650300609ce80502000083f801767431f683fe02736d6870cb650356ff1540'
    'cb650385c0755a0fb71d74cb65038d14b584cb65030fb72a66891a31ff83ff02'
    '733f8d0cbd278160000fb70189da21c221e839c27428b80001000085d2750b80'
    '7903007419b8010100006a000fb651025250ff35585fae01ff156cd5650347eb'
    'bc46eb8e9d61c310007200001020000000000000000000000000000000000000'
    '000000000000000000000000000000000000000000000000005589e583ec0453'
    '56578b5d08c745fc00000000e83f01000083f801743c6844cb6503ff33ffd085'
    'c0752fc745fc010000000fb70548cb6503a90010000074068b5318c602800fb7'
    '0548cb6503a92000000074068b5324c602808b4320ffd0837dfc000f84410100'
    '0031f683fe0c0f839c000000833d9435ae01047512833d9036ae01087c09833d'
    '9036ae010c7e0f83fe04747683fe05747183fe077f728b53040fb604722de000'
    '0000725e83f81073598d3cc51b4d62000fb6070fb757028b4f0483f800741783'
    'f801741f83f80274270fb68248cb650339c87729eb2c0fbf8248cb650339c87c'
    '1ceb1f0fbf8248cb650339c87f0feb120fb70548cb650385c87502eb05e82500'
    '000046e95bffffff31f683fe040f838f0000000fb70548cb65030fa3f07305e8'
    '0300000046ebe38b53100fb60c32f7d18b53080fb70221c86689028b53140fb6'
    '0c32f7d18b530c0fb70221c8668902c3a140cb650385c075385631f683fe0373'
    '258b04b50783600050ff1504d5650385c0750346ebe6681383600050ff1508d5'
    '650385c07505b801000000a340cb65035ec30000000000000000000000000000'
    '00005f5e5bc9c372836000808360008e83600058496e70757447657453746174'
    '65000000000070146503c414cb01c614cb01903665009d3665008104bf0060cb'
    '6503743044005704bf000100000088146503e43eee01e63eee0108eb6b0015eb'
    '6b00b10dad0161cb6503edce5b00940dad0178696e707574315f342e646c6c00'
    '78696e707574315f332e646c6c0078696e707574395f315f302e646c6c00'
)
# PADX BLOB END

# levers.asm replaces the input tick's epilogue, so its site sits inside the
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

# introwait.asm is assembled at a fixed org too. It goes in the .rdata raw
# padding after kbpage.asm, not in a zero run inside .rdata: those are live
# tables and float constants, where a zero is a value.

# INTROWAIT BLOB BEGIN
INTROWAIT_CODE = bytes.fromhex(
    'e82f97fcff6a006a006a006a00ff742414ff1590d5650385c07509e80a000000'
    '85c075dcff258cd56503a180cb650385c0740f83f801743a6a08ffd0b8010000'
    '00c368e5e96300ff1504d5650385c0741768f2e9630050ff1508d5650385c074'
    '07a380cb6503ebd0c70580cb65030100000031c0c36b65726e656c33322e646c'
    '6c00536c65657000'
)
# INTROWAIT BLOB END

# The gamepad tables and both dialogs are data, packed by asm/padtables.py and
# asm/dialogs.py from one description each. The pointers between them are
# computed there.

# PADTABLES BLOB BEGIN
PAD_COND = bytes.fromhex(
    '0200000000100000020000000020000002000000004000000200000000800000'
    '0200000000010000020000000002000003000200400000000300030040000000'
    '010006001027000000000600f0d8ffff00000400f0d8ffff0100040010270000'
    '01000a001027000000000a00f0d8ffff00000800f0d8ffff0100080010270000'
)

PAD_BINDS = bytes.fromhex(
    '9b4b6200e00000009d4b6200e10000009f4b6200e2000000a14b6200e3000000'
    'a34b6200e4000000a64b6200e5000000a94b6200e6000000ac4b6200e7000000'
    'af4b6200e8000000b54b6200e9000000bd4b6200ea000000c54b6200eb000000'
    'ce4b6200ec000000d44b6200ed000000dc4b6200ee000000e44b6200ef000000'
)

PAD_NAMES = bytes.fromhex(
    '41004200580059004c42005242004c54005254004c53205570004c5320446f77'
    '6e004c53204c656674004c5320526967687400525320557000525320446f776e'
    '005253204c656674005253205269676874004b6579626f61726420285265616c'
    '290047616d65706164202858496e70757429005477696e2d737469636b202858'
    '496e7075742900'
)

PAD_DEVLIST = bytes.fromhex(
    'ed4b6200fd4b62000e4c62000000000000000000000000000000000000000000'
)
# PADTABLES BLOB END

# DIALOGS BLOB BEGIN
EXTRAS_TPL = bytes.fromhex(
    'c000c88000000000080000000000d40068000000000045007800740072006100'
    '7300000008004d0053002000530061006e007300200053006500720069006600'
    '00000000030001500000000010000e0038000c00479cffff80004e006f002000'
    '730068006f00740000000000030001500000000050000e0024000c005b9cffff'
    '80005300450000000000000003000150000000007c000e0024000c005c9cffff'
    '800043004400000000000000000001500000000010002c0032000e00619cffff'
    '80004b0069006c006c0020003100500000000000000001500000000046002c00'
    '32000e00629cffff80004b0069006c006c002000320050000000000000000150'
    '000000007c002c0048000e00679cffff8000530063006f00720065006b006500'
    '6500700069006e00670000000000000000000150000000001000540048000e00'
    '419cffff800051007500690074002000500072006f006700720061006d000000'
    '0000000000000150000000009200540032000e000200ffff800043006c006f00'
    '7300650000000000000000000000000000000000000000000000000000000000'
    '0000000000000000000000000000000000000000000000000000000000000000'
    '000000000000000000000000'
)

EXTRAS_DATA = bytes.fromhex(
    '5553455233322e444c4c004469616c6f67426f78496e64697265637450617261'
    '6d410000d82f6500479c00004ccc6b005b9c000030f463005c9c0000312f3100'
    '312f3200312f3300312f3400312f3500'
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

# TIMER BLOB BEGIN
TIMER_CODE = bytes.fromhex(
    '68624e5f00ff1504d56503686c4e5f0050ff1508d5650385c074046a01ffd0e9'
    'ce2affff77696e6d6d2e646c6c0074696d65426567696e506572696f6400'
)
# TIMER BLOB END

# debugbox.asm assembles as one 364-byte run, but the patch writes it as two
# sites: the byte at 0x1f42d7 is alignment padding in front of the dialog
# procedure and has never been written, so build.py drops it rather than
# assume what the original file has there.

# DEBUGBOX BLOB BEGIN
DEBUGBOX_HOOK = bytes.fromhex(
    '558bec53817d0c000100007547817d107a000000753e68e8e86300ff1504d565'
    '0368f3e8630050ff1508d5650385c0741c8bd86a0068d84e5f00ff750868b0fa'
    '65036a00ff15a0d4650350ffd333c05b5dc210005b5de98019fdff'
)

DEBUGBOX_PROC = bytes.fromhex(
    '558bec5356578b450c3d10010000756fbe0ce96300bf030000008b068b0033c9'
    '83f8010f94c151ff7604ff7508ff1544d5650383c6084f75e168e8030000ff75'
    '08ff154cd565038bd8be24e96300bf05000000566a00684301000053ff152cd5'
    '650383c6044f75eba1d0846c00486a0050684e01000053ff152cd56503eb683d'
    '1101000075688b45100fb7c8c1e81081f9e8030000752a48755468e8030000ff'
    '7508ff154cd565036a006a00684701000050ff152cd5650305559c00008bc8eb'
    '1283f90275316a00ff7508ff1538d56503eb146a00516811010000ff35585fae'
    '01ff156cd56503b801000000eb0233c05f5e5b5dc2100081f9419c0000750f89'
    'cb6a00ff7508ff1538d5650389d9ebc3'
)
# DEBUGBOX BLOB END

# KBPAGE BLOB BEGIN
KBPAGE_CODE = bytes.fromhex(
    '833dac6bbf0001750e833d40156503007505e91f8ee5ffe9728ee5ff90909090'
    '6a01ff35ac6bbf00e8aa8fe5ff83c408e92d8fe5ff'
)
# KBPAGE BLOB END

# Each site: (offset, original, patched).

FEATURES = [
    ('sound', 'Sound fixes',
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
     'Section\tThe music routine goes in a section of its own rather than into spare bytes, so the file grows by about 3 KB.\n'
     'Calls\tThe game\'s 37 calls to the CD audio function are pointed at that routine directly, so a DirectDraw wrapper loaded alongside cannot take them over.', [
         (0x001c76d4, '0f840a000000', '909090909090')]),

    ('nocpucheck', 'Skip processor check',
     'The game will not start on a modern CPU without this. Same as\n'
     'ProcessorCheck=Off in v_on.ini, but with no ini needed, and it takes\n'
     'the MMX, Pentium and vendor checks with it.', [
         (0x00107930, '830dc884bf0001', '90909090909090')]),
    ('framerate', 'Fix frame rate (60 FPS)',
     'Three fixes, all for the game not running at full speed.\n'
     '\n'
     'Timer resolution\tWithout it the game runs at about 70 per cent speed on Windows 2000 and later. Not needed under Wine.\n'
     'Motion value\tMakes Motion= in v_on.ini work and stick. It is a divisor: 1 draws every frame, 2 draws half.\n'
     'Motion Type\tThe F5 radios wrote 3 and 2, so 60 fps was unreachable from the interface. They write 2 and 1 now, and read 30 FPS and 60 FPS.', [
         (0x001f423e, '00' * len(TIMER_CODE), TIMER_CODE.hex()),
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
         (0x001f427c, '00' * len(DEBUGBOX_HOOK), DEBUGBOX_HOOK.hex()),
         (0x001f42d8, '00' * len(DEBUGBOX_PROC), DEBUGBOX_PROC.hex()),
         (0x0023dce8, '00' * len(EXTRAS_DATA), EXTRAS_DATA.hex()),
         # over the menu resource this patch has just orphaned
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
          EXTRAS_TPL.hex())]),
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
     'Three profiles on the F7 screen, for both players.\n'
     '\n'
     'Keyboard (Real)\tthe game\'s two-lever keyboard scheme\n'
     'Gamepad (XInput)\ttwelve named actions, bind them yourself\n'
     'Twin-stick (XInput)\tthe arcade levers, nothing to bind\n'
     '\n'
     'Disables Keyboard (Simple) for the time being: it is the only page\n'
     'that binds all twelve actions, so the gamepad has to take it.\n'
     '\n'
     'A\tAccept\n'
     'Select\tCamera\n'
     'Start\tPause\n'
     'D-pad\tMove, and menu navigation\n'
     '\n'
     'A skips the intro movie, which runs under a message loop of its\n'
     'own. Start does not: the game ignores it while the movie plays.\n'
     '\n'
     'Twin-stick makes each thumbstick a lever: both the same way walks,\n'
     'opposite ways turns, apart jumps, together crouches. The triggers\n'
     'fire, both at once for the centre weapon, and LB/RB dash.\n'
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
         # PeekMessage: Start and A must reach the game while it is paused,
         # where the input tick does not run
         (0x001c530e, 'ff1590d56503', 'e88521040090'),
         # two pads are separate devices, so 2P may reuse 1P's inputs
         (0x000971bd, '0f8558000000', 'e95900000090'),
         # device list: Keyboard (Real), Gamepad (XInput), Twin-stick
         (0x0026c218, 'ecd36600d4d36600c0d36600b4d3660090d3660080d3660068d3660058d36600',
          PAD_DEVLIST.hex()),
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
         # the intro movie blocks the message loop in GetMessageA, where the
         # pump stub does not run. Poll from the call itself instead, so a
         # pad press reaches the window procedure and Space skips the movie.
         (0x0023dd70, '00' * len(INTROWAIT_CODE), INTROWAIT_CODE.hex()),
         (0x001c52ac, 'ff158cd56503', 'e8bf8a070090'),
         # what each pad input is and what it is called
         (0x0022411b, '00' * len(PAD_COND), PAD_COND.hex()),
         (0x00223c43, '00' * len(PAD_BINDS), PAD_BINDS.hex()),
         (0x00223f9b, '00' * len(PAD_NAMES), PAD_NAMES.hex()),
         # the routine itself: entry stubs, pump stub, tick, blocks
         (0x00207460, '00' * len(PADX_CODE), PADX_CODE.hex()),
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


class RipCancelled(Exception):
    """Raised out of the progress callback to stop a rip in its tracks.

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
NETPLAY_SRC_SHA = 'dc9ca84c78372fc59ff39217ce31a10139e526584c8bb42cbf5c256ab3636f3b'
NETPLAY_DLL_Z = (
    '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'
)
# --- end netplay blob ---

NETPLAY_NAME = 'dpctrl.dll'
NETPLAY_KEEP = 'dpctrl.dll.stock'


def netplay_dll():
    """The DLL bytes, unpacked from the blob."""
    if not NETPLAY_DLL_Z:
        raise ValueError('this build carries no netplay DLL')
    return zlib.decompress(base64.b64decode(NETPLAY_DLL_Z))


def netplay_status(gamedir):
    """'ours', 'stock', or None if there is nothing to look at."""
    if not gamedir:
        return None
    path = os.path.join(gamedir, NETPLAY_NAME)
    if not os.path.exists(path):
        return None
    if os.path.exists(os.path.join(gamedir, NETPLAY_KEEP)):
        return 'ours'
    return 'stock'


def install_netplay(gamedir):
    """Put our DLL in place, keeping the game's own copy beside it."""
    path = os.path.join(gamedir, NETPLAY_NAME)
    keep = os.path.join(gamedir, NETPLAY_KEEP)
    if not os.path.exists(path) and not os.path.exists(keep):
        raise ValueError('no %s here - is this the game folder?'
                         % NETPLAY_NAME)
    if os.path.exists(path) and not os.path.exists(keep):
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
        'User-Agent': 'vo-patch/%s' % VERSION})
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
            with zf.open(name) as src, open(dest, 'wb') as out:
                shutil.copyfileobj(src, out)
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
    for key in apply_order():
        if not wanted.get(key):
            continue
        sites = BY_KEY[key][2]
        try:
            if sites is not None:
                apply_feature(buf, sites)
            else:
                apply_dinput(buf)
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
        with open(path, 'rb') as fh:
            data = fh.read()
        self.exe_path = path
        digest = hashlib.md5(data).hexdigest()
        if digest == ORIGINAL_MD5:
            return 'READY - unmodified disc original', True

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

        try:
            buf, applied, skipped = apply_selected(buf, wanted)
        except PatchFailed as exc:
            return False, [str(exc), 'Nothing written.']
        except Exception as exc:             # a bug in here, not a bad file
            return False, ['Patching failed: %s' % exc, 'Nothing written.']
        for key, why in skipped:
            log.append('Skipped %s: %s' % (BY_KEY[key][0], why))

        if not applied:
            if skipped:
                log.append('%s was the only patch selected and its call site '
                           'was not found. Nothing written.'
                           % BY_KEY[skipped[0][0]][0])
            else:
                log.append('No patches selected. Nothing written.')
            return False, log

        if not self._backup(self.exe_path, log):
            return False, log + ['Nothing written.']
        try:
            with open(self.exe_path, 'wb') as fh:
                fh.write(buf)
        except OSError as exc:
            return False, log + ['Write failed: %s' % exc]
        log += ['  %s' % BY_KEY[key][0] for key in applied]
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
        """Copy the original aside. False means it did not happen and nothing
        should be written: a read-only folder or a locked file would otherwise
        leave a patched game with no way back."""
        bak = path + '.bak'
        if os.path.exists(bak):
            return True
        try:
            shutil.copy(path, bak)
        except OSError as exc:
            log.append('Backup failed for %s: %s' % (path, exc))
            return False
        log.append('Backup: %s' % bak)
        return True


def describe(text):
    """Split a description into prose and any 'key<TAB>meaning' rows.

    A blank line starts a paragraph; the breaks stay in the prose so it
    can go into one wrapped label."""
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

# The version is in the title because it is the only place a Windows
# user who double-clicked the exe can see it, and it is the first
# thing worth knowing about a bug report.
TITLE = 'Virtual-On patcher %s' % VERSION
INTRO = 'Select an unmodified v_on.exe.'
NO_FILE = 'No file selected'
ESSENTIAL_HINT = ('Fixes for what is broken on modern systems. Leave these '
                  'on unless you have a reason not to.')
EXTRA_HINT = 'Optional. Untick what you do not want.'

# Not a patch and not bundled: a separate download that does the things a
# byte edit cannot, so it sits under the list rather than in it.
EXTRA_LINK = ('Resolution and windowing', 'cnc-ddraw',
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
NETPLAY_NOTE = ('The stock game finds opponents by shouting on the local '
                'network, which no router forwards. This replaces its '
                'DirectPlay layer with plain UDP: the host opens a port, '
                'the other player types the address. The original '
                'dpctrl.dll is kept as dpctrl.dll.stock.')
NETPLAY_PORT = 'Host forwards UDP 47624. The joining side needs nothing.'
NETPLAY_NEEDS_EXE = 'Pick v_on.exe first: this replaces a file beside it.'
NETPLAY_IN = 'Installed. Both players need this, and the same patches.'
NETPLAY_OUT = 'Original dpctrl.dll restored.'
DDRAW_NEEDS_EXE = 'Pick v_on.exe first: cnc-ddraw goes in the same folder.'
DDRAW_LOCKED = ('Close the game first: Windows will not let the patcher '
                'replace a DLL that is loaded.')

MUSIC_HINT = ('Rips the soundtrack to music\\ beside the game, where the '
              'No disc required patch reads it. Source: a cue sheet or a CD '
              'drive. About 320 MB.')

MUSIC_TIP = ('Rip before or after patching, it makes no difference, and '
             'Restore original leaves the tracks alone.\n'
             '\n'
             'Cue sheet\tExact, and needs no drive. Sector offsets come from '
             'the sheet.\n'
             'CD drive\tRead raw. A cdemu device behaves like a physical '
             'one.\n'
             'Result\t26 files, music\\track02.wav to track27.wav. Track 1 '
             'is the data track.')

DONE = 'Done. Restore the original to change your selection.'
FAILED = 'Nothing was written - see the log.'


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
            self.btn = ttk.Label(parent, text='\u24d8', style='Card.TLabel',
                                 foreground=PALETTE['dim'],
                                 cursor='question_arrow')
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
                 radius=4.0, line=1.4, corners='nw ne sw se', grow=''):
        """A rounded rectangle, which is how the checkboxes are drawn. It
        works by coverage rather than by pixels, each point blending by its
        distance to the shape's edge, because Tk has no drawing API past
        put() and its -subsample does not average.

        Only fixed-size widgets get this. A ttk image element with a border
        re-composites its nine-patch on every expose, in software, which
        made resizing the window ten times slower when the cards used one.

        clam has no border radius and its checkbox is a flat square with two
        settable colours, so anything rounded has to be an image."""
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
                for (x0, y0), (x1, y1) in (TICK if tick else ()):
                    vx, vy = x1 - x0, y1 - y0
                    along = max(0.0, min(1.0, ((x - x0) * vx + (y - y0) * vy)
                                         / (vx * vx + vy * vy)))
                    ex, ey = x - x0 - vx * along, y - y0 - vy * along
                    px = _blend(px, tick,
                                cover((ex * ex + ey * ey) ** 0.5 - 1.1))
                row.append(px)
            rows.append('{%s}' % ' '.join(row))
        img.put(' '.join(rows))
        return img

    def _hint(parent, text, colour, font, pady=0):
        """The quiet explanatory line under a section heading; four of the
        five cards have one and they only differ in their text.

        The width is taken from a holder frame rather than the card body.
        A ttk frame's winfo_width() counts its own padding, so wrapping to
        that made every hint 26px wider than the space it had and clipped
        the last word against the card edge. An empty frame filled to the
        content area measures it exactly.

        Packs itself, because the holder is nobody else's business."""
        holder = ttk.Frame(parent, style='Card.TFrame')
        holder.pack(fill='x', pady=pady)
        label = ttk.Label(holder, text=text, style='Card.TLabel',
                          foreground=colour, font=font, justify='left')

        def fit(_event=None):
            width = holder.winfo_width()
            if width > 1:
                label.configure(wraplength=max(140, width - 2))
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

    class App:

        def __init__(self, root):
            self.root = root
            self.core = Patcher()
            self.vars, self.checks = {}, {}
            self._corner_cache = {}
            self._bodies = []
            self._rip_thread, self._rip_dir = None, None
            self._cancel_rip = False
            root.title(TITLE)
            root.minsize(430, 0)
            root.maxsize(1100, root.winfo_screenheight() - 60)
            root.bind_all('<Button-1>', close_info, add='+')
            root.bind_all('<Escape>', close_info, add='+')
            root.protocol('WM_DELETE_WINDOW', self._close)

            self._styles()

            outer = ttk.Frame(root, style='Ink.TFrame')
            outer.pack(fill='both', expand=True)
            self._statusbar(outer)                  # pinned before the body
            body = self._body(outer)

            self._section(body, 'GAME EXECUTABLE', self._file_body)
            # Open: collapsed, the first screen is a file box and an empty
            # log, and the patch list is the point of the window.
            self._section(body, 'ESSENTIAL PATCHES',
                          lambda p: self._patch_body(p, ESSENTIAL,
                                                     ESSENTIAL_HINT))
            self._section(body, 'EXTRA PATCHES',
                          lambda p: self._patch_body(p, EXTRA, EXTRA_HINT,
                                                     EXTRA_LINK),
                          expanded=False)
            self._section(body, 'CD MUSIC', self._music_body,
                          expanded=False)
            self._section(body, 'LOG', self._log_body)
            self._log(INTRO)

            # Width is settled here rather than on the canvas's first
            # <Configure>, which arrives while the sections are still being
            # built and measures whatever exists at that point.
            # Height allows for every section open, capped, so expanding one
            # scrolls instead of moving the window. Measuring the collapsed
            # content gives a window barely taller than the headers.
            for body, shown in self._bodies:
                if not shown:
                    body.pack(fill='x')
            root.update_idletasks()
            full = self.inner.winfo_reqheight()
            # Width has to come from here too, for the same reason: a
            # collapsed section still has to fit when it is opened, and the
            # canvas forces its content to the window width rather than
            # scrolling sideways, so anything wider is cut off. The CD music
            # row is the widest thing in the window and starts collapsed.
            wide = self.inner.winfo_reqwidth()
            for body, shown in self._bodies:
                if not shown:
                    body.pack_forget()
            root.update_idletasks()
            self.canvas.configure(width=wide, height=min(full, self.cap))
            # the minimum has to leave room for the scrollbar as well
            root.minsize(wide + self.vbar.winfo_reqwidth(), 320)
            self._fit()

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
            # The bar is packed first so pack reserves its width. The other
            # way round the canvas expands into the whole row and the bar is
            # squeezed off the edge at the minimum window width.
            self.vbar.pack(side='right', fill='y')
            self.canvas.pack(side='left', fill='both', expand=True)
            self.canvas.configure(yscrollcommand=self.vbar.set)

            self.inner = ttk.Frame(self.canvas, padding=12,
                                   style='Ink.TFrame')
            self.window = self.canvas.create_window((0, 0), window=self.inner,
                                                    anchor='nw')
            # Roughly fifty lines of text. Past that the content scrolls
            # instead of the window growing, so it does not resize under the
            # cursor.
            row = self.small.metrics('linespace')
            self.cap = min(max(300, parent.winfo_screenheight() - 160), row * 48)
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
            style.configure('Vo.Vertical.TScrollbar', background=p['card'],
                            arrowcolor=p['dim'], **edges)
            style.map('Vo.Vertical.TScrollbar',
                      background=[('active', p['line'])])

            self._draw_indicator(style, p)

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

        def _corners(self, parent, colour, spots, pad):
            """Fake a radius with four small images pinned to the corners.
            Fixed size, so Tk blits them; a stretched nine-patch would be
            recomposited on every expose instead, which is ten times dearer.

            place() measures from a ttk frame's content area, so the padding
            has to be subtracted or the corners sit inside the card."""
            left, top, right, bottom = pad
            for spot in spots:
                key = (colour, spot)
                if key not in self._corner_cache:
                    self._corner_cache[key] = _rounded(
                        18, 18, PALETTE['ink'], colour, PALETTE['line'],
                        radius=9, line=1.0, corners=spot,
                        grow=('s' if 'n' in spot else 'n')
                             + ('e' if 'w' in spot else 'w'))
                tk.Label(parent, image=self._corner_cache[key], borderwidth=0,
                         highlightthickness=0).place(
                    relx=0 if 'w' in spot else 1,
                    rely=0 if 'n' in spot else 1,
                    x=-left if 'w' in spot else right,
                    y=-top if 'n' in spot else bottom,
                    anchor=spot)

        def _draw_indicator(self, style, p):
            """Swap clam's indicator for drawn images. Keep the references:
            Tk does not own them, and a collected image leaves a blank box."""
            try:
                self._boxes = tuple(
                    _gap(box, 8, p['card']) for box in (
                        _rounded(16, 16, p['card'], p['ink'], p['line']),
                        _rounded(16, 16, p['card'], p['cyan'], p['cyan'],
                                 tick=p['ink']),
                        _rounded(16, 16, p['card'], p['ink'], p['card']),
                        _rounded(16, 16, p['card'], p['line'], p['line'],
                                 tick=p['dim'])))
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
                style.configure('Card.TCheckbutton', padding=(0, 3, 0, 3))
            except tk.TclError:
                pass            # keep clam's square rather than no box at all

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

            self._corners(head, PALETTE['head'], ('nw', 'ne'), (10, 7, 10, 7))
            inner = ttk.Frame(card, style='Body.TFrame',
                              padding=(14, 10, 12, 12))
            self._bodies.append((inner, expanded))
            if expanded:
                inner.pack(fill='x')
            build(inner)
            self._corners(inner, PALETTE['card'], ('sw', 'se'),
                          (14, 10, 12, 12))

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
            self.file_note = _hint(parent, NO_FILE, self.dim, self.small,
                                   pady=(8, 0))

            # Only packed when a file was rejected. "Not the original" on its
            # own leaves nothing to act on, so show both files side by side
            # and say what to do about it.
            self.mismatch = ttk.Frame(parent, style='Card.TFrame')
            wrap = tk.Frame(self.mismatch, background=PALETTE['line'])
            wrap.pack(fill='x', pady=(8, 0))
            inner = tk.Frame(wrap, background=PALETTE['ink'])
            inner.pack(fill='x', padx=1, pady=1)
            self.rows = []
            for colour in (self.dim, PALETTE['bad']):
                row = tk.Label(inner, text='', font=self.mono, anchor='w',
                               justify='left', padx=8, pady=2,
                               background=PALETTE['ink'], foreground=colour)
                row.pack(fill='x')
                self.rows.append(row)
            self.mismatch_why = _hint(self.mismatch, '', self.dim, self.small,
                                      pady=(6, 0))
            self.mismatch_hint = _hint(self.mismatch, '', self.dim,
                                       self.small, pady=(4, 0))

        def _compare(self, report):
            """Show or hide the mismatch box.

            Amber for a file that is simply the wrong one, red for a file
            that looks damaged - the advice differs, so the colour should
            too."""
            if not report:
                self.mismatch.pack_forget()
                return
            colour = PALETTE['amber' if report['level'] == 'warn' else 'bad']
            for row, (name, size, digest) in zip(self.rows, report['rows']):
                row.config(text='%-10s%11s B  %s'
                                % (name, '{:,}'.format(size), digest[:12]))
            self.rows[1].config(foreground=colour)
            self.mismatch_why.config(text=report['why'], foreground=colour)
            self.mismatch_hint.config(text=report['hint'])
            self.mismatch.pack(fill='x')

        def _music_body(self, parent):
            _hint(parent, MUSIC_HINT, self.dim, self.small, pady=(0, 8))
            row = ttk.Frame(parent, style='Card.TFrame')
            row.pack(fill='x')
            # The bubble is packed first so pack reserves its width before
            # the entry claims what is left, the same reason the scrollbar
            # goes before the canvas.
            Info(row, 'CD MUSIC', MUSIC_TIP, self).btn.pack(side='right',
                                                            padx=(6, 0))
            ttk.Label(row, text='Source', style='Card.TLabel',
                      font=self.small).pack(side='left', padx=(0, 8))
            self.rip_var = tk.StringVar()
            drives = list_devices()
            if drives:
                self.rip_var.set(drives[0])
            # Small on purpose: it expands into whatever the row has spare,
            # and this row is the widest thing in the window, so a larger
            # request only widens the window.
            ttk.Entry(row, textvariable=self.rip_var, style='Vo.TEntry',
                      width=12).pack(side='left', fill='x', expand=True)
            ttk.Button(row, text='Browse\u2026', style='Vo.TButton',
                       command=self._pick_cue).pack(side='left', padx=(8, 0))
            self.rip_btn = ttk.Button(row, text='Rip tracks',
                                      style='Vo.TButton', state='disabled',
                                      command=self._rip)
            self.rip_btn.pack(side='left', padx=(8, 0))

            self.music_note = _hint(parent, '', self.dim, self.small,
                                    pady=(8, 0))
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
            if not source:
                self._log('No source. Give a cue sheet or a drive.')
                return
            if not self.core.exe_path:
                self._log(MUSIC_NEEDS_EXE)
                return
            # Captured now: the exe can be changed from under a running rip,
            # and the finished message has to name where the files went.
            self._rip_dir = os.path.dirname(self.core.exe_path)
            self.rip_btn.state(['disabled'])
            self._log('Ripping from %s' % source)
            self._cancel_rip = False
            self._ripq = queue.Queue()
            last = [-1]

            def progress(track, done, total):
                # Runs on the worker. Raising here unwinds through
                # WavWriter's context manager, which throws the partial
                # track away rather than leaving a short but valid file.
                if self._cancel_rip:
                    raise RipCancelled('cancelled')
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
            """Drain the worker's queue on the UI thread.

            Tk is not safe to call from another thread, and after() from one
            hits a destroyed interpreter if the window is closed mid-rip."""
            try:
                while True:
                    message = self._ripq.get_nowait()
                    if message[0] == 'progress':
                        self._music('Track %02d  %d%%' % message[1:])
                    else:
                        self._ripped(message[1], message[2])
                        return
            except queue.Empty:
                pass
            self.root.after(100, self._poll_rip)

        def _ripped(self, error, files):
            if isinstance(error, RipCancelled):
                self._log('Ripping cancelled')
            elif error is not None:
                self._log('Ripping failed: %s' % error)
            else:
                self._log('Ripped %d tracks to %s'
                          % (len(files), outdir_for(self._rip_dir)))
            self._music(music_status(self._rip_dir))
            self.rip_btn.state(['!disabled'])

        def _close(self):
            """Stop a running rip before the interpreter goes away."""
            thread = getattr(self, '_rip_thread', None)
            if thread is not None and thread.is_alive():
                self._cancel_rip = True
                thread.join(1.5)
            self.root.destroy()

        def _link_row(self, parent, label, name, url, note):
            """A suggestion under a patch list, not a patch: nothing here is
            ticked or written by Apply. Kept visually quieter for that
            reason, but it does get its own button, since fetching it by
            hand is the step people stall on."""
            row = ttk.Frame(parent, style='Card.TFrame')
            row.pack(fill='x', pady=(10, 1))
            # Same font as a patch row's label, so the eye reads it as one
            # more entry in the list rather than a footnote. What it does
            # comes first; whose project it is follows, in the link colour.
            ttk.Label(row, text=label, style='Card.TLabel',
                      foreground=PALETTE['text']).pack(side='left')
            link = tk.Label(row, text=name, cursor='hand2',
                            background=PALETTE['card'],
                            foreground=PALETTE['cyan'])
            link.pack(side='left', padx=(6, 0))
            link.bind('<Button-1>', lambda _e: webbrowser.open(url))
            link.bind('<Enter>',
                      lambda _e: link.config(foreground=PALETTE['cyan_hi']))
            link.bind('<Leave>',
                      lambda _e: link.config(foreground=PALETTE['cyan']))
            # One button, because Install and Remove are never both useful:
            # which one it is follows whether ddraw.dll is beside the game.
            self.ddraw_btn = ttk.Button(row, text='Install',
                                        style='Vo.TButton',
                                        command=self._ddraw_click)
            self.ddraw_btn.pack(side='right', padx=(6, 2))
            _hint(parent, note, self.dim, self.small, pady=(2, 0))
            _hint(parent, DDRAW_WINE, PALETTE['amber'], self.small,
                  pady=(2, 0))
            self.ddraw_note = _hint(parent, '', self.dim, self.small,
                                    pady=(2, 2))
            self.ddraw_installed = False

        def _netplay_row(self, parent):
            row = ttk.Frame(parent, style='Card.TFrame')
            row.pack(fill='x', pady=(10, 1))
            ttk.Label(row, text=NETPLAY_LABEL, style='Card.TLabel',
                      foreground=PALETTE['text']).pack(side='left')
            self.net_btn = ttk.Button(row, text='Install',
                                      style='Vo.TButton',
                                      command=self._netplay_click)
            self.net_btn.pack(side='right', padx=(6, 2))
            _hint(parent, NETPLAY_NOTE, self.dim, self.small, pady=(2, 0))
            _hint(parent, NETPLAY_PORT, PALETTE['amber'], self.small,
                  pady=(2, 0))
            self.net_note = _hint(parent, '', self.dim, self.small,
                                  pady=(2, 2))
            self.net_installed = False

        def _netplay_sync(self):
            gamedir = self._ddraw_dir()
            self.net_installed = netplay_status(gamedir) == 'ours'
            self.net_btn.config(
                text='Remove' if self.net_installed else 'Install')

        def _netplay_click(self):
            gamedir = self._ddraw_dir()
            if not gamedir:
                self.net_note.config(text=NETPLAY_NEEDS_EXE,
                                     foreground=PALETTE['bad'])
                return
            try:
                if self.net_installed:
                    remove_netplay(gamedir)
                    self.net_note.config(text=NETPLAY_OUT,
                                         foreground=self.dim)
                    self._log('netplay: original dpctrl.dll restored')
                else:
                    install_netplay(gamedir)
                    self.net_note.config(text=NETPLAY_IN,
                                         foreground=PALETTE['ok'])
                    self._log('netplay: UDP dpctrl.dll installed')
            except (OSError, ValueError) as exc:
                self.net_note.config(text=str(exc), foreground=PALETTE['bad'])
                self._log('netplay: %s' % exc)
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
                self._log('cnc-ddraw: %s' % exc)
            else:
                self.ddraw_note.config(text=DDRAW_GONE, foreground=self.dim)
                self._log('cnc-ddraw removed: %s' % ', '.join(gone))
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
            self._log('Fetching cnc-ddraw from %s' % DDRAW_URL)
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
                        self._log('cnc-ddraw: %s' % DDRAW_LOCKED)
                    elif exc:
                        self.ddraw_note.config(text='Download failed.',
                                               foreground=PALETTE['bad'])
                        self._log('cnc-ddraw: %s' % exc)
                        self._log('Install it by hand from the link above.')
                    else:
                        self.ddraw_note.config(
                            text='Installed %d files beside v_on.exe.'
                                 % len(files), foreground=PALETTE['ok'])
                        self._log('cnc-ddraw installed: %s'
                                  % ', '.join(sorted(files)[:6]))
                    self._ddraw_sync()
                    return
            except queue.Empty:
                pass
            self.root.after(100, self._drain_ddraw)

        def _patch_body(self, parent, keys, hint, link=None):
            _hint(parent, hint, self.dim, self.small, pady=(0, 8))
            state = default_state()
            for key in keys:
                label, tip, _sites = BY_KEY[key]
                row = ttk.Frame(parent, style='Card.TFrame')
                row.pack(fill='x', pady=1)
                var = tk.BooleanVar(value=state[key])
                check = ttk.Checkbutton(row, text=label, variable=var,
                                        style='Card.TCheckbutton')
                check.state(['disabled'])
                check.pack(side='left')
                Info(row, label, tip, self).btn.pack(side='right',
                                                     padx=(6, 2))
                self.vars[key], self.checks[key] = var, check
            if link:
                self._link_row(parent, *link)
                self._netplay_row(parent)

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

        def _set_status(self, text, ok=None, level='bad'):
            colour = self.dim if ok is None else \
                PALETTE['ok'] if ok else PALETTE[level]
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
                self._compare(None)
                for key, check in self.checks.items():
                    self.vars[key].set(False)
                    check.state(['disabled'])
                self.apply_btn.state(['disabled'])
                self.restore_btn.state(['disabled'])
                self.rip_btn.state(['disabled'])
                self._ddraw_sync()
                self._netplay_sync()
                return
            level = (self.core.compare or {}).get('level', 'bad')
            self._set_status(note, ok, 'amber' if level == 'warn' else 'bad')
            self._compare(self.core.compare)
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
            self._ddraw_sync()
            self._netplay_sync()
            if not ok:
                self._log(note)
                for line in (self.core.compare or {}).get('log', ()):
                    self._log(line)

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


USAGE = """vo-patch.py %s - Virtual-On (PC, 1997) patcher

  vo-patch.py                     open the patcher
  vo-patch.py --rip SOURCE DIR    rip the soundtrack; SOURCE is a .cue sheet
                                  or a CD drive, DIR holds v_on.exe
  vo-patch.py --rip               list the drives it can see
  vo-patch.py --ddraw DIR         download cnc-ddraw into DIR (holds v_on.exe)
  vo-patch.py --netplay DIR       install the UDP netplay DLL (--remove undoes)
  vo-patch.py --selfcheck         validate the patch tables and exit
  vo-patch.py --version
"""


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


def netplay_cli(argv):
    """--netplay GAMEDIR [--remove]."""
    if not argv or len(argv) > 2:
        return 'Usage: vo-patch.py --netplay GAMEDIR [--remove]'
    gamedir = argv[0]
    if not os.path.isdir(gamedir):
        return 'Not a directory: %s' % gamedir
    try:
        if '--remove' in argv[1:]:
            remove_netplay(gamedir)
            print('Original dpctrl.dll restored.')
        else:
            install_netplay(gamedir)
            print('Netplay dpctrl.dll installed. Both players need it.')
            print('Host forwards UDP 47624; the joining side needs nothing.')
    except (OSError, ValueError) as exc:
        return str(exc)
    return None


def ddraw_cli(argv):
    """--ddraw GAMEDIR, for a machine with no display."""
    if len(argv) != 1:
        return 'Usage: vo-patch.py --ddraw GAMEDIR'
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

    why = probe_tk()
    if why is None:
        return run_tk()
    return ('Tk is not available: %s\n'
            'Install it: python3-tk on Debian, Ubuntu and Mint, '
            'python3-tkinter on Fedora, tk on Arch.' % why)


if __name__ == '__main__':
    sys.exit(main())
