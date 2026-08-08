#!/usr/bin/env python3
"""Virtual-On (PC, 1997) patcher. See README.md.

    python3 vo-patch.py                 patch a copy of v_on.exe
    python3 vo-patch.py --rip SRC DIR   rip the soundtrack, no window needed
    python3 vo-patch.py --ddraw DIR     fetch cnc-ddraw into the game folder
    python3 vo-patch.py --netplay DIR   install the UDP netplay DLL
    python3 vo-patch.py --selfcheck     validate the patch tables and exit
    python3 vo-patch.py --version

The version is the VERSION line below and nowhere else, so there is nothing
to keep in step with it.

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
    'eNrsvQ14VNW1PzyTTCCB4Bkk0aihTu1gk0ugiaIlAjZColSDphKUVqqgEOEaA4UZxZaPxJmR'
    'nI4Duffa1rbeqxRvr7e1/2vv9QLiVz4gAcU2gEqQD6NSnXH4CKhJgMC8v9/a+8xMQqy9//d5'
    '3/d5n+dPa86Zc/bZe+2111p7rbXXXnvGDxpsqTabzYH/YjGbbbNN/SuxffW/Wvx3weVbLrC9'
    'mPHW1zfby9/6euXCRctcS5Yuvn/pvAdd982rqVnscd27wLXUW+NaVOMqvW2m68HF8xeMHzFi'
    'mFvXUVFms5XbM2zeZx/+e9tJ9azTdsE3httTxtuew48b8V+h3dY1Elcn/muUr2rkPkXBbdfw'
    'y78l6mFN1I5+leCVS33HP05VRC6FKbZe6WiKrXTYX+nk3BRbeOiXv35yj82WM8jzvhtSbH77'
    'l3833rNguYc4/DcN0HPJnVD/APnc8fPneebhfrZN9x3dsz3fvxzGqnH8UlXweQMP2nWd/+u8'
    'ciXfmqDuX7lMEG2z5eK/184vN/7eZct43zDerjE86Pg3jl+g2u3VOBX43hukvkWqnOAaOLdl'
    '4npgkHKeamk3XYZZ1xcerL8LqhffZ1NjgzGSDz49r9xU2//591f/3THTd8QdLHVPCDQagXV4'
    'sD7T9r0a/Fj7EAbdF7Zv4O/NJPH5QVtwtiM6enzIc6WteI/hX4ISbQ53GPIjlj1x6u01viOO'
    'osYu44+j/e7ba8zmQKO3Y309bn29KUZgqSptQ3VmpTs3/I//arMFcdOWxiKEZU02v/NtdW8m'
    'vd11d3OmrcHXa/ceTW7+a6GNLFvcYvhJvl/evvdA0X5pnT37gkWlIVYd/u3EOCzhYc/abIB0'
    'VVup29HwfkUNnj+OS/g3v5HnVXxer577+fzvAXf4v6+x6iN9BvYb/nri7ok9FWjO4wJCJ/I7'
    'vjRL3en4XWg63LFsFvAdyfSlEav2iDMWi6n+BPZ704sa16RpDBQ1SverGl4mvJEJ8XIWPr1X'
    'WvDf/KwFCt9HXj0Xi60RBEQexFcodTl7/CxKcdxC5W6H73BX7Ed9s8y/3HnHzO/5juQFv+8I'
    'FrWlPaWrCD6Sbo52NqIn+72z4+TwEbuywp3ObrGUOcEdXnw1SORIJh+bc9wOPnr+bCzGR81t'
    'aawiRsAXdJnt37/r7nt+GJrXh04J/YSmxZI+wyg/vCC8RH0LBD6SXGWGbqXZd8bufQ5D8qw5'
    '6/BAUA5fFQcl+fFoVBmcdRhvIg8AlKqG8MtXxWtLNQJ34GFkMv6MD714JT8q2h2Zip8N1m/f'
    'kVwMnssiXFQdzgBh+LbmtjTwH7DoO5LF1+3obXamokJ0YZX5wz6zJf6iEC+CI5zq9QYn68p1'
    'mzM+J7CjWGJXcMbnviPpZlkvXvBpvno6qzf4wz682ODgiOz2jOS7LHyPqrd3oshu3HSNAmF+'
    'D3DvaNFwyzy+hX+i24hvqz8Cr2O9VNboybAqaV/vlHH2ZAqZOhScO1oaqr7ifyAhswXgBa/p'
    'wjeEjeyISgK7jcAnVCreDU4JS3UyMCRnNrGXfVUdPUb+fffL3x9NVbW4p8VrsZ1f6nhyLYO8'
    '7/qK9ye+4n3Yei8IyhFYHL6t6aDrltBDA7tp+4pu2v6mbqZ9RTfTvqKbaV/RzeT3uiu+IwXB'
    '7LkU+WRdFMqhIhHY7R3i21rQUtUQwhM8zbQpyZaF37m4OtdXkyQh6jhhs+5GF6YAoqpa+pPD'
    '6WBV0fr5iswuCI7w4K6osbjc7QJDZzdecTu4ZCLKbmjHrcwAbDGWXTqNc8mUq79psxlPNNad'
    'vlFumvEyo93Y2IgpivhP8KszmF3KRnZ7lq0vkda8w3xbnS1KfhK077WLNL8RF8iYzHD70+hL'
    'C4ScfxVKsIfkTSW2s4TlqyajBMQRvqL+Ev5f/CLXHV6LaxBV9K+/NFF/WxphkTnn75/mfGL4'
    '7wdegZi2tBLrzXS8iVRQUj4Ur83qzx1mK2Zo5/paSgyL8oKjXYr+0ApriO1Z78d7JXiGB50s'
    'eHse+Vw+8wBwPWQcoZnsN6uySSVOkW2sRAjaedfdyfisO7JcDWiBxZnZldMsGXex+ZR7YQqR'
    'V+Vbnp7iuTCofhc11m3lZ6AX80V3taogfaRGT4qatnmJZS+XyjLNVuDZ5euNGf6fpsgYFJiv'
    'uKnLm8+7PSlqFuXHq6octRDsseyF+HDzJBnxTLyoJLChSvcc3M/RPZ2rr/N5bS11z7Ljx+yv'
    'X/js1Kq4fK9P8TUO9TXbJ6/zfr6FIBU1otC1fFUFNWUCflynq7lePSx1TwX0VbcQ+jkcjHET'
    'FUL46trgtEzoKQR3YuwdfJTlVIySiy5NIKPgWkmaklnFYiSHe71nmojP9CxVPi/WDqapaoi9'
    'MxYfT9kPVjXWGAo1haEV7oUoW+hMMGEp606u0+JFzIiOZD709Y4y1l6PwaxFJXbv07gssnuf'
    '9PVevOqfzGq3+6WLOXEIFNuiFzX4GlN9nX2hyhRH6ObU4reMtS341NhYmvX3xsbyzIVNnekZ'
    '20PlTgdePf6cvCrPWmhsnJO1qOmD9Ix9vlMus9ydYzzR4jtlN/fhmv6m5/e+3qGrflu74uI0'
    'tB8svTg9iCJmm9laD9YzNr7ddNzZdCzHPGt+hs+9Tl/X130nL/R98azvs6ksYYaNjbuNjftJ'
    'DneBlo2NeMDnc8Ct1W4nhi1TyMXhdrkpVQo5SkCNoKFimiVdjjg0uQYVjQYVwQUVwUWKz1Jf'
    '6F8kcsVZqgqYT1F5li1J74y9s2XPjHudHCyzqefDpk/TFu3+HM3k7/KdyjHWXAOtjBSQRBFf'
    'Rgn4mWcRghBBMgk8dhYAJM/n/fr1VV2a34eP9XcY6sjTZ+O/E/z+PKAO+t0v4gL2foq/nnK/'
    'gMsUJ8fX/3e8HYk/nlFB9Z7szq+anbYGY5Nf1Pcp0VQWDjhItzfhnjaA/w78nPKpvLnawWd2'
    '78uQEM+wrVfcz9qF3UlGHFuWg5CNZfdSdxr3uZpdzdbQK+6nFIFn0jC1VL6ILv4ORJo52R3+'
    '8Jc224Y538BHYfuUczHCQRT6ztkNf02a+r5WM8qBK5J0F9axOUXNJbVT+ArgfmuIDJpDADqi'
    'QAkqyIMK8qCCPHI7/iTki+5d8JosTmMD+3hew++uF4iPCAGvJ+Emeuu4n4J7MmcmZ6x9YLcx'
    'p2e2ps3F17ZQNawqvGLvzALAO1upFJx9jtBunyLFjMDruuJ6VTGISONY1300qW5C+xxnNK1k'
    'JD//d/38ePLztLnaniCiOZYsGU0dMLZvo8UtMrV0AN3hP3wBfX0wtGpSE86NUxvmKVbK3rBS'
    'j+5GpdsZKY4pPpH+x2csw2+jFcDJCjYheDJYkRm8EKqqP4wfmChzXqVqhtlIkXx0ZPDGzGBW'
    'nm+bg4UWE5AUYbf5qkqqDR6RRH+vZtXwR7/gNO9pg4AgXPJbqRNZ4b2/iCMflm+WxxkqT0mt'
    'O8VhMNb+ToR7in0Kf65eo9UNqWM1vossFftlynHhnjKyGHU5I/AwmHo9ca1JDY2PqJ1CyYeX'
    '5Dxjo5DFa/zjBwqKUjTuHQm0jE5JcCBn10ol1UKz7RoONVtnafpoQyM3Cx5yp8brmJIqSlI6'
    '9Q1i4t9/jt+kKpcWkdmHccP5yFJbsp2KMGPZHWTxfuMeGXZOy18ORBBDEymFuNK0EvZ/BsPt'
    'k4T8iivVSfwZaUu8L9qNj27jR/8pcj0uP7OTUQeb5YGELNJMm+DWVwfSf3BclzSVmUz97/w1'
    'SyCWXcAJ6M12+gEc4X/ujsWgoF65+SAehNepXznShlIi1QxWEa8vnQ8G48Fzg/FgMlSDGFiK'
    'R/lkcFG2Jmm+UAUinj7FVy8VwDvZ+ui9pGVfc0qV+dO7cUtCB2GHjz0hvp2syFhrvmxNE0Eq'
    '/Bv5r9OJegXH//vCxYLrwBmxVvopufzKsauiRtBuKbtJIjO7S5NzkkU3OCJWqdr7D0r51LhU'
    '/SWwAjbj89f4J/JE3+DgXDkIOJT9kWpaBWSWHMUskXCf2APKvM4JXvMcNPngiHr8bU2jbWU3'
    'u3yd3/lNE259p1JW5cuMlaBr62psrEw952u01ze8jpLF273HfB9/BypZetPHGZaZVtf6As0E'
    'uEu6/xEeJraEL1D7Bt6un6uMqBG+rTm0DVS96+uVvSfaDK7OJco9km4ZglbXE4agRymg2sp7'
    '+wo6uljj+GR9Bu4oc5eyQCFH3CGPmJuXiKquTE86rG2Tcx7Oh6xxvQzTxW5saFl2MfqUCzP1'
    'HhhQLkgLt29rXouyp1wwBY8KWNoS6gdz8ijFTZx+oLI2DW5ey99UT/rg9WjI4nWx374jlVDh'
    '5ohCZfivxSBOGSW3/2SzNK5AAZ6uX4g2tpTsUmR0JJVyfvNc/DTKmrWpOluazFIGlWrSAmrD'
    'zitEBZ7dlrb9Cj0rp229Ij4/p1+lByuOgVxFn9oOr+Rkq/yPrBaYLwUOpkOWl6PxnFh78BrC'
    'Zyr0BLoNv3DPaNocMFZyMYyu6IXS396Rhp9rHhad5DqVYzELFToHGi/J9APzNIeOSOqfweyF'
    'ysR/AfV5X6ydwkK13t+vJztMSmvAS++/BLO38Vf2E/zVEByxg79GPMlfj8JOqAiOe4tPxj2F'
    'J4Z/m12U4orIfbHk+UFVXWcEyvF4PSsuTmMjRmASHzwhD7bJg7/jgyflwQ55cAkfPCUP3pIH'
    '6XSpCN7tSnWf3Q/nfZwEs/m6qJGCZbfxs0ZtIKixtfROoB+END0I9Ed+cE7kRJVvhXsuNOxH'
    'xYTVIwZ8r+d4D5c2vWv7ffh1mV/N98ZmkzjqTn9GWntsOpWi7AYRNmRCmM1X19IQDgoecTcx'
    'mP2kupsczH5K3ZUEdnhLzNGsKDKENQzWNUKun1uTYnymfoIzdaX76rh9VCkcdh5njRzIWZXu'
    'HOEnIafII0Aw2QzCYHIC/jpzNOvBC6phE8zRHvUrj70xRy9XvwpAE+jDChLWDm+G0MJtymy7'
    'Oslsc1iE+zfBxloEvohDjZPvyERMFELTaBJibDxVsp+LSDPWrKVW5A48Bb7W9Pey+Og6CJ9v'
    '68SWhsl1hr8XaJ38KHRZXmu9c4LiPfOdHum9E0xUmMwEdwRHCBOMECaYHhxNyjQPTRotbDAx'
    'OJqECe6bYL4/abRihEoajHgSvZT0o+quOx3jXLLuT1SO0ETwms34bjNXKPO7ituNtQG8WP8C'
    'ntU3vGC7ndPHmBjWHTbwUWD3quuK9g+YlxLzU5DI4HzTYq+v/w98LF8Wv+c9LF+HSi+yg2YL'
    'i/ZvYJsJVCh5ZPXdbgTuFO7q0Nz18hBqGs3ntP5RtHszH4SfOhWLvezoh1N5HxzNCS2wf9Vt'
    'xsYRRCCY6IPvNH2QEb02elmSP+x8+BsIf/0T9hdqZdosTmNFq9KL0wjY6vfVAs51FhxtafO1'
    '269of+RbsbgetH4rsdD5nabOjFADp+Pazfxr99S8zIk9ckrb/fGxNgL3o05U8um5fvosUCVE'
    '6WILnBmElRzuJaApX9or1BzCez+lHizfyRiOTSNqo22cL4VrAjs89TLDaz60iD99UOL/ktld'
    'FlJeZnBBZBKgpxNMdImi/WDKy+rAoy5O8aC+PKoXhJx0F7ngrDDdZf97TGdN2VKXguPVvsT6'
    'Tt2RA0PFsbFzqBiVnUPFRm8fKtXl2ZVv0rbbWswJXjNXe1a7Pd8o6ja7gvpT9U3dVlbX7IjT'
    'A2GidWNJOZpwxOMr7rfZUlv41yY+Tt1Ax2xwmmuDX67poYoUuCnMbOJ80cxcqoRtaXQFpGMN'
    'x8x+HI+nnAVwD08LTnPCwaskdImjLY2MPLPsphs2kMOBtslYyMOdq2Kai4ij6ZZCU2u65QPw'
    'ZAYVMEJ+sG46htI8Cr3o3kkzxy+XRtv2Wty264V/3L5t55p9Hm87cJtne5e3B3C7VtyiT7g7'
    'cb/lDptaZ7TwASfqjReBSTKBUTJ574XeI/huSIpa5C9q3DKDa1Q5X8Zf/P56fH+59f0I75Gi'
    'o3DS+t15qCPDllqxZbqUe95NIxXPC1LiQDMgYRiawe0E3P4APcDtRNx+mgrqN/3uK3G/uVYq'
    '8Lsni++aTS5Fk+OsJod7j7xUK/Nq0W58Xpmof7a6rcDtnMRThiNcZLuYt/NxuxFQ4pZuuaNo'
    'Fbd0zT2qynrkMztrWJ6oYYXUcAVva3F7q4Lbj9vjAvfj7voUBfISDfIT7qW42zJTUNcP/0/c'
    '+JqSU79r1/0Z5Y0A79NUUy/oVlnZTqkMD19M9GtzAqpXcLvQNoa3jbgtVf3aitsu1a/tiX61'
    'J/r1dqKGDty+oPp1IIWxJ9KvTtye0OPxp+TxOCzw/FX5y/5dKn7nhmJrvBzo31GpBJUf061v'
    'uVlTyUlFJZ8L9UgXexMA9gnBXMtbhkVpgnHgNqoBTE8lTA3J7V+v2r8+QS+Rl5Qehm9dqbr9'
    '6aoFd2octXmp8XYLUjngNwrN4naCQu0E3B7TAz4R94Pgge3XqPZvTrAIx1cPRGWikdlyWyfU'
    'mng6N5UEVirUittVqssLcRvW1IrbOk2tCeCXJ2pYkUqk3S3UmspwKAHez5XGmELaklRFpX73'
    '0lSLXutxt6WCzP9V4/ua6t9sq39p7F/RjqKjQsOstT1VY/uJBFRPJnr7VOLpM6mUXXN4+yxu'
    'x6vePkcTXPX2ed1b4vyFVEUy/5kq376YykgooenNiRpfwe0cm0e4ArfX2i4QrpCyiitSGSp1'
    'm4hW3N6i5WlqnEE6EpUdwO2TqrJOoQOp7HAq46AUKnenJjFIWPodvfR8uz8Zf19LEfzdbeFv'
    'iDciC4OcT3W73RoEmeMPqJX8TGduhZi65jZ6JrAUkUVfx4fizLdbky/WZK1FPazGak/hBppn'
    '8cW9DZy8ZW3vt6mySJcXX8mrdCvvC1fzlFFH04f+Ha0lcVHtOuXbwF1BUWNgd5VRJlNjUJbf'
    'UNV4UcQ2EY6rbMp4yLEW8Vj7br04N16WFZO0iXSnpT2UK0gDtLdy4bnKMQKX0nW6SVp6SZYs'
    '06h7KNPZE79bHr9boe+qzBFL9GKtNLxhKZ0oC5UT5WtA0AFZ6p6rIwiIloeh5G9gnUmzb9Fu'
    'IegBM3BK8gz8ojUDTx44A1c1/D86/8Ynjzw9T5AW3SkWj18ZvyuxJpTz5uQLrDn5GfzQc/IR'
    'xYGTE7NIaWIWmZ6ooTyFsX1u3jJCUDNJZUqCSaYmzyKzrSnyxrW2r2SEKqsZrg6NqRCXlvDA'
    'ZHGhCgcMs2s/SEKZN9vgAxnBQRaVU2IYAjsM//XKiZGjq3QJSSqtM7ZXubnQ6Ft6kFvV8F9k'
    'bksaeTqlH+PagNg74bsOQWlrEbIhJsGJhv8XNBv35oenfAKafeiA3BoMk1gzgsbcaDGEdd1J'
    'zSU3st8IfM/OWrDq+2vGs2SXkCWVphjUSmJ3pO9zbcdsng8khu9+n2sPKkIsQF+RwoiEHSm7'
    'Kjz8BIK6eh1GgNYAbF43oYNZnBM4avi77RI/kaNX9zOfUyEZ1Fo3on47YwLAkPbwGPQ6NM1O'
    'Z5OvNwMrV8rJ4CrajbVjt9bjezxwATaGKi43vtvedDoNq/rA3drruLSxy3fauWrYFo7yS85E'
    'UF4ujJBMrSZvR4Nm9jMMqdt0kE4Q3m6m+R2+P4puPiM+ERFRu18T/00/5Gx4VuwmrP8bP2s2'
    'nZHdn4m9b4mav+5z9Og1esuI2asMlxr0EiOiegeLQ4yVbWZL+I4DRGrMUyTD36mGv0OG31iT'
    'ZdfUQBJ4aI+iG4ufN49GBGL4twcT4+any0e5NZPWkdX4KzkscYVXnPu/35/v9FuvyXxut+Kv'
    'JFuJox6eeEBFHonl/I7yg4dbjrHLCNmLEVxay1u+pr7pYzWfwQtxW7OsDqFPt9In6tggg/np'
    'fhi719NW3r2ZwcLhX0YEqy2RveckPlLJ40RUy0XBEonhiCw6rdebIm8B8M31hK2amJ8iWDH8'
    'tFQjTf3sb41Vz01cSxgxXzmTbv5K9Cb4RntcHbAphc/4M8m+DGbTwQFH9/PoDqVk+I0wYBN1'
    'c3f4tXB8XD3PR02Ol/beD9X+EgYFDQ6MtDQYRACm5bz+PcxFCTBgqCKG5f/TMXz80HD485vT'
    'lf2hgxrOWx4g0yXFjyUBc74DQaOCdcbbt+pPrBe4ihqVIOUUfwnqd9O9wHifHMoVLgpcqGty'
    'Eam6H2w3pBSFv+pxTP9yB0dSrUnxolnRi4F3swlIuiI84gipdojhf5tD9XSHsK33w6Id5nbf'
    '1ix0RgfEGYH9BIpuGITJfRjMflF5CJ3mHmPjP9KlxvXna9AgvW3jcIU0VC42loxO/LL5+6uu'
    'xsZa1g4hnFvvGL6elfma7HH/HX8Xv7F6n9mOVRmA09NuF1+RBX6DRV8PJ1HwRIqYafvAG1mJ'
    'uGW9buAUidsSax+bRhRCemdOOUU41nQJdc9Xfn0u52TG9oxNa7cKbRoO78ePCUPknXMJf85X'
    'Cy7685P8YAsBHCsP794LIY8bVtkmnVJk/2L87gV9J25gq6NjRIKyhkSPw3d0JOoSx/qXgBWU'
    'zyzQoi9aImnIX9T3kTOWnzDhr5poFw8V4+8wBZSo5elC/prCyQihOpNVqA6dV4GY2aPme6O0'
    '1yjrQlG+ZYB6cSpVAc5SsGgmyMNVlzSYXeFffgR4+ZzR/jKLNa4Om11F++FniYV/gremajfy'
    'Uzp3LDmkRsoIDKNODz8emcduxeVYazjJK/OMK0gXbECL8lyJBYu8Cw1/l0O/ytMRFbzv1OEH'
    '8EdmP0+I25VrFE5ReFkx5QVonla8I+xk+O9J1YrQRtXXwN00PIVRzVZLYn3PmrrcGspcLYPy'
    'rBhWXNkLVzj1/F5oJUECJbOXqF7EefdpwbzhX8OB6DJP5IfzTyldrHD986IwQBp9k3E5bgYV'
    'jkFn8MCdwsbj6tNV1FO+mDSCvj7jUQdXMmSeYcXVEo0wX6+tlXAkR3QoFTNwVaolzVYI+GN0'
    'tKP70eA1OqgwT/fkr0/aOlDw3YQXNVuvoLgfteoVuWfVSJG0PWkp739YvdQVXan12o8+1pLy'
    'BdFq04yA3Zrorape1gPn4GKpWKrxqNhMtVwpE7Xy9+8we8wvIn+w+Ek1qeSJyBLjsZvPJSmv'
    'R/opr0pLqn0bY6B4BasFmgUVnwUVC9ZtJXcmz0/Ba9hO7F0l3rRok/VOTSz+dig0fJ8Eyat9'
    'SZD81K7bL9qfJGE+3xOHJbI4NlD+lSfmsS+dw6oH6GRqiMv1+lmk7qzoWZZE8nRCfE9PrE/o'
    'trHecKL/eoMSDb4p9FynrHowqASOWMzEu7HxiXTybH2lcU6tsWB6KX5z9SeasXepVRbfmZRV'
    'RWLmnzc/YWXFxu+HnIuvrqyOqK9DUzuh5AtZgKXqPuQGRkgJU1621rJdGy7EoYW7q8+pdZSv'
    'njeOSZzQV5eroCmiV028RTL5KUJNDN63dycGj+FH1v1vz/4Nmnf0+f76jgPuiUxMkdlxTTHT'
    'dp7+6b14EI2S5b66udaG/8m/9enYSRJk0NkY+E8Qr9wuT0IVDqxOmNnpss/EG/FtzVT2YMuA'
    '8Z1JPXL0Emys8p2BPRPY77mk3/wXy/pneRm1ez+H/C25EXy5cCdEBUJHGN9R1NjfvoFp3GgP'
    'ZgX4ETS/rugf+r9fXys7wjyWPtyWVqtDoCI/4r4ftd8HG5JSPZylnNjo0w+/sh4PD5bE3Srv'
    'VTh9qGwIknIzlV7s9k3JQtdTPMPa0uTGJhGwqK/X7plE4FGa/Xz5OQBad4Z/PWHOd6loNQXO'
    'thaHeuo9apXeqON98+Ir60qfZvxv2D9EVOW7rP1V8f1MjS39xovwaSddCMZ4+Ei1Eqkv7FHh'
    'iztr4sH54fZqieJySVCfg5FdjrB7MV6sronzqd7/gxXhZ4nYbmOteEeew49QRSZU82HFby69'
    'IPhjR+pt6cVvGo/6iAj4Xpqziru8H3IPxWlZF6Nk7LFZX+ZubPooxd5hLne2iXsinJ1BlYPv'
    'Um+CwX5pJlT3LIq2XK26C0mXTtVePMMf1LgKVfT5PjzjwQJbo+/D1xFMnkY47b6thdztUoX9'
    'hSlbqJ5s5oZnSEOXUdrMyLXU0WzMvCXdnOngJhHRILF3zsxCOyXSjhOE/QJUGnpLERP2doXa'
    'HvarfvIRPpOLGQbC2oKlOVJP8CbGph/Zoz9YdE4cI1QvdnLa+f65pP1napee70h5cMozRHDM'
    'MyK04HNupmvR68nPWJsai4DIkKM25Ahwb29T2NHU6QivAOLyt7ZJr21qd8s3fJ1dGwjPZjLJ'
    'OGEvBOQ/h4ULY10mqnqZj4LDqVxcwrluJoNfAxfwdlo69awAl7V8LZl1Z1jS8P09VeyV26N3'
    'Ap+n0pUqY2zcxH3a5gn/DsM8xrnyT1wOGOt92/7uc52IJjtXu+KROsZl21b/KFjWHqZgNLvq'
    '1/CrpLpXsNmfOELy3CxrD6aG6nkbbJC/09LNGW8bG7ebM3ZOdhr+sySvUy4j4CFI76K1VAHj'
    'uU4jcAO1tG6jvlgFMcg+ATQsI1K2E9h/zhrCvYm4x/UiSBq9F0CZ4h3Ur2stFFkgPsABXPm2'
    'snuXOYKTfC3pqa+x2dBj/Cv1mfJAfbJ0X3Dl2+ufVXtT1xZwAhlNmgp5DysiDzleDzkeDToh'
    '5i8FqJmUNMEKMWEoVlU0lNPXZp80hbWs2GHRBOLvpb9Vge5HqoFxWwLjOUA2o+DXcRjqztlq'
    'EbL4+OdsuwzSJjN8VR99p5sSUBqP+vl25fbI95L0AIxUOrsjBSMzLf1Kt+L5VfSF8WreP6Ef'
    'Gf6RKBS5RHq5/H3ZVjrs5RW4iTyJIRdMRjZwVpRBWGENwu1nxG4jW0zHs/BU/G44b//hLOGO'
    'XBR6UYIybVyzH/28Wzlz9nsmBMdN5qMpZFORr8TeO2Yrp769WNSHtPCmB6eJ3dEevCsd0O2P'
    '11YwVW/zyxXs0p/ygoyadygEq4rfK6D4zFSuKOHDHy2SIOzvQowU0gFhOnE3waxw9AcyWwMp'
    'bsiK9A382a9hroQ4uAdOtuJFvxC8Dmxfy/NEveutvjd6plGeFe2PfiN6yZeuDyGAnTTWHnQW'
    'vwEK8x4HzsaaM2Vea18onToPIsy8iflQuhFtT+gnBdxNLrs9uAf14fEAZKqG2vC/pQxrLbLi'
    'u/wS8hITrzesi3veD48G1UQ7kvC+Wm64caRZekrFI3m+M5uDJekQ0kR82FgokwCcwt5P2tKe'
    'd8cNe+vO6lzfDSro9oG4vyI8bXD4wkH6/B6Ol4tX4dZxu1+jFpH4t/+j/vMvwHEU7a6qmzjj'
    'B97hqSWT6yYyA4Yn3Wxv0XFKVXXLc4bbscMRj6oa7piJL8SvgMLGpiVDwNmeK4xNFVlFO0Kl'
    'zhxsYCjes3RoakU6LpniuskrPuENcwM56VXzB4JhCrkpZtb9RHH6w8Or6qaQgWf8wDOiaD/o'
    '2+xQ8gPsQgHwKl8SMu8HVXWv5uDHcLv33Srfq0OYe8Oz09gUyMJdUXfoqTS+bUiOf0m3zD3M'
    '7keqhBn+GR/wU18r4rzyiluX7o3Wq/42xiHxXhjMlsa3WI177C1VdVt065/fMdPY9J9DlHDz'
    '3MCNksamRwWKHXVRkbb+FJbtp/8Mip8/JfDzP2gfrbH1/vHKM6HiIJr6r1WSeVcS/STqixib'
    'fqV6s99zUPdE96Bod7x8d8k3XZ6hiMfa4otAaxLw3+P6910t/zP8yUMoNy399eH/V/F/4fn4'
    'D5a477q7p70pfLnqz51Fu0HyIPdksEYIWC/FwRp2l9nObdcoX1X3koYvGqyrtcmu9g+MTS8p'
    'OGOed4xN/6Ap9YmRLDlA/gVvzizeDvhuTsflQpJnc15xD+DbfdfdZvs9LWlsNGok5FMUPcgK'
    'TnfA2BuKzkF6vh3YvzqMWzBQXbNoKe0Jnv9exRSmYkHahcyl2XUR3vsybON4lRcPR/Ob8GD2'
    '91vU3AZmnYjul5B3lswV8TVdlMOEH4rbPEvpi5orPzPDEy+3thgVhJ9X3zCw8S5C8Tfu35+7'
    'v9q9ZF/j/hXuin1/+fX7nd1b7R5391aHikPH48LXUmWRfYV7IiNy4vjAKxef5qGw9/ai7qJG'
    'BobXfcLkMIjCW2FeD99gOTeFQGOfTpUL1wqUydXO4ByxotQ8n9PI/BrjKHPn6n313VtLPGNf'
    'ozmyueV1zAXrdbsHO4CR6Q4VlRWLPhGX/72yNlc1Jra6atz1JU9KEoyk+BLpbV7wZkf3thLM'
    '+1NdxVPdq5zBlNq/uLyXBW921efyISwUE3+35t1l2asKvw7JXPLCPJW5ZEDBjUpff4cofRMa'
    'u+z6BeagcuclL3NRDmfPpjKdWbzNbDVu/SLQCD3WmP5FcZexjjlxQjfG2qYxWJwKUA6Nqmy7'
    'jusCTnOtCo1NQ9R2QEd4252y7rXWJIm2poQKhgqKzyGIV9P5TFe9YyQiDgnxNFfxMc+3odeD'
    '3rtbiQc8meZe9ZHx2o2x4PDaj12eP4uxAyWghQSV+My7I0iQsLbBK2MWcYlUAVTfA+l2xpNv'
    '5iJ3+Amqe4xrjEGZgEP5E9/Wcplh8rvMaend09Ic3gn8GkX62+/6+8r+33s/CXSvHhr9Q0Og'
    'e9XPgR5ptYvrd9PSuUgafcayl+402xT+C4I3ZBY3E78ngd8bgN+TxV2rV7fdIIilIs8FRlVX'
    'LhXAc6+ScqLfVHie5qrPsgNrZhhSF0DPdNl7U1HS8P8D0TlNcLeVuJvpKp7pXrXXeO0O4K57'
    'q8vTQv0D6AuvuUcQH305If+l+1BSbnCQuwIBjvbUmHlD+sD+nz2v/57L0Gr0B9oPMM3V6khx'
    'oXaAB+CSyq66GeWSQTsYHEWwduoVEKn/yN0KtCbdLqsP3RrjLprIyQReofzIqHFwo7/nfiTz'
    '3Zkv971dobz4RllHcCo06OUuzR/hV+4g92aZJ8xmqhSRau1vozzruLv/O3rOZplv3WnuVTr8'
    'DZkcGWSj4frp5nFqyw/lxQjIkEzfR66iHcZGh+HrfD+jo94xjJPJqVTvEV9zqhkxu/JPhmNM'
    'IGPp6nr+bLN7hm8ey4H9T8LR3VKC30RB9DeSh4bW1cnob86zt329i82yncEfppuz2mli+psZ'
    'SvETuDSwxrJyq/HdttCSC0k2ZdvNpu6tTiPwR7FU082u4h5zZaMxo83XeJm2wrsRdQ4rvKzT'
    'oUzwi2GCm8Cdtx2G4Bam7DK7MnoN//vc471x5c6gt8Oc8Ur9rPd9n7hMGIXedhOAnAnSyBww'
    'fyEVTtcVZe+Hphf6Gr8NWDM6QBC1p4qNG5uMjTM6kSrH+MO5+qmxPccZ0e4yynoMpJhBa96t'
    'QdS8siM44xX0svgcAgHYgbKtlCNHU+TWPDl2Vkd+O56so/XsW7lzseH/D3Ri0qxGIxR2sFBj'
    'frtZdphMXbYj3fDfR9fAg4dTgmWHWdF/ca9dl7Fx1k6Ipfo7zrWW2AtREBNLqTRxOLQ8Vuzt'
    'MHyye3RlJwCKXkK94BbIq2EJwdOz7NrgLQ7fMbuS23F5NSQ4CqLb0659QOGsOcniqmfpG2gi'
    'uLKToGRy2eQWR2RPii3hr6/y/SQXK/mlJHk1PJt//7vf/Q7j2PMxRnHXp/aTxafNdqM8aTTz'
    'ejmaiWEUtAS+RSv3iBV/GyzbHly5tfZwjznNgRWctQz7NHvtXWZz/knfh3bPxRAfrTfGCotb'
    'PZcm60utQwqBKPXGe9JsnVTWYfj3CoGMLeuYVNZoPH6QaraaHlTLT6JDgsO/S5EeAodtZYcl'
    'f0tZJ7k4svWcZa8MoOCcfhT8rZQvpeAJPZqCUwej4KEWBQeuF0OefQcNq+6bvUDAOjtZe8ZW'
    'BkUVJa1fSH/t8c6FvsFiKxvz2wjYysMmoGkCgOyod3Vw1lZ4ouoZXyY0FHiFra087DtuHzh/'
    'ZGGM8HjZ8WBZB6ZVn8H1HYUUYMhsszDzO72vQpH2v8oCXn4vICjuNUJD0RAgmbFdQYJ+dW+F'
    'e6td+I6gmF7UMcY2hMocfV6BBQL+YfNkyHG97xM7dnjfYW+disFs9oxS+2BTCrFdVj3yngyu'
    '3OmLoV2X6hHY5yKuAp4ml9Q7Qboa5FDpkJTocqGrmZD7TlecxKHifpuMcbw/YwRTyRW7tV4l'
    'GDk9O4kzAjHvjn5+RGHZlYc5Upsk3LZsZ+uNAGJI6MaU4KyO4uZl7RQXM7aHVsWwPX5lp7k3'
    'UkOPkfBPIGKzKOccKae8LdKk1pMUfam+/egcd8mqinXHpP7ovyTZ/zIO/83qhFgC+yWDVyMj'
    'uRpE24scxBNW6L1NyKYYZLP2V/xgZSPgy29TgxX5s6LEyA/PqeKGv1X5r0hyjSlmazFYK/Cv'
    '6AMoNeKR0nwXWpIyCcwUCOJN5OZ4vg/FcJGrFFfJrpbdZ2JxPQ0N0c+rxGfgSqHLRmLzlli8'
    'UORONjbLYo6Pe8xeeJdZ6DBqMgJdfF3WSGmbnfjKnLU98oqIJ8GYBIVB/K78HYqyv1r6Rt6n'
    'V67Lt3KrzTAfPy0N1X1I0qw7rWjzYTyM3DMgfwjnO8y/yDIkOit0pqIdiIIte8X47nYqOU0X'
    'm2VbIRw8w/VI/54yAv6h7cVvmLM2GzOaEzLC+Vk/ubizu2xrrWcU+lf/WzWduFdTHGBa6TK9'
    'nRm9GCjkAUmVV8EZO80fNo6hSQRcJq0vrtxZ/GdPfvCH22EmX4EuI3lDrs2TY27P7x3DwGo6'
    'jIa12gugDQh/eDGFHN781s6dO83P7Gd8e2w9HyF64MO+pnCKvVme5+8p2p1/yPxhxyV7zVkH'
    'Fr2LRzsXvc839j0Q+q2+Tru5F/kC4Gsua7e/H/q+3Zz19hYGAvZ8mFrWgQ99jYXBHx6utwdn'
    'dG7OYDLC7ZJu7ZKOZTnG8+fsu44HZ3Sgh+if5JZT88ErGPPgDzm6a/+DYgZT605OrasKErPC'
    'yMSskKXk9cD5oNi70/Afl4l6M74t0dWAAPDrqqqesq2Nds8QpVmjzUDjI6OA3SQR5d9rp6zZ'
    'hhl4rl3kWHdzreHfKm2NC62yB707i1fuXJobnaHlTT+74ZznWtoNxwfaDUOV2dBumQ2PVybP'
    'w+e8b0BBjWYjn4OWX6KxKtUVqfgEXOwTLIIoU3JMaa6HlBx7I1mOXVUpmivrex35yOJCTOt7'
    'rXbPZa1DxqIjwqsym/d8gHl8V6SMKww93c0lhp+82jrEBQ96JLPfft+eQ769Noxdz/scvfvO'
    'WvMm8R6op1Ey6xWsKy4R9BtrmW1RIR27mnGvehJ5KjHfJvRyY+26c+fl27Hmq/knBthLIji3'
    'RuZo/VnNk7K5VukX0Nq8O83tEJHrOAtG5sf6VWCsZcDm/1TfsEROa+SwlhNKH6bRPNm33HnW'
    '8L8pboCKrCpGf/xWeug0F7ztazS0FBh2XKRAWSfEpKw4cV932WFrd4Eoakg1MOq7ymJlfaHJ'
    'Q6D0WXaNsrsUbYDevn2+rfVRwtb6s2VrXXa7kIX1GezUBW9bxMF4vPc2c5tAuO84E1Lqx5Tr'
    'CkgNYPgv3wM0szrjewO4RCRrvqGyQyTrP9xCqFcVGZtmHaoyZ2aZ05xiLjUkjae08wTaAQSy'
    'I1nWQzbNzIruwnqFue1O88+0ouiVnYNtirekUz9kcH/xn411EmMyLTP/VHGTsc5IjWt6QMJL'
    'vFJPExbWiitjukM3pWwhmTMorefDXZ9yN6z/v+yynpkJ6zJ/eyK+H15Es9n+BhY2DX8DF8R2'
    'rD6STA+Iyg50CAwwIbHSx1j750ogbzWdvnJsQP4pETzZkAn9Jhs/A/7ZL+YBSC4Pjf2lQg75'
    'HXZsfC5tMspOmu+Fq46Jtub5ERwDTs9+6Zvn7UVt7NWUWfj3cFtoWq65T6Jxw0+idHSj9r8r'
    '9PA7w/+dmKWQ+Jkx9SV+DuQwFIaFKAZkp/BhquoXCt8D1+C2S/B9uneUcG+mZUEKnyFrzClM'
    'rNGv/1X4f3uUI5QpzMf69ll1NIA/185VYwrUF283QvcwK1gTSZ+JbTget6Qn5A/LLwd4HIfp'
    'ahz+vVGyQNyrSndvHYL9AFxxvCVd0bWMi/voAPnB8PpvDypvxkk8EQJTI99JvN9cKC/DB45I'
    'T6iDfJ8TBfBiBG7T8W0Hzq/P3guMJfnXUH+h0D8DX9vs3uORw/3Ww/F+rMQXsf3XE/HgSnCK'
    '/0i+rzwyoD+R/yXxOMTcqh8DD5SPt6RHjuh8NN7hCur6LQmiiHwrUX/ohpjMkYb/9DkhF2CR'
    '/iNfJEV2Qlvwhb4nDUK2ne0jt6HY5xz/vXF6Z7MwA7xjSN6zzmmWLD8X14UfpOI4WcttNb7U'
    '8aby8WhLfoOu1kMfI1ZbxNWTGfkjB7WpZx/rJ9r9vXjRw6ndbErIY3FS3Rl6JKZkSIGsMPbS'
    'ZdzUezlWXZzMNLX5/qqqqp7jTbHLzV1Np1LyT3kKXuej8/eJIZB3m73nuLkdXzedTjF35TfB'
    'k/6TPF8s5s1smyYbfF7nn+LjkFRG+Vlze+jOlPz24l1biOKXCA3D7U9isKlyEBq4erLPsE95'
    'oA0irNnX6ap7g2Ts217GgIr3wh5sQbf/hOWd5j4GXGhPU+SBL/r5a9Xc4xZP+/LbId3hqKZH'
    'NQcedQZtZkrQ2H64VK+KSAxtrtlKz2TdKS5wewrplzoZbiVqMekcyqIHyi1JZ/R6VUkm8wKI'
    'BRvdAPrbh+L3H1XF/yNevGEweB7JtL7mn/MAS9WA/TI8CGD3K8COnhIyj142PuGn/Sv60XV/'
    'g3504S3J+pH05xdHVH/eH3Vef/Tk7pIOGfX0LRj+1wfrjoXnNz5R3cGSYo7Zqrpj+F9RvDGG'
    'edh9p86uuDM4M7N41+oKclwH/Z5vQDJKn83ecA+M8+hIxQeW/hMewO+E91sCr4tLAXH52Ojx'
    '+B7ItFtVkf9Z1JFclPN6CSNZbGr7RmEifiF5xCI5iefAUzOqezCqqvvvC+PVqXmGD3Nulik1'
    'V00TMrU38LlNP8/fjseGeRGna5Eha4fj1k4Ojthjlj71Dj3U5p8kpmeOey44XxYWmszejHZv'
    'RhUd098Wx/RZbmhgMBk2CV5gPPYbDs3reLLFSiBJhmeG2t6MMNZpHOKXyZPKWsl1GDTwXT2z'
    '7hXGx7sYW3OWDg3ekhnYsXwY7BjzRL0d2pXx/HFH01GH71NoDr+kddLr6zREj5jJSYjFVw0R'
    'Wcb03FuwB+uhZvobTwxj9a8k5MpLF8hOlPymzfSlNX2QYvxr857O7iaXB2KgqBHt2HcdtfeO'
    '/JTekzawyEv0fo6FBmc8354KSJo+TcVXRQhFQeon4w/tez7Fx0hyZJTtNduM11vNd5K6DsHh'
    'AJNw667Wt32nLjDWfCdVA8J6BZCi3ay029roZwHFoFn5XdZulLWyURC1vVFgGrmbXxd1sw/J'
    'YLxrtmW0m+94fRwZGvsPK/WMyq01AHzVXerOSPdkIHw6jxtP81ttcC3YoxXanuA4XIgyI9Ix'
    'dRG/xlruOG8dMp56/i+oAuzwjlRqf5I8ZDTAJG6v4vqPn5vbwTVUcHSq+VwqFv8mgiQT7hHA'
    'YbyOPgV2hzIZhpJu3NZU96FI4tN2X8dpUpi5TU8XZm9iEjD8v0GNMmn81f3Zg84fZrhqLFeH'
    'GlOeJBVyUigOG2srqPmEiReEeUuePTiN0Y9aIzCaugZeQDm7Tt2mlxgB6rmWQjDtsMLye6Bp'
    '3/bZ4avlN5XPMZgtUzyXKyFpFc86PNCeQkCCIP2y6E3oj7FpWi7568oqDuFjHJFznvFc/4KP'
    '2dc73lPl6y3w7MNM9QY2QEQ/HKjfvPhRf43vkwRsFeFffCS7EzPtgGdf3S0O4psznZrnZB0s'
    '4jsm8xz0gF+eRAN1cTkkIgsEc2X4BVQTWaDWLy29bTKfhZP0NlF0/itub1r6VRbLNZ4bTL8S'
    'efv5h4PYm8B65AG1z4/0+O8Sv2jxTFGs6KjFLyKChEMj9WekvPE6l3LLwZmnLsclK7JR7dvT'
    'TLJuHopF3k3E1VlwVAOOSE4CTs4XqVzGwS7PNb/CR5BR4ID6G2MROx43aN30t1Smfk5/GXaS'
    'Rn4tGpRD4HNaex0sg5M8WYXod24Gc0Tu7Rvg/8IEWHdkp00i+4+oQHfuRAr/ZJpskMWxCbaz'
    'MZUZQB+ZEZRcALb4hvk8bME5rHMB2FTOA+w6fsImaXRpXY7RK/W5ePykzdob/Vz87gVVlKec'
    '4NdmsTJSiYgq0n9TaJk9enuc/5+QKgDsi+LtKPFmFD8l36wahdh7tgo/jBHwc/Us83IkVTJV'
    'UWPjBZTzr9Hy8A+hAOgd4/0jMyJomGVnnl+SzeouPCmfDWGCqnfUrk63zkZrnrQ2NUNguopi'
    'oemdoR+g8q7aUz94uNzYuCPmDuW+gwiMBr3tI7wCJ7FEeBxE7Y9TJsDBm5HYFx26OeV1ycj0'
    '3mvcsm++2S/ePVw4GZRwAOV9Mfis9mSIKRJYzKjh9tclDPo9xrkhROYyybDHuNX670INCWWa'
    'ofJb27Fi7jIvMDaWiH8QnQncmrT+EFRDWbd1pxw5Qq+A8FHds9L9mEoL68JmpZxULBOl0/Fm'
    'C3kdfJDiyeYFk+RrjADHbZo3FUkQkEzncRkJPHJ5sSSGr8xMt7kL677cXvQkefMlNvK4NFKr'
    'irKBE9zoO4P5t3KQc9bc/ktfLMVzmS+GNubH2xhuvIb9g79U52iolsxD4auY1OkicbFPtceH'
    '5z1BbSRNDTAW7FMYwLtm/hC6Vm2bHRgk9AYYLe0NKlqgEZVnZTuGvvcrDIrVClWkS7Hn2/zC'
    'PCRLJ0FFJb7truccmmJCivax0QXT2f4HAZpvWyYVxybsHY94v+KjJQM+6mPK1pKv+OgfBnz0'
    'TdgQkeFnFSomL8QupSGK3OxJKDl5NoGq1KTn71nfVSMxg/4uJen9lrOCyjxTHEJ93wEcTwgc'
    '4U7umWHmT01UGMjQLKGUNMM/ZogaPozkz/QtQiR/laaH3rR7s8w2GmyPOPrFM74DTJf00VL0'
    'jpSJVPOlf1RafFxAkkW7SS9CZIfM5s3LCU/hB4DHQXiU0PC94TLVXXezQ6UxANZC3kwJ+aka'
    'YyurmoJsmv5LUbPEAs1lZM9ChFE9Mqz2jVr9bXJh9KshjUFtn8RWM3YIAFZgkqs0TyEInBv7'
    '2jL2Gv5xDm4IagQw33CoJCwJkWNR5GMT7Iq1NOegM4dkJqxHUhk11ryEc5EIIFJ4RvAxyqf4'
    'pyRJP+HIme9FsgVjUCZS41wmfin1tudMHCfERKAbXHOQbJ0+ABNZDo2JEphDJcTG9MGwcbAj'
    'gQ+/Q8VL8cgfxv0KbnA8EFPJ1MVsk411TKJjvH78JTvSduQjU1E/7fId+6nQSw7gSiOCWjFU'
    'Yo7sqfDrZIbaPpmN4jgcBGecVJKRdj+3wJ85FWei5xwW6wQa+qTrZJ10ss7jXSjaPmjRwgFF'
    'T7Dovw5adPGAoj+Hdzvy41Oasa5F4gfcT77a8F+ZlB/DYrDvn1bl9Og6rOc3cFELspgWEpOs'
    '97SlSWN/VJTDV6EZDnMIBdh1qWqw4zk33sKw25TDJdABDraIwh4nig8TPcGmNpldt3J2fVHN'
    'rllBxeVwOhsBBqS1Oq4aw6VrVRZc+qteVf0jSdWnx6v3nVJv7+IiRKW7wCrhFI6otcVTcvj1'
    'bfj7k4TBrRz1YvVxj3GJQxzSpe5VFECXXSPBaAVQhEcYm8rdqyRxkVIlqLFEPtWs8GFfAixH'
    'HKwjXCfH9PLYNI0JPRVH9sgLiIl56Otr4h1sh4IbQ+L0EF69Liug2H3LrMXhiyb1nyO6sR86'
    'sveUos4nEtQZfpWU+KOeuOR1JQ/8XNRWVReJfSekFBnWvJNEFjolesNVSJRpF31hAMmYb752'
    'NSv4c0+cNRgsWbszRc8UkT0Y+ipi4QNRqTJ2Gf5fAx+vp1GP/Admb96EaTtSCgiM1zm5Rjaw'
    'a/bICnzoO1ZI9cG8OvIaGjjYEU052KF7u+VCTKBFjeFr2K+P8QlAf0aLIQ2JgpN6kvjRIj/u'
    'EbQEZzkgbyK747/AKpHxqKHeHhmqOCDFmpoQMaAwdH1P4kVa8osxeBEupC49r9cC0ey1gLwI'
    'QI6hmhEOvcdkGL1CgvQYjZF5C6ehRYroXkWHg0Kn3u8mkUrPU6L0RX4vWa6npibASlWt/1t3'
    '4kVK8ou13QNGGmyYJLsUQhZ1x6Ux0yMnqbYjtGprrJ3whTCnVm+9Ga2OnDGRy7+I7xCRfuSw'
    'H8P48FRk0+l+Yfhf+m8mot+xYw8KpO/UqFWjxVmQPP/aG6FChyqM4jdWfE7HZjn6YeZgD4Sv'
    '0cG47pnq6zx+/YByNewf9Ptdq+X7TJfaZmbmhGY7jI0j6ysc/t2e4tANYEa1byZdorzt9cP8'
    'O7wnuJUQDpchvJrYjJiDwDlZt8K2jheU/yy6f8B5IA6ZA4LT+3xhx54PzekO7C4R22eJy6b3'
    'A5Sj2HxIjbnBJU6cwbTUiagiZaPOp08qVBPztWSFlmeFHH8QDT1rKDbjqfS5hcEcHIGAHZDQ'
    'cqcz+tNpdvSc4Ok9jD/mFr9DtoRMZ3Ye84y12f4884yJZz3IwVuNdL3V2KwHe7EIOx+D0/p6'
    '5iNzHioZhTjbIlJrDlbkV/A+vxXr9vRsOM3Tl8GgLor5mhzmu0wh7fvYbt7Qx71dxQeX/bes'
    'rqEFENXEIFuATeUtDU3rKwYyl00wO1DH3NAd55o+Ru7NSxAfkI+wxPyu6Kh+9sdRe/GnnqHB'
    'zOdgYJwEguaaCxmAPV+7eeZSBreNUWrzz6wDWXAKDfLT7RdyRr+4Cg7kJNcbr7/NERxStHvs'
    'TX2+92z52y95z9fqQN7WXeaNfb4P7MVnlx1CBU6mLg5l/T54VaDbe3Voal9xy7IxYzF8obKY'
    '79NLmj6FQx6EMIz5hQHvNg9O1PldHwA+kQC4wArA7U//yv59XqUz2qkMoq1qCmxX5iYzlwS/'
    'FXzc3Zgwd7crc5A7YAtQskO9cZthlD8gu7EZP1xoDkX0bOdQPcyM9M5lCh//vzHICYnoHYwV'
    'NwLOFGX3M1FXsAg70CkrfOdcq4ZXNYB/mu3Frat7sM4bujvWdCYNO0PDrbly1MREs42MEboz'
    'K5SZo9ePyZi+Zof5g77ipmXCP0DeRJXTpLhJ4jSMqR0gsqYzKdGMBmbGYLs4fiRqD1Y+nYWA'
    '/qjxKm7Mm5xNnWm+k5cDty2cij64VOg5z1RGMjpYSd+RBN3d5mRg2zq76FxPqblL4mELrpSP'
    'Kg8CiCBTZuNgKPs+blU3WEN53RvInsmE/LPpXcVL2XMEyizHswom2T6Ih7gv6Rjz5LsV3LBf'
    'cmDEM7g7kPYc/gZ27IfOfWDEC7g3vts07hqIoENcI+M6UDnelR8YsRnvDjXte8Ut+c9wQM9a'
    'mRwzqSY+Th32ebnsr3SX45Yv9x17/4SxpjGNcfZsEe69zix7OzrAqPbJYJPtoekMFq7wnb7I'
    'WMP1wYOOd2x7K2oOIhk4Id634/0TBztguKepZf58ML59Fxh/iU3hjn1bOPCQLjk8R+dvYB7P'
    'H9FCwBbg2rh7gpSXqrSbrUpfIln+A3qIU2G0CR+5P3G+BAdu+WXClqhIp13PjSelULPHz/B5'
    '+KrTXH5TtZjbrbiBQQFIPw+A+v4AfHwqvj/sJpF4kZf1+p1lVZdeLKz2lJKReaGs9PwWYKQy'
    '8kcVN0dMCbTYLWFhCStOo7boHBgVEkzebFKGY5CBYvODdOlceT6Ko8RCCWfpMCnT5Ww1uFHW'
    '6twa69IcKv982lkgc0oPXAvGzxvhukDp5ao0NL/b0lVp/9cukDY9aoMezNYp8gbJuMpS1HaT'
    '1FKhjGol6Uo0i0h8R6aUKKGcXEfFo0kso4pMrDUA+tkel6wsGdPbfX9hcMhsnMUD+1hWz/K5'
    'OXc2v+YKyJr2IYKP5Vyd3aruZw8HSRqBjfhFqoOsXKJ9WLJJBAUCRz0X7AuTnOtGkpypGaHo'
    '7I7GjrS3MWpgzOWoaAVaWc7eh5BKq7EPT0rwawWbSleVz+YzVF5vVf6UqOb4zoO3y0H/veRH'
    'niKI5QPNbR1TDuBhaJF9ELY79MFBpfgn+G+i5j/w7USeaTYR3s9DkX076u37Tzw072BH5FsX'
    'WvHFqpfEt7pDXqjQtVgE25e2Ey3CKX9gx6E39u34G+oui+0HdvYAO/uaOsZtx9f7duw/cXDf'
    'Q78GpjoQsBmL5ifWj95A+MlIUXBj5jtRJ4vA/I2/D82KTS727ANc3J/jkhP2ggppijYqIhn2'
    'pPjruGbocRcAsoIDIxoJgIYaGdo11DKDNXLzru5veGq3eH8rkWnrqFF/RKkvNLnLJ1VkGev2'
    '2+ODgDZKqG4OMgbkFx4KKAPq2CYSDKjBgGkyIrvta+w4mYDIlcCji3hEjFthxxsIBkRVuotA'
    '6zHgMHJwn/HTz2z98vbCDpPdS7Ag1DpQZOmo6MVJ8a+Yv9/yDKldGSv0fMZJBLpieNNFSoBB'
    'm6loLdMK5ES1s9WTYjIZBbWc6RmF/cUX6IQzwPuRgx0/xpILJZyv2l1uj+Sdjq+Xhbm1bLl2'
    'ZSu+v9Oh+f7KYQP4vsyhRkyMP3qWGqQ+S6xHbj0Tj3tT6JsE1Bv+SSPACSOELpH6YZtFsn8T'
    'XstiSdgMMt4uukHJ5UKOfv4hROLAJM7/Ag4Z4QMsNA0ZIWDO1gRWoK/V+khTIQa6qVBBSX4z'
    'vbT20FS78XRz4CinYGuCb8cTf7tn2KRqcMzjtU6tIEOdxOa4CpYU9WobouwoctX2bwyYJ3zH'
    'RYykwjB58KbScyX9YpiTv4ZvK4q/wH6IW7dRSue36qbyt4H+K/B2PpwbgWvS1YxoT2wql1qR'
    'IVpcynZd8UTjXxpxJjIOVfx6C1pYSG0kF6LcjwxY90DsvsyTn8fOEZfcXF/U4fvocvt7pjLm'
    'zA6EE4wmgTEeDzNQ+NFLtHrTZs1LAMneBbBzta5LNSSPcvxOqyilCABKMZ5ulOiGJQTh62lK'
    '4nOd5odUrbq4gjAzRVJ9ljA91yY1+0xMThGmz6IVev92VlyBYhPh+5Xm5eJ88hP9bau9CFwq'
    'hwgAop+NUrzMUbUEjxh1jSkBRsFXGP5/7JM5vNL65N5RqkjkETqrBs4JHFpR2BJzAucDKTWm'
    '410RE8spJ401lwDVELSdSuHJwABwjgmqGWOORYdCWKhv4P4d0DDll9+WkF/FFVkrHta9KdBS'
    'icvl1cMSiJ07jLIEG4jBWBocS+fyPYanCj06Y5kgdemFGkXlkplRUMQ4eIrkhxJ8C3bnIJaq'
    'QZzL+axe3Ussx4o0gXkuiQPgzbeoeVuclmH8zCU1M2fI3cp/LoezioeB0sSuxqZANF2ozbc1'
    'JaQ6dYjwwi6tJlSLLgEGjmzv0XFTSAlLzlsY2fGFtb5oSV0yJ0HMZ+rtKLLb5cpwz1H0zSF/'
    '+qI43VaiAwtBj5VkuHczhLFmp6Bw3mbNb4V8i+tC42eoOLJdyXFWkzdSB5Ywm8iv0cXhMIVw'
    'gAVsjW56jeuHqLEMLbSbx8Bmw3HG6nyM1jeGydGp7gIINY5jseozlpWGCb0ns4McM/wtNDRp'
    'jjoONPkFzkrNdav47fDwkUlJ6lqs9EDDFDeFjzrjNpsem8KEyiAsre2Uw8dVEhr8xlYyJJnF'
    'AIWPW/hq1dwvS3n5F8u2bo7vsXPKA6NqmRt+7SLRyuYSwTDYwucMlUGIA5HRDOmkmNctMbWV'
    'CLQfDu4wAi+qDDiVaItjkoexKCg2/NOGC4rXvo1rqKzIIloSBA/4EFTKagM+xUaSGruih4nC'
    '4ZUwR7nuaEiLlVp8+B/vFdHP48n9x0D1xc2G/2NmGgG0FYaWCO+cidsP4d8bCoPIF7tJJR2e'
    'aEkQ14C6s4aL0CzAu55DZIHwuQtUjQRd/LdvRbh5zIJTpncJGAn7sxWqqci/flq8qlo36Tfy'
    'XMYvl7QieeGRCtxCMYKr8eG/NH69VTNB4P4MLXgD72mzQLMImyu3eAvrs4BO9pv/SegerD0x'
    '+k3S+SDk+MwFJL0kzRMp5egBnxzXyf7lqCzYY7W+WE0zKzbJaJVLpmaMNdHksE6Dncqb1U77'
    '6qnBm7CZ1DsJG0++wJlXsEMQSI4Pp0fGcb7/a/EnxX8y/E9K5sTQQ9xcRO0zmqx9DolEzibF'
    'Z3OejE9nGMPckZwv4xNqYJU6CGahPktnYvBGp/HqnMecvs8uj/yxV/IF25XZZ2mH4ZcYM9YU'
    'GfOZiDcR9lXJ7VGmQ0+hknISWvo6hmzTluBQhZbGsDvt8UV22RkMCCjnH0mPN6CniLljoalE'
    'ftUjGu98xgY77AnJygKRPT2KOKER3gLA5WRhag0MUPu2M55/kXwXvv8CNb+2mj3hu5R9NjGf'
    'qg3KG/66nv74mq/odKLQ6REjoZsE+bAt8v0e6bgrTdGKyPfIJyc4qer5R07XAg307AkfGa44'
    'AulrE0whc5BBx21z5Dkr6bKlVOY3C/UqqaJk/1vHE/mIBsBXbuj5qVzUi/magAVPgPUP3fEh'
    'FB/ifMwnC8U8RXmZWzQlP5Gp+0f+VvNggLaylIWeVkRNcW7k4Dm1JhW646zvQ+yOcDOyfP0X'
    'SfEFgB1z8cAJd9YZYfF4Jje6SSEnw2cNYbEkwfzASC1Cy+kJNtaNAjV7h3fzQHYjMERNqQqN'
    'X5CXjHWnuUx2U1bkP/okvp1qCMSqiNc5Il/8Yx2K74y1j6fpiQ8L7w4heOrEhQQnWqXzONHw'
    '2mbHlIU55zyREFIzV/Eh641R2hWffxjrQJ2lf4d+HFFntDH4kLKBr+aomWW1kz6D1cGgkhlu'
    'k3TMKasXs8HtaUrocFY+yS/jep41F2AGALufhu9FG36HdphvUTFK4tZBbEA4fOE3bbUrCwXr'
    'eU8NapNr5hpC+/lQBCihoCvjacVidrzrfWVfGo39fc0dTfve2H/y4HvGmkPQZA59SL3MWMOT'
    'fhJqobYeKqkSYv+8IXKihFvqzYUnEoIksrxbnabHceN+N1qO1ohLKraXUTrq4GPgoZwSED2o'
    'l3yGXfLpfLtyIcW10KDyD4lVBOOl3bKMTPVcrKPI+M/iWBwEZZG9qm6A7KBfFdOKsTaET5Ct'
    'f64dfyrI3QtFEjz0WXL8P/ux7nORY9WQY2v34F4hXrtW2PrBJ5RrRblH4FOmdtwxoh2llIJu'
    'KdODO2/MT6mqR+49piIGOcUeJHk1MKqlJbQEWd+cEIuhrABJNlSWaaUgCXePlkwfFluGazK1'
    'AtQSOfCFAD3XEgDFONkvVJlmw2n0t6K/+fyvOWHOQVxFGk+LYdFxvi7+sdKLeXqFLVkfjxw9'
    'PqgmxHM04mQep3ut+nCxaEgkFVIYkVD4xmL3SFCWAvcdw3kd7A3dAMavkLOhWc+uA/wyP0Lb'
    'rfZCkpBPvbFHJgCH0GDL+8sqV3jW8IFs3ZKZsNFO2EWsEHIe2q10t9mW7qZEzx/VLFgZF1+E'
    'uKjvfLKq6R2UrO6J61H9NJrIk+CHjkbaZTxBl322WjB+uu4Yt9ma+Av56YKmvuoYN8cr++9L'
    'WNOo/2avAsriIc2md53kfg71rvI8DR3k9qM0rZ+xLlHFblMxL4OqYpHvgk+CD2fRHuHBA1AE'
    '1nGbjzXKEdlSgYK0ukJLYsUnjcevYlqJkyCMuUlTF7wRPNbyC5mf7j+uYIcpYlXEn+axVnie'
    'tndrbOs3KBT9QGpjih7lTgk5LtJV5R6XecUqDFF4lYXZyCOnkyallHNKfglBG+dE2hf07Ilk'
    'KFzNFsXuptOJmUvNW/mQBEhRsTrLBhXQ0v+yPv+KZVy1nskFrjyZMVqCjzixSCQpSxnx+/7q'
    'MTQ42kqcejbGbZY61cCZnCeEK2wMEfZ1Xh6amYVf6aGCLdz+Z7aFJvzKszw4xDwVusOxJ1z8'
    'qeFD7kPbay71VWb+OcgS5PwfXD8NXmVszHRjuQlrUns+Nk9lIP9LX3BqHyta+iG/Zww71vLS'
    'Q5X/1GemAK1IEOCIjrXWL/YWHzN8xfH49n8tPrb0jKSaB6WNZXhrVl2QOUzaAb07v6upNwUL'
    'VyAOv1PSt6hOJumjXMWdJsfYHFbUlRldlxxfDmhCU7OCtztDmQ1Fu5EU6+KoO+m9rK65ircv'
    'M3wTbZ4TuBnymfGKzd6cnPQq6d9X5Z9Sx1uvzxvPpH4pnpXo/wRm97vS8g9bR8VPVPn1dEm7'
    '52MWv1WlAbbOyViTxrewJL7PVMHjk9Ly4ffsAb9Lnsaiz2gPzwzlVxLi19HT4evMSeOnklDW'
    'ZuV/zLHyO9FwkYj/6HZ1Dk5qvF0ClN4Sz4OYG4dBZxeMw6B/Y49gS8MdRY0zuZ8Ia9Vh2Y92'
    'JmP1pKDjn23jVcLEFRwTp5klD3xTBFIkPuBalFW9TlJJd3D0GglBMPft6cS2fF9nL+XQJRfL'
    'tpk8HVqOkIBd5myHOT1dV+f5o+YO7I1RnOFOOue7EDm58KVDfdnka74MWczG3F7DfRqnUu27'
    '6k65eC7lGnOfJN4KZbWDzLPlqxPRXzQwDxPyYGJVtcT3Y0fGKgM8VYi9XpFlF4k/BfnCmFLr'
    'i7NCi0ip5QhmBthXM0tdhllQpjERY8PAXkcj/fNHzSraIRKhgEYttyYzuRNzqG2T6aq06ezl'
    'SftHqB8Hs8A/WYg3cBr/4Qlm2U9yd4qJx0VHsWoM/sPOntUdCH46lBH2XCIhmNPSV10uO90O'
    'mSt8WbLHHbEZBWZXYt8oNy3jOdB/KZ0brRiAg6HpmYQoM655TEtoHiuyVY6xzxAktV35GXIT'
    'lUfXJ/L1oW/hViaMdwiJeMbxOFBHgjo8dxPheiwxsjJnV3B/neQvlZFprvtUxvB0KmRN3WmO'
    '4ENl1gaySJbQy5/aSmS+bytJl5geTXu/+VLaq5d45AqXGv1oo+DBijdZIauQc4P3OBFyUnxo'
    'BaJG0NdW7FxcIYbdXCTyLri6eHG68VRj6k1iDXzQJzqpuFZoIIemZ4Wy/r1417J7zO1tcm4V'
    'TiNB5sAUvU0KFkVOcfuyG7oZ6Ou5UmWOVs9z8Tx//GDxF6qUrzG9raRP/AntS48QUCT5C2W9'
    'QNqEye57g6N1ZxaNRMwGjHoI3Zn9Muz+WPG7EtnwhvFPOCY6/wzmnQmMZolmWXKg2TGJWcN8'
    'qwkMnG88SJb6qZsuUHOPxLbkFR2NXtSfjuPy2I7olp6PU1N9u20kTMwkMD33YhKZhA0XSztB'
    '5lz6ydE5yd2hzK8houmDPrgcr4Yx9iZDISYPIwhL62jPMhAmcHT1CBg9G/V6E6JFSv+9DyP+'
    'mcSuSLSIy4oWsfKHqmYKESbRiWVfmgOIn4C3xs8AoeCUTslNbfh3MHKrxe69OA7/t3adwRLs'
    'OPM4HntOiBcCHom/MApyG9pczF97odrsOuNZYB4Lfitw1LtHhHn4A8YP4rfnLov2tmgRah5P'
    '8P9z2m/tCND4Qm3JJ3aEj55RzGcEZqh9NGBqJDsNx9fHBCCsxzPfGsJVCNIBRRNRU4HxA4Kh'
    'O+i5QoNiBLjLLy7RNTCyy89KA3wFvXjIa+65DBrHj1LivLiBVekm/tiW1qlz0RYdjdyLrxW+'
    'J0sgNS3dNqSaDJZQpbFzKR/nLdmRPry4eZWh+o34GJyn0Iz4mEx6EHLCvz+ljlom12TIcSg8'
    'zhCxXKtuCZU/ic2C5p/yzyH0xkO9KBcsYxH+ds83B/Gznccf271HQpOfFvgQ1hJakgXoQgWP'
    '+T64HJnBuCHJpfImFJhnsB+yXLbduCHlkSKSGsp59I3DCVGIClIro9VOYW8dFKQbqSAdW/oB'
    'cFBhRsYy0CSSHWQdqNnXkg0KLz74sgrmeRNdLWWa+lKD+RnpbObmVBeyxxNMipKHTimJ6ts6'
    'eYA+BH1q2Z+oVHXj+GvQA5KPKuXKGfy+E13Lb/EWgfCdENmCiCbExI0MVV6UFc0QeYIgo5FY'
    'Q/V96gheVVzo6bpkv+9NO7PbWfkg4/F2c9GX+ahRpBUOcQbT5rd6b6L805WHblOVj8qKOuP+'
    'zSbDxzwNuo3riid6jhg+nnMj0y10M60ndhjT0YbxXYTvvaZDW+aqaKfvdtEKpv3bI6RRYDaL'
    'F4iRsEs4I4UeyMLJ1tXuCUV8XdjUSYk62R6WhQPGyE2XQXbUcSihHIr1belDTQhcRUbjYFnf'
    '2Bskhq3rktNaIFPtRWBepTpRE3F6CM+bibAsJGUrh9BvYfydL5xNn7OvKdvX2Vf85ktqSJtC'
    'mXlweU6gIwplJ4Tm2B1cCDF8O5WpOCG+P6IjlDkWmx8QNJjcdB66Em+Z7Y4sblm6hwQID/pY'
    'Jqju7AutGIrE9sj6iT6PXeGerPJghXF6xBqoK90S60hHFFMnL+8XQsf4HGXNhyd3K5xyftcM'
    'rg2M5A+S8h/w2HmZ8CJVzBtCkCIPJfYhz3rVpXZl4xwDymEaNZrJch8LzuwL/aTPeLXZ13U5'
    'fAz5vt5hxtpmUs7CL4rPLbsvOK2XaR94TJMZed0lKlv+cWZIOWU27/kUR3hluTXgGW+YERV7'
    'tqeTS8Chmb3F7y6bH5z5uXlqz6dgQof7oMONYzsGwI9DApAfHPmHdT1gWYdbB7GhmqRvFH3g'
    'cDh5aXk/VJq+5GJ63/Bw2eyii+U37Tla95ZUijWGoqPoXFrwgd49R0PTs832PZ0Z4S+DT/Fb'
    'Abx+JVoQw0eUE/7Z5zw1E4M0W7RMTtntTZ9mjWEYHoLx4PT3zBjBYHxIbBjgzBRWtKNtmown'
    '7Jvte8IwmkI3uF66RqBjsAWyHb/F2WWIWPTp1P86EvofAjVL4zVsccWrqYtcyH0x07KMVxtZ'
    'C0dSObgvMJ2JOhLnR+zGd+9yre0fiaBlyDiTw+mMYR4vxXOom7c4Qz/9E6FBXT5n04dp0act'
    '/ON7ap/XYof52Z59e/5iNu05tuc4BO0xxP3LBjrf+0gmkiNdM3/izG9CK5FL9X513OsXOeiM'
    'xGvH+uW7aTN37TmOejthVfwkZ8+xjC5Ue8kO9NDXlGJuj8xT867+eAuP2XiJkMpRZ9C/K+UM'
    'E4RCD6tP9+/wfA3hnv3yxWwcJseF2PnWe+I8G1O+5z5tZIjzZkSddBs1ezKqfJNvsCFf1nlm'
    'qZLFlQwdkwOF8VVu1ZSY7WFGVlbU23XK93KLLrHNYjI0islao6AtMSe++4A70EvFuZqpRa5T'
    'HzGcSPbL80Kmi3oARRaqFp1Kuz3/HL6MOyBtf2dt4ZUmmf+a5OuS0GUP5B5OMkTAxKZuo7Qj'
    '/D26L9vCp9NkMSdTDtmUrKvp4SulBpWuoP/8aukTXI1kPCcqyBgS70plMIcBsJ+oBLD30TtT'
    'dJSTw68Jehhu05Zorlp3b7W31K6KYbH6V7KZIwXiePayHykzRWwYZAMvY5oE0bt+xv3rjat3'
    'YMJ9ld27ywwrPkUe7mr32GhWPD6+tXaF4+9sOE+yJaV/Zalmgarru6qud5DAlfulzai5Tdxz'
    'iMKfrDW5futtleZBaLVacYkXseTX1slFRzU0Iu+3Tka0u37A/QB0/cipspkIkdnhvTQ6uv95'
    'k7DHmvPfK35jWVrtRJu3y2y/q2XA+ToMGylFBeVcYz6ofC7TiWZu5w1eYmxMUdYLprECRsb8'
    'UW3/rMQmHO5DZ/TORuukoVKJky1lpLA4JPKEkrBhm214foDPpjMaqByrqioAroSx9dYxfDon'
    'gNNUTEZCdcTPs5wgVVGY+P9LEDNRAjF1XnkV7+GdC2hpSVeZqcIZ0QsTeC7BWQUmtg9IqnEL'
    'P0g3MS4ZDIsnksBJjyyJJa/f9QPZnshxzp6nfwnIAW4lDu9N68dDkaUM8tuYAi6W3J48+fHO'
    'okYyQKG4A7BAYp0DPh4DAp46myIswfyEge4t9bQtsI9AAzDdKD2pOYyuAzmIhQTJ7er6AKGG'
    'WZv9+EgreXPc1Yzh4jIBqi9Tnt3qMI+ulhX7ddKMOugImj13qvmjKumW36eWAhYW7Ufc/gNc'
    'YYDAPsblXkBEJWQhbMqHuKfU3oKMbfYmc4at+MyyG6hmSGSGcF6Wdis7tVs50+zN7xARwVR2'
    '4KLXwbcLlw3nCSJtZZpFyvWko86fYNPg0euprvK0wuuLuqMjGhL8GkylrQkvMxG1QhK6MbFB'
    'tU6Pngm2DXf3SWO7WfdnVu0bdWvY3pBoT/m/JpBFZGjCkheiNZwlpsuWWo6IJz4ipUbpe3o4'
    'SvSpTNwIN57a7aN9HJUJTG6TfD7Z46ghaGsZn+SXSxw3LMdcXaKCGh3jtd+ev5mrBGJ6ODPw'
    'IDLk56lywhfPi8J5L3B+dFTA6sucap20Uhwc4ZwqBxU6bumQg7S05ZMb26XYtVxM9FzP5Rsm'
    'wDmCiT7WqfyHXf9ZUZN41sAq7mEVmDf46YbHlZdn7+ZOFIw2xcawbKJ/JDwHz0ugE6v4s2VX'
    'DLSveJ7YkBjyTHozoCQXf7b0KDnWYaEJama4lqtLNh7RFj8fTLuKXWHq21CdnXg2SvK/8Tfy'
    'XPpamPJ7gIeUeXe0Xqy3AiJ5dFNvmq/RScTyjPe6WzK5ES1efmB7cwa0Vz6gvYIB7dXdlxlb'
    'LUa3NDua6lkLrGVfi9PkIjPKRbYz4Dg+/2eG69XSE9StTGhK9n4agspX4bkYXi/lP3MkrGAc'
    'uSNypiX5PAGeJoDZmvL9BUVU6QK3XeP+bhhfyAZU3O5x41A8JL+Sw4CcVjnm++OA5Flydzis'
    'ZpjyzSGHLX7gwGK7eOQmkYLMVHs7vO0z09X+L9KLQ7Wr8noQ72z3glADF0+ZcBVla632Xhmw'
    'v00JzhXx9QW1nKJL04oO82jp4LeD9zjablIouUmhZKgtqbsLlAbxNUSZgsQM6flnOMWOP5ed'
    'oI/Hp9ZceO7HPUkIj42ZgxHlda6+ztfXhfpara9L9NWjr8v1dYW+1uqrX1/r9fVxfW3Q1yf0'
    '9Ul9fUpfn9HXZ/X1OX19Xl9f0NcX9XWzvr6ir436ulVft+vrTrla6x2RN+/qt6Jl+//4n11f'
    'a3EKV0z/o1Skns4DNxq/kyjDjEp0Gxf+V0VNI/6zvVhRU4v/nPivi9eNFTUl/40r/rP9n3//'
    'v/hXveje+++7755l98x/+KpxRePnV1fb7rln6YL7Fy3zLFh6T9XSeQ8uuGdRTdViPJ2/YLDn'
    '+LfU9tDicTULPOOrF99vm2ebsWDZsnn3L7CNmT/e+j/ubUVXfXt8If5XZKtcuGiZ68F59y1c'
    'VLPgOteYZayj/7N58+cvRS2umsUeV9Vibw3CuGYuXPywa4n33upF91mvbdWLFz+wqOZ+l3fJ'
    '+PHjbcs83prx1ePvX7z4/uoF4+9b/KA8Ker/iDXOe2jeoup591YvsE1fNB+Sq2LxUo/rQe8y'
    'j+veBfi/5+EFC2pcRa55NfNd115zzdXXjLeV1aDXLs/CBa6Fi5d5vrnMgmC87c55izwEoWqx'
    'er8Yf5a6llTPe2TBUgI1bXFNzYL7WIS/bLYHFXJc85cuXrJkwXx0f77r3kc8C5YV8K5q6QJU'
    'UYVb4sT1dxU16WMraurxH+/539yCiprPcV2S9Ox/+l9YX5/Jr6gprZhWeXv5+NLyctsdi5Z6'
    'vPOqx91W47p1gYddsM2Y6Zo5r2aZa+aCpYuqbNPRd9c81/0Ye/RrySO2mxcvqrEeqJcKLdcJ'
    'Sq+zTZtXc9+CapT1Vs+XsVy8BJglmkAtDy9e+oBrCcoRLUllUMHi6ocWoNg8TwLPLlfe/EXL'
    '7pu3dP6C+fm2JUT5GGKv2ut6cBlIiOMiyFw278El1UTngwvm1ej3rnHXu+YvQIcUjgGu0Nz9'
    '3gX8iJ/is0eWnVfmVpDcrRrSRSSBpd4lngXzCfD3F3uXuu5bOK+6ekHN/RjxhfOWuZZ5ZExl'
    '9AmgxqL0zxr3pXyx2OvhKIOVllznQgVossDlXYZPZeArboL8rLi9xoH/nsTywJPvJ+TpDHz/'
    '8LiHr53gWuqt8Sx6cIGrCsTsXbrgumG2GzTXjFki4NQsdi16EG2OW0YCXFwDHOoh/r/auxbw'
    'KKqzPZndYNylQ7CgFFNcFfujIp3Z2Zm972azm8uGJGxuBEIk2Ww2F9hkY7K5AVU0kCi1ikgr'
    '2vibKiooSsQbKrZogKDiD1W0VFFRsd5b7I8Ub93/PTOzZBM24NOn7fP3eUieb8853/nO7Tvf'
    '+c53zpwzU9gWbOmSEqJIIrkxIdQNsxy5SDIYS+NrCUeQTyxVR0OkXhcI1wR1bOclnYSutGlx'
    'U7ijSdfcGmyrCaMbQ+GAn5Sra0bScCAc0rUHW1oJAjpBQ506TXUD+qRhSTBGS2oI1MmEqDHa'
    'q3C0xY/OmKmL+FvqgtKwvKR5pq6rIRiqIQEieO3+UBsybSaZzmhqC4UuhUs1UW1UCP8IUAX+'
    'AsrbVEv2qToxRiIyzFkqu4ziNi4djtu8BPPfEtn/J+DVy2T/m3Hp/1H4SsnjiS7Z/TPcH6Es'
    'j1JeeMkw7W1Lxs7nm7aTcVPaTl/+Bx2y24J6/Az0kXZfkxOwEfB2u4z7e7tMcy5oRUBph5z3'
    'xo7hfD5F+NW2kWXG/AQPjjc0NUS6JPaTt41QxPKgKL87HLi/9xXno7urfj5j1S7nN113+X7o'
    'edWp/vpnd557xVqbvjy7lIRhwQB0LlguMF0OAfqJCeMkh5JxW4n62SlnwSNO2Z2eLrvzZTf9'
    'Ztm9flB2Fx2X3OUrZrmIu++cgOSu+MNtkht8+WXi6ir76QxiFLfnGIl799qpjXDTP+hj74a7'
    'etqOba/DNfzky8Xj3dTyQ8auQZeb6j+wsszQ7qZ2F9Q1Pvegm7LfUnV43rvu9Bsf7/zx5ske'
    '31/ee/3w1NmeNa/lj9t7ZLknVvNtNxy4bcsrfTb+/g2vXXzMa7bff9Tz3YR79XfeduPelKcX'
    'XOIOb6qZMW63ZsymK+Wf15T8+ONbb8368ivnx6o5pnz9j9mvcjquK7f9rvWat+ZMbhsreWNr'
    'ewAqXLYbKkOBSqITmqF1KmvbmgLUCBShz3a7LboZ2QWll+o4YZZ+FqfTs3qBNbEm3YysYE24'
    'xa+DissuU2Kv0M+qDRgMl45IJ87i5HQiK7Dc6HRSLEyZf166f7SeZ9KdSffvkOsz/DyT7ky6'
    '/4xxS/ZXmp+U7XniH2teJWuA1Ox//T7KpNg+0JIiKmlZStL549Vqsj9K9l3JO5sO/TQaJe9W'
    'oFxMSg/tYsZ3q9wMS+cwM4mTqWHGZw0yKa5djNqjbZSR9zDT4fgR5VaiMrUUMVhCxNzAx9/+'
    'i+TnwaMeb2oy3UZ5JqjamselnkW3LUsdR7d1pqrotghdr3kOFK5B1y7XkGunG9XK0Sr1Ja8H'
    'q8fHfc6XFo6MroxRj9mO6Uo75oGevMuXtGMlnfGD5PJuFe0dXKTZKedMaEgdD4Hup/F0iwnB'
    '2Hw6qOR/HOn2KXzqJXzqUbmY1G51JjNlAZPqZabkM6nuk37Hwp/i18tMwq9bw6S6Bpnx4G6K'
    'a4gwP+OfVIp7ZMYz0CRy8OsjPhp9XOY3W65h1G5tO6Fl2+F3aSsYsidMbhTNAA+W4eMru2Xa'
    'SXMRn60t7qZp96AbXmYSnd5Ne4hf4vc80KcJuD1AxWTMLctYWgFpaxrqnsOk5ZGy0qohURIG'
    'v6ByKdJFkX7tRz4lyOddch47i/lWXcgcV7vhzu5O9g3mMJK/O3lhjzqLOSIF/L2qlXQpAjkI'
    '0D8DuZd4fMxhKfqER6VOQhwhyh7M3pU9lL0zuzu5RwrkdievpHvUvap8pTB6npJokUbBxNLk'
    'QXjJxU/yLLDPhN32s6R6DqgKupPrNYOkB+kiktVKOovZoPIipprpBx8GVJCmflUOPOWIIAg5'
    'x7k96jpCnsGsl2Jb4JLYCoWqVnELFXzpKHcxcs2FmyflVoQqkOrEWtTAbJIL98U8V+3cNVTT'
    'ow4p4aJeVZbiLVFKmqOE/YpbpuDzSQlytj5k0qCgW3vU5XIfrJfam6+0O1+J9yIb0rCQEh7d'
    'gBhdzC2UGY2u2eWVS6Nzdg4tGJWqcFemHOlX2FsYV9wwdzN71KVKxPyV9DB1/s4YHUWRQyFT'
    'IPgVjmj0fWqEjsxkptO9mmEZzdF2EOGdXhAntrL8wx2P7xZ9Jo+XmXk9dFW3ai6kBiLBzOxW'
    '9WCwtJMxP7MIKJeMapEQuYN5Uj7kIUI/3MeQz+cqKZ95dBGTRxwPkwMnB6PQqy0lcguu1DLp'
    'JCp/JT13J30N45vNzJvL+IDyDHp2eYbmk2E6T6Yt1EiobC09d3DXUKlEJf9mygRXSXkVr6Tp'
    '2ZqdmVAKJJizk7zxnDJg8Z3qjkY75TrZ8rvVTUTUM7RNmh7Vol66aChLO7dHRbfu8hE8Sskg'
    'bpaW3t2j8uyiu8hMYivvUfXSNRpUg+RPMLnwk2dXgHUoY2ZWNDqXkcbToQkYnFeSQX5wgheh'
    'Cs0gWDRbuxDNvoY5MMEN3GwNMoI8qqaPY/ZImFZmv+TWwZ0Nl56D5AQRUCIwCmREheKqHkpS'
    '0tJfaqThrdIkEU+ZtoXZLUcsV2jp3Bhmg4JR5owSHMTZVwxdQPo+j0mZR7RUSjWjLmNSSHwE'
    '8ekl0Wi29KCRSSmIxZNNBsBaxDcjvltOn1aiIYLWTqjSikFFaLaC5iBoemX5mr4YNLmgIcJY'
    'rOjgg6DJKY1G3bREM2WRpN+roLNvHswnYjjlKvgzBhdIU09pN12lGQxoGzSDmdoCCYVk5Dza'
    'dOg0X1k0qh4n9cU2OrtHXdCrgijVQz46mQ3Q7dugIrdKrldx6T4lgv4Ds172VMQ8c5lNsmf+'
    'zishf4w6D4GYiqIDmp35aIpM267ZWailN2t2elEpOeeg4l5DJDOb/MRiZLtgP+o7UBGNBmTe'
    'pPjQ7nztAg0xWGRejz3vT1L6kLoSt9aIW8CkVig8N5B5EPhqRSd0o0xdhwbDXtLz5JW/FLlJ'
    'GAHNBaexw9KUcupB61DsHaWekhyQrTKih66/UpGDWHk1SnnZ2lrF59VWyh5SPtncPYw0qtOU'
    'P0UpfzdovfH5K88ITcQ+QFxZQtsnnb6BMbiZ9NpR5opb28WwwMt1J3ecplRGo4wqzu7K6lX1'
    'YMIu0CgWIOqTpVVp6bhwhvZ72K84f9h4wp5D3XpJ3XqgSCd1J8N+yWdmemHRMDMzGbZWsl9h'
    'tKCqqajqeFQ1xbVT4vN0xX6lcEh/qTwe2XYp7XwprVuyg2QbiJx9mAc66f25hQq/8pm0HEZH'
    'lzBpmYzOrXRJrpbQk/cI7gZ9cpJsX7VoiMqil0jVmScFrpT8p+inVKW9qwOn79MYbQS0sxQa'
    'nSJLJL195FzWFZuwSF+RFylsAs2vT8FTmP6T3KNYSPpKp/BmPL5VKe3Hhk6UQRcohaQqer0e'
    'NHxc3cg5lLU1Cu8lGawhUwXYlDtIOoikI9/tOAIavZKO8JYci0vDN1R+Pro8zYlliyfWvhwy'
    'don9CvohKibP7hPyXI00492KCGdq6QUaRaTdQ6TzQ3ECPibvdQrvp9RGo/lx4ymdzpZF4hT9'
    'ZlDS5iHtB1Si8dZM33LSwoA835D7TV0XjZafKBNC26CIYY62Kla4TfkehQm0XUmxPnaP7ONM'
    'pko1MUlz8kBxaUl68kLMzvpo9MqkETLijkufrspOnN6gHEpY1xCN+kauCTB8oJvHZyvd5dXO'
    'Hg5I9TYh3QDSXU+NqLc7XjZpb4Jisf4jstJJ6rsoGj087mQ9pPqTeoQmImNlA+i34ywjeavR'
    'CXoXCuxRu5Hi6Xhd5dXKvD2INJtw6bQgKS6NWy6DfiYugUfriwtlapU+TMPctQ1fHbepT26j'
    'W2mj6vZEvM3SLkmAJeOS6HDydor1XcNyRZYhyNcTl286/dLJ6Ul/DSDtvCWKjRFvA7MYZsMG'
    'r1dbMxwgMvkR0g0sGR5rcemejbeTyVw5CUqtZGk0Gko4z7B03yi5x2Q3CgOrMmsUKlsr9Qn5'
    '5kg9jkCtSB6Tp1Wqv6kSCg6ZA8laYOu10ehB7Rg6EXZpkkpFJ8ggW/s95v6KXyhzvzJuda1M'
    'CpSWWqfM/csQvyJ+bqZDGNjQwBAboherpDe1RKM6pRyShrzT8ShwND2mHq9SHUtKOBeOVd8V'
    'Sn033RyN3jN1TF5sT1LtSsSLPG17gonDo70qAe1srapNnYA681T8LFHqt2djNJqZNGa7p7cl'
    '7Gn6jwnrIX/CQeLnjgcwLk8z93qUOqQ8GI0+E9+ndWRPQ1ekaORMbKmkyVYDfhdLv+4TFp0y'
    'J5KzAsuRj/TwckGMWP4l+ytERxFd2Aead0bvr7B0urxdlzdimUrsHHJGMGeTYr+CpFRaVzRp'
    'iLbIVwwR0k7y0avVoDtAjTFPEJ3RmqCXsrRS/cn1HPahaPRBkh6DGCYy2QYpjtk6pAzyDdxt'
    'oNmedAo5fSyxnJL5nPDcthl7rmOnZ+lfSSW2JZQnrN5IJKkL+UZz1UA0umzsvHyq8qSEYkLa'
    'Owljrf+RaDR3pL6jYzuoRD7JGdk9oLn0VOPyhURleLAiSyygxL5eTfT7o9GomDRCv8fnq6NX'
    'JswgmFBvVSQcJF2JOoK0KxU6suaxaPThsXmXTrcllJXOBNjc2LirR77bHo9G2e9p8+4D7cWn'
    'oY3ZaPtB2yePAV9QGgNYtGJ94Ktk1Kyy/vE9EY26EthiEJmOkZONbPsSPRFCmitOIUP0UrQY'
    'QpHqjmuxW0vPScCI2dqiBNgcrVvBukcIM2Yzzyjk91j7fYb6Gk7oqhbJThwrTXpsPxxpVifF'
    '27eVjIk4OcwMOLOhy7KkrZ6mE6tT2i9FyesQMmd5cDi5MoEdTrIpHsXcemVt0Yw0trFlvIp+'
    'KvEoLwQ68yQhd5+MpGR7mtioHnyRvWS0Xk2HwoizXU4nj1XIIyVOHknbK54aXusvUfQ40WUr'
    'gF+u6GRZD1cSjrEl0h57vrRrky9h5PzJuN+HNONOI+8zlLo8BlorNepZT2prfHNOux8ygDwY'
    'uY6puadYB82MrVdBH4q3XZpHzHWZ2kWyh9DnKe2pPxV9tew5XT2PI5/JSj3zTlHPWD99BPpL'
    'Rq2V1fgE9otxdZGeh0mly+sDIhsDoKlNqB90dDlG4ggF8T301/ZnsE/wT34G2La+YxFxm1ha'
    'Out9lgIzEPYAqgCHIBg+wBF0hA8bBP0wOsikuB3QhfhewO2ABwHPAvYC3mZpaaDQ+GKDmkqm'
    'xlHKaVNyWsgdCrcGcaiwHUdwWygPTmK2hLs8DS04Y+gjR0+9OA3W4A/FYYqCgWBDezAOU1zm'
    '8Uf8xcGmGiWOIv54ghFBclY3djj5zJ/810leLRc7BYeHU9+6aOpAHC7ipamKDJryuYZxh4Db'
    'D9xjcbgjwJmwgbwtDmfKpal0N5342Bnw6wADgN8C/gfwHuAogPbQVCpgGmAWIBewENAO6AH0'
    'AdYDtgB2APYDPgEcA9CZcnkM3KmAWQAbIAtQBCgH1ABCgAjgasD1gNWAOwB3Ax4AbAU8D9gD'
    'eB1wCPAZ4BjJP4umNIBzATrAZQAxSy43HW4JoBawHLAWcB/gCcDzgH2A9wDHAepsmjoHcBFA'
    'D8gAzAXUAyKA5YCbAH2AAcD2bLmMPYr7BtyPAF8DUtB3qYBpgFkAG8AD8AEqAIsA7YDlgFU5'
    '9Jk++H/SBxcmeYKhYCToboG6C/hDxcpZbU+SdOdgNJranpSFOwJ5DdUt/pYuaimdHYzk+Vsj'
    'mS0t4RZi5SOcH65pCwVzcH0hFIRp+iLBQdEGlOPh2JJWZYfC1f6QK4RT1NQ8JUTyhe2hhPLC'
    'gcWw2JVQaVNICveqFLWMk9mjq3a/ytvqyXAX5wX9NRk4TJ6JU+EfqxBqP4mU+lyVF/bXKK1A'
    'HdPU+W2hSANJVhIuw4zgrve3UDclF4eCwWbq0eSSUCsaMZec3aYOJ488kU5RHybHn2unqKnj'
    'YlmUhE/kS+WPC2GOCTQ2o7xi2d9Myp4v+XEKH/7zcWC1ubKyIYyl71uyv7KxujLQ1lLZ6O8k'
    'vVXpb2ytqwx2NqDc6qRKXAlowh2YR5MqyelldFgjdugqJWaFVJVtMtvq1P5q3HOgwmp/JNyA'
    '+zJq8IKwnrpaXRsg8yC5eFpLLkdQq9S1zbgjEKnFG41qm9siAWq1ulbqmd+qyZn3UDAQbmrH'
    '3Sx1o5LHC+rGYCOaQlEvEV9rEDV7U90SlKPfUwMhJ6Q+U5NG+xH/Z8lXD4n5i1pmCnaDiC8o'
    'y9ExtcwSivqK+JoIwY+S22NVo65I7gi0SvFeyl0fDCwu8tc0hDPaIhHSu4Xy5O4ONTRXh3Fj'
    'AyfpKQ+EJlyXEe70NtXIE7Ifdyxc1O8Q09rsjwTqlZkZvfA5ldnYHOmKS38UN3DIhZ2yhqaa'
    'cAde/IVwjZwlNScJouEJ1XkjQTzoKYwLlQQ7Iy7KQEMuQ3Vy5aTKBpFjCT27IRQqwQWKFuoG'
    'Wikb1XNRv6HnoCOGC3+Y9gWDi4drt4X24cLIcPhyVXEwcoKcGCR4aT/BjaiFk2CywoE2jL8O'
    '4peLpvpU5GKTu62llfD9N1Io1srnVCW40NAa8keCJ8yWN1WlzTVAxGgodUer3CsurH/IhZBs'
    'OW/qAqqs2OUO4RZMWzOxVxEaoSzmE0wxrkpESPytVHUDuWa1lpIEshVyC0G6ncI9CnI/prqr'
    'iVzyuSMWlkLrqXqwtJW6l8KlrUgluThC3Sf7myJhP7WRaggHIiElr4cpdHt7bQsuYVGbqVZo'
    'PTJ2H4GvqSYSJqe6FEIocPpfBrMziwoy83i9ZIMSexu4fzfEnZmnZiD8r4TS4syiWGttCJd5'
    'C/LzpetesOMR/kehrFhfOczFM39n/s78/Uf+pcp7WeewF7Fedi7rZ69iV7A3sS+wr7PvsJ+z'
    '37EMN5n7CQ4w27kCLsx1cqu5R7hnud3cHzitfor+Qv0C/WW8l5/DV/P1fBPfzq/g7+Af4J/g'
    'n+V38X/jacPZhnMNuYag4UGDUcgT1giTRa84X6wWm8Ul4vXiTeKt4t3ia+Lb4lHxG7HEuMDo'
    'Ny41bjA+YnzSuNv4jvF/jd8aedNy01Mmh9ljvsH8ifl3lrcthy1fWFzWF6zrbQ/Y3rGNs19o'
    'F+wN9mvst9jvtG+y77V/YD9mT3YwjiscmY4FjuWOHY49jg8cJqfXWeFsdfY7h5wHnH9yfur8'
    '1kk2c9ah/efhanI6W8M2slezvexrLM1NQ5vTublcC9fBXc/dzvVz96HlL3BRzqnP1S/SX6df'
    'pb9bv1V/QH9I/7H+O/3Z/Cxe4K18Pl/E1/FhvpO/hd/Ib+Yf57fzn/CUYZzhh4ZphosNZoPX'
    'UGSoNiw2RAxdhtsMHxu+M5wrXCDMEDghU1go1AkRYaPwsPCs8HvhDeFD4QvhG2GKyIFrPnEh'
    'eHa3uFUcEveCZ2+J74ufil+I34kq40XGWUaD0WnMNPqMc8HDOmOr8UbjGuNDxneNHxrVpnNN'
    'aabLTYLJaco31ZmuMd1l2mraYXrBdND0oelLk8o80cybreYSc4253fxL8y7zfvMb5vfMH5mP'
    'mI+bKct4y0UWkyXPUmSptHRbbrbcadlked7ye4vGOtnqtuZai60LrC3WG6y/tP7ausX6sfUv'
    '1rNs59jOt7E2k81rK7KV2cK2DbYB2wu2V9Bf59rT7BfZA/ZO+3L7zfY++332rfbn7C/a99s/'
    'ttc7NjjOR8+QDbY09MtL7GzuJe5B/af8cX6SIc1QZrgSstRr+IVhneFJw7OGY4azhAuFmYIo'
    'pAu5QqkQEOqFVqFLWCV8JMyATCUZ04xm8CXfWGQsM15t/MA41WQ2uU2FpptMt5s05lTz+ebL'
    'zKw51+w3X2W+ybzO/IX5G3OKZaJlqkVn4Swuy0JLvWUZ2vykZdDylkVlPdt6uVW0FloXWuus'
    'zdYu6zVo9xbrq9Z3rUet31nH2Sai3Zyt3natbZXtVts625DtK9tE+2V23m6yX2u/yb7Ofq/9'
    'ebvKMcFxgYN3uByzHSWOBsdSx2pHv+M5x4uOVx1/dHzhSHGe40xzXubknXbnPKffucgZcT7r'
    'fM0p3VTEHjl5npXCprIWNsyuYn/F3s0OsAfZ99nPWAEj1c9dx63h/pvbxD3NDXFvc8e4Kfpp'
    'uKCQqS/Vd+iX6fv1v9UP6T/RH9FTPMOfB4kt5xfxS/hV/G38Xfxz/Gv8X/lveDUktcbQZFhq'
    'WG94xPCU4XXDQcP7hq8MtKAVJoLrl+FuvFnIEQqFCuE64efCWuEOyOybkNivBZV4tugSi8VK'
    'sUe8R9wsHoCc0sZyY8B4rbHXeKcxxZRrKjPdYnrI9LLpr6apZsGcYfaa681L0AcPmp+H5B03'
    'f2emLZMtTkuFZY3l15YHLI9bnrG8bxlvvRA9YLI6rOXWKuti61XWpeiBNdbn0AcTbefZLJC1'
    'Ttt1tlts99petO2zvW573/axbYp9mp2zh+1t9qX2VdATt9kfsQ/ZX7a/Yr/Ycakjw1HnaHP8'
    'yrHRscWxzfGp40sH5RzndENffEP47ZPPy53DTmEvYVnw3M0uZleyN7O/ZO8B37ezu9m9bDI3'
    'AZryYlz2yOVKuHKulbuaW8Xdwu3k9nKvce9zn3DJ+gn6yfqL0Q/p+hz9HH2f/j79Jv2g/mXo'
    'kMP6z/UX8j7+FwbBRAojarncEBXSocvIgRzy/GUTtOo2fjd/mM8z7DIkC9nCd2Iexvqrxomm'
    'NSbGvNgyEXpwvX0HOcC1Wn4W8wNjgbHHtMm0xdSL1j3i2Ot4w3HI8YnjiONrR7JzsnOaM9u5'
    '0FnjbIJ8rXNudG6GlA06/+6k+uVnDhfiSksZ2tnHrmcfZZ9in4E8vcx9wX3FXa43652Qp2zD'
    '/ZJ0vGSYIegxCrMw/q4VgvbNzq8J7/BSHLJf/xD7Ay6Du0v/rl7kbZgvCvkW/lq+h1/L/waz'
    'xRY+3TBguE5cJa4R74CGe0DcIj4tPi++KL4iviG+J34i/lX8GvKjMZ5jnApdd5lRb7QYM4y5'
    'GNXlxmqjfOiI7MunoH8MuL5jgzb3sDlsHutjS8hm/D55nuuDHl/PbcDIGOAe47Zy27jt3A7M'
    'Znu4fdx+7gB3kDvEHeY+4j7jjnBHuePct9xafh3fx/fz6/k0QSdMh56eKbACukmwobUeyH+e'
    '4BNKhHkYBVVCDfRPSGiGDu8UlgnLhRXC9cKNwmqMjXVCn9AvrBc2CJuEAeExYauwTdgu7BB2'
    'C3uEfcJ+4YBwUDgkHIbe+kw4IhwVjgvfCpSoFlPE8WKqOEmcIqaJOnG6OEOcKbKiQTSJNjFd'
    '9Ig5Yh5mhhJxnlghVok1Yr0YwtwaETvFZeJycYVI+rXPOdb0T57VzArWy2/0oP4PGEZB2Q=='
)
# --- end netplay blob ---

NETPLAY_NAME = 'dpctrl.dll'
NETPLAY_KEEP = 'dpctrl.dll.stock'


def netplay_dll():
    """The DLL bytes, unpacked from the blob."""
    if not NETPLAY_DLL_Z:
        raise ValueError('this build carries no netplay DLL')
    return zlib.decompress(base64.b64decode(NETPLAY_DLL_Z))


_NETPLAY_SHA = []


def netplay_sha():
    """SHA-256 of the DLL this build carries. Unpacked once."""
    if not _NETPLAY_SHA:
        _NETPLAY_SHA.append(hashlib.sha256(netplay_dll()).hexdigest())
    return _NETPLAY_SHA[0]


def _netplay_is_ours(path):
    try:
        with open(path, 'rb') as fh:
            return hashlib.sha256(fh.read()).hexdigest() == netplay_sha()
    except OSError:
        return False


def netplay_status(gamedir):
    """'ours', 'stock', or None if there is nothing to look at.

    The file itself is hashed rather than the .stock copy taken as proof:
    a reinstall can put the game's own DLL back and leave .stock where it
    was, and the button would then offer to remove something that is not
    there."""
    if not gamedir:
        return None
    path = os.path.join(gamedir, NETPLAY_NAME)
    if not os.path.exists(path):
        return None
    return 'ours' if _netplay_is_ours(path) else 'stock'


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
            return False, log + ['Write failed: %s' % exc, 'Nothing written.']
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
