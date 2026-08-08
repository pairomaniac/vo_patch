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
NETPLAY_SRC_SHA = 'c3b108ab32f5a21daa1f070c6362f7289460177fef7ccec3cea92f38413b3dc3'
NETPLAY_DLL_Z = (
    'eNrsvQ14VNW1PzyTTCCB4Bkk0aihTu1gkxJoomiJgI2QKNWgaQlIK1VQCHCNQGFGsOUjcWYk'
    'p+NA7r20ta33KsXba1v7v/ZeLyB+5QOSoNgGUAnyYVSqM4aPgJoECMz7+629z8wkxNr7f5/3'
    'fZ/3ef605pw5Z5+91157rbXXWnvttaf9oNaWbLPZHPgvGrXZttnUvyLbl/+rwn+XXL39EtsL'
    'aW9+dZu99M2vli9ctNy1dNmSBcvmPuR6YO7ixUs8rvvnu5Z5F7sWLXYV3zXd9dCSefPHDhs2'
    'xK3rKCux2UrtabYHH17xD7bT6lm77ZKvDbUnjbU9ix+34r98u61zOK5O/FfHEuWVcp+k4LZr'
    '+OXfUvXwJ5/Z0K8ivHKp7/jHqYrIJT/J1iMdTbIVD/kbnZyTZAsP/uLXT+yz2bIGeN57S5LN'
    'b//i78Z65q/0EIf/rgF6NrET6h8gnzN23lzPXNzPsum+o3u25/qWw1jVjV2mCj5n4EGrrvN/'
    'XVSu6Jvj1P3LVwmibbZs/PfqxeXG3r98Oe9rx9o1hgcc/7qx81W7PRqnAt+7A9S3SJUTXAPn'
    'tnRcDw1QzlMp7aYKcej6wgP1d37lkgdsamwwRvLBJxeVm2z7P//+5r+Z033H3MFi97hAnRHY'
    'gAeb0m0zKvFj/cMYdF/Yvpm/t5HE5wVtwVmOjpFjQ55rbYX7DP9SlGh2uMOQH9HM8ZNnVvqO'
    'OQrqOo0/jfS7Z1aaDYE6b9umGtz6epKMwDJV2obqzHJ3dvif/s1mC+KmOYVFCMu6TH7n2+He'
    'Rnq7596GdFutr8fuPZ7Y/FdCW1i2sNHwk3y/uH3voYKD0jp79jmLSkOsOvzb8TFYwkOesdkA'
    '6ZrmYrej9r3ySjx/HJfwb34jzyv4vEY99/P5PwDu8H/fYNVH+gwcNPw1xN3GfeVozuMCQsfz'
    'O740i92p+J1vOtzRTBbwHUv3pRCr9ogzGo2q/gQOelML6talaAwU1En3K2pfIryRcbFyFj69'
    '11rw3/6MBQrfR165EI2uEwREHsJXKHU1e/wMSnHcQqVuh+9oZ/RHvTPMv949c/p3fcdygt93'
    'BAuaU57UVQQfSTVHOuvQk4PeWTFy+JBdWeVOZbdYyhznDi+5HiRyLJ2PzdluBx89dz4a5aOG'
    '5hRWESXg8zvN1u/fc+99PwzN7UWnhH5CU6IJn2GUV8wPL1XfAoGPJFaZpltp8J2ze5/FkDxj'
    'zjjaH5Sj18VASXw8ElUGZxzFm8iDAKWiNvzSdbHako3ATDyMTMSfsaEXruVHBXsjk/Gz1vrt'
    'O5aNwXNZhIuqw2kgDN+O7MZa/gMWfccy+LoVvc1MV1SILqwxf9hrNsZe5ONFcJhTvd7sZF3Z'
    'bnPaZwR2BEvsCU77zHcs1SzpwQs+zVVPZ/QEf9iLF5sdHJG9nuF8l4HvUXVLO4rsxU3nCBDm'
    'dwH3rkYNt8zj2/mnYyfxbfVH4HVsksrqPGlWJa2bnDLOnnQhU4eCc1djbcWX/A8kZDYCvOAN'
    'nfiGsJEdUUlgrxH4mErFO8FJYalOBobkzCb2s6+qoyfIv+988fvjyaoW95RYLbaLS51MrGWA'
    '951f8v7Ul7wPW+8FQVkCi8O3IxV03Rh6uH83bV/STdvf1c2UL+lmypd0M+VLupn4XnfFdywv'
    'mDmHIp+si0JZVCQCe72DfDvyGitqQ3iCp+k2Jdky8DsbV+emSpIkRB0nbNZd58IUQFRVSn+y'
    'OB2sKdg0T5HZJcFhHtwV1BWWul1g6My6a2aCS8aj7OZW3MoMwBajmcVTOJdMuv7rNpuxsa76'
    '7K1y04CXaa3GljpMUcR/nF+dwcxiNrLXs3xTkbTmHeLb4WxU8pOgfbdVpPmtuEDGpIdbn0Jf'
    'GiHk/GtQgj0kbyqxnSEsXzERJSCO8BX1l/D/4hfZ7vB6XIOoom/9xfH6m1MIi8w5//AU5xPD'
    'vwB4BWKaU4qsN1PxJlJGSflwrDarPzPNJszQzk1VlBgW5QVHuhT9oRXWEN23yY/3SvAMDTpZ'
    '8Hs55HP5zAPA9ZBxhKaz36zKJpU4RbaxEiFo5z33JuKz+thKNaB5Fmdmlk+xZNzl5pPuhUlE'
    'XoVvZWqS59Kg+l1QV72Dn4FezBfclaqC1OEaPUlq2uYlmrlSKks3m4Bnl68navh/miRjkGe+'
    '7KYubz7n9iSpWZQfr6lwVEGwRzMX4sNtE2TE0/GinMCGyt2zcT9b93SOvs7jtanYPcOOH7O+'
    'eukzkyti8r0myVc32Ndgn7jB+9l2glRQh0I38lUF1JRx+HGTruZm9bDYPRnQV9xB6GdzMMaM'
    'VwjhqxuDU9KhpxDc8dG38VGGUzFKNro0joyCazlpSmYVi5Ec7k2eKSI+UzNU+ZxoK5imojb6'
    '9mh8POkgWNVYZyjU5IdWuReibL4zzoTFrDuxTosXMSM6EvnQ1zPCWH8zBrMKldi9T+GyyO59'
    'wtdz+Zp/Nivd7hcv58QhUOzsuKzWV5fsa+8NlSc5QrcnF75prG/Ep8aW4ox/MLaUpi+sb09N'
    'awmVOh149fiz8qo0Y6GxZXbGovr3U9MO+M64zFJ3lrGx0XfGbh7ANfUNzx98PYPX/LZq1eUp'
    'aD9YfHlqEEXMZrOpBqxnbHmr/qSz/kSWed78FJ97nb7Or/pOX+r7/Bnfp5NZwgwbW/YaWw6S'
    'HO4BLRtb8IDPZ4NbK91ODFu6kIvD7XJTquRzlIAaQUPZFEu6HHNocg0qGg0qggsqgosUnqe+'
    '0LdI5JrzVBUwn6LyDFuC3hl9e/u+afc7OVhmffcH9Z+kLNr7GZrJ3eM7k2WsuwFaGSkggSK+'
    'iBLwM8ciBCGCRBJ47DwASJzP+/Try7o0rxcf6+8w1JGnzsd+x/n9OUAd9LtfwAXs/SR/Pel+'
    'HpdJTo6v/xu8HY4/nhFB9Z7szq8anLZaY6tf1PdJHcksHHCQbm/DPW0A/0z8nPSJvLnewWd2'
    '70uQEE+zrZfdz9iF3UlGHFuWg5CNZvZQdxrzmZpdzabQy+4nFYGn0zC1VL6ILv42RJo50R3+'
    '4Jc22+bZX8NHYfukC1HCQRT6LtgN/+IU9X2VZpRD1yToLqxjW5KaS6om8RXA/eYgGTSHAHRM'
    'gRJUkAcV5EEFeeR7+BOXL7p3wRsyOI317+NFDb+zSSA+JgS8iYQb761jAQX3RM5Mzmhr/25j'
    'Tk9vSpmDr22hSlhVeMXemXmAd5ZSKTj7HKPdPkmKGYHXdMU1qmIQkcaxrvt4Qt2E9lnOaFrJ'
    'SHz+O/38ZOLzlDnaniCiOZYs2ZHcb2zfQovbZWppA7rDf/wc+vpAaNWkJpwbozbMU6yUvWGl'
    'Ht2NcrczUhhVfCL9j81Yht9GK4CTFWxC8GSwLD14KVRVfxg/MFFmvULVDLORIvmO4cFb04MZ'
    'Ob6dDhZaQkCShN3mqSqpNnhEEv2DmlXDH/6C07ynGQKCcMlvpU5khPf/IoZ8WL4ZHmeoNCm5'
    '+gyHwVj/exHuSfZJ/Ll2nVY3pI61+C6yTOyXSSeFe0rIYtTljMAKMPUm4lqTGhofVjWJkg8v'
    'yXnGFiGLV/nHDxQUJGncO+JoGZkU50DOruVKqoVm2TUcarbO0PTRjEZuFzxkT47VMSlZlKRU'
    '6hvExO9+jt+kKpcWkZlHccP5yFJbMp2KMKOZbWTxPuMeGXJBy18ORBBDEymGuNK0EvZ/CsPt'
    '47j8iinVCfwZaY6/L9iLj+7iR/8pcj0mPzMTUQeb5cG4LNJMG+fWV/rTf3BMpzSVnkj9b/8t'
    'SyCamccJ6I1W+gEc4X/pikahoF677TAehDeoX1nShlIi1QxWFqsvlQ8G4sELA/FgIlQDGFiK'
    'R/lkYFG2LmG+UAUinl7FVy/mwTvZ9Oj9pGVfQ1KF+dN7cUtCB2GHT2wU305GZLQ1XzaliCAV'
    '/o3819l4vYLj/33hYsF16JxYK32UXH7l2FNeKWi3lN0EkZnZqck5waIbGBFrVO19B6V0ckyq'
    '/hJYAZvx+av8E9nYOzA41w4ADmV/hJ4SYZYsxSyRcK/YA8q8zgre8Cw0+eCwGvxtSqFtZTc7'
    'fe3f/k09bn1nktbkyowVp2vramwpT77gq7PX1L6GkoUt3hO+j74NlSy1/qM0y0yrbnqeZgLc'
    'JV3/BA8TW8IXqH0zbzfNUUbUMN+OLNoGqt5NNcreE20GV+dS5R5JtQxBq+txQ9CjFFBt5b11'
    'DR1drHFsoj4Dd5S5R1mgkCPukEfMzStEVVemJx3WtolZK3Iha1wvwXSxG5sbl1+OPmXDTL0P'
    'BpQL0sLt25HTqOwpF0zB4wKWtoT6wJw4SjETpw+orE2Dm9P4d9WTOnA9GrJYXey371g5VLjZ'
    'olAZ/hsxiJNGyO0/2yyNK5CHp5sWoo3tRXsUGR1LppzfNgc/jZIGbarOkiYzlEGlmrSA2rz7'
    'GlGBZzWntFyjZ+WUHdfE5ufU6/RgxTCQrehT2+HlnGyV/5HVAvPFwMFUyPJSNJ4VbQ3eQPhM'
    'hZ5Al+EX7hlJmwPGSjaG0dVxqfS3Z7jh55qHRSfZTuVYzECFzv7GSyL9wDzNoiOS+mcwc6Ey'
    '8Z9Hfd4XqiaxUJX3D5vIDhNSavHS+6/BzJ38lbmRv2qDw3bx17An+OtR2AllwTFv8smYJ/HE'
    '8O+0i1JcFnkgmjg/qKqrjUApHm9ixYUpbMQITOCDjfJgpzz4Bh88IQ92yYMr+OBJefCmPEil'
    'S0Xwbleq+6w+OO/lJJjJ1wV1FCx7jZ/VaQNBja2ldwL9IKSpQaA/8oMLIicqfKvcc6BhPyom'
    'rB4x4HsTx3uotOld3+fDr8r8ar47OpPEUX32U9LaY1OpFGXWirAhE8Jsvr6KhnBQ8Ii78cHM'
    'J9TdxGDmk+quKLDLW2SOZEWRQaxhoK4Rcv3cmhRjM/VGztTl7utj9lG5cNhFnDW8P2eVu7OE'
    'n4ScIo8AwWQzCIOJcfirzZGsBy+oho0zR3rUrxz2xhy5Uv3KA02gD6tIWLu8aUILdymz7foE'
    's81hEe7fBRtrEfgiDjVOvmPjMVEITaNJiLGxVMl+LiLNWLeeWpE78CT4WtPfS+KjayN8vh3j'
    'G2snVhv+HqB14qPQZXmt8s4OivfMd3a4924wUX4iE8wMDhMmGCZMMDU4kpRpHpkwUthgfHAk'
    'CRPcN858b8JIxQjlNBjxpONK0o+qu/pslHPJhj9TOUITwRu24bttXKHM7SxsNdYH8GLT83hW'
    'U/u8bSanj1FRrDts5qPA3jU3FRzsNy/F56cgkcH5ptFeU/Mf+Fi+LHzXe1S+DhVfZgfN5hcc'
    '3Mw246hQ8sjqu90I3C3c1aa566VB1DQaLmj9o2DvNj4IP3kmGn3J0Qen8j44khNa4OCau4wt'
    'w4hAMNH7365/P63jxo6rEvxhF8NfS/hrNtqfr5JpszCFFa1JLUwhYGvfUws4N1lwNKfM026/'
    'goORb0ZjetCmHcRC+7fr29NCtZyOq7bxr92z+CVO7JEz2u6PjbURWIA6UcknF/ros0CVEKWL'
    'LXBmEFZyuJeCpnwpL1NzCO//hHqwfCdjODqFqO1o5nwpXBPY5amRGV7zoUX8qQMS/xfM7rKQ'
    '8hKDCyITAD2dYKJLFBwEU15VDR51cYoH9eVQvSDkpLvIJeeF6a7632M6a8qWuhQcr/TG13eq'
    'jx0aLI6N3YPFqGwfLDZ662CpLseufJO2vdZiTvCGOdqz2uX5WkGX2RnUn6pvqnewugZHjB4I'
    'E60bS8rRhCMeX3a/xZaaw7828XHyZjpmg1Ncm/1yTQ2VJcFNYWYS54umZ1MlbE6hKyAVazhm'
    '5uN4POk8gFsxJTjFCQevktBFjuYUMvL0kttu2UwOB9omYiEPd66yKS4ijqZbEk2tqZYPwJMe'
    'VMAI+cG6aRtM8yj0gns3zRy/XOpsLVW4bdUL/7h9y841+xzetuE2x/YObw/hdr24RTe623G/'
    'faZNrTNa+IAT9dbLwCTpwCiZvOdS7zF8NyhJLfIX1G2fxjWqrC/iL35/M76/2vp+mPdYwXE4'
    'af3uHNSRZksu2z5Vyj3nppGK53lJMaAZkDAEzeB2HG5/gB7gdjxuP0kG9Zt+97W431YlFfjd'
    'E8V3zSaXockxVpNDvcderJJ5tWAvPi+P1z9L3Zbhdnb8KcMRLrNdztt5uN0CKHFLt9xxtIpb'
    'uuYeVWU98pmdNayM17BKariGt1W4vVPB7cftSYH7cXdNkgJ5qQZ5o3sZ7rZPF9T1wf/GW19V'
    'cur3rbo/I7wR4H2Kaup53Sor2y2V4eEL8X5ti0P1Mm4X2kbxtg63xapfO3DbqfrVEu9Xa7xf'
    'b8VraMPt86pfh5IYeyL9asftKT0ef04cj6MCz9+Uv+zfleJ3ri20xsuB/h2XSlD5Cd369ts1'
    'lZxWVPKZUI90sScOYK8QzI28ZViUJhgHbjs0gKnJhKk2sf2bVfs3x+kl8qLSw/CtK1m3P1W1'
    '4E6OoTYnOdZuXjIH/FahWdyOU6gdh9sTesDH434APLD9xar92+MswvHVA1Eeb2SW3FYLtcaf'
    'zkkmgRULteJ2jeryQtyGNbXitlpTaxz4lfEaViUTafcKtSYzHEqA93OlMaqQtjRZUanfvSzZ'
    'otca3G0vI/N/2fi+qvo3y+pfCvtXsKvguNAwa21N1tjeGIfqiXhvn4w/fTqZsms2b5/B7VjV'
    '22dpgqvePqd7S5w/n6xI5j+T5dsXkhkJJTS9LV7jy7idbfMIV+D2RtslwhVSVnFFMkOl7hLR'
    'its7tDxNjjFIW7yyQ7h9QlXWLnQglR1NZhyUQuXe5AQGCUu/O6682O5PxN9XkgR/91r4G+SN'
    'yMIg51PdbpcGQeb4Q2olP92ZXS6mrrmTngksRWTQ1/GBOPPt1uSLNVlrUQ+rsdpTuJnmWWxx'
    'bzMnb1nb+22yLNLlxFbyyt3K+8LVPGXU0fShf0drSVxUu0n5NnCXV1AX2FthlMjUGJTlN1Q1'
    'VhSxrYTjOpsyHrKsRTzWvlcvzo2VZcUEbSLVaWkPpQrSAO2tbHiusozAlXSdbpWWXpQlyxTq'
    'Hsp09sTuVsbuVum7CnPYUr1YKw1vXkYnykLlRPkKEHRIlrrn6AgComUFlPzNrDNh9i3YKwTd'
    'bwZOSpyBX7Bm4In9Z+CK2v9H59/Y5JGj5wnSojvJ4vFrY3dF1oRy0Zx8iTUnP40fek4+pjhw'
    'YnwWKY7PIlPjNZQmMbbPzVtGCGomKU+KM8nkxFlkljVF3rre9qWMUGE1w9WhUeXi0hIemCgu'
    'VOGAIXbtB4kr82YzfCDDOMiickoMQ2CX4b9ZOTGydJUuIUmldUb3KzcXGn1TD3KTGv7LzJ0J'
    'I0+n9GNcGxB7J3zPEShtjUI2xCQ40fD/gmbj/tzwpI9Bsw8fkluDYRLrhtGYGymGsK47obnE'
    'Rg4age/aWQtWfX/NeJbMIrKk0hSDWknsivR+pu2YbfOAxPC973HtQUWIBegrUhiRsCNlV4WH'
    'nkJQV4/DCNAagM3rJnQwi7MCxw1/l13iJ7L06n76syokg1rrFtRvZ0wAGNIeHoVeh6bY6Wzy'
    '9aRh5Uo5GVwFe7F27NZ6fLcHLsC6UNnVxnda68+mYFUfuFt/E5c29vjOOtcM2c5RftEZD8rL'
    'hhGSrtXkFjRoZj7NkLqth+kE4e02mt/hBR3o5tPiExERtfdV8d/0Qc7mZ8Ruwvq/8bMG0xnZ'
    '+6nY+5ao+ds+R49eo7eMmP3KcFmMXmJEVO9gcYixstNsDM88RKRGPQUy/O1q+Ntk+I11GXZN'
    'DSSBh/cpurH4edtIRCCGf3s4Pm5+unyUWzNhHVmNv5LDEld4zYX/+/35dp/1mvRn9yr+SrCV'
    'OOrh8YdU5JFYzm8rP3i48QS7jJC9KMGltbz9K+qbXlbzKbwQdzXI6hD6dCd9oo7NMpifHISx'
    'ezNt5b3bGCwc/mVEsNoY2X9B4iOVPI5HtVwWLJIYjsiis3q9KfImAN9WQ9gqiflJghXDT0s1'
    'Ut/H/tZY9dzGtYRh85Qz6fYvRW+cb7TH1QGbUviMPxPsy2AmHRxwdD+H7lBKhl8PAzZRN/eG'
    'Xw3HxtXzXIfJ8dLe+8HaX8KgoIGBkZYGggjANF7UvxVclAADhsqiWP4/G8XHDw+FP78hVdkf'
    'OqjhouUBMl1C/FgCMBc7EDQqWGesfav++HqBq6BOCVJO8VegfjfdC4z3yaJc4aLApbomF5Gq'
    '+8F2Q0pR+Jsex9QvdnAk1JoQL5rRcTnwbtYDSdeEhx0j1Q4y/G9xqJ5qE7b1flCwy2zx7chA'
    'Z3RAnBE4SKDohkGY3AfBzBeUh9Bp7jO2/BNdalx/vgEN0ts2BldIQ+ViY8mO8V80f3/Z1dhS'
    'xdohhLNrHEM3sTJfvT3mv+PvwtfXHjBbsSoDcLpb7eIrssCvtehrRQIFj6eImXIAvJERj1vW'
    '6wZOkbiN0dbRKUQhpHf6pDOEY12nUPc85dfnck56dN/olFar0Nah8H78mDBE3r4Q9+d8ueCi'
    'Pz/BD7YQwLHy8N79EPK4YZXN0ilF9i/E7p7Xd+IGtjo6SiQoa4j3ODyzLV6XONa/AKygfGaB'
    '1vGCJZIG/VV9Hzln+Qnj/qrxdvFQMf4OU0CRWp7O569JnIwQqjNRherQeRWImt1qvjeKe4yS'
    'ThTlWwaoFyZTFeAsBYtmnDxcc0Wt2Rn+5YeAl88Z7S+zWN3asNlZcBB+lmj4J3hrqnYjP6Vz'
    'x5JDaqSMwBDq9PDjkXnsVlyOtYaTuDLPuIJUwQa0KM+1WLDIudTwdzr0qxwdUcH7dh1+AH9k'
    '5nOEuFW5RuEUhZcVU16A5mnZ28JOhv++ZK0IbVF9DdxLw1MY1WyyJNZ3ranLraHM1jIox4ph'
    'xZW9cIWTL+6FVhIkUDJzqepFjHefEswb/nUciE7zVG4494zSxfI3PScKA6TR1xmX42ZQ4Sh0'
    'Bg/cSWw8pj5dRz3l8wnD6OszHnVwJUPmGVZcKdEI8/TaWhFHclibUjED1yVb0myVgD9KRzu6'
    'Hw3eoIMKc3RP/vakrQMF34l7UTP1Cor7UatekXtWjRRJLQlLef/D6qWujtVar/3wIy0pnxet'
    'NsUI2K2J3qrqJT1wDi6WiqUai4pNV8uVMlErf/8us9v8PPJHi59Uk0qeiCwxHrv9QoLyeqyP'
    '8qq0pKq3MAaKV7BaoFlQ8VlQsWD1DnJn4vwUvIHtRN9R4k2LNlnv1MTib4VCw/cJkLzSmwDJ'
    'T+26/YKDCRLms30xWCJLov3lX2l8HvvCOayyn06mhrhUr59Fqs+LnmVJJE87xPfU+PqEbhvr'
    'Daf6rjco0eCbRM910pqHgkrgiMVMvBtbNqaSZ2vKjQtqjQXTS+Ebaz/WjL1HrbL4ziWtKRAz'
    '/6L5CSsrNn4/6EJsdWVtRH0dmtwOJV/IAixV/QE3MEJKmPKyqYrt2nAhDi3cXX9BraN8+bxx'
    'QuKEvrxcGU0RvWriLZDJTxFqfPC+tTc+eAw/su5/e/7v0Lw7nuur7zjgnkjHFJkZ0xTTbRfp'
    'n97LB9AoWe7Lm2uq/Z/825SKnSRBBp2Ngv8E8cqt8iRU5sDqhJmZKvtMvBHfjnRlDzb2G9/p'
    '1CNHLsXGKt852DOBg54r+sx/0Yx/kZcddu9nkL9Ft4IvF+6GqEDoCOM7Cur62jcwjevswYwA'
    'P4Lm19nxx77vN1XJjjCPpQ83p1TpEKjIj7jvR+33wYakZA9nKSc2+vTBr6zHw4MlcbfKexVO'
    'HSwbgqTcdKUXu32TMtD1JM+Q5hS5sUkELOrrsXsmEHiUZj9fehaAVp/jX0+Y810yWk2Cs63R'
    'oZ56j1ult+h435zYyrrSpxn/G/YPElX5Hmt/VWw/U11jn/EifNpJF4IxHj5WqUTq8/tU+OLu'
    'xbHg/HBrpURxuSSoz8HILkfYvQQv1i6O8ane/4MV4WeI2C5jvXhHnsWPUFk6VPMhhW8suyT4'
    'Y0fyXamFbxiP+ogI+F4aMgo7vR9wD8VZWRejZOy2WV9mb6n/MMneZq50Not7IpyZRpWD75Jv'
    'g8F+ZTpU9wyKtmytugtJF0/WXjzDH9S4CpX1+j4458ECW53vg9cQTJ5COO2+Hfnc7VKB/YVJ'
    '26mebOOGZ0hDl1HcwMi15JFszLwj1Zzu4CYR0SCxd87MQDtF0o4ThP08VBp6SxET9la52h72'
    'qz7yET6TyxkGwtqCxVlST/A2xqYf26c/WHRBHCNUL3Zz2vn+hYT9Z2qXnu9YaXDS00Rw1DMs'
    'NP8zbqZr1OvJT1ubGguAyJCjKuQIcG9vfdhR3+4IrwLicnc0S69tanfL13ztnZsJzzYyyRhh'
    'LwTkP4uFC2NDOqp6iY+CQ6lcXMG5bjqDXwOX8HZKKvWsAJe1fI3p1edY0vD9A1Xs1S0ddwOf'
    'Z1KVKmNs2cp92uYp/y7DPMG58s9cDhjtfcv+zrPtiCa7ULXqkWrGZdvW/ihY0hqmYDQ7a9bx'
    'q4S6V7HZnzhC8twsaQ0mh2p4G6yVv1NSzWlvGVtazGm7JzoN/3mS1xmXEfAQpHfQWrKA8Wy7'
    'EbiFWlqXUVOoghhknwAalhEp2Q3sP2sN4f543OMmESR13kugTPEO6teNFoosEB/kAK5+S9m9'
    'yx3BCb7G1ORX2WzoMf6V+kx5oD5ZdiC4+q1Nz6i9qevzOIGMJE2FvEcVkYccr4UcjwadEPNX'
    'AtR0SppgmZgwFKsqGsrpa7ZPmMRaVu2yaALx99LfikDXI5XAuC2O8Swgm1HwGzgM1RdsVQhZ'
    'fPwztl0CaZMevq6XvtOtcSiNR/18u7ol8t0EPQAjlcruSMHIdEu/0q14ftXx/Fg175/Sjwz/'
    'cBSKXCG9XPmebCsd8tIq3ESewJALJiObOSvKIKyyBuF758RuI1tMxbPwZPyuvWj/4QzhjmwU'
    'ekGCMm1csx/5nFs5cw56xgXHTOSjSWRTka/E3ttmE6e+/VjUh7TwpganiN3RGrwnFdAdjNWW'
    'N1lv88sW7NKf8ryMmncwBKuK38uj+ExXrijhwx8tkiDs70CM5NMBYTpxN84sc/QFMlMDKW7I'
    'stTN/NmnYa6EOLgHTrbidXwueO3fvpbn8Xo3WX2v80yhPCs42PG1jiu+cH0IAeyksdags/B1'
    'UJj3JHA22pwu81rrQunURRBh5o3Ph9KNjta4fpLH3eSy24N7UFeMBSCTNdSG/01lWGuRFdvl'
    'F5eXmHi9YV3c8154JKimoy0B72vlhhtHGqSnVDwS5zuzIViUCiFNxIeNhTIJwCns/bg55Tl3'
    'zLC37qzO9d6igm4fjPkrwlMGhi8cpM9vRaxcrAq3jtv9CrWI+L+DH/adfwGOo2BvRfX4aT/w'
    'Dk0umlg9nhkwPKlma6OOU6qoXpk11I4djnhUUTtzOr4QvwIKG1uXDgJne64xtpZlFOwKFTuz'
    'sIGhcN+ywcllqbiki+smp/CUN8wN5KRXzR8IhsnnppgZC4ji1BVDK6onkYGn/cAzrOAg6Nts'
    'U/ID7EIB8ApfEjLv+xXVr2Thx1C7950K3yuDmHvDs9vYGsjAXUFX6MkUvq1NjH9Jtcw9zO7H'
    'KoQZ/gUf8FNfE+K8cgqblu3vqFH9rYtB4r00mCmNb7ca99gbK6q369Y/mznd2Pqfg5Rw89zC'
    'jZLG1kcFil3VHSJt/Uks20f/GRA/f47j53/QPlpj633jladDxUE09d+qJP2eBPqJ1xcxtv5K'
    '9eag57Duie5Bwd5Y+a6ir7s8gxGPtd0XgdYk4L/L9e97Gv9n+JOHUG4a++rD/6/i/9KL8R8s'
    'ct9zb3drffhq1Z+7C/aC5EHuiWANE7BejIE15B6zlduuUb6i+kUNX0ewusomu9rfN7a+qOCM'
    'et42tv6jptSNw1myn/wL3p5e2AL4bk/F5VKSZ0NOYTfg23vPvWbrfY0pbLTDiMunDvQgIzjV'
    'AWNvMDoH6flW4ODaMG7BQNUNoqW0xnn+u2WTmIoFaRfSl2VWR3jvS7ON4VVerOjIrceDWd9v'
    'VHMbmHU8ul9E3lk6R8TXVFEO434obvMspi9qjvxMD4+/2tpilBd+Tn3DwMZ7CMXfuX9/zsFK'
    '99IDdQdXucsO/PXX77V37bB73F07HCoOHY/zX02WRfZV7vGMyInhA69cfJqDwt7vFXQV1DEw'
    'vPpjJodBFN4q82b4Bku5KQQa+1SqXLiWoUy2dgZniRWl5vmsOubXGEOZO0fvq+/aUeQZ/SrN'
    'kW2Nr2Eu2KTbPdwGjEx1qKisaMfGmPzvkbW5ilHRtRVjbi56QpJgJMSXSG9zgrc7unYWYd6f'
    '7Cqc7F7jDCZV/dXlvSp4u6smmw9hoZj4uyPnHsteVfh1SOaS5+eqzCX9Cm5R+vrbROkb0Nhl'
    '1y8wB5U7J3GZi3I4cxaV6fTCnWaTcefngTroscbUzws7jQ3MiRO6Ndo8hcHiVICyaFRl2nVc'
    'F3CabVVobB2ktgM6wjvvlnWv9SZJtCkplDdYUHwBQbyazqe7ahzDEXFIiKe4Ck94vgW9HvTe'
    '1UQ84MkU95oPjVdvjQaHVn3k8vxFjB0oAY0kqPhn3l1BgoS1DV4Zs4hLpAKg+h5MtTOefBsX'
    'ucMbqe4xrjEKZQIO5Y99O0plhsntNKekdk1JcXjH8WsU6Wu/6+/L+37v/TjQtXZwxx9rA11r'
    'fg70SKudXL+bkspF0o6nLXvpbrNZ4T8veEt6YQPxexr4vQX4PV3YuXZt8y2CWCryXGBUdWVT'
    'AbzwCimn4+sKz1NcNRl2YM0MQ+oC6Okue08yShr+fyQ6pwjudhB3012F091r9huvzgTuuna4'
    'PI3UP4C+8Lr7BPEdL8Xlv3QfSsotDnJXIMDRnhw1b0nt3//zF/XfcxVa7fiB9gNMcTU5klyo'
    'HeABuISya25HuUTQDgdHEKzdegVE6j92rwKtXrfL6kN3RrmLJnI6jlcoPzJqHNyOP3A/kvnO'
    '9Jd63ypXXnyjpC04GRr0Spfmj/DLM8m9GeYps4EqRaRS+9soz9ru7fuOnrMZ5pt3m/uVDn9L'
    'OkcG2Wi4frptjNryQ3kxDDIk3fehq2CXscVh+NrfS2urcQzhZHIm2XvM15BsRszO3NPhKBPI'
    'WLq6nj+b7Z6h20ZzYP+TcHQ1FuE3UdDxG8lDQ+vqdMdvLrK3fT1LzJLdwR+mmjNaaWL6GxhK'
    '8RO4NLDGsnqH8Z3m0NJLSTYlLWZ91w6nEfiTWKqpZmdht7m6zpjW7Ku7SlvhXYg6hxVe0u5Q'
    'JvjlMMFN4M7bCkNwO1N2mZ1pPYb/Pe7x3rJ6d9DbZk57uWbGe76PXSaMQm+rCUDOBWlk9pu/'
    'kAqn85qS90JT83113wKsaW0giKozhcat9caWae1IlWP88ULN5Oi+k4xodxkl3QZSzKA1744g'
    'al7dFpz2MnpZeAGBAOxAyQ7KkeNJcmueHj2jLbcVTzbQevat3r3E8P8HOjFhRp0RCjtYqC63'
    '1Sw5SqYu2ZVq+B+ga+Cho0nBkqOs6L+4167T2DJjN8RSzcwLTUX2fBTExFIsTRwNrYwWetsM'
    'n+weXd0OgDquoF5wB+TVkLjg6V5+Y/AOh++EXcntmLwaFBwB0e1p1T6gcMbsRHHVvex1NBFc'
    '3U5Q0rlscocjsi/JFvfXV/h+ko2V/GKSvBqebX/4/e9/j3Hs/gijuOcT++nCs2arUZowmjk9'
    'HM34MApaAt+klXvMir8NlrQEV++oOtptTnFgBWc9wz7NHnun2ZB72veB3XM5xEfTrdH8wibP'
    'lYn6UtOgfCBKvfGeNpsmlLQZ/v1CIKNL2iaU1BmPH6aaraYH1fIT6JDg8BtJ0kPgsLnkqORv'
    'KWknF0d2XLDslX4UnNWHgr+Z9IUUPK5bU3DyQBQ82KLgwM1iyLPvoGHVfbMHCNhgJ2tP28Gg'
    'qIKE9Qvprz3WudDXWGx1XW4zAVt91AQ09QCQHfWuDc7YAU9UDePLhIYCL7O11Ud9J+39548M'
    'jBEeLz8ZLGnDtOozuL6jkAIMmc0WZn6v91Uo0v43WcDL7QEEhT1GaDAaAiTTWhQk6FfXDri3'
    'WoXvCIrpRR2jbIOozNHnFZgv4B81T4ccN/s+tmOH90x702QMZoNnhNoHm5SP7bLqkfd0cPVu'
    'XxTtulSPwD6XcRXwLLmkxgnS1SCHigcldawUupoOue90xUgcKu63yBgn+zJGMJlcsVfrVYKR'
    's7MSOCMQ9e7q40cUll19lCO1VcJtS3Y33QogBoVuTQrOaCtsWN5KcTGtJbQmiu3xq9vN/ZHF'
    '9BgJ/wQiNotyLpBySpsj9Wo9SdGX6tuPLnCXrKpYd0zq7/jXBPtfxuG/WZ0QS+CgZPCqYyRX'
    'rWh7kcN4wgq9dwnZFIJs1v+KH6yuA3y5zWqwIn9RlBj54QVV3PA3Kf8VSa4uyWwqBGsF/g19'
    'AKVGPFKa70JLkyaAmQJBvIncHsv3oRgucp3iKtnVsvdcNKanoSH6eZX4DFwrdFlHbN4RjRWK'
    '3M3GZljM8VG32QPvMgsdRU1GoJOvS+oobTPjX5kzWiIvi3gSjElQGMTv6t+jKPurpW/kPXrl'
    'On2rd9gM8/Gz0lD1ByTN6rOKNlfgYeS+fvlDON9h/kWWIdFZoTMV7EIUbMnLxndaqOTUX26W'
    '7IBw8AzVI/0Hygj4h1oKXzdnbDOmNcRlhPPTPnJxd1fJjirPCPSv5rdqOnGvpTjAtNJpetvT'
    'ejBQyAOSLK+C03abP6wbRZMIuExYX1y9u/AvntzgD1tgJl+DLiN5Q7bNk2W25PaMYmA1HUZD'
    'mux50AaEP7yYQo5ue3P37t3mp/Zzvn227g8RPfBBb304yd4gz3P3FezNPWL+sO2K/eaMQ4ve'
    'waPdi97jG/s+CP0mX7vd3I98AfA1l7Ta3wt9327OeGs7AwG7P0guacOHvrr84A+P1tiD09q3'
    'pTEZYYukW7uibXmW8dwF+56TwWlt6CH6J7nl1HzwMsY8+EOO7vr/oJjB1LqbU+uavPisMDw+'
    'K2Qoed1/Pij07jb8J2Wi3oZvi3Q1IAD8uq6iu2RHnd0zSGnWaDNQ98gIYDdBRPn32ylrdmIG'
    'nmMXOdbVUGX4d0hbY0Jr7EHv7sLVu5dld0zT8qaP3XDBcyPthpP97YbBymxotcyGx8sT5+EL'
    '3tehoHZkIp+Dll+isSrVFan4BFzsEyyAKFNyTGmuR5Qcez1Rjl1XLpor63sN+chiQkzre012'
    'z1VNg0ajI8KrMpt3v495fE+khCsM3V0NRYafvNo0yAUPeiS9z37f7iO+/TaMXfd7HL0Hzlvz'
    'JvEeqKFRMuNlrCsuFfQb65ltUSEdu5pxr3oSeTI+38b1cmP9hgsX5dux5qt5p/rZSyI4d0Rm'
    'a/1ZzZOyuVbpF9DavLvNFojIDZwFI/OifSow1jNg83+qb1gipylyVMsJpQ/TaJ7oW+k8b/jf'
    'EDdAWUYFoz9+Kz10mvPf8tUZWgoMOSlSoKQdYlJWnLivu+SotbtAFDWkGhjxHWWxsr7QxEFQ'
    '+iy7RtldijZAb9+62Nb6MG5r/cWyta76npCF9Rns1PlvWcTBeLx3t3GbQLj3JBNS6seU6wpI'
    'DWD4r98FNDPaY3sDuEQka76hkiMk6z/eQajXFBhbZxypMKdnmFOcYi7VJoyntLMR7QAC2ZEs'
    '6yFbp2d07MF6hbnzbvMvtKLolZ2NbYp3pFI/ZHB/4V+MDRJjMiU990xhvbHBSI5pekDCi7xS'
    'TxMW1oorY7pDtyVtJ5kzKK37gz2fcDes/7/ssp6ZDusytyUe3w8votlgfx0Lm4a/lgtiu9Ye'
    'S6QHRGUH2gQGmJBY6WOs/bNFkLeaTl8+0S//lAieTMiEPpONnwH/7BfzACSWh8b+Yj6HfKYd'
    'G5+L642S0+a74YoToq15fgTHgNNzUPrmeWtRM3s1aQb+rWgOTck2D0g0bvgJlO7Yov3vCj38'
    'zvB/O2opJH5mTH2RnwM5DIVhIYoB2Sl8lKr6pcL3wDW47Qp8n+odIdybblmQwmfIGnMGE2vH'
    'V/8m/L89zhFKF+ZjfQesOmrBn+vnqDEF6gtbjNB9zApWT9JnYhuOxx2pcfnD8isBHsdhqhqH'
    '39VJFoj7VemuHYOwH4ArjnekKrqWcXEf7yc/GF7/rQHlzRiJJ0JgauTb8ffb8uVl+NAx6Ql1'
    'kO9zogBejMBdOr7t0MX12XuAsQT/GurPF/pn4Guz3XsycrTPejjej5b4Irb/WjweXAlO8R/J'
    '9+XH+vUn8r8kHoeYW/Nj4IHy8Y7UyDGdj8Y7VEFdsz1OFJFvxusP3RKVOdLwn70g5AIs0n/k'
    'iyTJTmgLvtB3pUHItvO95DYU+4zjvz9G72wWZoB3FMl7xgXNkqUXYrrwQ1QcJ2q5rcaXOt5k'
    'Ph5pyW/Q1SboY8Rqo7h60iN/4qDWdx9g/US7vwcvujm1m/VxeSxOqrtDj0SVDMmTFcYeuozr'
    'e67GqouTmaa2LaioqOg+WR+92txTfyYp94wn7zU+unifGAJ5d9q7T5ot+Lr+bJK5J7cenvSf'
    '5PiiUW968xTZ4PMa/xSehKQySs+bLaG7k3JbC/dsJ4pfJDQMtz+NwabKQWjg6sk8xz7lgDaI'
    'sAZfu6v6dZKxr6WEARXvhj3Ygm7/Ccs7zQMMuNCepsiDn/fx16q5xy2e9pXfg3SHo5oe1Sx4'
    '1Bm0mS5BYwfhUr0uIjG02WYTPZPVZ7jA7cmnX+p0uImoxaRzJIMeKLckndHrVUXpzAsgFmzH'
    'ZtDfARRfcFwV/49Y8dqB4Hkk3fqafy4CLFkD9svwAIAtUIAdPyNk3nHV2Lif9m/oRzf9HfrR'
    'pXck6kfSn18cU/15b8RF/dGTu0s6ZNTQt2D4XxuoOxaeX/9YdQdLillmk+qO4X9Z8cYo5mH3'
    'nTm/6u7g9PTCPWvLyHFt9Hu+DskofTZ7wt0wzjuGKz6w9J9wP34nvN8UeF1cCojJxzqPx/dg'
    'ut2qivzPoo7EopzXixjJYlPbN/Lj8QuJIxbJij8HnhpQ3UMdqrr/vjRWnZpn+DDrdplSs9U0'
    'IVN7LZ/b9PPcFjw2zMs4XYsMWT8Ut3ZycMQetfSpt+mhNv8sMT2z3XPA+bKwUG/2pLV60yro'
    'mP6WOKbPc0MDg8mwSfAS47HfcGhew5PtVgJJMjwz1PakhbFO4xC/TI5U1kSuw6CB72qYdS8/'
    'Nt6F2JqzbHDwjvTArpVDYMeYp2rs0K6M50466o87fJ9Ac/glrZMeX7shesR0TkIsvmaQyDKm'
    '596OPVgPN9DfeGoIq385LldevER2ouTWb6Mvrf79JOPfGva1d9W7PBADBXVox77nuL1n+Cf0'
    'njSDRV6k93M0NDjjudZkQFL/STK+KkAoClI/GX9s3fcJPkaSI6Nkv9lsvNZkvp3QdQgOB5iE'
    'W3e1vu07c4mx7tvJGhDWK4AU7GWlXdZGPwsoBs3K75JWo6SJjYKo7XUC0/C9/Lqgi31IBOMd'
    'szmt1Xzb6+PI0NhfodQzKrfWAPBVV7E7LdWThvDpHG48zW2ywbVg7yjT9gTH4VKUGZaKqYv4'
    'NdZzx3nToLHU839BFWCXd7hS+xPkIaMBJnB7Fdd//NzcDq6hgqNTzWdTsfh3ESTpcI8ADuM1'
    '9CmwN5TOMJRU46766g9EEp+1+9rOksLMnXq6MHvik4Dh/w1qlEnjb+7PHnD+MMMVo7k6VJf0'
    'BKmQk0Jh2FhfRs0nTLwgzFvy7MFpjH5UGYGR1DXwAsrZTeo2tcgIUM+1FIIpRxWW3wVN+1pm'
    'ha+X31Q+R2G2TPJcrYSkVTzjaH97CgEJgvSrOm5Df4ytU7LJX9dWcAgf44hc8Izl+hd8zL6e'
    'sZ4KX0+e5wBmqtexAaLjg/76zQsf9tX4Po7DVhb+xYeyOzHdDngOVN/hIL4506l5TtbBIr4T'
    'Ms9BD/jlaTRQHZNDIrJAMNeGn0c1kflq/dLS2ybyWThBbxNF579i9qalX2WwXN2FgfQrkbef'
    'fTCAvQmsRx5U+/xIj7+T+EWLZwqiBcctfhERJBwaqTkn5Y3XuJRbCs48czUuGZEtat+eZpIN'
    'c1Es8k48rs6CoxJwRLLicHK+SOYyDnZ5rvsVPoKMAgfU3BqN2PG4Vuumv6Uy9XP6y7CTNPJr'
    '0aAcAp/T2utgGZzkyQpEv3MzmCNyf28//xcmwOpju20S2X9MBbpzJ1L4J1NkgyyOTbCdj6rM'
    'APrIjKDkArDFNsznYAvOUZ0LwKZyHmDX8UabpNGldTlKr9Rn4/ETNmtv9LOxu+dVUZ5ygl/b'
    'xMpIJiIqSP/1oeX2ju/F+H+jVAFgXxBvR5E3rfBJ+WbNCMTes1X4YYyAn6tn6VcjqZKpihpb'
    'LqGcf5WWh38QBUDPKO+fmBFBwyw78/ySbFZ34Qn5bBATVL2tdnW6dTZa87S1qRkC01UQDU1t'
    'D/0AlXdWnfnBilJjy66oO5T9NiIwavW2j/AqnMQSWYx6q36cNA4O3rT4vujQ7UmvSUamd1/l'
    'ln3zjT7x7uH8iaCEQyjvi8JntS9NTJHAEkYNt74mYdDvMs4NITJXSYY9xq3WfAdqSCjdDJXe'
    '2YoVc5d5ibGlSPyD6EzgzoT1h6Aayuodu+XIEXoFhI+qn5HuR1VaWBc2K2UlY5kolY43W8jr'
    '4IMkTyYvmCRfZQQ4blO8yUiCgGQ6j8tI4JHLiyUxfGWmu809WPfl9qInyJsvspHHpZEqVZQN'
    'nOJG32nMv5WFnLNmyy990STPVb4o2pgXa2Oo8Sr2D/5SnaOhWjKPhK9jUqfLxMU+2R4bnncF'
    'tZEUNcBYsE9iAO+6eYPoWrVtc2CQ0BtgtLgnqGiBRlSOle0Y+t6vMChWK1SRrsSeb/Nz84gs'
    'nQQVlfhaXM86NMWEFO1jowums4MPATTfznQqjvXYOx7xfslHS/t91MuUrUVf8tE/9vvo67Ah'
    'IkPPK1RMXIhdSoMUudkTUHL6fBxVyQnP37W+Qy60Ffq7pIT3288LKnNMcQj1fhtwbBQ4wu3c'
    'M8PMn5qoMJChGUIpKYZ/1CA1fBjJn+lbhEj+KkUPvWn3ZpjNNNgecfSJZ3wbmC7qpaXoHS4T'
    'qeZL/4iU2LiAJAv2kl6EyI6YDdtWEp789wGPg/AooeF73WWqu64Gh0pjAKyFvOkS8lMxylZS'
    'MQnZNP1XomaJBZrDyJ6FCKN6ZEjV61X628TC6FdtCoPaPo6uZewQACzDJFdunkEQODf2Naft'
    'N/xjHNwQVAdgvuZQSVjiIseiyMfG2RVrac5BZ47ITFiDpDJqrHkJZyMRQCT/nOBjhE/xT1GC'
    'fsKRM9+NZArGoEwkx7hM/FLqbfe5GE6IiUAXuOYw2Tq1HyYyHBoTRTCHioiNqQNh43BbHB9+'
    'h4qX4pE/jPsV3OB4IKaSqY7aJhobmETHeO3ki3ak7chFpqI+2uXb9jOhFx3AlUYEtWKoxBzZ'
    'M+HXyAxVvTIbxXA4AM44qSQibQG3wJ87E2OiZx0W6wRqe6XrZJ1Uss7jnSjaOmDR/H5FT7Ho'
    'vw1YdEm/oj+Hdzvy4zOasW5E4gfcT7ze8F+bkB/DYrDvn1Xl9Og6rOe3cFELspgWEpOsdzen'
    'SGN/UpTDV6FpDnMQBdhNyWqwYzk33sSw25TDJdAGDraIwh4jig/iPcGmNpldd3B2fUHNrhlB'
    'xeVwOhsBBqQ1Oa4bxaVrVRZc+qseVf0jCdWnxqr3nVFv7+EiRLk7zyrhFI6ossVScvj1bfj7'
    'E4TBrRz1YvVxj3GRQxzSxe41FEBX3SDBaHlQhIcZW0vdayRxkVIlqLFEPtGs8EFvHCxHDKxj'
    'XCfH9PLYFI0JPRVH9skLiIm56Our4h1shYIbReL0EF69Jiug2H3LrMXhyyb0nSO6sB86sv+M'
    'os6NceoMv0JK/FF3TPK6Egd+DmqrqI5Evx1Sigxr3k0iC50RveE6JMq0i77Qj2TMN169nhX8'
    'pTvGGgyWrNqdpGeKyD4MfQWx8L6oVGl7DP+vgY/XUqhH/iOzN2/FtB0pBgTGa5xcI5vZNXtk'
    'FT70ncin+mBeH3kVDRxu60g63KZ7u/1STKAFdeEb2K+P8AlAf1qLIQ2JgpN6kvjRIj/uFrQE'
    'ZzggbyJ7Y7/AKpGxqKHGHhmsOCDJmpoQMaAwdHN3/EVK4otReBHOpy49t8cC0eyxgLwMQI6i'
    'mhEOvctkGD1CgvQYjZJ5C6ehRQroXkWHg0Kn3u8kkEr3k6L0Rf4gWa4nJ8fBSlat/3tX/EVS'
    '4ov1Xf1GGmyYILsUQhZ1xaQx0yMnqLbDtGprrB/3uTCnVm+9aU2OrFGRqz+P7RCRfmSxH0P4'
    '8Exk69k+Yfhf+G86ot+xYw8KpO/MiDUjxVmQOP/a66BCh8qMwtdXfUbHZin6YWZhD4SvzsG4'
    '7unq6xx+/aByNRwc8Ps9a+X7dJfaZmZmhWY5jC3Da8oc/r2ewtAtYEa1byZVorztNUP8u7yn'
    'uJUQDpdBvJrYjJiFwDlZt8K2jueV/6zjYL/zQBwyBwSn9vrCjn0fmFMd2F0its9Sl03vByhF'
    'sXmQGnOCS504g2mZE1FFykadR59UaHHU15gRWpkRcvxRNPSMwdiMp9Ln5gezcAQCdkBCy53K'
    '6E+n2dZ9iqf3MP6YW/yO2OIyndl5zHPWZvuLzDMmnvUgB28l0vVWYrMe7MUC7HwMTuntnofM'
    'eahkBOJsC0itWViRX8X73Cas29Oz4TTPXgWDuiDqq3eY7zCFtO8ju3lLL/d2FR5e/t+yuoYW'
    'QFTjg2wBNpW3ODSltxDIXD7ObEMdc0IzL9R/hNybVyA+IBdhibmdHSP62B/H7YWfeAYH05+F'
    'gXEaCJpjLmQA9jzt5plDGdw8SqnNP7MOZMEpNMhPd1DIGf3iKjiQk1hvrP5mR3BQwd7Rt/X6'
    '3rXltlzxrq/Jgbyte8xbe33v2wvPLz+CCpxMXRzK+EPwukCX9/rQ5N7CxuWjRmP4QiVR3ydX'
    '1H8ChzwIYQjzCwPenR6cqPP7XgB8Kg5wnhWA25f+lf37nEpntFsZRDvUFNiqzE1mLgl+M/i4'
    'uy5u7rYoc5A7YPNQsk29cZthlD8ku7EZP5xvDkb0bPtgPcyM9M5mCh//vzPICYnoHYwVNwLO'
    'JGX3M1FXsAA70CkrfBdca4ZW1IJ/GuyFTWu7sc4bujdafy4FO0PDTdly1MR4s5mMEbo7I5Se'
    'pdePyZi+Bof5g97C+uXCP0DeeJXTpLBe4jSMyW0gsvpzSR1ptcyMwXZx/EiHPVj+VAYC+juM'
    'V3Bj3uasb0/xnb4auG3kVPT+lULPOaYyktHBcvqOJOjuLicD2zbYRed6Us1dEg+bd618VH4Y'
    'QASZMhsHQ9kPcKu6wRpKq19H9kwm5J9F7ypeyp4jUGYpnpUxyfZhPMR9UduoJ94p54b9okPD'
    'nsbdoZRn8Tew6yB07kPDnse98Z36MTdABB3hGhnXgUrxrvTQsG14d6T+wMtuyX+GA3rWy+SY'
    'TjXxceqwz8nlYLm7FLd8eeDEe6eMdXUpjLNni3DvtWfYW9EBRrVPBJu0hKYyWLjMd/YyYx3X'
    'Bw873rbtL688jGTghPjArvdOHW6D4Z6ilvlzwfj2PWD8pTaFO/ZtYf9DuuTwHJ2/gXk8f0QL'
    'AVuAq2LuCVJestJudih9iWT5j+ghToXRJnxkQfx8CQ7cyquELVGRTrueHUtKoWaPn+Hz8HVn'
    'ufymajFbrLiBAQFIvQiAmr4AfHQmtj/sNpF4kZf0+p1lVRdfLqz2pJKROaGM1NxGYKQ88icV'
    'N0dMCbTYLWFhCStOI7brHBhlEkzeYFKGY5CBYvP9VOlcaS6Ko8RCCWdpMynT5Ww1uFHW69wa'
    'G1IcKv98ynkgc1I3XAvGz+vgukDplao0NL+7UlVp/1cukTY9aoMezNZJ8gbJuEqS1HaT5GKh'
    'jEol6Yo0i0h8R7qUKKKc3EDFo14so7J0rDUA+lkel6wsGVNbfX9lcMgsnMUD+1hWz3K5OXcW'
    'v+YKyLrWQYKPlVyd3aHuZw0FSRqBLfhFqoOsXKp9WLJJBAUCxz2XHAiTnKuHk5ypGaHorLa6'
    'tpS3MGpgzJWoaBVaWcneh5BKq64XT4rwaxWbSlWVz+IzVF5jVf6kqOb4zoO3K0H/PeRHniKI'
    '5QPNbW2TDuFhaJF9ALY78v5hpfjH+W+85j/w7XieaTYe3s8jkQO7auwHTz0893Bb5JuXWvHF'
    'qpfEt7pDXqjQjVgEO5CyGy3CKX9o15HXD+z6O+ouiR4EdvYBOwfq28a04OsDuw6eOnzg4V8D'
    'U20I2Ix25MbXj15H+MlwUXCj5tsdThaB+Rt7H5oRnVjoOQC4uD/HJSfsBRXSFG2URdLsCfHX'
    'Mc3Q484DZHmHhtURAA01MrRrqGUGq+PmXd3f8OQu8f6WI9PWcaPmmFJfaHKXTijLMDYctMcG'
    'AW0UUd0cYAzILzwUUAbUsVMkGFCDAdNkRHY7UNd2Og6RK45HF/GIGLf8ttcRDIiqdBeB1hPA'
    'YeTwAeOnn9r65O2FHSa7l2BBqHWgyLIRHZcnxL9i/n7TM6hqdTTf8yknEeiK4a2XKQEGbaas'
    'qUQrkOPVzlZPkslkFNRypqbl9xVfoBPOAO9FDrf9GEsulHC+SnepPZJzNrZeFubWspXala34'
    '/m6H5vtrh/Tj+xKHGjEx/uhZqpX6LLEeufNcLO5NoW8CUG/4JwwDJwwTukTqh50Wyf5deC2J'
    'JmAzyHi7js1KLudz9HOPIBIHJnHu53DICB9goWnQMAFzliawPH2t1EeaCjHQTYUKinIb6KW1'
    'hybbjacaAsc5BVsTfCue+Fs9QyZUgmMer3JqBRnqJDbHlbGkqFc7EWVHkau2f2PAPOGZlzGS'
    'CsPkwZtyz7X0i2FO/gq+LSv8HPsh7txJKZ3bpJvK3Qn6L8PbeXBuBG5IVTOiPb6pXGpFhmhx'
    'Kdt1xeONf63Dmcg4VPGrjWhhIbWRbIhyPzJg3Qex+xJPfh49W1xyc3wdDt+HV9vfNZUxZ7Yh'
    'nGAkCYzxeJiBwo9eodWbZmteAkj2ToCdrXVdqiE5lON3W0UpRQBQkvFUnUQ3LCUIX01REp/r'
    'ND+katXJFYTpSZLqs4jpubaq2Wd8YoowfRat0Pu3MmIKFJsIL1Cal4vzyU/0t032AnCpHCIA'
    'iH42QvEyR9USPGLU1SUFGAVfZvj/qVfm8HLrk/tHqCKRR+is6j8ncGhFYYvPCZwPpNSotndE'
    'TKyknDTWXQFUQ9C2K4UnDQPAOSaoZozZFh0KYaG+/vt3QMOUX35bXH4VlmWsWqF7k6elEpfL'
    'K4fEETtnCGUJNhCDsTQ4ls7lewxPFXp0xjJB6rJLNYpKJTOjoIhx8BTJD8f5FuzOQSxWgziH'
    '81mNupdYjlUpAvMcEgfAm2dR884YLcP4mUNqZs6Qe5X/XA5nFQ8DpYldjU2eaLpQm++qj0t1'
    '6hDhhZ1aTagUXQIMHGnp1nFTSAlLzlsY2fW5tb5oSV0yJ0HMZertDmS3y5bhnq3om0P+1GUx'
    'ui1HBxaCHsvJcO+kCWPNSkLhnG2a3/L5FteFxs9QcaRFyXFWkzNcB5Ywm8iv0cWhMIVwgAVs'
    'jS56jWsGqbEMLbSbJ8BmQ3HG6jyM1teGyNGp7jwINY5joeozlpWGCL0nsoMcM/xNNDRhtjoO'
    'NPEFzkrNdqv47fDQ4QlJ6hqt9EBDFDeFjztjNpsem/y4yiAsre2UoydVEhr8xlYyJJnFAIVP'
    'Wvhq0twvS3m5l8u2bo7viQvKA6NqmRN+9TLRyuYQwTDYwhcMlUGIA5HWAOmkmNctMbXlCLQf'
    'Cu4wAi+oDDjlaItjkoOxyCs0/FOGCorXv4VrqKTAIloSBA/4EFTKagM+xUaSxXZFD+OFw8th'
    'jnLd0ZAWy7X48D/eI6Kfx5P7T4DqCxsM/0fMNAJoywwtEd4+F7Mfwn8wFAaRL3arSjo83pIg'
    'rn51ZwwVoZmHd91HyALhC5eoGgm6+G/fjHDzmAWnTO8SMBL2ZypUU5F/7ax4VbVu0mfkuYxf'
    'KmlFcsLDFbj5YgRT8fzXuq82aSYILEjTgjfwrjYLNIuwuVKLt7A+C+hkv/mfhe7B2uM7vk46'
    'H4Acn76EpJegeSKlHD3gE2M62b8elwV7rNYXqmlm1VYZrVLJ1IyxJpoc1mmwk3mz1mlfOzl4'
    'GzaTeidg48nnOPMKdggCyfHh1MgYzvd/K/6k8M+G/wnJnBh6mJuLqH12JGqfgyKR8wnx2Zwn'
    'Y9MZxjB7OOfL2IQaWKMOglmoz9IZH7zVabwy+zGn79OrI3/qkXzBdmX2Wdph+EXGjNVHRn0q'
    '4k2EfUVie5Tp0FOopJyGlr6BIdu0JThUoWVR7E57fJFddgYDAsr5R1JjDegpYs5oaCqRX3WL'
    'xjuPscEOe1yyskBkX7ciTmiEdwBwOVmYWgMD1L7ljOVfJN+FF1yi5tcmszt8j7LPxudStUF5'
    'w1/d3Rdf8xSdjhc6PWbEdZMgHzZHvt8tHXelKFoR+R75+BQnVT3/yOlaoIHufeFjQxVHIH1t'
    'nClkDjLouG2IPGslXbaUytwGoV4lVZTsf/NkPB9RP/hKDT0/lYp6MU8TsOAJsP6xKzaE4kOc'
    'h/lkoZinKC9zi6bkjem6f+RvNQ8GaCtLWehpBdQU50QOX1BrUqGZ530fYHeEm5Hlmz5PiC8A'
    '7JiL+0+4M84Ji8cyudFNCjkZPm8IiyUI5geHaxFaSk+wsWEEqNk7tIsHshuBQWpKVWj8nLxk'
    'bDjLZbLbMiL/0Svx7VRDIFZFvM4W+eIf7VB8Z6x/PEVPfFh4dwjBUyfOJzgdFTqPEw2vnXZM'
    'WZhzLhIJITVzFR6x3hjFnbH5h7EO1Fn6dujHEXVGG4MPKRv4araaWdY66TNYGwwqmeE2Scec'
    'snowG3wvRQkdzsqn+WVMz7PmAswAYPez8L1ow+/ILvNNKkYJ3DqADQiHL/ymTXZloWA978kB'
    'bXLNXINoPx+JACUUdCU8rVjMjne8Lx9IobF/oKGt/sDrB08fftdYdwSazJEPqJcZ63jST1wt'
    '1NZDOVVC7J83RE4UcUu9ufBUXJBEVnap0/Q4btzvRsvRGnFJxfYSSnc4+Bh4KKUERA9qJJ9h'
    'p3w6z65cSDEtNKj8Q2IVwXhptSwjUz0X6ygy9tMYFgdAWWS/qhsgO+hXxbRirA/hE2Trn2PH'
    'nzJy90KRBA9/mhj/z35s+EzkWCXk2Pp9uFeI164Vtn54o3KtKPcIfMrUjtuGtaKUUtAtZXpg'
    '5435CVX1yP0nVMQgp9jDJK9aRrU0hpYi65sTYjGUESDJhkrSrRQk4a6RkunDYsvw4nStADVG'
    'Dn0uQM+xBEAhTvYLlafYcBr9nehvLv9riJtzEFeRurNiWLRdrIt/pPRinl5hS9THI8dPDqgJ'
    '8RyNGJnH6F6rPlwsGhRJhhRGJBS+sdg9EpSlwAMncF4He0M3gPEr5Gxo0LNrP7/Mj9B2kz2f'
    'JORTb+yRccAhNNjSvrLKFZ4xtD9bN6bHbbRTdhErhJyHdivdbZaluynR8yc1C5bHxBchLui9'
    'mKwW9wxIVvfF9Kg+Gk3kCfBDWx3tMp6gyz5bLRg/3XCC22xN/IX8dEFTX3OCm+OV/fcFrGnU'
    'fL1HAWXxkGbTe05zP4d6V36Rhg5y+1GK1s9Yl6hid6mYlwFVsch3wCfBFRm0R3jwABSBDdzm'
    'Y41yRLZUoCCtrtDSaOFp4/HrmFbiNAhjTsLUBW8Ej7X8XOanBScV7DBFrIr40zzRBM9TS5fG'
    'tn6DQh3vS21M0aPcKSHHZbqq7JMyr1iFIQqvszAbeeRswqSUdEHJLyFo44JI+7zufZE0hatZ'
    'otjddjY+c6l5KxeSACkq1mbYoAJa+l/GZ1+yjKvWM7nAlSMzRmPwEScWiSRlKSN+31s7igZH'
    'c5FTz8a4zVCnGjgT84RwhY0hwr72q0PTM/ArNZS3ndv/zObQuF95VgYHmWdCMx37woWfGD7k'
    'PrS96lJfpedegCxBzv+B9dPgdcaWdDeWm7Amte8j80wa8r/0Bif3sqJlH/B7xrBjLS81VP7P'
    'vWYS0IoEAY6O0db6xf7CE4avMBbf/m+FJ5adk1TzoLTRDG/NqA4yh0kroHfndtb3JGHhCsTh'
    'd0r6FtXJBH2Uq7hT5Bibo4q60js2JMaXA5rQ5Izg95yh9NqCvUiKdXmHO+G9rK65CluWG77x'
    'Ns8p3Az61HjZZm9ITHqV8O/L8k+p46035YxlUr8kz2r0fxyz+11r+Yeto+LHq/x6uqTd8xGL'
    '36nSAFvnZKxL4VtYEt9nquCxCWn58HtWv99FT2HRZ6SHZ4byKwnxa+tu87VnpfBTSShrs/I/'
    'Zln5nWi4SMR/R4s6Byc51i4BSm2M5UHMjsGgswvGYNC/sUewsXZmQd107ifCWnVY9qOdS1s7'
    'Iej4F9tYlTBxFcfEaWbIA98kgRSJD7gWZVWvk1TSHdxxg4QgmAf2tWNbvq+9h3Loistl20yO'
    'Di1HSMAec5bDnJqqq/P8SXMH9sYoznAnnPOdj5xc+NKhvqz3NVyFLGajZlZyn8aZZPue6jMu'
    'nku5zjwgibdCGa0g80z56lTHL2qZhwl5MLGqWuT7sSNtjQGeysder8jyy8SfgnxhTKn1+Xmh'
    'RaTUcgTTA+yrmaEuQywoU5iIsbZ/rzsiffNHzSjYJRIhj0YttyYzuRNzqO2U6aq4/vzVCftH'
    'qB8HM8A/GYg3cBr/4Qlm2E9zd4qJxwXHsWoM/sPOnrVtCH46khb2XCEhmFNS11wtO92OmKt8'
    'GbLHHbEZeWZnfN8oNy3jOdB/JZ0bTRiAw6Gp6YQoPaZ5TIlrHqsyVY6xTxEk1aL8DNnxyjs2'
    'xfP1oW/hJiaMdwiJeMbwOFBHnDo89xLheiwxsjJnl3F/neQvlZFpqP5ExvBsMmRN9VmO4MMl'
    '1gaySIbQy5+bi2S+by5KlZgeTXu/+ULaq5F45DKXGv2OOsGDFW+ySlYh5wTvcyLkpPDIKkSN'
    'oK9N2Lm4Sgy7OUjknXd94ZJU48m65NvEGni/V3RSca3QQA5NzQhl/K5wz/L7zJZmObcKp5Eg'
    'c2CS3iYFiyKrsHX5LV0M9PVcqzJHq+fZeJ47dqD4C1XKV5faXNQr/oTWZccIKJL8hTKeJ23C'
    'ZPe9ztG6O4NGImYDRj2E7s58CXZ/tPAdiWx43fhnHBOdew7zzjhGs3RkWHKgwTGBWcN8awkM'
    'nG88SJb6qZsuUHOfxLbkFBzvuKwvHcfksR3RLd0fJSf79tpImJhJYHruxyQyARsulrWDzLn0'
    'k6VzkrtD6V9BRNP7vXA5Xg9j7A2GQkwcQhCWVdOeZSBM4PjaYTB6tuj1JkSLFP+uFyP+qcSu'
    'SLSIy4oWsfKHqmbyESbRjmVfmgOIn4C3xs8AoeCkdslNbfh3MXKr0e69PAb/N/ecwxLsGPMk'
    'HntOiRcCHom/MgpyJ9pcwl/7odrsOeeZb54IfjNw3LtPhHn4fcYP4rfnHov2tmsRap6M8/+z'
    '2m/tCND4Qm2JJ3aEj59TzGcEpql9NGBqJDsNx9bHBCCsxzPfGsJVCNIhRRMdpgLjBwRDd9Bz'
    'jQbFCHCXX0yia2Bkl5+VBvgaevGQ19xzFTSOHyXFeHEzq9JN/Kk5pV3noi04HrkfXyt8T5RA'
    'alq6zUg1GSyiSmPnUj7OW7IjfXhhwxpD9RvxMThPoQHxMen0IGSF/3BGHbVMrkmT41B4nCFi'
    'udbcESp9ApsFzT/nXkDojYd6UTZYxiL8Fs/XB/CzXcQfLd5joYlPCXwIawktzQB0obzHfO9f'
    'jcxg3JDkUnkT8sxz2A9ZKttu3JDySBFJDeUi+sbhhChEBamJ0WpnsLcOCtKtVJBOLHsfOCgz'
    'I6MZaBLJDLIO1OxrzASFFx5+SQXzvIGuFjNNfbHB/Ix0NnNzqgvZ4wkmRcnDZ5RE9e2Y2E8f'
    'gj61/M9Uqrpw/DXoAclHlXLlDH7fia7lNnoLQPhOiGxBRD1i4oaHyi/L6EgTeYIgo+FYQ/V9'
    '4gheV5jv6bzioO8NO7PbWfkgY/F2c9CXeahRpBUOcQbT5jZ5b6P805WH7lKVj8jocMb8m/WG'
    'j3kadBs3FY73HDN8POdGplvoZlpPbDOmog3jOwjfe1WHtsxR0U7f6aQVTPu3W0gjz2wQLxAj'
    'YZdyRgo9mIGTrSvd4wr4Or++nRJ1oj0sCweMkZsqg+yo5lBCORTr29KH6hG4iozGwZLe0bdI'
    'DFvnFWe1QKbai8C8cnWiJuL0EJ43HWFZSMpWCqHfyPg7XziTPmdffaavvbfwjRfVkNaH0nPg'
    '8hxHRxTKjgvNtju4EGL4ditTcVxsf0RbKH00Nj8gaDCx6Rx0JdYy2x1e2LhsHwkQHvTRTFDd'
    '3htaNRiJ7ZH1E30evco9UeXBCuP0iHVQV7ok1pGOKKZOXtknhI7xOcqaD0/sUjjl/K4ZXBsY'
    'iR8k5D/gsfMy4UUqmDeEIEUeju9DnvGKS+3KxjkGlMM0ajSTZT8WnN4b+kmv8UqDr/Nq+Bhy'
    'fT1DjPUNpJyFnxdeWP5AcEoP0z7wmCYz8ppLVLbck8yQcsZs2PcJjvDKcGvA0143Iyr2bF87'
    'l4BD03sK31k+Lzj9M/PMvk/AhA73YYcbx3b0gx+HBCA/OPIP63rAsg63DmJDNQnfKPrA4XDy'
    '0vJ+qDR9icX0vuGhstlFF8ut33e8+k2pFGsMBcfRuZTggz37joemZpqt+9rTwl8En+K3PHj9'
    'irQgho8oK/yzz3hqJgZplmiZnLJb6z/JGMUwPATjwenvmTaMwfiQ2DDAmSmsYFfzFBlP2Dct'
    '+8IwmkK3uF68QaBjsAWyHb/J2WWQWPSp1P/a4vofAjWLYzVsd8WqqY5cyn0xUzKMV+pYC0dS'
    'ObgvMZ3xOuLnR+zFd+9wre2fiKDlyDiTxemMYR4vxnKom3c4Qz/9M6FBXT5n/QcpHU9Z+Mf3'
    '1D5vxA7z890H9v3VrN93Yt9JCNoTiPuXDXS+95BMJEu6Zv7EmVuPViJX6v3quNcvstAZideO'
    '9sl302zu2XcS9bbDqvhJ1r4TaZ2o9opd6KGvPslsicxV867+eDuP2XiRkMpRZ9C/y+UME4RC'
    'D6lJ9e/yfAXhnn3yxWwZIseF2PnWe+oiG1O+5z5tZIjzpnU46TZq8KRV+CbeYkO+rIvMUiWL'
    'yxk6JgcK46vsiklR24rFqKWsxq5TvpdadIltFhOhUUzUGgVtidmx3QfcgV4sztV0LXKd+ojh'
    'eLJfnhcyVdQDKLJQtehU2uv5l/BV3AFp+4a1hVeaZP5rkq9LQpc9kHs4yRABE1u7jOK28Hfp'
    'vmwOn02RxZx0OWRTsq6mhq+VGlS6gr7zq6VPcDWS8ZyoIG1QrCvlwSwGwH6sEsA+QO9MwXFO'
    'Dr8m6GG4TRs7stW6e5O9sWpNFIvVv5LNHEkQx7OW/0iZKWLDIBt4CdMkiN71M+5fr1u7CxPu'
    'K+zePWZY8SnycFe6R3dkxOLjm6pWOb5hw3mSjUl9K0s281Rd31F1vY0ErtwvbXaYO8U9hyj8'
    'iVqT67PeVm4ehlarFZdYEUt+7ZhYcFxDI/J+x0REu+sH3A9A14+cKpuOEJld3is7RvY9bxL2'
    'WEPuu4WvL0+pGm/zdpqt9zT2O1+HYSPFqKCUa8yHlc9lKtHM7bzBK4wtScp6wTSWx8iYP6nt'
    'n+XYhMN96Ize2WKdNFQscbLFjBQWh0SOUBI2bLMNzw/w2VRGA5ViVVUFwBUxtt46hk/nBHCa'
    'islIqI7YeZbjpCoKE/9/CWLGSyCmziuv4j28cwAtLekKM1k4o+PSOJ6LcFaBie0Dkmrcwg/S'
    'TYxJBMPiiQRwUiNLo4nrd31AtsdznLPnqV8AcoBbicP7U/rwUGQZg/y2JIGLJbcnT368u6CO'
    'DJAv7gAskFjngI/FgICnzicJSzA/YaBrew1tC+wj0ABMNYpPaw6j60AOYiFBcru6PkCodsY2'
    'Pz7SSt5sdyVjuLhMgOpLlGe3Msyjq2XFfoM0ow46gmbPnWr+DpV0y+9TSwELCw4ibv9BrjBA'
    'YJ/gci8gohKyEDblw9xTam9ExjZ7vTnNVnhu+S1UMyQyQzgvQ7uVndqtnG725LaJiGAqO3DR'
    'a+DbhcuH8gSR5hLNIqV60lHnT7Bp8OjNVFd5WuHNBV0dw2rj/BpMpq0JLzMRtUoSujGxQaVO'
    'j54Otg139Upje1n3p1btW3Rr2N4Qb0/5v8aRRWRowpIXoimcIabL9iqOiCc2IsVG8bt6OIr0'
    'qUzcCDeW2u2jvRyVcUxuk3g+2eOoIWhrHJvgl4sfNyzHXF2hghodY7Xfnr+ZqwRieigz8CAy'
    '5OfJcsIXz4vCeS9wfrSVw+pLn2ydtFIYHOacLAcVOu5ok4O0tOWTHd2j2LVUTPRsz9Wbx8E5'
    'gok+2q78h53/WV4Zf1bLKu5jFZg3+Onmx5WXZ/+2dhTsqI+OYtl4/0h4Dp6XQCdW4afLr+lv'
    'X/E8sUFR5Jn0pkFJLvx02XFyrMNCE9TMcBVXl2w8oi12Pph2FbvC1LehOjvxbITkf+Nv5Ln0'
    'NTLldz8PKfPuaL1YbwVE8uj6nhRfnZOI5Rnv1XekcyNarHz/9mb3a6+0X3t5/dqrfiA9ulaM'
    'bml2JNWzRljLvkanyUVmlIu0MOA4Nv+nh2vU0hPUrXRoSvY+GoLKV+G5HF4v5T9zxK1gHLkj'
    'cqYx8TwBniaA2Zry/XlFVKkCt13j/l4YX8gGVNjqceNQPCS/ksOAnFY55vvjgORYcncorGaY'
    '8g0hhy124MASu3jkJpCCzGR7K7zt01PV/i/Si0O1q/J6EO9s95JQLRdPmXAVZaus9l7ut79N'
    'Cc5VsfUFtZyiS9OKDvNo6eC3gvc5mm9TKLlNoWSwLaG785UG8RVEmYLEDOn5pzjFjj+Xn6KP'
    'x6fWXHjux30JCI+Omo0R5XWOvs7T14X6WqmvS/XVo68r9XWVvlbpq19fa/T1cX2t1deN+vqE'
    'vj6pr0/r6zP6+qy+Pqevz+vrC/q6TV9f1tc6fd2hry36uluu1npH5I17+qxo2f4//mfX1yqc'
    'whXV/ygVqafzwI26b8fLMKMS3cb5/1VeWYf/bC+UV1bhPyf+6+R1S3ll0X/jiv9s/+ff/y/+'
    'VS66f8EDD9y3/L55K64bUzB2XmWl7b77ls1fsGi5Z/6y+yqWzX1o/n2LFlcswdN58wd6jn/L'
    'bA8vGbN4vmds5ZIFtrm2afOXL5+7YL5t1Lyx1v9xbyu47ltj8/G/Alv5wkXLXQ/NfWDhosXz'
    'b3KNWs46+j6bO2/eMtTiWrzE46pY4l2MMK7pC5escC313l+56AHrta1yyZIHFy1e4PIuHTt2'
    'rG25x7t4bOXYBUuWLKicP/aBJQ/Jk4K+j1jj3IfnLqqce3/lfNvURfMgucqWLPO4HvIu97ju'
    'n4//e1bMn7/YVeCau3ie68Ybbrj+hrG2ksXotcuzcL5r4ZLlnq8vtyAYa7t77iIPQahYot4v'
    'wZ9lrqWVcx+Zv4xATVmyePH8B1iEv2y2hxRyXPOWLVm6dP48dH+e6/5HPPOX5/GuYtl8VFGB'
    'W+LE9Y3yytTR5ZU1+I/3/G9OXnnlZ7guTXj2P/0vrK9P55ZXFpdNKf9e6dji0lLbzEXLPN65'
    'lWPuWuy6c76HXbBNm+6aPnfxctf0+csWVdimou+uua4FGHv0a+kjttuXLFpsPVAvFVpuEpTe'
    'ZJsyd/ED8ytR1ls5T8ZyyVJglmgCtaxYsuxB11KUI1oSyqCCJZUPz0exuZ44nl2unHmLlj8w'
    'd9m8+fNybUuJ8lHEXqXX9dBykBDHRZC5fO5DSyuJzofmz12s37vG3OyaNx8dUjgGuEJzC7zz'
    '+RE/xWePLL+ozJ0guTs1pItIAsu8Sz3z5xHg7y/xLnM9sHBuZeX8xQsw4gvnLnct98iYyugT'
    'QI1F6Z817sv4YonXw1EGKy29yYUK0GSey7scn8rAl90G+Vk2s9KB/57A8sAT78Xl6TR8v2LM'
    'ihvHuZZ5F3sWPTTfVQFi9i6bf9MQ2y2aa0YtFXAWL3EteghtjllOAlyyGDj8v9q7FvAoqrM9'
    'md1g3KVDsKAUU1wV+6MindnZmb3vZrOby4YkbG4EQiTZbDYX2GRjsrkBVTSQKLWKSCva+Jsq'
    'KihKxBsqtmiAoOIPVbRUUVGx3lvsjxRv3f89M7NkEzbg06ft8/d5SJ5vzznf+c7tO9/5znfO'
    'nDOjdHFhW7ClS0qIIonkxoRQN8xy5CLJYCyNryUcQT6xVB0NkXpdIFwT1LGdl3QSutKmxU3h'
    'jiZdc2uwrSaMbgyFA35Srq4ZScOBcEjXHmxpJQjoBA116jTVDeiThiXBGC2pIVAnE6LGaK/C'
    '0RY/OmOmLuJvqQtKw/KS5pm6roZgqIYEiOC1+0NtyLSZZDqjqS0UuhQu1US1USH8I0AV+Aso'
    'b1Mt2afqxBiJyDBnqewyitu4dDhu8xLMf0tk/5+AVy+T/W/Gpf9H4Ssljye6ZPfPcH+EsjxK'
    'eeElw7S3LRk7n2/aTsZNaTt9+R90yG4L6vEz0EfaS0JOwEbA2+0y7u/tMs25oBUBpR1y3hs7'
    'hvP5FOFX20aWGfMTPDje0NQQ6ZLYT942QhHLg6L87nDg/t5XnI/urvr5jFW7nN903eX7oedV'
    'p/rrn9157hVrbfry7FIShgUD0LlgucB0OQToJyaMkxxKxm0l6mennAWPOGV3errszpfd9Jtl'
    '9/pB2V10XHKXr5jlIu6+cwKSu+IPt0lu8OWXiaur7KcziFHcnmMk7t1rpzbCTf+gj70b7upp'
    'O7a9Dtfwky8Xj3dTyw8ZuwZdbqr/wMoyQ7ub2l1Q1/jcg27KfkvV4XnvutNvfLzzx5sne3x/'
    'ee/1w1Nne9a8lj9u75HlnljNt91w4LYtr/TZ+Ps3vHbxMa/Zfv9Rz3cT7tXfeduNe1OeXnCJ'
    'O7ypZsa43Zoxm66Uf15T8uOPb70168uvnB+r5pjy9T9mv8rpuK7c9rvWa96aM7ltrOSNre0B'
    'qHDZbqgMBSqJTmiG1qmsbWsKUCNQhD7b7bboZmQXlF6q44RZ+lmcTs/qBdbEmnQzsoI14Ra/'
    'Diouu0yJvUI/qzZgMFw6Ip04i5PTiazAcqPTSbEwZf556f7Rep5Jdybdv0Ouz/DzTLoz6f4z'
    'xi3ZX2l+UrbniX+seZWsAVKz//X7KJNi+0BLiqikZSlJ549Xq8n+KNl3Je9sOvTTaJS8W4Fy'
    'MSk9tIsZ361yMyydw8wkTqaGGZ81yKS4djFqj7ZRRt7DTIfjR5RbicrUUsRgIY1Zjo+//RfJ'
    'z4NHPd7UZLqN8kxQtTWPSz2LbluWOo5u60xV0W0Rul7zHChcg65driHXTjeqlaNV6kteD1aP'
    'j/ucLy0cGV0Zox6zHdOVdswDPXmXL2nHSjrjB8nl3SraO7hIs1POmdCQOh4C3U/j6RYTgrH5'
    'dFDJ/zjS7VP41Ev41KNyMand6kxmygIm1ctMyWdS3Sf9joU/xa+XmYRft4ZJdQ0y48HdFNcQ'
    'YX7GP6kU98iMZ6BJ5ODXR3w0+rjMb7Zcw6jd2nZCy7bD79JWMGRPmNwomgEeLMPHV3bLtJPm'
    'Ij5bW9xN0+5BN7zMJDq9m/YQv8TveaBPE3B7gIrJmFuWsbQC0tY01D2HScsjZaVVQ6IkDH5B'
    '5VKkiyL92o98SpDPu+Q8dhbzrbqQOa52w53dnewbzGEkf3fywh51FnNECvh7VSvpUgRyEKB/'
    'BnIv8fiYw1L0CY9KnYQ4QpQ9mL0reyh7Z3Z3co8UyO1OXkn3qHtV+Uph9Dwl0SKNgomlyYPw'
    'kouf5Flgnwm77WdJ9RxQFXQn12sGSQ/SRSSrlXQWs0HlRUw10w8+DKggTf2qHHjKEUEQco5z'
    'e9R1hDyDWS/FtsAlsRUKVa3iFir40lHuYuSaCzdPyq0IVSDVibWogdkkF+6Lea7auWuopkcd'
    'UsJFvaosxVuilDRHCfsVt0zB55MS5Gx9yKRBQbf2qMvlPlgvtTdfaXe+Eu9FNqRhISU8ugEx'
    'uphbKDMaXbPLK5dG5+wcWjAqVeGuTDnSr7C3MK64Ye5m9qhLlYj5K+lh6vydMTqKIodCpkDw'
    'KxzR6PvUCB2ZyUynezXDMpqj7SDCO70gTmxl+Yc7Ht8t+kweLzPzeuiqbtVcSA1EgpnZrerB'
    'YGknY35mEVAuGdUiIXIH86R8yEOEfriPIZ/PVVI+8+giJo84HiYHTg5GoVdbSuQWXKll0klU'
    '/kp67k76GsY3m5k3l/EB5Rn07PIMzSfDdJ5MW6iRUNlaeu7grqFSiUr+zZQJrpLyKl5J07M1'
    'OzOhFEgwZyd54zllwOI71R2Ndsp1suV3q5uIqGdomzQ9qkW9dNFQlnZuj4pu3eUjeJSSQdws'
    'Lb27R+XZRXeRmcRW3qPqpWs0qAbJn2By4SfPrgDrUMbMrGh0LiONp0MTMDivJIP84AQvQhWa'
    'QbBotnYhmn0Nc2CCG7jZGmQEeVRNH8fskTCtzH7JrYM7Gy49B8kJIqBEYBTIiArFVT2UpKSl'
    'v9RIw1ulSSKeMm0Ls1uOWK7Q0rkxzAYFo8wZJTiIs68YuoD0fR6TMo9oqZRqRl3GpJD4COLT'
    'S6LRbOlBI5NSEIsnmwyAtYhvRny3nD6tREMErZ1QpRWDitBsBc1B0PTK8jV9MWhyQUOEsVjR'
    'wQdBk1MajbppiWbKIkm/V0Fn3zyYT8RwylXwZwwukKae0m66SjMY0DZoBjO1BRIKych5tOnQ'
    'ab6yaFQ9TuqLbXR2j7qgVwVRqod8dDIboNu3QUVulVyv4tJ9SgT9B2a97KmIeeYym2TP/J1X'
    'Qv4YdR4CMRVFBzQ789EUmbZds7NQS2/W7PSiUnLOQcW9hkhmNvmJxch2wX7Ud6AiGg3IvEnx'
    'od352gUaYrDIvB573p+k9CF1JW6tEbeASa1QeG4g8yDw1YpO6EaZug4Nhr2k58krfylykzAC'
    'mgtOY4elKeXUg9ah2DtKPSU5IFtlRA9df6UiB7HyapTysrW1is+rrZQ9pHyyuXsYaVSnKX+K'
    'Uv5u0Hrj81eeEZqIfYC4soS2Tzp9A2NwM+m1o8wVt7aLYYGX607uOE2pjEYZVZzdldWr6sGE'
    'XaBRLEDUJ0ur0tJx4Qzt97Bfcf6w8YQ9h7r1krr1QJFO6k6G/ZLPzPTComFmZjJsrWS/wmhB'
    'VVNR1fGoaoprp8Tn6Yr9SuGQ/lJ5PLLtUtr5Ulq3ZAfJNhA5+zAPdNL7cwsVfuUzaTmMji5h'
    '0jIZnVvpklwtoSfvEdwN+uQk2b5q0RCVRS+RqjNPClwp+U/RT6lKe1cHTt+nMdoIaGcpNDpF'
    'lkh6+8i5rCs2YZG+Ii9S2ASaX5+CpzD9J7lHsZD0lU7hzXh8q1Lajw2dKIMuUApJVfR6PWj4'
    'uLqRcyhraxTeSzJYQ6YKsCl3kHQQSUe+23EENHolHeEtORaXhm+o/Hx0eZoTyxZPrH05ZOwS'
    '+xX0Q1RMnt0n5Lkaaca7FRHO1NILNIpIu4dI54fiBHxM3usU3k+pjUbz48ZTOp0ti8Qp+s2g'
    'pM1D2g+oROOtmb7lpIUBeb4h95u6LhotP1EmhLZBEcMcbVWscJvyPQoTaLuSYn3sHtnHmUyV'
    'amKS5uSB4tKS9OSFmJ310eiVSSNkxB2XPl2VnTi9QTmUsK4hGvWNXBNg+EA3j89WusurnT0c'
    'kOptQroBpLueGlFvd7xs0t4ExWL9R2Slk9R3UTR6eNzJekj1J/UITUTGygbQb8dZRvJWoxP0'
    'LhTYo3YjxdPxusqrlXl7EGk24dJpQVJcGrdcBv1MXAKP1hcXytQqfZiGuWsbvjpuU5/cRrfS'
    'RtXtiXibpV2SAEvGJdHh5O0U67uG5YosQ5CvJy7fdPqlk9OT/hpA2nlLFBsj3gZmMcyGDV6v'
    'tmY4QGTyI6QbWDI81uLSPRtvJ5O5chKUWsnSaDSUcJ5h6b5Rco/JbhQGVmXWKFS2VuoT8s2R'
    'ehyBWpE8Jk+rVH9TJRQcMgeStcDWa6PRg9oxdCLs0iSVik6QQbb2e8z9Fb9Q5n5l3OpamRQo'
    'LbVOmfuXIX5F/NxMhzCwoYEhNkQvVklvaolGdUo5JA15p+NR4Gh6TD1epTqWlHAuHKu+K5T6'
    'bro5Gr1n6pi82J6k2pWIF3na9gQTh0d7VQLa2VpVmzoBdeap+Fmi1G/Pxmg0M2nMdk9vS9jT'
    '9B8T1kP+hIPEzx0PYFyeZu71KHVIeTAafSa+T+vInoauSNHImdhSSZOtBvwuln7dJyw6ZU4k'
    'ZwWWIx/p4eWCGLH8S/ZXiI4iurAPNO+M3l9h6XR5uy5vxDKV2DnkjGDOJsV+BUmptK5o0hBt'
    'ka8YIqSd5KNXq0F3gBpjniA6ozVBL2VppfqT6znsQ9HogyQ9BjFMZLINUhyzdUgZ5Bu420Cz'
    'PekUcvpYYjkl8znhuW0z9lzHTs/Sv5JKbEsoT1i9kUhSF/KN5qqBaHTZ2Hn5VOVJCcWEtHcS'
    'xlr/I9Fo7kh9R8d2UIl8kjOye0Bz6anG5QuJyvBgRZZYQIl9vZro90ejUTFphH6Pz1dHr0yY'
    'QTCh3qpIOEi6EnUEaVcqdGTNY9How2PzLp1uSygrnQmwubFxV498tz0ejbLf0+bdB9qLT0Mb'
    's9H2g7ZPHgO+oDQGsGjF+sBXyahZZf3jeyIadSWwxSAyHSMnG9n2JXoihDRXnEKG6KVoMYQi'
    '1R3XYreWnpOAEbO1RQmwOVq3gnWPEGbMZp5RyO+x9vsM9TWc0FUtkp04Vpr02H440qxOirdv'
    'KxkTcXKYGXBmQ5dlSVs9TSdWp7RfipLXIWTO8uBwcmUCO5xkUzyKufXK2qIZaWxjy3gV/VTi'
    'UV4IdOZJQu4+GUnJ9jSxUT34InvJaL2aDoURZ7ucTh6rkEdKnDyStlc8NbzWX6LocaLLVgC/'
    'XNHJsh6uJBxjS6Q99nxp1yZfwsj5k3G/D2nGnUbeZyh1eQy0VmrUs57U1vjmnHY/ZAB5MHId'
    'U3NPsQ6aGVuvgj4Ub7s0j5jrMrWLZA+hz1PaU38q+mrZc7p6Hkc+k5V65p2inrF++gj0l4xa'
    'K6vxCewX4+oiPQ+TSpfXB0Q2BkBTm1A/6OhyjMQRCuJ76K/tz2Cf4J/8DHBxe8ci4jaxtHTW'
    '+ywFZiDsAVQBDkEwfIAj6AgfNgj6YXSQSXE7oAvxvYDbAQ8CngXsBbzN0tJAofHFBjWVTI2j'
    'lNOm5LSQOxRuDeJQYTuO4LZQHpzEbAl3eRpacMbQR46eenEarMEfisMUBQPBhvZgHKa4zOOP'
    '+IuDTTVKHEX88QQjguSsbuxw8pk/+a+TvFoudgoOD6e+ddHUgThcxEtTFRk05XMN4w4Btx+4'
    'x+JwR4AzYQN5WxzOlEtT6W468bEz4NcBBgC/BfwP4D3AUQDtoalUwDTALEAuYCGgHdAD6AOs'
    'B2wB7ADsB3wCOAagM+XyGLhTAbMANkAWoAhQDqgBhAARwNWA6wGrAXcA7gY8ANgKeB6wB/A6'
    '4BDgM8Axkn8WTWkA5wJ0gMsAYpZcbjrcEkAtYDlgLeA+wBOA5wH7AO8BjgPU2TR1DuAigB6Q'
    'AZgLqAdEAMsBNwH6AAOA7dlyGXsU9w24HwG+BqSg71IB0wCzADaAB+ADVAAWAdoBywGrcugz'
    'ffD/pA8uTPIEQ8FI0N0CdRfwh4qVs9qeJOnOwWg0tT0pC3cE8hqqW/wtXdRSOjsYyfO3RjJb'
    'WsItxMpHOD9c0xYK5uD6QigI0/RFgoOiDSjHw7ElrcoOhav9IVcIp6ipeUqI5AvbQwnlhQOL'
    'YbErodKmkBTuVSlqGSezR1ftfpW31ZPhLs4L+msycJg8E6fCP1Yh1H4SKfW5Ki/sr1FagTqm'
    'qfPbQpEGkqwkXIYZwV3vb6FuSi4OBYPN1KPJJaFWNGIuObtNHU4eeSKdoj5Mjj/XTlFTx8Wy'
    'KAmfyJfKHxfCHBNobEZ5xbK/mZQ9X/LjFD785+PAanNlZUMYS9+3ZH9lY3VloK2lstHfSXqr'
    '0t/YWlcZ7GxAudVJlbgS0IQ7MI8mVZLTy+iwRuzQVUrMCqkq22S21an91bjnQIXV/ki4Afdl'
    '1OAFYT11tbo2QOZBcvG0llyOoFapa5txRyBSizca1Ta3RQLUanWt1DO/VZMz76FgINzUjrtZ'
    '6kYljxfUjcFGNIWiXiK+1iBq9qa6JShHv6cGQk5IfaYmjfYj/s+Srx4S8xe1zBTsBhFfUJaj'
    'Y2qZJRT1FfE1EYIfJbfHqkZdkdwRaJXivZS7PhhYXOSvaQhntEUipHcL5cndHWporg7jxgZO'
    '0lMeCE24LiPc6W2qkSdkP+5YuKjfIaa12R8J1CszM3rhcyqzsTnSFZf+KG7gkAs7ZQ1NNeEO'
    'vPgL4Ro5S2pOEkTDE6rzRoJ40FMYFyoJdkZclIGGXIbq5MpJlQ0ixxJ6dkMoVIILFC3UDbRS'
    'Nqrnon5Dz0FHDBf+MO0LBhcP124L7cOFkeHw5ariYOQEOTFI8NJ+ghtRCyfBZIUDbRh/HcQv'
    'F031qcjFJndbSyvh+2+kUKyVz6lKcKGhNeSPBE+YLW+qSptrgIjRUOqOVrlXXFj/kAsh2XLe'
    '1AVUWbHLHcItmLZmYq8iNEJZzCeYYlyViJD4W6nqBnLNai0lCWQr5BaCdDuFexTkfkx1VxO5'
    '5HNHLCyF1lP1YGkrdS+FS1uRSnJxhLpP9jdFwn5qI9UQDkRCSl4PU+j29toWXMKiNlOt0Hpk'
    '7D4CX1NNJExOdSmEUOD0vwxmZxYVZObxeskGJfY2cP9uiDszT81A+F8JpcWZRbHW2hAu8xbk'
    '50vXvWDHI/yPQlmxvnKYi2f+zvyd+fuP/EuV97LOYS9ivexc1s9exa5gb2JfYF9n32E/Z79j'
    'GW4y9xMcYLZzBVyY6+RWc49wz3K7uT9wWv0U/YX6BfrLeC8/h6/m6/kmvp1fwd/BP8A/wT/L'
    '7+L/xtOGsw3nGnINQcODBqOQJ6wRJotecb5YLTaLS8TrxZvEW8W7xdfEt8Wj4jdiiXGB0W9c'
    'atxgfMT4pHG38R3j/xq/NfKm5aanTA6zx3yD+RPz7yxvWw5bvrC4rC9Y19sesL1jG2e/0C7Y'
    'G+zX2G+x32nfZN9r/8B+zJ7sYBxXODIdCxzLHTscexwfOExOr7PC2ersdw45Dzj/5PzU+a2T'
    'bOasQ/vPw9XkdLaGbWSvZnvZ11iam4Y2p3NzuRaug7ueu53r5+5Dy1/gopxTn6tfpL9Ov0p/'
    't36r/oD+kP5j/Xf6s/lZvMBb+Xy+iK/jw3wnfwu/kd/MP85v5z/hKcM4ww8N0wwXG8wGr6HI'
    'UG1YbIgYugy3GT42fGc4V7hAmCFwQqawUKgTIsJG4WHhWeH3whvCh8IXwjfCFJED13ziQvDs'
    'bnGrOCTuBc/eEt8XPxW/EL8TVcaLjLOMBqPTmGn0GeeCh3XGVuONxjXGh4zvGj80qk3nmtJM'
    'l5sEk9OUb6ozXWO6y7TVtMP0gumg6UPTlyaVeaKZN1vNJeYac7v5l+Zd5v3mN8zvmT8yHzEf'
    'N1OW8ZaLLCZLnqXIUmnpttxsudOyyfK85fcWjXWy1W3NtRZbF1hbrDdYf2n9tXWL9WPrX6xn'
    '2c6xnW9jbSab11ZkK7OFbRtsA7YXbK+gv861p9kvsgfsnfbl9pvtffb77Fvtz9lftO+3f2yv'
    'd2xwnI+eIRtsaeiXl9jZ3Evcg/pP+eP8JEOaocxwJWSp1/ALwzrDk4ZnDccMZwkXCjMFUUgX'
    'coVSISDUC61Cl7BK+EiYAZlKMqYZzeBLvrHIWGa82viBcarJbHKbCk03mW43acyp5vPNl5lZ'
    'c67Zb77KfJN5nfkL8zfmFMtEy1SLzsJZXJaFlnrLMrT5Scug5S2Lynq29XKraC20LrTWWZut'
    'XdZr0O4t1let71qPWr+zjrNNRLs5W73tWtsq2622dbYh21e2ifbL7LzdZL/WfpN9nf1e+/N2'
    'lWOC4wIH73A5ZjtKHA2OpY7Vjn7Hc44XHa86/uj4wpHiPMeZ5rzMyTvtznlOv3ORM+J81vma'
    'U7qpiD1y8jwrhU1lLWyYXcX+ir2bHWAPsu+zn7ECRqqfu45bw/03t4l7mhvi3uaOcVP003BB'
    'IVNfqu/QL9P363+rH9J/oj+ip3iGPw8SW84v4pfwq/jb+Lv45/jX+L/y3/BqSGqNocmw1LDe'
    '8IjhKcPrhoOG9w1fGWhBK0wE1y/D3XizkCMUChXCdcLPhbXCHZDZNyGxXwsq8WzRJRaLlWKP'
    'eI+4WTwAOaWN5caA8Vpjr/FOY4op11RmusX0kOll019NU82COcPsNdebl6APHjQ/D8k7bv7O'
    'TFsmW5yWCssay68tD1getzxjed8y3nohesBkdVjLrVXWxdarrEvRA2usz6EPJtrOs1kga522'
    '62y32O61vWjbZ3vd9r7tY9sU+zQ7Zw/b2+xL7augJ26zP2Ifsr9sf8V+seNSR4ajztHm+JVj'
    'o2OLY5vjU8eXDso5zumGvviG8Nsnn5c7h53CXsKy4LmbXcyuZG9mf8neA75vZ3eze9lkbgI0'
    '5cW47JHLlXDlXCt3NbeKu4Xbye3lXuPe5z7hkvUT9JP1F6Mf0vU5+jn6Pv19+k36Qf3L0CGH'
    '9Z/rL+R9/C8MgokURtRyuSEqpEOXkQM55PnLJmjVbfxu/jCfZ9hlSBayhe/EPIz1V40TTWtM'
    'jHmxZSL04Hr7DnKAa7X8LOYHxgJjj2mTaYupF617xLHX8YbjkOMTxxHH145k52TnNGe2c6Gz'
    'xtkE+Vrn3OjcDCkbdP7dSfXLzxwuxJWWMrSzj13PPso+xT4DeXqZ+4L7irtcb9Y7IU/Zhvsl'
    '6XjJMEPQYxRmYfxdKwTtm51fE97hpThkv/4h9gdcBneX/l29yNswXxTyLfy1fA+/lv8NZost'
    'fLphwHCduEpcI94BDfeAuEV8WnxefFF8RXxDfE/8RPyr+DXkR2M8xzgVuu4yo95oMWYYczGq'
    'y43VRvnQEdmXT0H/GHB9xwZt7mFz2DzWx5aQzfh98jzXBz2+ntuAkTHAPcZt5bZx27kdmM32'
    'cPu4/dwB7iB3iDvMfcR9xh3hjnLHuW+5tfw6vo/v59fzaYJOmA49PVNgBXSTYENrPZD/PMEn'
    'lAjzMAqqhBron5DQDB3eKSwTlgsrhOuFG4XVGBvrhD6hX1gvbBA2CQPCY8JWYZuwXdgh7Bb2'
    'CPuE/cIB4aBwSDgMvfWZcEQ4KhwXvhUoUS2miOPFVHGSOEVME3XidHGGOFNkRYNoEm1iuugR'
    'c8Q8zAwl4jyxQqwSa8R6MYS5NSJ2isvE5eIKkfRrn3Os6Z88q5kVrJff6EH9H8B0SPw='
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
    ('devmode', 'true'),            # cnc-ddraw's name for "do not trap the
                                    # cursor". The game does not use the
                                    # mouse, so trapping it only makes the
                                    # second monitor unreachable.
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
# Clear space between a description and the button to its right. Added to
# the button's own measured width, so it survives a longer label or a
# different font.
BTN_CLEARANCE = 28

ADDONS_HINT = ('Extra files beside the game rather than edits to it. '
               'Apply and Restore leave these alone; install and remove '
               'them here.')

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
NETPLAY_NOTE = ('The stock game finds opponents by broadcasting on the '
                'local network, which no router forwards. This replaces its '
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

    def _hint(parent, text, colour, font, pady=0, gutter=0):
        """The quiet explanatory line under a section heading; four of the
        five cards have one and they only differ in their text.

        The width is taken from a holder frame rather than the card body.
        A ttk frame's winfo_width() counts its own padding, so wrapping to
        that made every hint 26px wider than the space it had and clipped
        the last word against the card edge. An empty frame filled to the
        content area measures it exactly.

        gutter keeps the text clear of anything floated to the right of
        it - on the add-on rows, a button. Pass a callable to have it
        measured when the line is laid out rather than guessed at now.

        Packs itself, because the holder is nobody else's business."""
        holder = ttk.Frame(parent, style='Card.TFrame')
        holder.pack(fill='x', pady=pady)
        label = ttk.Label(holder, text=text, style='Card.TLabel',
                          foreground=colour, font=font, justify='left')

        def fit(_event=None):
            width = holder.winfo_width()
            if width > 1:
                edge = gutter() if callable(gutter) else gutter
                label.configure(wraplength=max(140, width - 2 - edge))
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
                          lambda p: self._patch_body(p, EXTRA, EXTRA_HINT))
            # Separate, because these two are not patches: Apply never
            # touches them and they write files rather than bytes.
            self._section(body, 'ADD-ONS', self._addons_body)
            self._section(body, 'CD MUSIC', self._music_body)
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
            """cnc-ddraw: somebody else's program, so the project name is a
            link to it. Not a patch - Apply never touches this."""
            self.ddraw_btn = self._addon_head(parent, label, name, url,
                                              self._ddraw_click, first=True)
            gut = self._clear_of(self.ddraw_btn)
            _hint(parent, note, self.dim, self.small, gutter=gut)
            _hint(parent, DDRAW_WINE, PALETTE['amber'], self.small,
                  pady=(4, 0), gutter=gut)
            self.ddraw_note = _hint(parent, '', self.dim, self.small,
                                    pady=(4, 0), gutter=gut)
            self.ddraw_installed = False

        def _addon_head(self, parent, label, name=None, url=None,
                        command=None, first=False):
            """Title, optional project link, and the one button - laid out
            like a patch row so the two entries read as a list."""
            if not first:
                rule = tk.Frame(parent, background=PALETTE['line'], height=1)
                rule.pack(fill='x', pady=(14, 0))

            row = ttk.Frame(parent, style='Card.TFrame')
            row.pack(fill='x', pady=(12, 4))
            ttk.Label(row, text=label, style='Card.TLabel',
                      foreground=PALETTE['text']).pack(side='left')
            if name:
                link = tk.Label(row, text=name, cursor='hand2',
                                background=PALETTE['card'],
                                foreground=PALETTE['cyan'])
                link.pack(side='left', padx=(6, 0))
                link.bind('<Button-1>', lambda _e: webbrowser.open(url))
                link.bind('<Enter>', lambda _e: link.config(
                    foreground=PALETTE['cyan_hi']))
                link.bind('<Leave>', lambda _e: link.config(
                    foreground=PALETTE['cyan']))
            btn = ttk.Button(row, text='Install', style='Vo.TButton',
                             command=command)
            btn.pack(side='right', padx=(6, 2))
            return btn

        @staticmethod
        def _clear_of(btn):
            """How much room to leave a description on the right, measured
            from the button rather than assumed."""
            def measure():
                return max(btn.winfo_width(), btn.winfo_reqwidth()) \
                    + BTN_CLEARANCE
            return measure

        def _addons_body(self, parent):
            """Separate files that sit beside the game, not byte patches.
            Apply and Restore do not touch either of these; each row
            installs and removes itself."""
            _hint(parent, ADDONS_HINT, self.dim, self.small, pady=(0, 4))
            self._link_row(parent, *EXTRA_LINK)
            self._netplay_row(parent)

        def _netplay_row(self, parent):
            """Ours, so there is nowhere to link: the explanation lives in
            the README."""
            self.net_btn = self._addon_head(parent, NETPLAY_LABEL,
                                            command=self._netplay_click)
            gut = self._clear_of(self.net_btn)
            _hint(parent, NETPLAY_NOTE, self.dim, self.small, gutter=gut)
            _hint(parent, NETPLAY_PORT, PALETTE['amber'], self.small,
                  pady=(4, 0), gutter=gut)
            self.net_note = _hint(parent, '', self.dim, self.small,
                                  pady=(4, 0), gutter=gut)
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

        def _patch_body(self, parent, keys, hint):
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
