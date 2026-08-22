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
        'for the retail disc build.'),
}

RETAIL_HINT = 'Reinstall from the retail disc and pick the installed copy.'

# Handed to this window instead of the executable often enough to be worth
# naming. The CD MUSIC section wants the cue sheet; this one wants the game.
DISC_IMAGES = {
    '.cue': 'cue sheet', '.bin': 'disc image', '.iso': 'disc image',
    '.img': 'disc image', '.mds': 'disc image', '.mdf': 'disc image',
    '.nrg': 'disc image', '.ccd': 'disc image', '.toc': 'cue sheet',
}

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
    'd5650300609ce80f02000083f801767431f683fe02736d6870cb650356ff1540'
    'cb650385c0755a0fb71d74cb65038d14b584cb65030fb72a66891a31ff83ff02'
    '733f8d0cbd278160000fb70189da21c221e839c27428b80001000085d2750b80'
    '7903007419b8010100006a000fb651025250ff35585fae01ff156cd5650347eb'
    'bc46eb8e9d61c310007200001020000000000000000000000000000000000000'
    '000000000000000000000000000000000000000000000000005589e583ec0453'
    '56578b5d08c745fc00000000e84901000083f80174416844cb6503ff33ffd085'
    'c07534c745fc010000000fb70548cb6503a900100000740b8b5318c60280e8fd'
    '5b03000fb70548cb6503a92000000074068b5324c602808b4320ffd0837dfc00'
    '0f843c01000031f683fe0c0f83a1000000833d9435ae01047512833d9036ae01'
    '087c09833d9036ae010c7e0f83fe04747b83fe05747683fe077f778b53040fb6'
    '04722de0000000726383f810735e8d3cc51b4d62000fb6070fb757028b4f0483'
    'f800741783f801741d83f802742c0fb68248cb650339c8772eeb310fbf8248cb'
    '6503f7d8eb070fbf8248cb65038b0b3b048d8ccb65037f0feb120fb70548cb65'
    '0385c87502eb05e82500000046e956ffffff31f683fe040f83850000000fb705'
    '48cb65030fa3f07305e80300000046ebe38b53100fb60c32f7d18b53080fb702'
    '21c86689028b53140fb60c32f7d18b530c0fb70221c8668902c3a140cb650385'
    'c075385631f683fe0373258b04b50783600050ff1504d5650385c0750346ebe6'
    '681383600050ff1508d5650385c07505b801000000a340cb65035ec300000000'
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
    '01000600c83200000000060038cdffff0000040038cdffff01000400c8320000'
    '01000a00c832000000000a0038cdffff0000080038cdffff01000800c8320000'
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
    '005253204c6566740052532052696768740047616d65706164202858496e7075'
    '7429005477696e2d737469636b202858496e70757429004b6579626f61726420'
    '2853696d706c6529004b6579626f61726420285265616c290031502044656164'
    '7a6f6e6500325020446561647a6f6e6500'
)

PAD_DEVLIST = bytes.fromhex(
    'ed4b6200fe4b6200124c6200244c620000000000000000000000000000000000'
)

PAD_SIMPLEDEF = bytes.fromhex(
    '11001f001e002000100012002e0022002d0013002f002100c700cf00d300d100'
    'd200c900520051004f004c0053005000'
)

PAD_INIKEYS = bytes.fromhex(
    '31502053696d706c652041737369676e0032502053696d706c65204173736967'
    '6e003150204b6579626f6172642041737369676e003250204b6579626f617264'
    '2041737369676e00'
)
# PADTABLES BLOB END

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

VOXT_CODE = bytes.fromhex(
    '89d381f9419c00000f84bb00000083f95474766a015f8d47525053ff154cd565'
    '038d34bd94cb6503566a036a0d50ff152cd565030fb60683e83083f80977190f'
    'b6560183ea3083fa0977056bc00a01d08d50fb83fa5a76040fb64603ba081c60'
    '00803a0074085389c18bdfffd25b4f79a5b8481b60008038007402ffd06a0053'
    'ff1538d5650331c0c3ba081c6000803a0074336a015f536a28598bdfffd25b4f'
    '79f46a015f8d47525053ff154cd565038d14bd94cb6503526a006a0c50ff152c'
    'd565034f79df31c0c36a0053ff1538d5650368419c0000596a0158c3'
)

EXTRAS_DATA = bytes.fromhex(
    '5553455233322e444c4c004469616c6f67426f78496e64697265637450617261'
    '6d410000d82f6500479c00004ccc6b005b9c000030f463005c9c0000'
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
    '558bec53817d0c000100007532817d107a000000752968e8e86300ff1504d565'
    '0368f3e8630050ff1508d5650385c074078bd8e87070040033c05b5dc210005b'
    '5de99519fdff000000000000000000000000000000000000000000'
)

DEBUGBOX_PROC = bytes.fromhex(
    '558bec5356578b450c3d100100007532ff7508e86070040031ff8d475250ff75'
    '08ff154cd565038d14bd94cb6503526a006a0c50ff152cd565034783ff0272da'
    'eb453d1101000075450fb74d108d51ae83fa01763283f902740d83f954740881'
    'f9419c000075308b5508e8eaeaeaea85c074146a00516811010000ff35585fae'
    '01ff156cd56503b801000000eb0233c05f5e5b5dc2100083f9517513833d9435'
    'ae010475086a1f8f059036ae01ebd8ebc2'
)
# DEBUGBOX BLOB END

# KBPAGE BLOB BEGIN
KBPAGE_CODE = bytes.fromhex(
    '833dac6bbf00017510a1401565034883f8017605e91d8ee5ffe9708ee5ff9090'
    '6a01ff35ac6bbf00e8aa8fe5ff83c408e92d8fe5ff'
)
# KBPAGE BLOB END

# BINDLIST BLOB BEGIN
BINDLIST_CODE = bytes.fromhex(
    '50a1ac6bbf006bc07083b83868bf000358c3909090909090e8e3ffffff740683'
    '7df810eb04837df8217c0883c404e9e0a4e9ffc390909090e8c3ffffff74088b'
    '04c543486200c38b04c538d46600c390e8abffffff74088a04c547486200c38a'
    '04c53cd46600c3'
)
# BINDLIST BLOB END

# BINDMAP BLOB BEGIN
BINDMAP_CODE = bytes.fromhex(
    '50a1ac6bbf006bc07083b83868bf000358c3909090909090e8e3ffffff740683'
    '7df410eb04837df4217c0883c404e9a2a7e9ffc390909090e8c3ffffff740839'
    '0cc547486200c3390cc53cd46600c3908b4424046bc87081c17068bf006bc018'
    '05884762006a185051e8be86feff83c40cc3'
)
# BINDMAP BLOB END

# BINDBLOCK BLOB BEGIN
BINDBLOCK_CODE = bytes.fromhex(
    '83b83868bf00037406054068bf00c3057068bf00c39090906bd07083ba3868bf'
    '00036bc01874060500d66600c30588476200c39083b93868bf000374088a8441'
    '4068bf00c38a84417068bf00c3909090240f3c0a720204270430880747c3'
)
# BINDBLOCK BLOB END

# INISAVE BLOB BEGIN
INISAVE_CODE = bytes.fromhex(
    '5356578b9d38ffffff6bf3708db67068bf008bbd34ffffff31c98a040e88c2c0'
    'e804e83dd6ffff88d0e836d6ffff4183f9187ce6c607006bc31105e04a6200ff'
    'b534ffffff50e8b0fbfaff83c4085f5e5be9954ee9ff'
)
# INISAVE BLOB END

# INILOAD BLOB BEGIN
INILOAD_CODE = bytes.fromhex(
    '5356578b5c24106bfb708dbf7068bf006bc31105e04a6200e8c3aaffff83ef30'
    '6bc3138d80024b6200e8b2aaffff833c9d401565030375138d77306bfb188dbf'
    '70146503b906000000f3a55f5e5bc3'
)
# INILOAD BLOB END

# BLOCKCUR BLOB BEGIN
BLOCKCUR_CODE = bytes.fromhex(
    '518b0dac6bbf006bc97083b93868bf00035974078d804068bf00c38d807068bf'
    '00c390909090909050518b45086bc8708b04854015650339813868bf00595875'
    '05e98687feffc3'
)
# BLOCKCUR BLOB END

# INIPARSE BLOB BEGIN
INIPARSE_CODE = bytes.fromhex(
    '50e85ffdfaff83c40485c0741e89c631c9e816000000c0e00488c2e80c000000'
    '08d088040f4183f9187ce6c30fb606462c303c0976022c27c30000006a015e8d'
    '04b594cb6503506bc60c05344c620050e8d2fcfaff83c4084e79e4c3'
)
# INIPARSE BLOB END

# PAGESEC BLOB BEGIN
PAGESEC_CODE = bytes.fromhex(
    'e86fbcffff750f837df81a7d01c383c404e9ea60e9ff83c404e92261e9ffe851'
    'bcffff750f837dec1a7d01c383c404e9d062e9ff83c404e9ed62e9ff'
)
# PAGESEC BLOB END

# PAGESEL BLOB BEGIN
PAGESEL_CODE = bytes.fromhex(
    'e80bbcffff750f837df41a7d01c383c404e96f64e9ff83c404e9ab64e9ffe8ed'
    'bbffff75048345f42483c404e9d464e9ff00000069c14701000089049d8ccb65'
    '03880c9d97cb650388c8d40a6605303086c46689049d94cb6503c3'
)
# PAGESEL BLOB END

# COMMITDEV BLOB BEGIN
COMMITDEV_CODE = bytes.fromhex(
    '89048d4015650383f801740583f80375275657516bf1708db64068bf0083f803'
    '750383c6306bf9188dbf70146503b906000000f3a5595f5ec3'
)
# COMMITDEV BLOB END

# INIALL BLOB BEGIN
INIALL_CODE = bytes.fromhex(
    '6a016a00e82faafcff6a016a01e826aafcff83c410536a015b6bc30c05344c62'
    '0050e85652f7ff5a6a285985c0741f668b00662d30303c09771480fc09770f86'
    'c4d50a3c5f77073c0572030fb6c8e8c155fcff4b79c35bc3'
)
# INIALL BLOB END

# DEVORDER BLOB BEGIN
DEVORDER_CODE = bytes.fromhex(
    '030001020405060701020300040506078b803868bf0083f80777070fb6803447'
    '6000c38b45f483f80777070fb6803c4760008b4decc3'
)
# DEVORDER BLOB END

# F11PAUSE BLOB BEGIN
F11PAUSE_CODE = bytes.fromhex(
    '6a00e89aa8f8ff83c4046a0068d84e5f00ff750868e7e7e7e76a00ff15a0d465'
    '0350ffd3e8bea8f8ffc30000be0ce963006a035f8b068b0031c983f8010f94c1'
    '51ff7604ff74240cff1544d5650383c6084f75e0c20400'
)
# F11PAUSE BLOB END

# MOVIE BLOB BEGIN
MOVIE_CODE = bytes.fromhex(
    '5589e583ec405356578b7d08a1345fae018947f4a1385fae018947f031f668b4'
    '876603ff15a0d4650385c0742b68be87660350ff1508d5650385c0741b89c368'
    'cf876603ff15a0d4650385c0740a68da87660350ffd389c685f675068b35d4d5'
    '65038d45f050ff7708ffd685c00f84e00000008b45f82b45f08945d885c00f8e'
    'cf0000008b45fc2b45f48945d485c00f8ebe0000000fbf47108945d085c00f8e'
    'af0000000fbf47148945cc85c00f8ea00000008b45d8f76dcc89c38b45d4f76d'
    'd039c37f118b45d88945c8f76dccf77dd08945c4eb0f8b45d48945c4f76dd0f7'
    '7dcc8945c88b45d82b45c8d1f88947f48b45d42b45c4d1f88947f08b45c88947'
    '108b45c48947146a01ff75c4ff75c8ff77f0ff77f4ff35c888ef01ff15e0d565'
    '0331c08945dc8945e08945e48b45c88945e88b45c48945ec8d45dc5068000005'
    '006842080000ff35f088ef01a148d66503ffd05f5e5bc9c364647261772e646c'
    '6c00444447657450726f6341646472657373007573657233322e646c6c004765'
    '74436c69656e745265637400'
)
# MOVIE BLOB END

# CREDITS BLOB BEGIN
CREDITS_CODE = bytes.fromhex(
    'a08104bf000a055704bf008a15483d6c00a2483d6c00803d6409ad0102753284'
    'c0742e84d27421803d493d6c00007428fe05493d6c00803d493d6c003c7219c6'
    '056409ad0103eb10c605493d6c0001eb07c605493d6c0000c7051c1cae010000'
    '0000c3'
)
# CREDITS BLOB END

# NAMEENTRY BLOB BEGIN
NAMEENTRY_CODE = bytes.fromhex(
    'a0c55eed010a05c65eed01240188c4a08104bf000a055704bf008a15483d6c00'
    'a2483d6c0084e4750884c0740784d27503b001c330c0c3'
)
# NAMEENTRY BLOB END

# CAMSKIP BLOB BEGIN
CAMSKIP_CODE = bytes.fromhex(
    '833d9435ae01047518833d9036ae010c7409833d9036ae011475068b5324c602'
    '80c3'
)
# CAMSKIP BLOB END

# OVERLAY BLOB BEGIN
OVERLAY_CODE = bytes.fromhex(
    'ff742404e8f6fffcff83c404833d9435ae0104756b833d9036ae01207562803d'
    '6409ad01027559803d493d6c00007450b840010000bab8010000f60598f56b00'
    '04740d833d60f56b00007404d1f8d1fa8b0d405fae01518b0d5c5fae01890d40'
    '5fae016a016800ff000052506861815f00e8c617fdff83c41459890d405fae01'
    'c3484f4c4420544f20534b495000'
)
# OVERLAY BLOB END

# TITLEVER BLOB BEGIN
TITLEVER_CODE = bytes.fromhex(
    'a19435ae0183f8017545a19036ae0183f806740a83f817740583f8117531baed'
    '3d620031c9803c0a00740341ebf7b84f00000029c829c86a3250e8ec9aeaff83'
    'c40868ed3d6200e807b1eaff83c404a1405fae01c30000000000000000000000'
    '00000000000000000000000000'
)
# TITLEVER BLOB END

# Where the blob lands, where the version goes inside it, and how much room
# it has. The blob carries zeros there: the version comes from the git tag,
# and the blobs are built from source, so the patcher writes the string in
# afterwards rather than the patch table carrying it.
TITLEVER_AT = 0x00223198
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
    return ('vo_patch %s' % VERSION)[:TITLEVER_LEN - 1]


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

    ('credits', 'Show the version, and credit the patch in the ending roll',
     'Two places, one box.\n'
     '\n'
     'Title screen\tThe version of the patcher in the bottom right, in\n'
     '\tthe game\'s own lettering.\n'
     'Ending roll\tTwo lines under the CYBER TROOPERS VIRTUAL-ON title\n'
     '\tat the top of the credits, in the roll\'s lettering.\n'
     'Files\tscrstfcg.bin and scrstfmp.bin beside the game are\n'
     '\trewritten and backed up. Restore original puts them back.\n'
     'If they are missing\tNothing is written, the version included.', [
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
         (0x001c5900, 'a1405fae01', 'e893d80500'),
         (TITLEVER_AT, '00' * len(TITLEVER_CODE),
          TITLEVER_CODE.hex())]),

    ('movie', 'Intro, loading and ending screens',
     'Four fixes to the screens either side of the fighting.\n'
     '\n'
     'Intro movie\tThe game sizes it for 640x480, so scaled up it sits\n'
     '\tsmall in a corner. Fitted to the window instead.\n'
     'Loading text\t"Now Loading . . ." is hidden. The load is over by the\n'
     '\ttime you read it.\n'
     'Ending credits\tSkippable - hold A, Select or Space for a second.\n'
     '\tStock has no way past them at all.\n'
     'Initials\tThe screen after the credits takes those buttons too,\n'
     '\tas well as either weapon trigger.\n'
     '2P\tA and Select are 1P\'s, so 2P skips nothing and enters\n'
     '\tinitials with RT.', [
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
         (0x0060c25c, '00' * len(MOVIE_CODE), MOVIE_CODE.hex()),
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
         (0x0018fc25, 'c7051c1cae0100000000', 'e8a6ce0a009090909090'),
         (0x0023cad0, '00' * len(CREDITS_CODE), CREDITS_CODE.hex()),
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
         (0x000d60c8, 'f605c55eed01010f850d000000f605c65eed01010f84f2010000',
                      'e8f770160084c07511e9fe010000909090909090909090909090'),
         (0x0023d1c4, '00' * len(NAMEENTRY_CODE), NAMEENTRY_CODE.hex()),
         # HOLD TO SKIP over the credits, drawn through GDI so it does
         # not scroll with the tilemap. The call five bytes before the
         # surface is flipped is made in the stub instead, which is
         # what puts the text on the frame about to be shown.
         (0x001c58e7, 'e8f31b0000', 'e8f41b0300'),
         (0x001f74e0, '00' * len(OVERLAY_CODE), OVERLAY_CODE.hex())]),


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
     'Music\tRead from music\\trackNN.wav beside the game. Rip them in\n'
     '\tthe CD MUSIC section below; with none there, the game\n'
     '\treads the drive.\n'
     'File size\tThe file grows by about 3 KB.', [
         (0x001c76d4, '0f840a000000', '909090909090')]),

    ('nocpucheck', 'Skip processor check',
     'The game will not start on a modern CPU without this. Same as\n'
     'ProcessorCheck=Off in v_on.ini, but with no ini needed, and it removes\n'
     'the MMX, Pentium and vendor checks too.', [
         (0x00107930, '830dc884bf0001', '90909090909090')]),
    ('framerate', 'Fix frame rate (60 FPS)',
     'Three fixes, all for the game not running at full speed.\n'
     '\n'
     'Timer resolution\tWithout it the game runs at about 70 per cent\n'
     '\tspeed on Windows 2000 and later. Not needed under Wine.\n'
     'Motion value\tMotion= in v_on.ini is a frame divisor: 1 draws every\n'
     '\tframe, 2 draws half. The game ignored it and wrote it\n'
     '\tback; it works now.\n'
     'Motion Type\tThe two speed choices on F5 set that divisor, and\n'
     '\tneither reached 60 fps. They read 30 FPS and 60 FPS now,\n'
     '\tand set those.', [
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
     'Removes the menu bar. F11 opens a dialog with the Debug options in its\n'
     'place: No shot, SE, CD, Kill 1P, Kill 2P, Scorekeeping, Credits\n'
     'and Quit Game. Motion is not among them; it has moved to F5.\n'
     '\n'
     'With the gamepad patch in, the dialog also takes each player\'s\n'
     'stick deadzone as a percent, saved to their own "1P Deadzone" and\n'
     '"2P Deadzone" v_on.ini lines when the dialog closes; Defaults\n'
     'puts both back to 40.\n'
     '\n'
     'Credits is not one of the game\'s own. It runs the ending from\n'
     'wherever you are in a match, and does nothing outside one.\n'
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
         # the pause-and-resume wrapper the hook runs the dialog through,
         # matching the built-in F-key dialogs; see asm/f11pause.asm
         (0x0023b324, '00' * len(F11PAUSE_CODE), F11PAUSE_CODE.hex()),
         (0x001f42d8, '00' * len(DEBUGBOX_PROC), DEBUGBOX_PROC.hex()),
         (0x0023dce8, '00' * len(EXTRAS_DATA), EXTRAS_DATA.hex())]),
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
     'own inputs. Their bind sets are separate and both are saved.\n'
     '\n'
     'A accepts, Select is the camera, Start pauses, and the D-pad works the\n'
     'menus. Either of the first two skips the win and lose screens between\n'
     'rounds. On-screen prompts that named a key now name the button.\n'
     '\n'
     'Stick deadzone\t40% per player, set from the F11 Extras dialog\n'
     '\tand kept in "1P Deadzone" and "2P Deadzone" v_on.ini lines\n'
     '\t(05 to 95).\n'
     '\n'
     'The keyboard page gets two fixes as well. 2P can use a key 1P has\n'
     'bound, as long as 1P is on a pad and not using it. And Default resets\n'
     'whichever side you are editing, instead of always 1P.\n'
     '\n'
     'v_on.ini and escrgame.bin are moved aside and rewritten. Restore\n'
     'original puts both back.', [
         # The bind page serves the gamepad and Keyboard (Simple) both, so
         # its list length and address go through asm/bindlist.asm and
         # asm/bindmap.asm, which pick them by device. The letter and
         # digit sections are Simple's alone; asm/pagesec.asm and
         # asm/pagesel.asm skip them for the gamepad.
         (0x000970bf, '837df8210f8d2e000000', 'e8385b16009090909090'),
         (0x000970d5, '8b04c538d46600', 'e8425b16009090'),
         (0x0009729c, '8a04c53cd46600', 'e8935916009090'),
         (0x000974ac, '837df4210f8d23000000', 'e86b5816009090909090'),
         (0x000974be, '390cc53cd46600', 'e8795816009090'),
         (0x001fcbe4, '00' * len(BINDLIST_CODE), BINDLIST_CODE.hex()),
         (0x001fcd04, '00' * len(BINDMAP_CODE), BINDMAP_CODE.hex()),
         # Which saved block that page reads and writes is picked the same
         # way: +0x08 for the gamepad, +0x38 - the hidden 2 Joysticks
         # profile's, inside the structure v_on.ini keeps - for Simple.
         # See asm/bindblock.asm; the player comes from the maths in
         # flight, not 0xbf6bac, because the Default copier also runs at
         # startup for both sides.
         (0x00095f35, '053868bf0083c008', 'e812871600909090'),
         (0x0009724c, '053868bf0083c008', 'e8135a1600909090'),
         (0x0009736d, '053868bf0083c008', 'e8da721600909090'),
         # the Default button's shipped set comes from the same pick:
         # the gamepad's table or SIMPLEDEF, by the pending device
         (0x00097355, '8d04408d04c500d66600', 'e80a7316009090909090'),
         (0x00097397, '053868bf0083c008', 'e8b0721600909090'),
         (0x00097531, '053868bf0083c008', 'e816711600909090'),
         (0x0009740f, '8a84414068bf00', 'e86c7216009090'),
         (0x001fe64c, '00' * len(BINDBLOCK_CODE), BINDBLOCK_CODE.hex()),
         (0x00201038, '00' * len(INISAVE_CODE), INISAVE_CODE.hex()),
         (0x0020642c, '00' * len(INILOAD_CODE), INILOAD_CODE.hex()),
         (0x001fcc64, '00' * len(BLOCKCUR_CODE), BLOCKCUR_CODE.hex()),
         (0x00200f0c, '00' * len(INIPARSE_CODE), INIPARSE_CODE.hex()),
         (0x00200f70, '00' * len(PAGESEC_CODE), PAGESEC_CODE.hex()),
         (0x00200fd4, '00' * len(PAGESEL_CODE), PAGESEL_CODE.hex()),
         (0x001fcb4c, '00' * len(COMMITDEV_CODE), COMMITDEV_CODE.hex()),
         (0x0023b9f4, '00' * len(INIALL_CODE), INIALL_CODE.hex()),
         (0x00203b34, '00' * len(DEVORDER_CODE), DEVORDER_CODE.hex()),
         (0x00223ee0, '00' * len(PAD_INIKEYS), PAD_INIKEYS.hex()),
         # window title, shared by both pages now
         (0x0026c88c, '4b6579626f617264206f6e6c79202853696d706c652074797065202d2025645020736964652900',
                      '42696e64696e6773202d20256450207369646500000000000000000000000000000000000000'
                      '00'),
         # the gamepad's default binds, 1P and 2P, twelve slots of stride
         # 2. Keyboard (Simple)'s shipped set moves to PAD_SIMPLEDEF.
         (0x0026c400, '11001f001e002000100012002e0022002d0013002f002100', 'e800e900ea00eb00ee00ef00e600e500e400e000e200e700'),
         (0x0026c418, 'c700cf00d300d100d200c900520051004f004c0053005000', 'e800e900ea00eb00ee00ef00e600e500e400e000e200e700'),
         (0x00223b88, '00' * len(PAD_SIMPLEDEF), PAD_SIMPLEDEF.hex()),
         # each player's profile 1 dispatches to the routine
         (0x000422a8, '502e4400', '60806000'),
         (0x001bc13b, 'e3cc5b00', '72806000'),
         # PeekMessage: Start and A must reach the game while it is paused,
         # where the input tick does not run
         (0x001c530e, 'ff1590d56503', 'e88521040090'),
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
         (0x000422ac, '5a2e4400', 'c4496200'),
         (0x001bc13f, 'edcc5b00', 'd6496200'),
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
         (0x00096253, '236b4900', '381c6000'),
         (0x0009625b, '2b6e4900', '381c6000'),
         # And the startup call that refilled +0x38 with legacy joystick
         # defaults every launch parses that line back instead. See
         # asm/iniload.asm.
         (0x00095604, 'e8742c0000', 'e8230e1700'),
         # The ini loader routes each player by saved device, and slot 3's
         # route was "load nothing" - right for 2 Joysticks, whose data was
         # re-derived, wrong for Simple. Route it through the section that
         # runs asm/iniload.asm instead.
         (0x000958a1, '03', '02'),
         # And whatever the saved devices, both keyboard-page blocks load
         # their lines at the loop's exit; see asm/iniall.asm.
         (0x000958aa, 'e900000000', 'e845611a00'),
         # A last common check demanded the DirectInput joystick subsystem
         # whenever either player's saved device was 3 or 7, or it forced
         # both to Gamepad and skipped the whole ini load. Simple needs no
         # joystick; the check is 7-only now.
         (0x0009522e, '03', '7f'),
         (0x00095248, '03', '7f'),
         # The list shows gamepad, twin-stick, Simple, Real; positions and
         # device numbers are mapped both ways by asm/devorder.asm.
         (0x0009651a, '8b803868bf00', 'e825d6160090'),
         (0x00096784, '8b45f48b4dec', 'e8ced3160090'),
         # The device page's plain OK only commits the device number; the
         # shared live table must be reseeded for the new device too. See
         # asm/commitdev.asm.
         (0x000959f7, '89048d40156503', 'e8507116009090'),
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
         (0x0009703f, '837df81a0f8d27000000', 'e82c9f16009090909090'),
         (0x00097257, '837dec1a0f8d13000000', 'e8329d16009090909090'),
         (0x00097428, '837df41a0f8d27000000', 'e8a79b16009090909090'),
         (0x000974cb, '8345f424e905000000', 'e8229b160090909090'),
         (0x0009753a, 'e8f1de1400', 'e84d571600'),
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
         # The win and lose screens read the camera key, not the accept
         # key, which is why Select skips them and A does not. The tick
         # calls this to write the camera slot for A as well, on those
         # screens only. See asm/camskip.asm.
         (0x0023d1a0, '00' * len(CAMSKIP_CODE), CAMSKIP_CODE.hex()),
         # the routine itself: entry stubs, pump stub, tick, blocks
         (0x00207460, '00' * len(PADX_CODE), PADX_CODE.hex()),
         # the tick ORs every active input together, but the game's
         # gestures are exclusive lever positions, so a held direction
         # contaminates jump and guard. Strip it back off at the end,
         # and only when a pad was actually read, so the keyboard path
         # is left exactly as it was.
         (0x00207702, '5f5e5bc9c3', 'e997000000'),
         (0x0020779e, '00' * len(LEVERS_CODE), LEVERS_CODE.hex()),
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
MUSIC_NEEDS_EXE = 'Pick v_on.exe first: the tracks go beside it.'


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
NETPLAY_SRC_SHA = '588fa211349ffe48518b39648b3eee2f6ff6aba76a25b088d12f1c48de2d547d'
# sha256 of the compiled DLL, so the patcher can tell its own build
# from an older one already installed.
NETPLAY_DLL_SHA = 'c768ade6b02103643b960c5224bc40d12e2b65f7795724ea7489252cde072fcb'
NETPLAY_DLL_Z = (
    'eNrkvX98U9X9P56bppBCyo3SaicwqgtKBzricDOunZUSRKVahaKbVN3e2uneuDFIFCctrTeR'
    '3l0CddON7aOTvtG92cY23+/xhoKISVv7Q5mWgthSwIJVE4JSwLWlLc339Xyde5O04LbP5+1/'
    'Xx7a3Jxz7jmv8zqv3+d1Tgq/W21KMZlMFvo/FjOZak3iX77pn/+roP8nTN05wbQ17W+X10oL'
    '/nb5oocfWZG9bPmPf7D8e49m/9v3fvSjH3uyv/9Q9nLvj7If+VH23DsWZj/64wcfuiY9fZxD'
    '76PIbTItkMaN6LfLNOEr4yVzjunP9GWA/p8lmV6ZSJ92+j+IFouW8rNZwC3p8AugRGHnRRLN'
    'K5+qssV7+GMXTfhjltnUzxM1m2aO/weTXGY2PZz2+dXPv2cyZV2gfPIcs+kl6fPfu8bz0EoP'
    'fX62RQfoz8mTEP8eoP+uefB7nu9hxiZ97jQ903+PbEdrFbxmuWiYJVNBq97n1vPa5V/z0MP3'
    'l9LqPDCZEW0yfZn+b7hAu++vWMHr8zVJx/AF1z94zUNi3H4dpwzfsQv094hox7gmnJts9Nl9'
    'gXkU3MVTncmD6/31XKCdZynDZ2VE6e1OXwgvDy398b+ZxBrSWppAap+d126O6f+n/4rVD5UT'
    'WU0W4sfipeHhl02mlvprAluvRF3AcyXq7168UDkxWXPb/EHvshobtXO2+YPlxUpY0vIenLN4'
    'qbN3E0qjcuAxE/UUJnkSe085YdkJslF7euT/ypw7bfFStZ46eL9mPj0q/WbZ10K11HwqfYSL'
    '/2AycfPAQ5+prffed39JnY3HV/ol70cP1oDFtOswmLbS4uyMTjZgdDXJvs2iIx73AI2rttOQ'
    'eRiSBjzi7NSHlGTfp9SSv/mDsu8Z4wtX/ZG++Ts9P67Z8N4iqvZcphXbm+Y6LKArdZHDqhVb'
    '1QxHrFU5YVNSMV2J4Y3cFovFxLwZg7sIg02p6BXQrZkCKCJXoI2A91V+6Yco2JZoEVWN+Yh3'
    '0QiD24sWL6WerfQRnvN7enEBvXgN8IJai6g1odbxe+NdABy5Nt5OR/BnhKTIBC7VF1d8YH3V'
    'dxarjQvVfcqJmWqmPUjTb/M+zgtNeLg9/KV5hIFVDiuGxCvqbEd4GjEMYQLF6hKHBUX/fYco'
    'cncnl+46F4tpxd1UQWthKa12tjWlYogYMGCsdZziSqvDfySFECiIJfVBy/P4/PAK6oh78exK'
    '7j9XjErFbmVQ8jxsAGrWAV11k4CqLrqG8RH+d6NAGUyR/T8mMCLfSEILdSL776aC0TM+M8Tj'
    'R74l1jv89TkJrEh6G9nom8jLn4FOBDKS+4nmxzGXXOy5/YLFZUMG+iINw7FYaTXzq7UpdYlO'
    'YVqhXSu2aW5r+FqqbyGuiS/wtgR/U71OoCp1Hr75P8Dm1fgX52/Ut9K6ZM4kJiMmIoyuUOvj'
    'pUvAenm5om6TnXoCmBOpTp3sALdb1UaU5CSVbLKAlno9F6EiA69YHIe6qLcD9LAxY9HS8H2C'
    'CpgGBLxsf+zGn2hQlz8Efw33FPSkGT201tgFedpUiyOW6RBw0ZyUE7nyNnDHlYvvuMtZNdfx'
    '1dpUcHabZ6JmpsnPkLfNsVDx12qhCKjAGqC2wErNUjGpDLtgPvuQYzG+23gtMMrWAoySpTTk'
    'xgm2upiwp9YrJxbQqOoCR5aPBMtRYP82ydmrvpub7bkm92rPVwMrXwzc06UOVAxcJs+pD9wn'
    'Vc2dnS1vk3xBzzQlJCmDUz1T1CO52d42LpR9z4IWQ1J0tz6Qerxx7uRskzNGsmiyNvNcBUkp'
    'dSYBVUVTVxsD+ak0f1e951KlSVJiKd5DzqDSsEBnLsKjutRhCSx1ZKse4p2ZjvASklPEkXs1'
    'dGc556PuAguuM6lmKsjSpMY5qfQsb7vJUjXHUgv6jr4i+jmunon8hJbN2clSzJg/EcfsAsaX'
    '5XiKwBdIgHiXcDtZfjaovoG606gDeaiReO1M1DbSH/k5Wgp5W5DKCDf07GvxWAkZ/ruJm5VV'
    'jsnEmkvMvDhscqHDfqNDZ5voUH627vLTaoTQNP3yU1SjXTeT4JK3RaoWOK7CG72sJrQ8B6+m'
    'FYgxoEX1Z3qHBy5Q9/d/UNf/D+rOJtURfqdTHcFnidcPjKzPTq6Xty11XIlGff9ggMg/qPvE'
    'qNOufkBMWSM6xRALaIh39VYnk0G4QH3PP6k/9Q/q5W066s+JRr4W2XfCJJQ1v5yqc5jemXUu'
    'CRGUf6p3mllEgBNBk+oFFRhV8nNBqlXfuOC0w5+DEqVhqRA3ur4lWsLwkb+cY7keWOXIB9Nn'
    'mxLi3egqcxaBofYrJ+yBRY7ZgQWOG8LnBknI10EOEf2tcsw2M/kZBBrxnDP0fHyuhjR5dydm'
    'WXsr/QFUqxwzE/OaW4d5ESK+2JkzB/kGSU9QtdFSNIN4W8TUYYkjiOFeCO2RORfCts3z/Zp8'
    'lrreNKUh6956ww55CMtlcXyXPsL7N9LgZOh5DpNcdejT0IihWfH89maqJc1NrSF+w3/eyKCG'
    'f0mf3GU1+rsn0V9TKoaG/An/dCMkluxbCb1qcTSl5hs191BN5HtCL0OXUV3rFcKIimV6aFYz'
    'Uh2wPxvl7anLoQMx282QCtvbazxUAJsRZRupbMcPoMHqarKmMTZIXtr9wdonULjEYZPdIRiD'
    'BJ0PJZjTQcKn0uC49z7Gh5CGGTUVxjjAqnbdEoFbeo+hOlDjE8MSosZrdjS8azp9Fa8BQbre'
    'ATe8hJHQVS53YmdNrpu6SkMGDRxX+Y+ZII73VZ6AJ6ZlYg7adUt58WRfJcyFTBswsYTU3irH'
    'lUr9OEyBZkRqMdM+jeW3UJyLHF8jIp9FJm8j6dC7wrNfZGAurBypO+IV2GNxJSkQv6mb1qGy'
    'AdAQTdXDTk7qY5lQuIk+5o7sI7qztJrpL/0B0ZL9cFLeVuOt3KzHr3mVsCHJm+pXQGba/C2e'
    'CTvhFBL5WcOf8toQXcX1NdV/iio23sNvn1d/t5a3mQZbrA4s1KZUYZGO3diYGswmcqrKDNGH'
    'Mmgun7wT5iXrQ5Jt9mGlTqr62etU6Tro/VT5+MbdaB/6OK2y6RX0MGhZPlbtCEXMZ152Bnvr'
    'zZ7x8vYxperYWvSifCB5xsrbMsZUZYytwdBKUFK6btyEx5oHhJFzmZYJUO6tRb/33R+5dDju'
    'TwCtyf4a6H+2vI0hJuxkw5YIMqbkNbCtYg5/O9FTfL65TxFHERzymp1oVSH7DhJmcitl/8+Y'
    'XPbQAioDF3l+UjkQowJ5vULV2pRa6r4WoZWcdlervO4JKqx5hcqqql8xLcYUpsXI7diEIn9b'
    '+QxnrzGgvG0dQMMkG6Wqqr9Qa27qOu3t5uaBuROlTegeBsxscBXLIQMSS/lTlXnoAVQq+0uJ'
    '66PvEl3lKt5DRhsyE25H+WtULm9Lx6oJBCpHbyRorpa3tTiDoaNp0Uml1fK2avRW9az0SgXj'
    '3pWKluVWVyo6W/0+Gz9s8fB6K3koHstGpbzuHrgPxUnuA/k3tWNAWXNJ8tdC2I/wLkqrm1If'
    '1MVWZBJVVOv9yevS6VtNA1DTdWOoK62iFqQjBarwIfvG0htMLpGPh1nOMfJnpAJRzAzZwpiF'
    '2UDyYQu0SuouUG34hgGC8S/DOvzxFZX9X6WyGiDnW6nVGMQ/jQq09DdQkv4sl0xEyZQWlEzZ'
    'wCUSSq77G0que55LTsIxywOlmkhUTK2E9Uaq30EcPZ2k50zZB8+EeG5q+H7SlhHHcDI+ktbs'
    'N9RRU2pYl9yMxsjZczrctQhSRo7TV50pyIQAKLyuanPYgq4PDp/n31bcYPKOT/IvWT6SEGWB'
    'ml+guzpeMgvsYKVq1vNVwp0IrJQg1gifZlYl+XOExdhILDmPO5gf95VujjulFkd47G/Ye4tl'
    'LhD1scxsWA4eR1wBZC4QppgYNjYtg74K+xqvzRWvRY+Wsn9ReWKtsHrnSoYrUm1A7kkrVVZa'
    'zWq9J9MZrGxYKyRtwqOGArlIt5XNIk4iJlPLPdjUN5T+mKeBlmcumpSXWiqYjuBavERNar9l'
    'YseUKtnfJKXgoWeP3vdK/XMVPmn5H4WJsuzyi1+ao49fZVaCY0k+5q73fsZS0xkkEEpvo3mc'
    'LCVqXUDtb9c7uUv/XMxLRWB8N5a5gdWnR0wXZbdrBTbNpIHai8iR1NUJfZtFc1hAn3b6ZM1c'
    's7UgoaOAhww0nAmLJBdxnCzgm8ziBY5ZOc15nakQgtPMrDnmB7Y4KsRb8/XuM6h4CbpP7hFR'
    'RzaxcgldW4TWIv3fP1Fedy+tVcUu7kXy/pqeKvlpvdJ/afnP5O0+xy8hQbY/64CnVxoKWktD'
    'dVZ5e1De3haYn+V6S17LFs1axwZhic4mnpq5A1PQyFYkWrpePRK9s1oJpihdQ4EFkiXwnRTX'
    'EXndkxKE7LO2Cha2Plslfz4/Gd9Dx6xpdSh8Cl+6rGntge9YAqummKR6enUtouXKQLb8LPlk'
    '4Mhn68ctccz0PK/0jy3/VcVqSfI+o91s0ZYSLE1qI+nhWfK2PaGT9tCnWepgFTVVz9Dr3kuU'
    'nsuVUxcrZ+agCdnLPS9Vwcvbxn9a5G2doLTcvWxRZQClqJpF87OB9Ig8ZpFxEctcCp4BHa4F'
    'HUpMh5ZI1jkj3pbBdPygzmK8jjv3FX7fntOshvqOhY6nPtL2Gb2Vs1c5S8pvohBCC5IoJk4p'
    'ZJ/mnkcouRzsyxpJIE+/j+HBp7k6YIDpqaG4PqbFma3R4kQ2CTgF/+6C0vQ59uPjeUcrfeTZ'
    '6Y/nlryLsFz+GqKj0spdjnb61p8C6sCTvEal8ryPYfA//Qv6ezUMdqXfLq9BJJtUebiN3Hah'
    'H/OKBLVbT1wh3HDDDXivZulX2AzUGR4hD5/jFV0m/GavMC4PJJWxNwkhgLcbU5fR6xABgaWO'
    'WaiuEGwUy3xWyDDI4ikET0Uet5T9YYI9qbtafYjMCiE/k6o+SR5JSBlr+17h/Bkep1HeoZcb'
    'nup7TanLHCIIJlpkfCJa2HXOta0SNmq8x4G97FdIbQhoXc/AZKFebxd309/TrpvNtejVpvdq'
    'NSIOqW2JXsgEt29aqZvCoxvaRjVU8tDSvGky2dmPjWtKXalDj3cRF9Gus+tLeCZF9HA67rpn'
    'onEt4g/oaEDyyGQeDZC3GQxY/J8JLDKW+vVXk8IYLLGOi3JrVK8/btSr7dQifN2npHuNSCZU'
    '4Ew9OpLkGEK/gJA5Om/X44uJxbRi9dHHfxokFbk4YacywehaS30DJPM3YhDWXbK/DkHifBLr'
    'KP8f+rLDIoTedFVwi/oOLInoRLIX3DbNcr3SZEHT90H4prjrhq7herJ8eUFAEf7jz+E2eppo'
    'JQAgf4db2hiuoSepR6dajxyYn1J59nU268YCz0EpD99WB3SHld8upHciK5Pjxdg7+BUEZz9N'
    'pGNYoNFkoHG6QGN0utFeE6wdCtvzsO/qlQ38tIf/6wTZg8Lc/MWwETfQxQWJlekkVmZGfjUk'
    '5E8NaJ/mlV6RB2lJXIcV3QkgqasxfWT+bk1qJ/tXCBPCejGRY1xYJJhMu1qsuC2Zxd7VrtOj'
    'ZMmRCw4YYHIPQy5ntyGAYQkvpCHJkLqy9loqCLvFN7CXxS34QAh1XQYAklmOxRdk9IVtoxh9'
    'NFQ6PIYxdEAIAqEgrJGdI+3LkWHJEbHq00klHDkMGkE+BCEnOzZ5APJ7ah3VC/1DjDdpRNQx'
    '/ia9IQW1PESI4gJBl0PnyxdEkLYYfGPD7ioZESMCeWiCMO2j68mLgvxWwlLecAw6YQc1b0yF'
    'lDcpw0R8T+ndGKOdJ/+pq4o8bi/7TsJR8+gk8bJJdyrY8U4i18hj8bgVFe+cmQ1z7Jegrcan'
    'l4Nbwg+th500xhx5V6dH7Wru9RNvmYgWbzHo/TpDL42W6wc+R64fGCHXI1EaYAeCFjtZBGIe'
    '4TVhUJfs24soRx4PHJN9TnhQaMS7i/OYJ6WdT1D3tZoumtkomlsXWEXmRi4MQTLNfXFRb7lB'
    '6P3wj9eJUNNsoJpFASTJ0QDpOHrTpL7hmRDIj4n1eHqqpC9jUrgGnQXbxE6IEbaJXD7EwuG8'
    'hveMaphMBxeSw5H2QSFlRjDWqjlxZdxL9TtRGjlFT7zcO+fv5UGM8f3B2vt1nAAM2V0nkJEE'
    'AuiHRV1F3iqWMD4n9PoqNgKsOrVZUMW2kI7GyFZiP8bSa2k7CT2+oxCJqUOGrks1TTOeLPEn'
    'q/7EHT2vhyH7aUlfpEL2GjbhtVI1E/2wKmG/IB1TK9007BDOUIvnwRqd94Rdk6sHUWH6aFev'
    'nDNSTxtsmWwWvKurS7Qxwr/JCh/14Dp/Ay1m5CJiE4Y57o7JvlOGLtNMkZtiF17xDRdYcQiH'
    'SBohyxmMB/2ETrf89ALNeXUOnjvP311Yi51V5cT8eNBsJqxdf4s3VWmYf289mbwz4zE34dXM'
    'gils7H5Zxwk9YUFUhKku7tbQ7Na9YDLiiv4271dqHhTxqaUcRXXNdcz2fI9GcQYTAbUaLBxq'
    'vB/J21OxUqX07VpvuzJoKd+XCOV4myryEP2Y692tRFMBAOIQiAoS6tEovPNnvEH/ojoFEQM9'
    'vtiF4EHDfJY59yK+SBoGbkNd3tevIup7Nlg5MI8f6qhVWuu9JKZJkibFA3T/Wmjml+hDWOS+'
    'Q6Tu8iby4y3019nGBrvs/0qyAH2cXIKah2nqOx/eh/3JWtc+g5es1N1G9DrFULUn9G2xfcQl'
    'AnpDKm46RLPYhBA1G/ibshG2Fa83pe6/wuCS1vhT+xVxy9EyaHQrNrqoc+PVpMoD2pQHzlfk'
    '+7BXk2g8lJLQ75/fLE7M1+qvxWMS9wjq5MhqZQNQqmdy2E0JfNdg4oQfcq6EyiC6+kT2XS0h'
    'moWJs7vz2HwtD3iFWP9aqr4rvBNaYEcVobgiD48kyVtrf8YYD4UtxxCX4g6noBt/r+xrR/xH'
    'NK3JoL+y/7cs+mT/b+hzxhRgvHJQBn08/bqRcXEVx0ja92HvAXh+hZ/261Gp8ImPdPsLqzGe'
    'y2VfGrFcEiLjGLkecicTLbGFY/W3Yd8G/qZoy20MlfuGiOsRN31TS2cGa/FeprbPSJ0OpLwo'
    'Q/Vfb4nzOWpANnln2FEMQBNOAauSl3Z9wOPINfhP5+vr2eddSv62Z2QIYzS/7wOvN4HXvxTT'
    'lQfBJBt8nC/UAG+PkIG3kcmfHu5XYhd5f17B/FzpVWsQUXSlIroo+6FEap7lgje44Aco2MAF'
    'LVywGAXPc8HfuMBNBeFZhGthCj9mou5l/9dYG2GICtmPpJoR4cuLsAqZHL7MFOHLGAc0OXyZ'
    'LsKXn6Akj8OXeSJ82YmSJY5cdYHjHiEzG+c67qhUp2B3giryEWhSp3jEt7nqIscidcpK8W0+'
    'qYgl6pRVQEGL7EMq0M6LxN7DHeHlgP56I26cJPy+Lftfw75DzCKvK6GH5Hh2IX2Xt6VCEubO'
    'lf1+bHyfTIjDVQlx+Ed/fBVk324syTKR3pHLwpge8mtWioe5NavEw/xIoREHRrQBeVNDKbLv'
    'ShZwpYooITfm20OfT88jZYZpNBkPD+r2YObDYtvJnwfYqnW8IrhYxLRAD4uYBujhHl57elhi'
    '7HhMSODxqx+Skp0CWo9cdi7uT+r4HLGVZ9K37Ax/UEuHNbDza/vYQ1HOSh67cta8cyZ933Et'
    '5MbcvRx8WOKw6XHzZ6g0nPZBnO5l35SBWKx2PhVHrhoAebERyj0OoLsBcy2627mWu6sLLHE8'
    'AObiTTaxT2V5d7+uvdkK0Tn/YPjfIkQerQOGnU2D79gPSZOlp3+FnyKJFrlqOAmW3mE9+g1M'
    'KijpphKRNgdMmbrpjb0owvi1WzCZE4eo7AMahgYY2DdigMuPJa+yNWP/ohF6ireeYdE9Q0ua'
    'e7vsuxMbQ0Wy7/v4vE321wyCiNPkdRsGwVSGvP7ZIJhUCGh/GQxQkG5j6kUo2YRyInDw5H9g'
    'w1E32MmPsZvZiWZUMlKFoZ7Ap7DmAOLs/YuWjsSnbqpB3wnnlje9tfSRKhEh1GS1uPeuu7TM'
    'B3T9nCoS/ZCcEWu7805Df+uvDCW9Qo2SsxYSjYFGWALJaDS8V0yB1uAmAj38n120KN8fQD6X'
    'Lj9pKRFAT2W0RW4eAGbHyeu+TQ+suqC0nPRlJ5jcGRTxkffO/JFxW0oeExWbTZvA+cQZwC5C'
    'yvL2IK8kjOWm1Ax94Us3YRP8bo2VsJI3mddlOutHEZMeuUl+l7y9X4gUg6/uZH9RyKJHMJ/3'
    '3h9BSa59n0NJPz4rHBcdF8/vN5Ssb/8IJbuG+ovMocbMaZHec0y+t1OjqzeSImRgw99HozuS'
    'QjEs14Q9dyKNIcmVRAQFSb/UgfuAkd9Gq67vv7eJfRO8wLaKRY9rYR57HJJJRAp0DsVL4RvI'
    '9dFSNmGnXivI3uTjT2ugyEyGpcrbUY8snAw7rikVjruVvCA1cy0V550jcB5/Riuwa3Y1ExJR'
    'y7c0pUIGLnTffNMmSEXi46KmVEjD7KICzpnAhg2CvZm+eLTVfyvyrz7ZiX2SJp+jS0JmeHNF'
    'YK2jV+KSbj1f+/VO4NXnQED2UtN0VJ2gx+mmCRI99tCjTYJrv8WBQLnYf2pGAlnIXKreOSxv'
    'u9TX5v2kSUKatRIcs7MQfX66P7F9XFqthtA+SO2LjPbOWOBOi3I0pVS9PrCLofNcGt8fVerM'
    'zmCpOme4ty7Fe7qW3UjzWJOpKHDrJa8NAdw5AttzrFbTGPqwDZuW0Ycdbg29l7GzgJc6Pn6z'
    '8kYC3mITAXCCQG1iDGxO8WQYcbqg2flJqfrw8OYU75kmU5oppWgHBg8UXfLa2Hdp2HwxbL71'
    'YlMGfdh+abLRh/10Coadn7GjUORXqK0Yr47Gu5XGKzT5WsR45n82nikx3uWjxluUNN4ZY7y5'
    '/6vxSI0X7XhKH2/GiPEmm67EeA6CicbrNca76385vyR8fn3EeHNMDox3jZjfZ8Z47i8OnzeM'
    'GM8jxrtWjPd3Y7ybvrjx5owY7w8j5tdvjFfwvxjP2RIougyLWFmUjMfZGGeXGOcsjdOUn1HJ'
    'O6Xzs4ibPBdzfsJI/uLgIMZi/lrwbjJ/ZZvmgr+OgBDm2AdSdP5yfxH8NYL+ykbRXynmcYWg'
    'vz4DX0VfPL4exDjXC3xF/jV8jZRHyrsj5VH5heRR4Rcuj9RR8mEt5rFSzOO4ga/5XwS+TEn4'
    'yjb9FuMcFOvyicDXU/9X9LVhBL6+jA7n2LpMk4GvcwZ9FX0x9CUl8MUR5KR5bME8vmtKxzxO'
    'Gvha+EXjq8L0F4xTJPDV86/hq0LHl8T4GnMgGV8HAPgcEszpwNeplC9W3yXJr/EHRtJXQxJ9'
    'RQ183fpF4Ms8gr7aMc5sga9P/yV8OT8JzLkMYxFjx/F0j+kA8PQfAHiOPYyO5uiM/cMsAuki'
    '7ofgaaGe9flLxF87ef7zL3nt0hHz/6WpV8A1AXBJJsy/KGPnHfr8/1/wzZgWREr4ZnReNmLM'
    't0eMaaYxa3V9oYSkUtVMWOBzAN4ezggV2MbWth62tdZk6bktD0zjQDXyJql8OlIWMuBOvMSZ'
    'ApKR/qPvbZk5GSSoJwFtgu8hMoE2LWV72MZR94dTOM8nN54QtMghNiBe4mTkptQH9BQ2eoFT'
    'cngTqZRDDg+LcNMcHeB5+uetYtPBM5a8C1jF1J12NdwSRNQ5r2cOmb75yecwrHYjtlAVz1yY'
    'Ceu3AxEr9nhMxgZW6aZlwvNB302pHr2uKXVl/GmV/sS518IFQsIt4B2fCg9b9j1gGWFPE/EJ'
    'i9owtA2zWreoLckW9SGTblHfbkpY1K/B/tb30S5ARyMN6x0wrKvVN3AcxLCnL2J7uiVQbFE+'
    'JHva6pkA/iA6+6HOHy06f5iS+MPGjGFrIK+H+OOE4A/BaLeO4I9/LN9spi8nyYXB/718q2JF'
    'QIqf5dvQFyjf/im/LTJ943/Hb+QsT+eoqTPObjqreUtq4LcSbWfUwJXd6TiArQB5bl24Z28i'
    'W9H/R7hmnPzJJwPWnBJxsljmKqLuyMpTRp5jEb0e3kZvRhs4vz6eL6rlIXSOnXrQLGdW8/YH'
    'wuctYg9yOrp2foLDMGpj7SVwpXe1xWLhjz/k/dIqG+9n7ED5/n2xmNJv8X4Qey+nB118XexC'
    'Wwj9mCj2JfRsrGcJIok6V4j/pHD0HXrxXJr3N0hH8PfK/lqe2EYdSo5GOYM6zmYiJVv3Yjdj'
    'Yn96B0E0NOYM2vAUAq8GX7X0h/lg6aaXOJiqFdjk5+pUu0iHWNITj/sBXD0jahpMMHxFBP/x'
    'rzvbxIzf3ItitTGSKurDovmaQeNtEfB/HrLjHGC6pTUpqfQ7w3p8f6kR8dCTvMRCCdnHK/g1'
    'Po8HXF3y3qKlr0k6pSSFC4C48Ktvx2KRHw4nxflO8PZV8jbn7yCMIbQiiJcBePWN8LXdYtFw'
    'GJGWBwHxqW1YtBTvB5wovPPLor9DNIyrVb691R9kdMl38GbuJqA1nIvhf4NtxbbasUDPH1t5'
    'hDciPQh807I62/qwksFA0VT5ltbQQKpSJ/nbVl+3I76cajq6ikwf5jCMAD+RgDrF2O28ARuP'
    'A/by6TvANjvxJ9pQ6wESPno7niMs+3qxdfo8TqHsjb5kBHxvxhHjdN5PbPHe+k+xn+AkkZjR'
    'MBmRG/APvtJzPN8mEwnF/jbvFkIApFf4D0SC/ORsC//2nfjSe7ZEVZIH+jfvWD3vnMD6HGB4'
    'pAtBRMAkzhOQ3uIutUWODKTrBG6NOYOVZ2P08mPjlQZbnVXEXWgl7OEbCU36fFDB8vXEZGdQ'
    'hKugJdNwlKZxrmM8km2tWdgh9rcln3gY+lu8C6CkGucBMqJfisPjuTLcAQ7uHyP7kFkafuso'
    'yOyJj5WGDJIarffeh4EfM4m1UOv9LbIf+2haOlLN/S3l79VsxQ7NNk70p7HLXHvLn4gn/KNO'
    'PxdRgQYkJBxVli/zO0q9NKKd6+DqDrW1r1XiLHYaXx9d4GMKL0Sn7N85crkdxGzhX+4hEkIU'
    'TF0Qz3lfhIrCt5BtgP7EmmyNP72iP0UmjsjHF7nFsQMzUiFQ886Cf9ZUM+E8qJ+bkSBHqAGE'
    'bd728RAenw7H6cZbkAQcy5OzBEPkT/+yHMnGPtY/axX9jcG/UUz9/vj5cRHvXIAUMY7Aa1sd'
    'D9IXf8xj13yOByDUt4qPxjhKfaA+LV1I2vIpIu7XE5aIdGpQqIQlIYaDq8Nqj/MTZyz84R4k'
    'Jqiic5KtVUa8VU8BkP0TkTm9hI1GuzRqq9iUvFuErZC5PEswxFXKKkf+xbLvb0bCy4N6Ih+e'
    'J4tnsZ+0BShqjZ+OqCB9I/vKaNSw9X0iBKQnVF3Je/TTgYBPZP8DWDjeaVfrDS6/0zAvc3Ug'
    'Z+sqPp/YZ7aeFIxJXB9OOX8SuipjRZT5iphEHAMq4132neXtWbUjpyfnrNjNniKgF7lWC0gr'
    'HlGO3Ri+nOgE2wvfSkd0WH4K+63GGnnuNISh7FssJcSY7J/Pm3VClL3/ZkKU7XszydhAloKu'
    'b0YR55IWQZfOzkg95EH4rbd0aXCUE6ZSZf8zw/pJFGP2rxnXESA1gr2P+Om2+XpuU4mxr+hs'
    'Ud9Xj0SmG/ueWjrjn6YZ2yt4DFPW+ew/sUGto8/3+jl6Z+8INtuSdNzD/33p/Nmsa47FKhtA'
    '/Wo7h++txv64cfAvqbcH+OgKd9cr+z4Y/hfYLvJafF46+y1tEYsWmSvOGyp5iPibdbYrf0h7'
    'nrmNk5aEHFxrB61WLZg4rKXzIaQ6ybV39cc6Qe9TPrgx9EGactZcnsP0ET8n9WwKjv5ULRoz'
    'HD+VtDoi3grM6aJeiBUqj+HOHmIIlcsbKzCUiT4Av0AMXzgRx47QJKQO80aL1IImMjn3VP9z'
    'lKz8VxAX3ahGI9/HLjep9UUOB/ZZ4mrJlpwPokvRSy+g0W2o/6cjNVYn/6uxBhctRSKx52px'
    'VYJBD61cEyiyaPkWNdPK9wl4Iy3GOeD6xH0Y0JjalK5di5YqgzHPNcSMlyTgjWW8wDVRyfsZ'
    'SYLWRYS6DbuJJkqGWpB/5Azq/l49rZqW4UdjQndPtFaU1+Tzbp7HsC+aUvP1bbmoT7+Pge9r'
    'IE+cPCzshLcIk8am788gfaTYrrmtulceHhijowyXP1Tr8Ct5GTRDM/XjGdeUys+crGz2XKL0'
    'S55bCNz4O9zvq9OLkAyDv55j0P8pNLaZfJx6iyj1fjLyHd2e0Nz2eIoAbH43w1RKMF2oeWm1'
    'M1g/Yr0Ab6DERiDrQYnwd0uEvJ1kime1c/dUvxWE+rMH46eYwtdQW1zMsoRRAUMg3Pkgyh9M'
    'uv9Cv6EgV7vuQZEYtA6n6Woepi/OFiVoE3LK7tq3fIK20pJyt9W1T36qUty/oAQzXKe8x3BG'
    '+b+J/zkxxnfSpL8emLwt9IFZaldX2tl7NoUPWyGrUZdyMzk+l9kCxXswmclic1nkJiwTqZLo'
    'ygdLw90VKBpSjg16rIGioHLsde/FSipAleJXYzzGO+PQn7X5fOlHO/mXWvGelCkYSr3Nqi60'
    'cBoD0hkW0DAZyDfgYexE5q/EMqfP4S1XR7t+atmn2yfO3shUGCDoR5ubxT1oN8Pt63lPbwqX'
    'SM+Bb8UC3JKUYB2/P2SBlvcAsBsjE1eHWtw/k/qAkW8gEQIDloqAxY+lDYUtoS5LeEEaS3U9'
    'ATQHB9G+onT1bAI8tVnvL1p6Nf7gBNJmLVM8jpfXT4BgwzeQqezfAg1ZgPRLfw0/clK2/0Zk'
    'FNTbKgfRUlYehL1Ytkc9Er0vQY/KWavsH4MDsNtexL1i03Anlq/ziTGVb5mofc4+rXi/tI9w'
    'mF45iAOwq3+kDFasekRzt4ZrSDtV8UtKo61yWAwyhOUptwTWoFx1t2pjAtV41CpEwX5tnlXe'
    '1pxr95QpZ7O9FVrJ/px9KYmh1ZDsD/JVRCt/z/y+xEHMvh+MQGPy2gqvfAutZHj3kGEHwjTA'
    '/nvNZALjVQf9UUPj07MBk+89WjDGFh+QFO3l7YkxS/1ivqbk+WZVDpoqyPNc+1NCA80duc7r'
    'fwQTxd1K8w1vGkL8hjvR5/6UBbVle6IlkB+EoApqIlroJzMhZ9DUWJE25M+W7Wf+W2HRvqXU'
    'W1N2o33gafwN34gOuEC8sbxDK9tPhjmnn8rrfoFU2ykg84C3O+7vMWNaXg9YntLspHYuUz2c'
    '3mPVithTgPgXaW52pUn6Vh46W9Vi0CwQTMitAHK/h7sdhMG3AN/vpO8j7svxTIMnnI18gilL'
    'Re4RKYtvkCEOkHR4UjgJRG1k1QUu9Pd6cBeSlQiWo0javVZ/p/e9eH/36IdP41w0Ir+3ZplI'
    'vBtLglS/HwfynrMqhJBkXsu7X7+GIR8SOYX0gFpgGQFyDSBWDbjVAusIAJCvaGGdpjTHor1J'
    '+bJiPAZDK7GCMaiZM9hSH49vjBpDRAmucrZExyOeHuI1aSV5e5BWxHvS3+KZod7JWmPzfUnv'
    'Awrume/1icsbNRN9RluvGaWvoTDNsg/5yY/PoPFu1BEl+/7AWnaZLoXiWsnw7/tTvFG9redY'
    'eArRabQTcQAdz/fzA24he0MsMq1wBubRTusGoM+UgFm9J5tSl8bz2ZcZT8ZkJusHeiurw7dZ'
    'koGoDv+RiDj6m/j84q8UiVc4hzxZX5KT/iGtaWnl9YXf9Y5Pyc+tvB53KHrGqK0l9bhfq7Ry'
    'ZdZ4qUR+tk5trcd6LSYNuVArsaXclStvn5Mhb18+JjDXnFVKhHi5s8Wg0yIbKcCxKUWk/Zbb'
    'WPNNJ80XdgbvvS/5fi5B/85OvhGOEQB9rfRbH19eWpkHViWwfqBl5oJpd+I7oAs8hSfvHaWV'
    'O7PoYbzknStv/3kGPcrbV44JLLETNDFPgbM3epER322UlPrprmHPNbi7ikSeVfiOrJFOLuHL'
    'izrJSBSWbVK8pWGys1MvZP0WTICVpaUzWDviYD2DJ0/y+fDSyh06hMeL5e2VABHIIizK2/9r'
    'DH0jTForo3gPCMwmBG77XPy9fT7+kuG5eDSaPFJ9AkOfydufwoD1I/Obkjog0tcKrTStKdzN'
    'q/Fp/YKnZRM2qT5y5at6vxF5uyIw/3/G6LMjBeCZ4mxD/735V2V7xhJT1ikRCU4GTeOgN6Lz'
    '+P8F/FxIVkr9yPvIQJ/G+5fW8Ou1cbirBNxEt/H1LK2sNeCm9agQ6yFhPbAQ8vafjEksxhRn'
    'S/WIdbg4aR3a7lVbaSW0IgctSF9b6PhUGobo2dmShM+7udfzAXtF0Mm9arNBVwm4jmsVFSa+'
    'naxL3r6Y2Kt8TGDRJUzQ7zh7tVttrmaC51YrfVwGqq6b7uojeFruvU9tTtxDlIoRomOrybEg'
    'm1Obb/G3eMfSZEha7vd3rg5r821E05UhNCtRm+NGdOcHI+3pO4vycPVnYIHDtjyzMoJnJc10'
    'NT654vFoTogK7vkOvUbUXm04F0n2ONRbtua14v65T4pgn9r56oJEDIQsEhvCIA8ICz38zSl8'
    '34FaH776Trhu2WDAkvrE/XJ8Q1jno9aOYGdZe8eH/+f9rt4GyfOV3gaL7Ps59dlZVrs7ha2S'
    'sqAzGL0swS+dZZup8BVq6V3k7HUGVW9r5ce4eVTzZqjfDnj3k07PCBS3YqMhUNxOgE6GyYSr'
    '2NhBEvo7ixyg8DNfBWxLR8qL3oZ8z4zdcCZq618nSfyiXnG4vcndauG9DGcs+vOE/unfzTmU'
    '02KrS6/+dv6GyL8n36n4GOmjELElrpjI0O6y9DbmQ9Fnuwoc5XYtpeKjbO+XtYXZVTY7FeJY'
    'WUE2M2nS/hdSK22O8LN3w45NbpPkz6gHFqpvkc2tuhsCZBTiRLe7PSkdO3wAy1ZgczWqdfLt'
    'B/xBsojlW067euT1OHQYmBdrKrDG+H7CIPwhBOwMuetuIK+xIVAIW5P4K38YEFH36HZ1ER+f'
    'WPd/0ElheyBXUt+OZgn/iWZluSgOMQnub2oLLUTvySj4QN49L6aNJzR43mFTFudzFydN1DXs'
    'bdHKgoGbY8Q91xN0EVwUmdND5klvQarFcwOBLmBevxC7Df9ulYzzEAdrcWVXeBWCPEjHjKkL'
    'yTpZ/bFxCx6yMwus1Ua7RSPbeT9O9HwWNsCrhKFhXCLz+nn3Kd6tvsf3ZdIKzNRusrnqgOT3'
    'CMk3EZIHXP3yejiDNIemm3QkdyMsuMnEToraF/0K7OOC7KoMibAmoh11Uk6/ujBb6klxd3sK'
    'qBnZpkSahLmF2a6FjvIOeffdhLnehmzPG+TcAXnhnxSbgPboa4z/guxGizmbvlNf1FPS3Mq/'
    'Tv0ld3ZYm4iO9uiBc8aHQ+8spM9baZjJaCOwCqxqnXaTBSx4U2BOTL3JCvtHx6Pt3Gg83mxM'
    '+Am4AwWWyLjk+3ECd8TUQlT7zwyLBeSbu5L9SSbvevZca00dUCLt2hwymVd+RT+nGu67DZxs'
    'V+vUUyxpIqXDSedVw82LTKbo+0nr9Q66pNVqusmGBdG+gytfimtnUH1vk+Qt6q3P9yzm+QSK'
    'P6xydwfc3c4WckyUD7LVd+Rttm8rR99P66iy3aBfrniC3BClLiWnTj0bjp5LIAv38lyNS0+o'
    'i+IPqZdoLf1R66L/db6/DFewxKoWN6uFe5T+H8u+/8ARnidtzqCffKIG+ZamwPyLQS/u1t4G'
    'u+z7APakuwssXqo8OdnkGV/7xz/84Q99R/cel/ZqT1rVZtc7allQLmxSgpN05/rkWdwe2qwV'
    '7mHHehI51qq7nUzqsv0/xlGkJrU1rTmQew6RTdnfIOI5rRXdfeQskBAAq9uQUe9tD6yM0dwh'
    '41c5rNIwZM0sLP/VC6m6rN01LPs+xF0oPTn9LndQXn8GrjeNossZ2R+jflzedln5b6L310Fh'
    'O2KCPtSybupBLeyKcKB7BWZySvUG5QWNiZlMo5m8LnOEQEdCYj5ihpiK7MvDYNsItd52tcSq'
    'dGWr3m7N21zlfl/tJ9e0yn0kHl91H9EKuy/vITYOSYGiWUSVSvCb46jh3ooBlzwvRLVVN1Fn'
    'hV3OoPyn4X0ne0PZsrtPdverPWn93t2al+HuoFXE9OshUATOrBIjRStuAD5y6shXXX9CR4js'
    'uwHrXBYkORHApdnq2ZyQeptVwzJbZf9arm0gBql6DzsqT8T63K24GFz2IfquFbcyFl1ANiGO'
    'pH9hF40VnWLY8YZcEWLA1bfiG5Amn46QJiSHLxHCpNUQJo47mf+Nt5a/qaHfbn+LvP5Nia9l'
    'ZgqvHkWhWUkU+ntBoXxR03eMe5vEnHEYG7jAvJvk9aAJQwP17MQHJknWhediLF7jPGkWl1aN'
    'Ccwzu9ztKyoCP42pjWpxV5xeIjki/m7IIV8/seFJacVJ9SwjCGexmMyAn/hLnyXuVbgwnb3W'
    'S6+NNegsZQSdRTje4G0Xq+xHNAgEoJP8unTUPkkjtWqFQTKyaOK0rrL/ChBGcQMRgXoN00jr'
    'NNMYWGMIOvknDeubmIXdBlPB7nr3DkT5ugOWCYyISJ8RP9ERJfs3jRh+tabjU1wQ8RjWstFi'
    'z45rVsKN/NQ3MP7CEVbJUbZKPK26ocS4zLqD6Sj6Bu7DvHMY5PuCYBleGl6VyENA5WKpccws'
    '8For6egDESlxDw4tWOTNwViM6gM/MfORdUEqkXH0YjIGvnTughjYd7uBAWPKvlN8OK5wf2O+'
    'Th9K0Fw1f5iPQfOirMw0YFT3Ko3mqnnDkVeHRqHce4jtwuIRg5VisGIa7JLoM+fr+8Xq20Ji'
    'd5G6d7Zo91nodX+QrC/5lmYldGlvk90zvlQpn0yzeR78W35Bafy9z8iUScjh1t76Ck8Gjaq5'
    'uwjFVT/HFTofrFbvI9HbRTOHOCtP4a3Ekl1aGU2BzHfV26y692B9S1pdg54czb2HHI8rNHdz'
    'qbKSlEKW2p/TPo3eqkBgY1zj2Jkkttk+KOkigVX7tz179jS5D7Gm7JBOKXtNfUfVVuXYUChs'
    'lurU9py96mm1sH2Gu30Hmk4qPvTIaTw80kp/9kgHqPU+Z5saUrok6Uxg9s9V7/7dSOTp+yiF'
    'BLG3K3A3uS+z5C0dUtXY2jQm0e69UVcPH+Cljr/Ur5YcWnGZVkKND6nvqSVdJKAjX0vcj64V'
    'Nmglu8Dp3mYSPus4tEss5e6C9GjIaSLJuO5mKXHka5aIoMOU3LjAZJLKuuiby90qPxtkrhM3'
    'CN+FZSHzLlj+DY36LmvNGchpVefBOprWVx/kbeOfYCjm7arHJVgG5Jy8ZpyLoU5pvWSfja+E'
    'uDqwUnKVta74rnqQFiKanZRPsJjt4HnMdvOyaY2+oS22EPcJpqOSeSx/x2oTBN/pZvBLZNIk'
    'veV9U5tnjWZyf8zHi7OpANJ5cbZaB1uWYG8rf0K73dL7BjpenO1a7Cg/oo2p+DDb82YyN9+K'
    'nvn16Ovk7yWMhsgi8E1ZF01E9r8vRBPNq9E0Sy3rirwtzt32tSttJqL8voNE85Fl8XtBDIVA'
    '9pNnUqNpBr0YOU6vuIqB+UilIa/UJjKuZN879L3RlE3EyPeYJ8XfeSoC7aoKYdieLBweB1jl'
    'VhiVNGFaes9w4mr2pKV/+VZe+qZ5QgjenpDz89g2XYxzU+u+mXQf0WmhNxacpgESbVZ/TNwd'
    'kRPtoGGJHd0NvPWz/ulzenpN5EUWOl2RN4dH+X/x/SZlpf2c7HuL3dqijFKkkvyONbRdfWi/'
    'EpR1sXBrD4sFdxcpHHGLp8Pe5O42Mg9514hEVEqB8L3QXyB3TPRL+vz+X/2ur98y2u96aH/S'
    'fhPp1WuAn5mnkm5ox36ODqS3S4cxbKaOEGM3Unb5aj7yGI9glMA8QF3ulLcXHylVF2aoBfbI'
    '6eT9I2OcBsICQRCB0yDOSy/MiO4V8Ub4W2/zb1CQwezu1m6zwmmdQ8vgent1Kfm5OadcIXld'
    'Gok9tqWAJgfynyMSvvWWjyPn0S77h824CKPcTTIX2yUdYtfo98z9vr/C2rqTvZg2z6zehjHe'
    'GZG2hF+sq1fR0VUsTbphBZwQXl1vgyT7cVHT5nyPzbBJek6yb0Tzz6Q1qTwGuq4cYML24SIt'
    'zALyROQJXFoLeva1ecYQl8i769SD4b9QB2S6ea7FsJ5Dwu5/95FG8HBeMf17vDlQMFntEJfZ'
    'HqXW0e1xO5A8ptWP0ItkguA+ffZ/bUrD5JHxD8jYLDO3J3QRGtfvYEnQrU8YEfTJSQ4azQ0e'
    'TjiDBuOdw8T9VRIi2dlx+/oC83n/UyDExs4iwDs4GhyST7PE8eitoimW8mVO7wj/B2lyf6/n'
    'dSqVemgqur93kN8I+6g9Ub33ZPS3LHcCN8XUAr4NB2lifQUWIF/2wz9QImYii1Fxg7mfjoob'
    'RCZxfifVz2D/mOo55dOIk3A4get5fMt573+Z3+c1k31vn9MdHjKddmL51MJDfcf2Hoeic3dR'
    'ZznNSlmXRI62+5B3UmRxXM5inL1SiMaSfc9xDv3qE5GKxDl4WjSiVdn3VYi99Ylyffl2xInW'
    'Z+b71hiaPmRgLscBpmqmXSKRVsauvK6LPsma974bOYANvmrZ3wNBd5uNCEX9kB77Ogh5gqye'
    'Rzd/wR5dKPLSEI9DpXcOc0/+u+kz8vS50edziZ9fB7kuDvwkBp9cdR9Se4ixQ/1T1eJ2Yu/a'
    'H5SWlvZ9qnq7QsNT1b2hs+acU54pu1EqzmM0SVTbTO1DA2Z1b07IG9VWTFeGY15bU8F0RPI4'
    'nOfqIzkhF/apzYHF5pxW1162GDmpVb69TituJ2ohrBK+ETJ4ju3T6URWsFLryHGsfBNyQWl2'
    'Y2v9YNhxIhaTVlihbjrwgxzC448c/vv58xPiP5uDtyvvIJ3d+ag14O4JFH6GYCkn+XTmOsLH'
    'jtOQVNZYeRYbrZ7rsLtB698TboeOI5HfM5GDqckbCNVN+TZcjLsTPkr0d0R/eEPtYH7k31tJ'
    'fud8eJ6wGR3gz0jAUnTAbhkF2NI4YD8eYCKPTkmyd3Q/gyNOwnsU9kkB7JM87TbdPhGO5xHy'
    'NkbbJ3+Yw1oM/b6emA/4EfM5cfF589H160yekFyFH32SffuN6XQFCrtH4PmnEVg53TSdGG9o'
    '+zqEyJ4Gp0KJnVu1RFtoc723uhjC4jBcvwGSTvjlCFrrs+rp8BNnSa5NHCUvMo6P4ncAeyMD'
    'OzP59wX8Qe9sVM2eY4hgvVtQ3ckhMcmx578HeRyWou/QcsXzsPnGaIO/k5YRZxvjcriOSTRs'
    '+UR0veWipK7F/cLhzTfRJPsJFFnNRAwGKk9ehzCd1ErrFjElyfuE/7OEte9mtXiL9qSt1N/i'
    'taa1yj6kmAXmnStV3a8osQny0/jJMfl19yta4RbOAFfdG8HZ7pc092Z4NEux/XmbJUAmvXcz'
    'OTbkJKnNauHWyqO8HF3ZalmtVralyv1XtfD56NeqXYW1yyeQR6Dda/N3ylXP8c2MswLfkaok'
    'tSQobxm2hE5alOO4B49jDEqXjKtrfbgqWSvcpd1Cgqs8jTpgY8BGrjn5S39Q+tMeq+Vwz1+j'
    'f2W62jEBWwAc8G/LORs6apZfrtvX1RvK9pA4cJKvF9Qw9wateIu8pVXaezzFHZwovPpKgmkH'
    'YlNUkUJShV4/nlKqel9BL39q3Xdc9NJKfpN7s/x6o46S4i2EFfKXIp8k20NA4po+ot1kgJy9'
    'OWeb3EjvMFGfTe6XWMPqAMrk67gbcdit8BUBxkt1khTEO0kAyO462X2AAEhrpZG9a/wxr0JM'
    'QOugureq/X3urRx/Gdvobr0m4N5LTkBgmRSdU62VBF0ltcuztMLnackvMlBKdqiZ7V2qb0y5'
    'JnCbFHmJ9Z3mDWK1ymoRE9vOhoW1yR3kzFd3LdT5TrZabWqP/HrhKzn9Up3ypA1nFrTirZXH'
    'WOYOSEr7gOreJTRBJHR2Kol7Ies19y7Z9yyCX96g2oh4K9oQ0vsiI1RCWO0pneHepZ4meJSg'
    'eQOvXy2uql+BK4FbMV82CB6RDF0etyeYv2d/NIq/OXqxtbrXvbVC9l9Cb6EL9htxHxIV58v+'
    'F6WEQXLmQxHxOEg0rTTfE/7gQ8MYnEZ60uyZKkY1mjd8OGo8V9/yrGhetby9YDIx19ulMDZX'
    'Q68Ne66COU92odJ/jed+pX+m5yApKIU6iB6L2yOeD0eaWh8nYCkK341Kd1Ci4Tsqb7ME2fHd'
    'qBW/pDQsYbU25mQ8nt4ezjlDPVcZ8kUYmwH32+GfUC+RR3V7XS0MRn4Ecabbh5/hLpqehL3E'
    '19JBuLw6bMTt2K7ag3bNiXbw8V4F0ffqBM9SRCd5MFWTezPLP/cWzkZ9Rb9vCMSkFm4ErRS+'
    'FPkskRcVk9evxd3w8rkR8cJFGLcoadygx0HCIXLMuL+Ibc/1n8DEOTyUYNA4feNgBO6ibhwz'
    'i8gvguifWhf5FW+HWOLQE3/psBPxAPxI9dB5/qIzhhz+PZydkhG+61uYXQNHx+mhGQ9N7j3I'
    'jlb9czkBy1aqgm93ITXCDc1nmoa9WCOw418gtvZwMM/djgxNd7fYiOqSd6fwKSdI7SfJ3Izm'
    'xeMuNK9D0NWprpKuclnz7iJNLftxsXtVxpeVehK3h8ioF7Y8+riY88Wned/QeLw4pA36xqK8'
    'bSx+fCKKxfIviiGBDLuGDeqw+tp8NCHajMVw9LArUE6dtlcMfPfxB+RtbTHHc9kd8XOTHuGu'
    '+m6gxX6Mo/vzpNxvyL4JaSZT7tdl/yGyhCrKzdfKvv9MGzHSYjP1qQ7rV+XE7+sNz8pNk9d8'
    'F8CfTZHX4FIjLOW/UzdsHGr+InZ7PNO05/CkmREQOqbnLs4taNVeQLGaoufRETjqMEJMs4U9'
    '7T5U2bDHpN/CX1pd+VAQt5AprwGuFPU17tKdJfvC1KMoNQeWWWTfr5A56s4SRZJGJs2d8Rap'
    'tGJjoQhIfgcKd5EAVwa9ogvV5lD3qnXwj3BPljdL4yEisMIrC4M4GHn+2H30ombV/gflijvL'
    'ojb/Whk2eybxfbLdY+LDetPk3S1UiThw4a5wD2we0f3pc2IlBHaHE2iPIEir8QIHinfVTgOv'
    'hXHdURCc8dwiAYIOizdLpxrluFkzBe6zQHU//XAqdtAwUV3UbA4/fYzmVmLMzcuhl6DoRGnO'
    '3mxh8U4TexO//Do9cI8t4N4F22/4s+T38j73vYZR7/3y78nvpX7ue9NHj3cm+b2/DSVNNnCP'
    'RWMsKWEzslOf3mrhBScsaaZw6gfJLz6LF5nXQfqnvol9yV3ho7gQ7ImhCyCet1xW6kuSu1T2'
    'bUg1WIEVr2AC9iq5xcOy75l4CympxWxIMYaBlL0sGBubG76NOrCFu5xtmIlBd7X4AYjwVSOg'
    'PwBfqjgYn7nyZrZaHOytIyLHj/kSvlbaOAekdJrJXZpnIpr7K3WP3JAqeXthNdmWT4ypeLOC'
    'XkpuRkzwKwK68zVcJ6i9wH+fWwWmqzwaWy37cGVuaWUkdiOtBZLB28P5WMUyA6wbBw02TIXT'
    'x4Wyr9hiUDuBMSY1zpOedJ1tZN8PUyH2s7RLVDOYRe+uZwBIkv0INBGjcVSHZUHk5Kj5Y+b+'
    'MfRw2BRYae0Ijpr7qymY1MNU3/kn/JW3v7AMs0JSbhIWDrcn8OAB2b2GH5/R/Pz3uSWMhw+A'
    'h6stwMOgKVdePwSgXj+zQ8olm+eUs3ekMSh1BH5tCRJlFu6C5UpGKxa1I5xGTMNRWsxHKQxa'
    'DOq4BBNjoehv8zzRFzrHO7s3cTAsS4hFnK8wqdAxVakWHSPFWaDTIkNAtOqIqxtgCcU2lEDc'
    'Nr1ms15jidc8jwvRzhLT/FBHs07+EUV/xYsGhcF8vf1Svfg+vSdrvKe79Jq5A9BA+QC5ZDOa'
    '2KE/2bQIz5rNv0eViGjmW0RI83dgx51OKuE3yZazytuLf1fKgYv9+gg9Z5OkxWYL5IQf21+0'
    '+vdYdTlx4GQyw7zcf/4b3xj1xuye5DdWXOCN3FFvtI1446b+JFmkS10wMvM0y1mNzCuSuiRt'
    'N3Qmvzm2f5QUo7WeVWvtwE97ynN7NHczVrtU0q8RJ/+y6qtQ9LtIK/WEF1BfkQJIAWjbDwg3'
    'r8I+Vc6aOd4gz22O/Kx/lEiLkwoQk1AtBhmu78cuAZzPi3E3uJlIOGKlp4o95Bo1c56oOxi5'
    'fZB5SPZNAWQdEbuw/+TtQWdbZCuGfB2qLfKVsxg9GHkRG8jRWepl+jLm9IMzmvGNehXjC5Nl'
    'B/8OUxrVH26PWg63H24HCi8hhDiD4bWHCZo1g6zctEuqzJEWPBdZAsusmimSK6Zq5okojeYk'
    '8b2j70JV/NOEv6Wq8AqSsZHJ/cKrH3YmMHy8gyqO0uzICpot+wd6uZ8UvZ+UpCEW9wmwwKNK'
    'o5WMhcg0dBhK2ysuDX81FebuCQP6UtUcGYcvZHlEKvswX5XnehnNdRqS8MIv4HrJDQNc1czl'
    'VC+qnkJVGbObN1dn576Shkh48EIAMk7/p1cfWTXDQ55jiWgDgjZYiDDiDTszQSWre5MypJEP'
    'juzkDK3QqpydWD6JXehEvEoKkikbKJJdb676DCGYXZNoYbO0kiElaEF+a+L3rgvtmteqldjQ'
    'y3eEIx7j95v4/b2rP8NV8asmwdSU1OvlbRMCKy1V8y1qv68lceGdvG0cfuusTqqy+lq8p/yd'
    'HrOagrzlfpFP4YxFa0f83rY4cY8Ub23+kBK27DumUp9FVtVtLYkMTzKZLrA/zedv3DatxKqt'
    'tDuDalnQ9aRdXvsr9utsgXsyOCmrOdRlVt8PZKRgPxmpR91KfYZaskeq18zONu3rgfkSuQtq'
    'T98pchRUd4Prbfmp7YzxVuH57NGdjSZ3u/gQ7kZJLeIAE5QmbFz3/WAoxd06sXi/s1MtRsij'
    'hJ5yQl/aN6OwfVLJISpoVxotavEhpUtSbx+iAV1l3StCWkmt9nV/r/fWQMGQy71nhUsrC6oH'
    'ZxRbleNfCh03B24ezmkNZLzGZ4T2acXNakdUnNtSjkuuOs9YbfIzhNrTanEzEFfWrK5Ejiof'
    'lhNZ6pbLefusqoJ9nT3kwzl7sZ5KvUWTnG0zCoaUg6acni8NKCGLM6YeUG8aUj6SXCXdKw5z'
    '8+ZAxq+1awlCV2DOkCu0IodgCxTEBHg5rerBEfBYtSXPDBFAp0YC1IXdNneD5m4wshJ1eqs8'
    'sYv3Gm2alwgvC0fa4N3ZVW+t5nXwKxmqd482S21S3eTnNmuF2WqPWrxV6XpHLWxVxyLwNVbp'
    'J2tFQXDklHLUQn6w7MeVk3DWroOSHseuLy1BYVA5m12eTj4f6LlOcoVW96kZjvBfsnDEjRRL'
    '4O6MwD3IqQgNpqolmwO2DXH/N4sYRamzqN8FFk5RE61kM6dZS4HJL4BLXCFmFnlOe2jQHDV+'
    '3wSSGPtMtPxRSVv0YgaZEFH5NXpQb7aHulKV01MJi/VgqZZLOHSH384urELYA9fqaHfYcRb6'
    'ITiuVCF+vCG87nI0rTpcslkr3qK5N0sdauFGHIqVTWqhT4lIlW+aTD03qoUb1MJqrXAjKTje'
    'vc1pRhizcMNh9+b2aRsPLlp6KH0z/fW3HEp9hT7lW0Kd7upD6bX0fPV1NJsjIdL1E1T3xk73'
    'xkPpQSo+EuooOyBvdx+o2EPeuHt/x6P7O4s3djx6oOPT90/Ja162IIWYRlCJ6zIkcta3qO05'
    'tGwbEZEpXqucvUReg9/bOmz5W3bnoqWHiwFLR8v7pw63y2vewUVE/mW6gGtyV4gIxeqLWTg0'
    'udcyH9SpxRXVTXy7tvDgN0qFa9XAKv5JcyNvgWRhv8V7v+ZubTIh/K25iYvtKawkbU1u669p'
    'LpUNu3SHNXJuwNi3p4WovpT3v3F7Fh74IKOUOAX/Ar0a3gM7ql70ED+LwfkSIwa0xgesHjng'
    '2gERb2m6me/aiQwav681l39VLfz8RN5q1Yp3BWz2nHqiiMippD2laoEmBrB4rYGpticuOg9T'
    'mnsjUgz28O7mxhx3hR6hIR3qB8r6DqrPsU/hblb60+Q1/2YWx+HXD7B960vFjxfnwXOWf4lf'
    'e1H9DxutybF5YIxo7fsTrpr1e/Q14TNJt3NdiuznH2XlwbTCWgZQalb9D3Iik4R8og3yHa1q'
    'K5yX4u7wtWcRRvFp7q1aPvYCVHe1Z5pWXM0bG/42+ZY65SMSE9Vqney7hAiG7SniIXe15t2g'
    'DE8gMK9gGKv8/8auDn6iWQw/3l0t+4/RV6I5jRGob+YfLq7GkeOOMGj4RDpoOGomC8dd3R5s'
    'Tz1ES3f4BZ62mPxzSzkohWe19WqTKT9F9S9lrGyA0HHxCACnyb9Ex8hhdhM0xtBhfvOw5e9Z'
    'nXxlku8nNnIDyw6053XTSFqhD/wVuFPSWezI0cOs9JnVNhOrdbo3y9vcm5XgrCORjpYqqfPU'
    'Y3cdbo/0TzAln0/uZqHb7eqRA/i9mI7U/dQ5Cb1DLUfe7Gj5vM7mxDoJBX+krjpC7Ve30isd'
    'LZ2nDnc89mcYfPK2W2NaiS8qV7vaZV9HOvT8/JhaF5gTqzjr8hxXi30kE6usGs+7yf1Stkn8'
    'jIx7baRRSrZHiLJ08/LCBNbpDh5Kb8bwBGhJLQGquWuVIE4V0qTC6X3nEYnING9yc8qVIEXm'
    'BSY0EaLbwEkyrxAfVZNAez9yuP2nY4hO4SA/ulGKuAcTDNbEUIjImUXVqX15ik7tSJUcRe0P'
    'pLCNxgannLRPTR0nRFrkDg6NVOlQQjFPTP+H0Cbir1sFDbvmZchrczgltvaw5Q0IUWzGzEVo'
    'y7sBefwuoNLfQ2vent4F0o0LWOWwmW8Q0x0+WklamCZ3UByA8EmNKU59zbDpQ9bD0xdjnCDp'
    'rPFwu6jEc7H4qaClqClbS8uoeVsbqezbWtkef4ysvLGa+xXN+1LarBHCcSQzkd5yQ3VdgIfQ'
    'bBqDzXWgPnlNxniix5aOLqEmLqVZRK2gRzCpRpxW/GyTu1pEm7FRB7uMVGiLrOLXJQTTgc/G'
    'mkUgu3grVecc0e6yBOZJOafAJXvlwO853tEcmHdO6Ton+17CV3dVTivs2GGJkCu/GEKKf6/8'
    'Yp1vr8eKYyVnbVBAFZJ7rcZSlATb2yrJWHEcVS3eHP4Fo3BzYon5tEX5JK1wrb+lPMP1ttos'
    '395IcjkHnec0qoVrNdZtJJCu1AIMeosHO5X+t3G7ANcZdsDiDBguxuHX6RczAoTE9/8QEZ6y'
    'ZklehyDWq1N5VddqxRU59UrUonwwVTqolnSr7f628ovQHy4R+F2mMIRI76yV6qjmYhCCOL8f'
    'VjNF90zN1LOZ8ICBjrKigORbv5szX2TfNqauDf5O7yrqYMSvCOondZ63Mx2xuiPXu+QSzpe6'
    'XVcTJT6iUgiNUlyBHkcdkTybMYSMO+qEBiE15t6gumsju8/CGYvvkgRFElstdUUkDR3b144O'
    'v6LrWbFcmrdCbxp5WOzP6AAw3oXggvlVjqv9zvg7gY+g8Wu3T2TwYlKJVkwW1i4skYOj2cRW'
    'AtDI60Z+BMzM62X9xgrs6G4iTIwvafaS99LQmyLJvp8z+dUiPeMMoWKcUlZtlv3f4hCOe2vV'
    'PGgXl7tb9t1pZdQlY5V/VP4WGRja5SoOxo+swaY16glk2TeNhg9ny0k3+2ACu5LahM0ySskK'
    '7yYrXOgQEIFOcb7TTH2B+bNUIlXioyCn0b+QIWzXejTVM+06MnhbPh8bSv4h5HYsYttGLa4K'
    'P4r2xVVkWQIxUyfoG3HNaa2y/yegp+IGbZz6Jlm1yjFpfDGp7WZOJ9+qDLhk31/TGIfr7hrH'
    'W2R8gGWxE6tdGKyyB5ZJNCWamLDHNpPbwPGrdD0VVvadwwiFzcRXNlfIY8W5s+fThTz0+iIX'
    '47x7eDCdkUOYEKF9RkFV8S7Gwp8nYlWKd8GyksSPYQnCGb0orJq2hv+cLtrTgjDV0nu/DV5e'
    'F5dM/t6xzDj+SWMNW4UoT7Ql+sb+3TBBp5VtEL8yd3m1Ovt8ApifztddGZqfQMb21WyxcG/1'
    'gKBnVRXEyPFcFSS+6XO2aN5q3AkE00q72a4Mkx7jAxv9krz+D9AxN+NSVd7fpc/noJ5DXcSw'
    'L1WrR8ifqfjWLE9v5NeEHtLnG4XXyXpjtyxkDzb+172FC4yE0Uk+k1Zgl19botiVM1MjSwcT'
    'enJQkv13I+gClW7B1T/uXfI6qGTFXSEp7rUS9cE85cT6CMlWicScwg3a/4CZkfrmDroa5bW4'
    'CEs9nVMHDdAFoeklM3iDbuvlNEZKBpKlSuSBv7NtHHmIlPNI7wZ2opcECN+AGlgsdQTbT7NB'
    '0syWUzNxZjMhtf3NqjFi1YTIIEvqU1JckcMdP52qz897CfQVKKzYF3m1PynPtps0C6kS320p'
    'UNhspHHghFbw84ebE0uM8eSm6A5SgT6+Z8C1T/ZBE6pHAvcighTLdXmi1KHOJlUpkeNDLGBB'
    '6V9HiJMVCuL2hRvIj8qp0wjdxRvhLXerjaTP3BWQedwq4iAhyys1c3j0Sl02fP5KpSfnE/tE'
    'DYPRaHJGvj8kYrkQCiQJiGqq0jm93cI2XjCcZhMqgtTDcBqLixwhtMs2yz4rQRI52c+sF7+I'
    'hxi9uCF8dIKQfHGZhRg32J0U1LsEEYnc4gYSK7K/CTCvtku6hpDXX4kdr5szhAEn8oxLCLVb'
    '+fI4MAY1ltcdZs1K0tn3rRRjZ1wtbo4+hDgrLZFKUohocfL5HPq7NJ6S64hRI8/ticted0P4'
    '6bRRoGd+AgKnARpo3VV0XYtaXQNdxRK/NnAreVGr/QgPeYNqD1kgJbtYTj5jQZgKkQ41FDfs'
    'aqvskY8GmNKPtBCB48Qf2WI+9R0Qvsp+jk7x8rYX4EJ1BHV2iCb9nkVQEsSqvg9ChTcRhJFe'
    'GCSpOpasd/X9IxGcow/NqroVebWuHu9bar8m3Kiyzb7gwZbDHR2p8Hg66tpDHW92nj58UF5T'
    'T1g+cgz2qrxmUQoHE9i+Y7umimw72Mxb2OLYqi3LIOlV9Vv6djgVhrBuBEbcTFrVEPW4ng+c'
    'xz+1EzWzkNeXG5zo/DsnbfpKIAL+h0Vzr7zuP/8O+bBEt8TFPA+LXXH2xMR+bnt6O5ULi1bY'
    'nf+SkxjpO0UljeI+lvDsMbib20aKc56d9FAgw89XWs6zGae4w/99WdKvUU0m/Wxj/Rx5jgPT'
    'MHlwdk7MK9n4iXz1s4TGXMMYq6UFoRUyZIFdqMlIO+907AKdFW9FwYdgc56/tMOpY+qq07zx'
    'MyeZ34qbw57xoyj2F7CIi8Fs6x4Cx2B0/JwXrY2upwUTTWCIdglw7IYlUkvKO3IPG32j3baC'
    'z1jkiKhFXDVGnvs70NDxqTMo0hhKfPDrCGL5N0HZHRLqSvijuiOad4bWLghXFq7FZnoLb0C9'
    'ctfyzxo4vWk3pzeRDuN3ZX/WKfZo7cb98IG7YhUrY7M8f4cfmtz/4dNiYbjPyFEk+CeMzerk'
    'FfrsDIvA0WKC6IIMjfATqbrR4A7CNDDxtEebBpGHsNnbjAhYYTc8EeGARH56xmit++xiGpG7'
    'CJzRyErGVOQrGOaODE7zGWTjZH3asEEfiTXayqra0LyI7QVdB+S1f8YmYGFFzgHyiBDIPG3A'
    'cxznTfjV5J4gds40mlyRQrx3f4ao83dSrecHVG/gmwYsGBRYjXsI+oQqT5JET+utI3FeDboR'
    'ZMYJSaoRhUAfkVsvMAs+qu2u5jRNLjg8MEojQBQ4g8rqDBOZPzqKfkFlxv5DhlaI/Qe1WXvS'
    'HupPxZzLwq6m1VORo5RvF6qPnjLEDbkZxjkTKgwU0XzDgdynSDoGZj+lHJ3qeUQcxr7PkkN6'
    't0cbsy/sGpQV1ufFXeoBtfBEPB4wUd5W2KM0WpR6y75j9HwirVWdN6QVDOGd5V1aYVgrITeu'
    'K6cjsOBnQyppCM8MJWSJXin0mmtYVr4DEupX34Od/LKrcfkggaN5u3N6AranaEJea05rqN+s'
    'zrOTd+z7Bh+OT8Dvtarz+MZ93Cur7o1uOm9/Riu2BuZkaHfZA7ZqZ5ureXlm9Iok/WFR6rJd'
    'zStk5XqT5xQ9jDkj7zJJdbjOR7+PQ20kBNtrwtfwTxt7VtMUrseNRFdqUx6co9+3q/u+sX2i'
    'GS6WljzH0bwo8FA/0qcFPGw3r0lFI3/Q+x1tSpG4GM70tcVLcSkW35aR9Lzsd4uWhrc/ilt1'
    '8Q4kbN8R5aOswNN4g6+zyzYurBLDGL+Lbvbs0UEmjXMXSBS3h3GUUoyPJtbz7l2c/HnwYI8u'
    '+SooHb/OoHFjSYk1jENrymDaapdmeSHIU/SUa3Ylj0FXM7jMW6i6PzNGENdMuT/D5UzXsX/a'
    'wbsXfMnHvq59HynBSUpXPzTUFRmcJTjF6O0ei7rM6vmrTtxN+TZxLK5/5H0/akfNrOCipXx3'
    '375jSt0kNVQZCeLqrQOhsymhT1OkU5Vns/HDYH7lmLhbRO2Q6vZ9pGbivaTjqCPxVGL1d3qu'
    'U560pJXb1BIrbgiJPDwR15WEW0QOog6nRsRJn+pk8THRkz7ixpERiAAWjo7Gb4vO4bmaGzui'
    'qrsr1D9VK7erZ9i3sqll7aq7lZO89yOeuV/+y1wtQ/O28smOvgfpueyQNJDi7ppY3O2MqVSp'
    'hCTwcVm3q7B99d/Us2orTjIfSWv2ZLietJZPQgr7GXXu7gzcOVBuVxpy9egZn1fRf47bPCxu'
    '/fV0kd1AVkNgMf+KY8DClgNSWXAf9qqLxdU0R9Qz4SBLvq5Ex9GXq5l+lBPTw78Ywq23gma+'
    'mqCZTBR57iUe1leYfyOE+izK4Ps+eXXVusrjWNPQQIpSZ6kcwHo+5jYOOEQuYsJp1btsyhfX'
    'WeZbwUzelz+HGgP6OcuibJ0SgknnH7DfW2bDzuLddm0+yaVVE9RW9U01hFsOsFWs3WwJTB7n'
    'ut9aliLQBXwXdoeRkkarBXmVia20ZSS3IR0Dlt+6Gldcy7qz1bhJhoTvbIcSzqR3Q2EzIdai'
    'n0kMf+MiPpyNF2/OwIFkSzWfkrQqwYyArUotPORqlpVfcaiqS/U2kPGdM6Acz8RWru05vr7h'
    '7kxXcRezhVywF9dgnI6mc7zyEKGQxODd2vUkG4lG/DHPcaSxFu5R9zl71eL9Tr53N0Q+dnff'
    '94ZSUpQ2k9Rz0QApAKKugiGs8AFXaPlhrXg/eTuau12dPc7odvnvNW+DVkKqdfW4qB39RCWP'
    'VZv72yFaoTM47HmPHRcc6tuziXyAmVqJDdTfrnSlyP4DyMQfNKunPd/V8jZOE78/f4qPlfbW'
    'S95JCf7fO+j5pvZNf6/n22ofVXlOsmOXwTs8s0DBjViLPzKmuhEDmkQrtHdQPe3FnYTiB5T5'
    'XhFhaHaHo7AQvokjjL2er+ok5d1ENWqfISoFGRV3g4w0I297ldhP2wV7YCxG9bjEnaXUMLo9'
    'sV9i8Q/Bjk+EVdia/fugDqofv6rgbBM3kBv37YcXAygdEZ4rdKhk/6fiFwOykgVMpEMU8nv4'
    'fUscrPVMIssA8S6duzahKwFe9L+aUjfq9+nRuB8nH7dN4odcMEOJjUzw+RYt3x4aTJVILLUH'
    'iiTtZqurrtyuvyMFSfi46lb3QUAQOnfxLtshLEIjRyRxDcDKjPLJoQ/MMOWXOKzS+wZDhL89'
    'gfElxgLz5NuVD6aqJd0B29Mi3sCGi1q2P4dM71Z1kHjS3eUMJu33fJtslG6iVTJZ9n2M20XS'
    '+kG082CvHFjepe5F1knZfvWtGSTVi1uVaKbSmBmKml2nOGFG32SX53SolqmkwVO04nakMxbZ'
    '9USHTk4/O0Qyk0nnZdfbKw7Rn+W90XeN82BaoVXz2haSzNS+Y8+p984ib4t1digjYLkosOiS'
    'DCQzCIhdoRUXKWGLctyiXeua5en5UqfyliSu9xolj4g/yqxagd11s132ByBcRK+BO0SvEzOi'
    '40qrXSFZqeIzF9znDa7rPSdY6y0fp5KfYCUOd/PVmDSVbIQ13HvEpbvY6lGX2WiAwMIMyIGg'
    'ntHi3oXt9VZMn8TRfKxJwFJJyohEEwkEd4PK9+e6u5CeguyONi1/aMZNRr4HZ8jvpwUhs7L4'
    'EGHTGVM+ktSFQyRC3sRCF+5XgpmukNrM+Jd/4e6a4bbK20q6SDKSWPR1aiUNKlnNQjzKc0Nk'
    'WUshor7C/Wq9WkiCq5XUnKzU8R79fgCGeKzEoAgIuiByBASQqUkQ4G1cJUMQRqScs8rRIen/'
    'a+9b4JsqsoenSYq1qaFKVdS6m1XQoqV78869edGmT9pCoS0ggiVNbttImtQ8WiqghfIqtS4q'
    'Kioq4gtfKyoqIq7VsoguqyjKoosuKrpV2BUVXVTW+50zd9ImpYjf9/2+//ft/zNweuZx5nXm'
    'zJkz987M3bH0rdhZ3Qu3wuJy3NFVnZ+ndGesWvpq9Fu61WRnfKvJzrgKY/1IeTyyaw0eyrwz'
    'YQyx2+z7N31LeQ1DsY+NQWarJ2ZFnyNv1r3avXAbcL5j4X5sbsorXUc/a4rf907Pd+IYkBcF'
    'yD82UHqyl3VXH+txHwOzYeQLL3ce/vW4V7r4zqPpI69/nMh3fcz7pmvhfhDYmd3uo13fgSqZ'
    'T7Pa/dG4N7pq+7te7vpu9+cwQGr7Xynqx7qd+hoOILbHo/ZQT/XR3fv5PZFJ3dVHkBRWAkX9'
    '7xf1D5jruD/nqDp6oXypD0Tu/mgg8RA6Xe9AzJBMZL49p6bHClhFxr20+wu8aKbywKI3aKqF'
    'h3QSNETdHTmKhe3+Z9e/ekrP7Nq1G4d7Un7H23fyfPN+URrTku/P3t9/5jfym7tFssLuju3v'
    '2v3S51mLPqZbXd5d9CohmWTRRxK+8ix6F6aGRp0E64gfgQ14khZYecZzVDN9MO673Z+eCsm/'
    '230Qcun8IYWugLtmjO58V+pqyeoqzQT5co98oWYFWHuZi77Qq/DQWSq+18zCTTpvdo1MPE+o'
    'exWK+dPugz3tWpaRnEX0BUjds1r4M04cZ20x0XOVkEXX2QOpB9ZHlWjJ9ExJGfnMqbhLLnoG'
    'fX6YtG9udl/i/afVbLG5F4+kHcySv2eV0vXyuPf41yKpHVYSOwxGbteuV/tk+667NgN0HrQr'
    '8+DZA9+Pwn1apzZ02vNJ7Kuu+HJr1aB9HR8Zr6EW7Atngn8U+rdQf+tZnX1a+pEKrXxRLV2b'
    'NKyKp1otby3Zi0vH1M7eTPpcF3pnUXkGvb2Q3ug3eH9vQnkzh5Q3cUh5M5LKW+TNkK6lEypN'
    'fD7yuQ9mv86+zPiy4BwoNnF/JDB79kv7U5Wp6yCn5Nsj2f3zpbgTrTazAd/yvhXLbnBIpK0J'
    'OLgihV1bOzif9YD1WnSE2QnZ3UV4E/Fo5s2Sr27NYHuGMnHjVsIXkugl5VFqFWjxRvzu2iMg'
    'N+v7p4zAj3ldEj80lrDii883PUUfyyYZzmNL/zry2W9HFu7t/5Be0N7/2inU2smgJ4ey5VXA'
    'RQk32g23fxJ3jNph+urvwcRFb/fffIp8lDajm4PBNA3GTwMsf3AE3kHtsF0Hs+X0Ka+k9HVc'
    'KxH5HsvO7Qrb7MzI1d30VsYM+eLutJHPjog/FUTVu7T32lefp6deBtY1CevF2l09Cz8+mBUP'
    'gdwXpH5EotMh6xPmWiLnuvvghlW4IOMGM0Y/mLiyKTVceRAG8XHquHxAd4yGBYq83pE/Z0Vz'
    '0PXO7qOdMrtviL7KpivSkUsek+cUTNq/Bg+N4HcE6rurVd1uMMOiOT0FKnq5XEaXIk7WrsKM'
    '41dBDN6b/ReYH2Dh8uNLPype+lSJ3dm/XUWXUq4uZU+WFW+BSDv4+4HvdatUctl00WXHR/7K'
    'rpeB+nQosKcDd4bh+cM2iY4WWjJui8b7rTuH3m+M7VmYFifbhW83LN11qldKZJaUyCw5hSQ0'
    '9iklXugR/VXnQRX/dXgkbffX0i7qjXyJm7ZfVdLPWuCNwHWD+kx+esHR5UUWclC2E+huRze+'
    'ejxr5DPKV0bQAVN7BE/U3YZduTA7pTWCj0PvZSY6btHHpzE1Bewz4dGazoWZKfLnaIA7WSns'
    'ERxIkIINSLRukXFpNEpOP4emHx2/ObtHPEo3nNFHO3T+H43PjaBOoA8aulIwPiUpPk33VkNX'
    'pq43+XnQiqJvTlAJ0CAozp9FJPY9tUSilJ9V06V4MLD/HEWS3vhsPn3oq1wxYjPeA0dvEm2Q'
    '5wO2YkD1kQVK7tOeok+B86BC7lJSFbKc9uWWUnzCtWBMlnzjbebIwq+YPkHxjcoftuu3/UgH'
    'cnydlrAfO0teMi7t3VwIGY0sKoKVclb/pUpZwxiU8qZYvNexW429/S6dMkCG5kNhOtz3UtcF'
    'K5qvQcehwjlT3lep6exTgNJ5qUtjW5gZccQ/CkArliWrhkzcLUwVRea4o1T/3fJvqiBexiTp'
    '0H8jZIECw47tQx9Y/wF7ZmfACjv2KTKma3fXjoNqLDflFVSGBL8OcBQ6I4t+cqMGiqrNontX'
    '8Dt/IOPj5YLeHMw5Pt9uq9BJl3cdTdLA8n3WbARAed92z87sPxd3CW/vV+JlPX/dMgH7IBrv'
    'g4yRhe/FPzMif+5C1VP0Lc6tm45hN3Dxa4cHftLY1SAmiNcwvJbh+xjewPCjDG9keBPDmxne'
    'ynAvw9sY3sHwToZ3Mfw2w3sZ3sfwfoYPMHyI4cMMH2H4KMPHGCZuGasYTqP4ngpgUDfpS7iv'
    'AcZEwkcI6XdS9jANmcf2tlxPF/H4PTr2MC2VbiF6NeHm7cH8sHfQGpmzrwaW8VxB/Er/XPyI'
    'q8legJ/XwsxbMX6PrHty7q3Ar+1QFSDtx+cXu56vCSQHDujtlZAQTZs992Jj5Aze2NwLCQ5u'
    'lsZimoTv+6wqoO0d7N9+yp8G9u+zS6cn3ZePmmDX1hr6+EBiuw9/+Q3+2CdtyWHgEXvRSM/i'
    '4Gdm8ELsXtcgDd5IgY85O56qCWQCHEb8bE1gwjOAAbSQx0bG619+/xm/gL++0euti9T52vTj'
    'dXm+QIDU1YXFRn8kKobrGsKeZrHOH2wIQahPHC4cfu6QTxRIaSgS1Xp8vrAYiQgkTFpD44Ni'
    'NC8QaiQeEm0Kh2KNTdpok6gNiwFPO/H5w6I3GmindQjOFX3asRFtNAR/hbE+racBitGODcS0'
    'zRFSCTl6GkXSLGOtLxxqaRF9ghYI69ujYiQXXQ1hUdSGGsBJPxX8q5rAfoCOX9cE0E1BWxOY'
    'Bf6WxLD/SahiaTnAY3158f+kKIgVxuY1AR8ujmi9wJS844MZg/JILJIXERs9oSC0XswDTpEh'
    'XshUp7fkcfBPR2qa/BFts8fbBLHQ7siQAJarNhiKahtCsSCwoLop1KZtidUH/N54NMmbxTrF'
    'H/RDceFWMUywKOzXUJDIHRMIheb6g43aWEteXh6JRGPBvEBeYyjUGBDzvKFmGqJLDsJiPa0e'
    'f8BTHxBJqd8nkqpQOKptjoFI1IvwP9omikGtTusJ+rRmk8lgyiP5lEVaLC6iDfjnitqi2vH5'
    'Be7CIpylBhkXbxt0LXq9kGeoWSvXPg9kLxbw0XaHReAGJWn2RL1NNPM4FSEt42oCl+bUBFaM'
    'kwHdcdjP/JvHDcYPpUN3US2prSZyBaDgYBAEGDiFbCL5Ecq0YYvH+OkeP9JC78itCsGfsLYF'
    'GC4TlBI3cddW15CB1NjNLAd5UBB8EjApBDyMtEFYQxjYcKLmQpcAQ1oC7YNkLCt/EAcLjCqg'
    'kMegtsUTbcqVhyVUkSQ0TXvcuMW60vGuhUqNB0EcLB08lATb6BVxaGoTxK1FhMJb/R7IJugT'
    'r24NxSICaxe0qRH0ibbNH8WSPFE2eFiNfSFRluy5QRDpeFE0PxgD8WrnQrGBQKgNWyDXbJo/'
    'HI15AuMnB7WTxCiymlRWa6uBfdpqMexvGGgniH7lQCtygC/QgDZP2AdZjSOFMo/KqshUOkoE'
    'UhQD/SOS/GbIxOshbioPAsosjaAyziK1nrA88mV2JI/wPDpIBLlspj4pI+iH2KnMTy7PRdYH'
    'ISVkSsUf6+iPaiMwvKm+xMAWMRwJBbXtoZi2zROMYjA2N087HdMCRbs22t5C0/mDuYNCo41E'
    'PeFoBHu0pZ1MDIFssBpMLiduT9Ar0rm8Zoi8gtzIQ06WxDxSWOWumVqRV1hRkTAegRW0bC00'
    'tS0UnqttgdbmyfPF4JiNhAKtotzpA6qxBSVvLOp3qv+hKwlBJlKFH/E0twRQ5TeLniCj0I53'
    'an1U9Og8AA3AJKQxJmIiTArJ2iPH0UwCeZrEaudHhROOtUBX5RGtNsfnj3hBBkTfOHJZKBbW'
    'eps8gYAYbITmN3lgYEbpHEQ5gtVlEkbbF5+nwhgRikVRdYEItAhayACKz9XGIjjf4UR13W75'
    '04y5u2W76beA0d7aVQv+OdMCKoAZYHONrpo2YFdVQr5t49vMRm04Foz6YeQ0gO6NhUUhneQz'
    'bTm2hVYThNnfDHUZH2GCrtWyYTElJobbaUKoCmql+GSqHewKyAXLG0hTFQ5FcTCwVHS8UoHk'
    '5o2dh3S1QRyjQW1LRIz5UAMFQl4PlqttgaQhbyighREdwQCYNtPJT6epRzn3Xy3GabGGEHQ8'
    'IdQY2ss4HfZAJ4GQe8KNItVhY1tyte1+MeCLK+hWTyAGmbZgpjnBWCAwDjAJkhgJwL9xqGU9'
    'k0hZsAFt4GVg53bI8B5zX75YxkcS4jhwtzL/KsAPM7dj6SDN/ypMZ3kcWi7jaYBXAGxh/icS'
    'aLOXnTifi1YcHzZn0c8of4mMm6Hd+4A+pxPmSuQBQqcctqlTpnkd8G0A/1wi533GkoSyINy2'
    'OLnMuBvDUdaA6zBbRNtpF+BNEQRXIIR43CHvg8vfcj21Y053zsrtrh/a764aVbjbpfp+4Z1n'
    'jV9t188sqUU/IR0A2nxYwcASZj/AOlzKuE5k/+I5nHMAFv6klXyYpR8zQcaXyXjC72S8ok/G'
    'Vx6luGNJXj7iXWd4KV7yl1spFv/8Z8TaunWKAlyVt5ZaEK9ffW4z4AmfrOXWA171q21b9wA2'
    'XvTN3Aw36dhvae/Ld5N1e5dON7a6yY5Jjc0vPeImjhvmHJjxoXvCdU/PO//xMwurvvhoz4Fz'
    'ywtvfKdyxBuHOwrjNd/atffWJ99aazc8uOGdC78t4x0PHin898j79Xfeet0baVsuH+sOPerL'
    'GbEj/YRNZ+WfHUx9+unNNxV/853rM+Vka6X+fO670rbFM+0vRq59f/KZsRMlB3uRtBIvrAei'
    'JI/46AgjsJioC3jrUHW0gHKqa4gFvSQpiJS43YI2p2RS7TitzjAeZnWD/pewX8L+y8L+f/ll'
    'xZ8DXT2VpCxISzkvQ6XC/Rn4wWKE/b+VJEqTr0lbqig4LbXqjxn4oiUXguws/s2UhPji5cpl'
    'qs5URXN6X/72/FfykbxETVV5gD5glqTfJ9LnL1e6l6nckGJeQopyNSsfj0tlGCTpgsQ6zJLr'
    'gPG4gTwb4s9LjJ8ux5+obbksbxWkW0uGqfvC5LojbQVAFOh/m0g/96fLifMwAOkaqeGrmaCY'
    'rckB5NZAukLGw60QTwZ4kq/JWK7M12QuU+VrsjpTizRzFAs0s0o1c6aka7Ly+zSZ+ds1Gfmv'
    'aNLy/6hRFSOflkBa/JB01ChJ7ylPmE9HSrVmnhvQZZp56FNUyPhKzbwyQGVyZKWMfr5vWDT1'
    'p9OVnST5sGhmku8yTRQrP/0nCnIPw7BS9VWaFkwYHUhRCWj2yXObEfctoHkfnzWVK3yttdMs'
    'SY8xOelUuDXaaelA0adRFakVlcxZAB1nxP4H+gqLJC1g9Muw3zqVbg3XpMkt13BlmtzJGs4P'
    'pfRBwu0oNlRuopBuNaSLJacr0mgnabLLNNoyTXapRluhyYbi6zWZbhoCf90JOWE+W7G+kM/r'
    'LJ/lmM8ylJtOVammV6W4WrNB5QYHSF8mpMyAlGnQYlWh2s9iwHaGsQRyV2Nlch5vtyKftXai'
    'ulZ2ECtEc0C7Bmg3DZRZgmW6sUzFjelQSDErpEKdWCTyNwBps3lJSlEO4dcExWvpg20rwXGB'
    'i5e1QHfMJkl4IptMSW5fUZwpJUPaVqCWw2s0o+HvZOqeTf9OpyHuIfTljB6KwH0fmdBJpXZJ'
    'euFEY7FYsy51imZtqhtw7TBCWqaerVlNY+cALgUMKmBtahk6KjQraIyimjmU01IgDolqWZaK'
    'aSxqDgtACxa/TQH1anFJkif9hPXapVS0a7YpJ4GjBfBEwLWaHcDeXcpmhmdAuJuGD+JKwDHN'
    '5gF/GWCPppf6GxieD3gSzUf2zwZcBHgay7dMs5Pi0mEZchWLVcyIO1azdIr1rMBKCIjjeIal'
    'gAPMXzuk4nE6xNiAeuafzPDlgLGC05k/wio+h2FFKXPEC1A0DGHJQIDiiiHMmDKk0TPjWbgZ'
    'QVs8ZTGj8AKejAFFCfxz0+rJBLGB2shY1hNrRxByqESSLkqJj8tSTVUtG5b56laN1a2pmgt+'
    'dx+yWdEAzuI+HN6z5DBcsEBavBX27VJJ6jv1xDKdovBrVoNmBEcNOEBcU2o0ayiuBOxmfneC'
    'v0mziuJy6HT3kE6vVCu+BLJyiL6MkStWalZQhzJbwYJmxKPaNWspbRngSUhiSonX5nlWG6TN'
    'h+h8cE9PcCeGT0lwz4pnsAQCptDqxolkHGUEMLaALfiN6o4pkrTx9BPyqCNrlubYKJj+siqG'
    'FXPFLZqjo2B+yVJq0zWHqCuiOTKqFHA94MmAFQWQA0Z4IQCxopxlqXQoWBrl31MYEfQEc2xl'
    'Dtkm2QR1XVIvST2oFys02hlsplBN12gx/m2IPwLxJRhfTWcVOZ7ZNEcgfoxXkuyyXZM5Ox3n'
    '1ik0Hu2hLJCTQoh3yfHadjbf5EJ4AMLfSxmiv7WNoL6LmPouViui4HUzb6lasYeW76V/1+D8'
    'B/moREnCOz1OwOv9CsVGzU4YUftBaeyjuIxhxR0sQpmSotklBy0eRgQnqiPxFLshunRItFsd'
    'YtFehgluw8sE/ZrmlyQva3tVOgrz5eloAExiE8XJbEYrpOflvpFtzXrZ1hzDbMYZED8z0RYt'
    '7FTG0v/YB0SFOPfhoSR8vrgK6LJPYuuPYWUu8bP+GrD107HQSjWztzlme6wFuluGlO2Xyy5V'
    'B2RHhdpLMdYD7whOu1K+hPKn6jGa1ePQ0Lbldypx7qe2A+QzPXH94GY2+/MJNjtwAHnUBDBn'
    'riR9oTjexp+UQF6sVj6iSPDnq09cxxxWx6PNknTVcGsH32A+yNdS7MKgJLUy+yPOL8VlMp+w'
    'rFxmq9wHdCuG8HWqTFaiVkyTXRPVlB7XRaqQJH0yhD48QH+N7AJD82T9/mgocV1TeFrqDNrv'
    'bnXZoMxhv28CuuLE8oqWKTtV87HBlNDK1nj7gO5RMkwf9SQweaK6dNAjl4E6tKpFkpZh2gWY'
    'thL6fpmytFN1VbwQlCe8jnIz0BlYu7BP8EqLAxB2zUC5lVRuPHG5VBQNyCNub7deJUnxVT7K'
    'Ne609kHY9YllF2D7WtL73NuZXBQOtBX5MQPS9EKazYllsrbOpqnk1oHZ78aWu2VvqVqWkRL0'
    'nKhvMlnfaMMnHzdxWiPQpv3MsT4aaCuO60tFfryBJ1s754SHyN7wfUz7BsvpDyeO6ULaNyG5'
    'b8rVl1FM+wF1RXZEksKJY7wA83Z3pirHpwwZpjQNylpTdIheGHyu8NHQNFgn/Ird6pgkTUzS'
    'M7AI6VQF0uNMKJefW+AuxM1Au5wcl38x5D8rWfPIPMK7HbNaJWl96vE6QvlkorYpUGMZLUC/'
    'er4kjVUNbTcUoTxdObQNqN/ugzSrrpWkSSfXh1UJviI129+Dd1us6wCbTjlcu5TFKUldCRPk'
    'kOcz2E784FbuEknaPYQ3xctUJVCLLYljHNs5A+j3LcE7hRPbSWVv8gDfJ6qb4k6UV7zXwbpU'
    'kv50fJq+OB3q2U1Atx/omobjx90JtS9VNyf5FMnsoa8dgCeq5ZJUmzpcf/xdOZQXmMYIabjr'
    'wGZSJ/NiIu30nYnsdKt/Yoxq4+P5VklyDpkH2+WhIssY6kugiQ43D9UniRjND+eYdbcmPksr'
    'OS21dvB5Hp5f2wjxL6YMJ7MpiV15wrrPiOuH2ySp96yEfMpYPtcqkqSqLWke9iX4ytTKA6ok'
    'sT9RmXZWZvRxSTpjmLq3JA0FxXNDxyumnYXvuTdKkiORl+0//XzPGE8L6XYN0SPFnaqFA/IM'
    's/EVA55SNfZvDdrQkK6Avk4anL/rWf/mMF2ofUKSPkzUm7Ke7krUUdh3+I2ZA0BbP8QeqJF1'
    'bJG6UXaUMXsDL3Ie86Qk/S0x77jsXJssqVhfPLP8NtA/kkjflp6k8/C6qTlPSVJwOPnxJMtP'
    'Lps/122SpNXDPT+el9xLSE/numckKXe4/EclTw1j2F0Hmc9KUnkST5YpC6Fv4uyjz7DRHgA6'
    '9TB2onJ1SrKqmJCsSbEftZAu7TlJ0qUMM+4VK5MSzEny1SbnXTToQ1tqHuR7GPL9fLjxfXdS'
    'Rq2JzM1muvC+LZI0LjGt+NPyHNc7a7cMsQdB7zQxuTzZumUVpM1NTNvw02XG7ekVkC4wRHZb'
    'ZZF1q+fKhecwe3rnlsHnjMfRsgF0snoehTxOT8rjp+sZt5v2Q7rVienq0+VRqPD+MV5Hahs+'
    'z8b2EP0XTuioGqaTFwDtacPNWQuSZhlFeZJKn5oUWZaQ78nakL11iFxckS4PmuLBuQDbMGYr'
    'frR5GNkrTBJb2macg7YBvX84+lmD5Ceq2wRWt8Nbh+gPWd/dM6DvitSXJbgnJahVWo85mM8f'
    'JGn2cHptUkK10a7A+RNoxwynfx5ONn4qk5pcPGjcE5mveCO+8UVJqj5eVxefzKYesN97B+33'
    'bGZTcBCWn8jT+XKhOPdUYXshvuM4O779RCyiZeEacwOkG/Ez1xXXJdTr59Av6JU/XPHL7z/r'
    'F+QU9DXkKQxywF8IMAcgs5KQDljI7RJAB4K7dzLIHuB1AO0QvxzgNoBHAF4AeAPgA7zwHHJU'
    'gDGtIqlkRHyjIO4ydwdCEbEqHGr1+8QwKRQj0XCoXd5sWYVbNcuC/qjfE0gImSp6RX+rmBBS'
    'Pb3QE/VUi0EfiyPoTiRI8uIe3Phe8l9+8m/eBMWAe2eZgmQXKMihhLAZExVkFYQF8gfDtkFY'
    'pltBdiWEvQ1hUQjbmxCmLVeQBW7FsOX2Ii3AYYAfATSFCnIBgBlgEsCVAAsAVgKsAXgA4BmA'
    'twA+BfgRQFOkIGMAcgEEgAqAWQBhgFUAawE2AGwqkuvQC3gXwAcA/QBHAI4BqIohL4CzAH4F'
    'kAOgBxAACgAmAtQA1AMEAFoBOgBWAqwGuAvgYYBNAH8A2AHwNsAHAP0AR4rl8n8EfEYJ1BnA'
    'CTAVwAcQBegAuB5gHcAzAC8D7ALYD3AYgJQqSBbARQB6ACdABcBMgCDANaVyGSsZXgP4AYDN'
    'ANsBXgfYB3AI4HsABfS1BuBcgBwADsAOUAxQVab4pY/+Q/poVEqhGBCjojsM6tLrCVSzfb7G'
    'FHqCYmgweTylOCyKFf76sCfcThoVJWK0whOJFoXDoTAh89FfGfLFAmKpJ+gLiGB6XDM0bDoh'
    'D2NYVdjf6omiDm/wB8SyYDSfbDw+vDqK26DzcUVC40JetkWZEJOyJBCq9wTyA4GQl9iZD+uH'
    't4TKvoqQdy4wlvlqgwHqn6tk04P/6uNa3q0sixQWuKsrRI+voD0qFsEy+k9K8LUeR0reUFaE'
    'PD7GDajjj8rKWCDqx2Q1oekwM7mbPGHSkFodEMUW0pVaE4hAI6bh/mHyXGryrmhCtqYm7q0m'
    '5J+p8SxqQgP5krQR06Ea4gm49JsRAZgJvc0t4L5YdrdgzS6Ju4P5ZDx1B0RwkiJSV9dcX+eN'
    'heuaPdDS60mdpznSWCfO80ON3iR1YjgcDBGiT6nDfbUgEs3EDO5QPSG3pdRRdn6iqIvJjL1T'
    '6akPhaPkAaUnGgJT/mElcAs7hzyrbPDijE3Iy8oG3NpPXlE2tMSiXrJT2UB77F1lQxs2jBCF'
    'CndmB0RvKNgKloSqmeWhUTWLzdA0QkZSV0s77pUAV3MIpu7T0RURodYXqMKinOQSFQTImRGz'
    'ChnggXgrdTWBvPIqmVmEONElylI8QSWzhxA3uoJIIKpaG1qAx9EGQq5VtXkjNH4icTeJ3rlT'
    'PT5/qCAWjaJMTJVNE3fA31If8oR9hGwhhSBqocaC0LyyoHxmpsoT9jTnk16IibTgUQpmV0CP'
    '/JMUNbdE2xPSf0OKgngyaro/6Au1wdoT/D45S1KVAgJVGGgsi4rNUHaCr0acByPKpABpDjTK'
    'laOVFSHHWkW5PxCo8TeD6bRSwcqG6uWT9YrJ0DmDhT+uqBLFuYO1e0pRFYpEB/2XKKvF6AA5'
    'mlOECBiWVAsnhhSHvDEYtTF0y0WTNUo8ZuaOhSPI9zupL97KF5Q1YU8wEgAhHzC6/qKsbfFB'
    'QJzmmLItIvdKPrmZ4FGGEjlvoiXTq/PdAdETjEHv6tCXpKpmYkg1Hl/B+NWk3o+H3m4mVEgj'
    'IMsgSLeTRjGKpz/q24N4qOWOuJ/67idNwNIIeYD4g2K0Do88kAdldzAa8pCHiT/kjQZYXo8R'
    'CGyKkMcJdH8rHqoiZCOJgO7Fkf8kwQM6URhnTxOWgO4PV/yXQnnR1ElFFQY9ta9xnQZh/7eg'
    'OdLqDUflmuSA//8k1FYXTY232g7+6WWTKivpaSRYI4P/fwemV+vrBjn63/yXOXh2G88frar9'
    '5Sz3L79ffv+9x7y8v+9UTsdN4Eq4Ri7MPca9x33Cfc0R3Rk6s65cN003R7dYd6PuLt39uq26'
    'Pt3rutP15+qj+s2GQ4YM4xnGC4w5xjyj2eg0VhlnG/3GFmO78UHjk8bnjNuNatN5plzTZtNL'
    'plPM5eaPzd+b/2G52fqAdTw/n+/g1/Fq4SLht0KhUC6sFu4Q7hGeF/4ifCj8XTgsfCek2E6z'
    'nWE7x3axjbfNsHlsjbZ5tt/Z7rU9anvf9rHtS5vabrVPsc+019kX2G+2P2Af5TjfcZFjvGOi'
    'Y5qjznG94zbHXY4Njt879jq+dmidFme5c4rzCmfEea2zx3mL82Hn087XnR87P3MqXemuUa7z'
    'XHpX2LXYdb3rdtc9rhdde1z7XF+58KXIHHwoxo3gzuBGc2O4SzgzZ+dKucXcDdxT3Evcv7kz'
    'dbk6p+4KXaNunm657mbds7rvdNn6dfoH9Pv05YZ2483GO4z3GV8yOkx/ML1mest0wPS5iZgv'
    'NNvNlWavOWC+0fyw+QXzTvM+8wHzIfPX5u/MKsvZlgssegtvcVvKLZdb6i3NlohlvqXHssEy'
    '2jrGWm6ttjZbd1jftR6yKvlL+Dr+Ob6Pf4P/kP+S/xefLmgFu5AvTBW8wpVCizBfuEnYLnwk'
    'nG+bbKux+Wz32Tbb3rL1247YNPZL7Tr7RHu9/Ur7YsrDR+wb7X32XfYP7Z/aFY6pjpkOr2O+'
    'o9NxnWOd41HHVkef41XHu46PHf2O7x0K50jnWOcEZ5mz0XmVc75zqfNG5wbn487Nzj84tzl3'
    'Ofc6P3EedH7t/IFy+GzXha5LXMWuZlfMdY1rjes+12Ouv7rwIfd9VP7O4S7gTJwTZNDDBbkO'
    '7jrudu5+bhO3hXuRe4sz6Qp0JbpJunrgcYduqW6lbrXuHt0juqd1O3Wf6cr1V+kX6nv0t+sf'
    '1T+hf1bfq39d/7b+I32//pj+bMP5ht8YLjbwBpdhtsFrWGS4xbDesNXwZ8OHhlONWuM4o9WY'
    'D5I7w3gFyG7IeLWxw9htvMH4lPFF4x+Nbxr/YvzY+JnxG+MPxpGmc005JrOp0DTXtNR0i+kh'
    '0ybo0T+Z3ja9b/rUNN5cap5pbjTHzNeYl5tXmTeYnzdvg17da/4A5P5r81GzxjLaMtbCWUyW'
    'AkuJZRr06lxLt+UGy32WJyx7LH+1HLJ8ZUm3nmedaJ1h9VmvtLZY26wLrIusN1pvhRHztPV5'
    '69vWv1kV/BTez7fwi/jf8ffyG/le/hX+df4d/hP+IP8NrxDShDOFXwkXC5wwQagRRCEmLBAW'
    'C8uE64S7hUeEp4VDwlfCUWE8jKl8W5VtNoyqdttiW7ftZtvDtg9senuPXeWIOg44aqAPja5/'
    'uOSNBvjhTx93PnC9Ur9Nv9BwxHDE2GS6B0Z2r+nfJqU5zTzebDJXmSPmBeabzPeZnzPvML9h'
    'fsf8N/Nn0HJiGWG5Ato627rXejr/W76Qr+Sn8h4+xC/mb+DX84/ym0CC/8CPFi4USkFmY8IS'
    'YaVwg/CjkAbj/wLbpTadrdA2yxaBuq60PWvrs/3VdpFdD+PfYw/YF9mvt99qX2d/yP64/Tn7'
    'R3al43THGNADZke+o9zhczzoONd5idPgFJyTnZeDrLaBpN4OkvqE8zXnO86/gQZQgHye57rU'
    'ZXMVuSa5PK4rXRFXu2u5a5XrNtfdrp3AhR9c8gsXPGuTCXogn2uF8X8n9zC3mfuI+4z7irPp'
    '3LomkMwbdHfoHgId+ZHuoG603qx36ev1Qf1i/Y369fqN+uf07+n367/U/6g/xZBpOMtQYPAZ'
    'gob5hrWGhwxPGF41vGHYY/jS8INBZTzdeK7xQpBOi3GCscw42bgIpPIe48Mgl8+BVB40/st4'
    'jslgcpnmmK4yLTa9a/rEdKp5pHms+THzP0DL8pZaS9SyxtILctVv+cKSZj3Xmgd6o966zLrG'
    'eqd1vfUxkKdPrBl8Pj+Rv4z38Vfyy6EP3uT38B/wn/E/8BOEYqFeuEpoE24U1gj3Cg8Jm4SX'
    'hbdAK59lu8hmtxXYSm21tqDtOtuNtidsX9i+sX1vI/bT7KOgV6rtl9mvsN9kv83+pP09e799'
    'hOMcxwXQI+WOWsccR4PjJtDNk5wzQBs3geZYAHriC+c3Tsk5yvVr13iXBTTEta6lrptcd7me'
    'cW0H/u9xHXR96/q3S37Bh+++TJyLK+KquZlcjLuGW8Jdz93LPc5t40boztadr7tYZ9MV6bp0'
    'G3Wf6u8244Wb+G77Fs5qLeNNQqWgdJ3qOt11vsvo6nTJG1/oO2vOxl3F5eqX6m/Rf62/yHC/'
    'YYfx78Z2012mNfYi1wrXmy554yUu7sZD6eXcI6YXQZ//y5QC8p9l1povNQtmt/kKc5v5BvMa'
    'GAXvmT81/xN6g1iMFq8lZrnDss9ywHLQorSeatXw5/K/Ae6X8c38Cv56/n7ebeu1vWF7xzbK'
    'XmGfZveBTr7Vfj9uUNgol1lgeNJwzNBhfNNyOvRdNi/y60Crfw7j+XtBYUuHufJcGCuX2PQ2'
    'AXpnom2qbaat3nalLWy7Gkb4Suin223rYZQ/adtie8mGmj/fXg495bcH7UtgDN1uX29/wv6y'
    '/YBdPliH7/LSOI4zclaY8SZwhTDrVXBVHG7AxHeme7l93H7uANfPHeIOc0e4o9wxsBlUujRd'
    'hi5Tl6UbrcvWaXVjdDkwP3I6o86q69cf0h/WH9EfBb1MDCpDmiEDRkGWYbQh26A1jDHkGDos'
    'KyyrQG7XWtaBXtxg0fJj+Bw+l+d4I2/l7fwE0B2lfAVfxdfwM/hZ/ByQ3CY+APowys/jF4BN'
    'sQS4eR2/il/Nr+HXgoVxH78B9MtG0DCb+a2gLbfxO/id/C7+bX4vv4/fzx/g+/lD/GH+CH+U'
    'P8YTQQUaNEPIFLKE0UI2zKRjhBwhF7SpUbDCrDoBbJVSoUKoAt06Q5glzBF8QpMQgFk2KswD'
    'TdsBmmsFXgZSpaDvQzNAV2Rz/++af/8Dv8Vjaw=='
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
F11PAUSE_AT = 0x0023b324        # where the debugbox patch sites the blob
DBGPROC_AT = 0x001f42d8         # and the dialog procedure
DBGPROC_VA = 0x005f4ed8


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
                'hint': 'Pick v_on.exe here. To rip the soundtrack, put the '
                        'cue sheet in Source under CD MUSIC instead.',
                'level': 'warn',
                'log': ['%s is a %s' % (os.path.basename(path), kind)],
            }
            return 'CANNOT PATCH - that is a %s.' % kind, False

        with open(path, 'rb') as fh:
            data = fh.read()
        self.exe_path = path
        digest = hashlib.md5(data).hexdigest()
        if digest == ORIGINAL_MD5:
            return 'READY - press Apply patches', True

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
                log.append('%s - the credit is skipped, version and all, '
                           'everything else applies' % why)

        try:
            buf, applied, skipped = apply_selected(buf, wanted)
        except PatchFailed as exc:
            return False, [str(exc), 'Nothing written.']
        except Exception as exc:             # a bug in here, not a bad file
            return False, ['Patching failed: %s' % exc, 'Nothing written.']
        if 'credits' in applied:
            stamp_version(buf)
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

        # The banner's tile indices go in the executable and the tiles go in
        # escrgame.bin. One without the other draws the old artwork through
        # the new table, which is worse than not patching at all - so check
        # the file is there and writable before anything is written.
        writable, why = self._folder_writable()
        if not writable:
            return False, log + [why, 'Nothing written.']

        if 'padxinput' in applied:
            ready, why = self._banner_ready()
            if not ready:
                return False, log + [why, 'Nothing written.']

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
            return False, log + [
                self._why_unwritable(os.path.dirname(self.exe_path) or '.',
                                     exc, os.path.basename(self.exe_path)),
                'Nothing written.']
        log += ['  %s' % BY_KEY[key][0] for key in applied]
        log.append('Wrote %s' % self.exe_path)
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
                    log.append('Kept the patched %s as %s.patched'
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
                log.append('Restored %s' % name)
            except OSError as exc:
                log.append('Could not restore %s: %s' % (name, exc))
        if not log:
            return ['Nothing to restore - no backups found']
        return log

    def _folder_writable(self):
        """Can anything be written beside the game? Returns (ok, why not).

        Checked before the backup rather than discovered halfway through.
        The advice depends on why it failed, so the reason is read off errno
        rather than pasting the OS message into a sentence about permissions
        - "cannot write here (No such file or directory)" helps nobody."""
        folder = os.path.dirname(self.exe_path) or '.'
        probe = os.path.join(folder, '.vo-patch-write-test')
        try:
            with open(probe, 'wb') as fh:
                fh.write(b'x')
            os.remove(probe)
        except OSError as exc:
            return False, self._why_unwritable(folder, exc)
        return True, ''

    @staticmethod
    def _why_unwritable(folder, exc, name=None):
        """Turn a failed write into advice.

        Windows is checked first, because it folds several different causes
        into EACCES: a write-protected drive, a file another process has
        open, and an actual permission problem all arrive as errno 13, and
        the answer to each is different."""
        elsewhere = ('Copy the game folder somewhere you own - your home or '
                     'Documents - and patch it there.')
        # The caller passes both, rather than this guessing from the path:
        # splitting a Windows path on a Linux box gets it wrong, and the
        # sentences need the folder in some cases and the file in others.
        name = name or folder
        win = getattr(exc, 'winerror', None)
        if win == 32 or win == 33:          # SHARING_VIOLATION, LOCK_VIOLATION
            return ('Something else has %s open. Close the game and any '
                    'launcher or anti-virus scanning it, then try again.'
                    % name)
        if win == 19:                       # WRITE_PROTECT
            return ('%s is write protected. If the game is on a mounted '
                    'disc image, copy it to your hard drive first.' % folder)
        if exc.errno in (errno.EACCES, errno.EPERM):
            # Deliberately not "run as administrator": that writes a backup
            # and a log the player then cannot delete, and Program Files is
            # the usual cause.
            return 'No permission to write in %s. %s' % (folder, elsewhere)
        if exc.errno == errno.EROFS:
            return ('%s is read-only. If the game is on a mounted disc '
                    'image, copy it to your hard drive first.' % folder)
        if exc.errno == errno.ENOSPC:
            return ('No space left on the drive holding %s. About 7 MB is '
                    'needed for the backup.' % folder)
        if exc.errno == errno.ENOENT:
            return ('%s is gone. Has the folder been moved, or a drive '
                    'disconnected, since the file was selected?' % folder)
        if exc.errno == errno.ETXTBSY:      # the same thing on Linux
            return ('%s is in use. Close the game and try again.' % name)
        # Anything else: report it and stop guessing at the cause.
        return 'Cannot write in %s: %s.' % (folder, exc.strerror or exc)

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
            log.append('%s Credit line skipped' % why)
            return False
        if not all(stock for _p, _d, stock in found):
            log.append('Credit line already in place')
            return True
        (cg_path, cg, _s), (mp_path, mp, _s) = found
        for path in (cg_path, mp_path):
            if not self._backup(path, log):
                log.append('Credit line skipped')
                return False
        cg += b''.join(CREDIT_NEW_TILES)
        at = CREDIT_CELLS_AT * 2
        mp[at:at] = b''.join(c.to_bytes(2, 'little') for c in CREDIT_CELLS)
        for path, data in ((cg_path, cg), (mp_path, mp)):
            try:
                with open(path, 'wb') as fh:
                    fh.write(data)
            except OSError as exc:
                log.append('Could not write %s: %s'
                           % (os.path.basename(path), exc))
                return False
            log.append('Wrote %s' % os.path.basename(path))
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
            log.append('Could not read %s: %s' % (ESCRGAME, exc))
            return
        if len(data) != ESCRGAME_SIZE:
            log.append('%s is %d bytes, expected %d - left alone'
                       % (ESCRGAME, len(data), ESCRGAME_SIZE))
            return
        if not self._backup(path, log):
            log.append('%s not patched' % ESCRGAME)
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
            log.append('Could not write %s: %s' % (ESCRGAME, exc))
            return
        log.append('Wrote %s' % path)

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
            log.append('v_on.ini left alone - the gamepad profile may not '
                       'work until you delete it by hand')
            return
        try:
            os.remove(ini)
            log.append('Removed v_on.ini - the game will write a fresh one')
        except OSError as exc:
            log.append('Could not remove v_on.ini: %s - delete it by hand'
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

# The version is in the title because it is the only place a Windows
# user who double-clicked the exe can see it, and it is the first
# thing worth knowing about a bug report.
TITLE = 'Virtual-On patcher %s' % VERSION
INTRO = 'Select an unmodified v_on.exe.'
MIN_CONTENT = 520               # px; narrower and the hints wrap badly
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
NETPLAY_IN = 'Installed. Both players need this, and the same patches.'
NETPLAY_UPDATED = 'Updated to this build.'
NETPLAY_OUT = 'Original dpctrl.dll restored.'
NETPLAY_OLD = 'An older netplay DLL is installed. Install to update it.'
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
FAILED = 'Nothing was written - see the log below.'


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
            self._openers = {}
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
            # Collapsed: none of these is part of patching, and open they
            # push Apply below the fold. A header you can see beats content
            # you have to scroll to find.
            self._section(body, 'ADD-ONS', self._addons_body, expanded=False)
            self._section(body, 'CD MUSIC', self._music_body, expanded=False)
            self._section(body, 'LOG', self._log_body, expanded=False)
            # Last and closed: the version and the link are reference, and
            # the one tick box in it is not a fix to anything.
            self._section(body, 'ABOUT', self._about_body, expanded=False)
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
            # A floor as well as the content width: with the long sections
            # collapsed the window would otherwise shrink to the widest
            # checkbox, and every hint below it would wrap to three lines.
            wide = max(MIN_CONTENT, self.inner.winfo_reqwidth())
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

            def toggle(_event=None):
                set_open(not inner.winfo_manager())

            for widget in (head, arrow, name):
                widget.bind('<Button-1>', toggle)

            self._openers[title] = lambda: set_open(True)
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
            self.rows_wrap = wrap
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
            # Some refusals have nothing to compare - a cue sheet has no
            # business being weighed against the executable's checksum. Hide
            # the table rather than leave the last file's numbers under a
            # message about this one.
            if report['rows']:
                for row, (name, size, digest) in zip(self.rows,
                                                     report['rows']):
                    row.config(text='%-10s%11s B  %s'
                                    % (name, '{:,}'.format(size),
                                       digest[:12]))
                self.rows[1].config(foreground=colour)
                self.rows_wrap.pack(fill='x', pady=(8, 0))
            else:
                self.rows_wrap.pack_forget()
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
            self._link_row(parent, *DDRAW_LINK)
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
                    self._log('netplay: original dpctrl.dll restored')
                else:
                    updating = self.net_state == 'old'
                    install_netplay(gamedir)
                    self.net_note.config(
                        text=NETPLAY_UPDATED if updating else NETPLAY_IN,
                        foreground=PALETTE['ok'])
                    self._log('netplay: UDP dpctrl.dll %s'
                              % ('updated' if updating else 'installed'))
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
            if hint:
                _hint(parent, hint, self.dim, self.small, pady=(0, 8))
            state = default_state()
            for key in keys:
                label, tip, _sites = BY_KEY[key]
                row = ttk.Frame(parent, style='Card.TFrame')
                row.pack(fill='x', pady=3)
                var = tk.BooleanVar(value=state[key])
                check = ttk.Checkbutton(row, text=label, variable=var,
                                        style='Card.TCheckbutton')
                check.state(['disabled'])
                check.pack(side='left')
                Info(row, label, tip, self).btn.pack(side='right',
                                                     padx=(6, 2))
                self.vars[key], self.checks[key] = var, check


        def _about_body(self, parent):
            ttk.Label(parent, text='vo-patch %s' % VERSION,
                      style='Card.TLabel', font=self.bold).pack(anchor='w')
            # Without the scheme, which is nine characters of nothing and
            # makes the line wider than the card wants to be.
            short = REPO_URL.split('//', 1)[-1]
            link = ttk.Label(parent, text=short, style='Link.TLabel',
                             font=self.small, cursor='hand2')
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
             'title banner: %d tiles (%d spare), %d bytes of %s'
             % (BANNER_UNIQUE, BANNER_SPILLED, BANNER_UNIQUE * 128, ESCRGAME),
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
            updating = netplay_status(gamedir) == 'old'
            install_netplay(gamedir)
            print('Netplay dpctrl.dll %s. Both players need it.'
                  % ('updated to this build' if updating else 'installed'))
            print('Matchcode needs no forwarding; direct IP needs UDP 47624 at the host.')
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
