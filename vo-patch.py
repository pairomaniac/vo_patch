#!/usr/bin/env python3
"""Virtual-On (PC, 1997) patcher. See README.md.

    python3 vo-patch.py                 patch a copy of v_on.exe
    python3 vo-patch.py --install CUE DIR   install from a disc image
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
         (0x001c5900, 'a1405fae01', 'e893d80500'),
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

# The patches a lockstep match cannot differ on: the frame rate and the
# round-loss fix change what the simulation computes. Both are Essential and
# always applied, so this patcher cannot produce a build missing them -
# SYNC_SITES reads them back out of a file an older release may have written
# without, and net/dpctrl.c fingerprints the same two bytes.
SYNC_KEYS = ('framerate', 'continuefix')
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


# Returned by music_status when it has no folder to describe. The window
# treats it as a tag and swaps in its own prompt (INSTALL_NEEDS_TARGET), so
# the wording here is only ever seen by code.
MUSIC_NEEDS_EXE = 'Pick v_on.exe first: the tracks go beside it.'


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
        self.ini_name = (option.get('inifilename') or 'V_ON.INI').lower()
        # Whether a language directory is copied at all is a property of the
        # pressing: the retail discs say so in Select1 and keep the help
        # files in english/, the OEM disc does not and keeps them in v_on/.
        self.wants_language = 'langexeclusive' in option.get('select1',
                                                             '').lower()
        self.languages = [name for name in ssp if name not in NOT_A_LANGUAGE
                          and (not self.wants_language
                               or ssp[name].get('langexeclusive'))]
        self.default = (option.get('defaultsection') or '').upper()
        if self.default not in self.languages:
            self.default = self.languages[0] if self.languages else ''

    def language_dir(self, language):
        if not self.wants_language:
            return None
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
    probe = os.path.join(folder, '.vo-patch-write-test')
    try:
        with open(probe, 'wb') as fh:
            fh.write(b'x')
        os.remove(probe)
    except OSError as exc:
        return False, why_unwritable(
            folder, exc, elsewhere='Choose a folder you own - your home, '
                                   'Documents or Games.')
    return True, ''


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
    try:
        free = shutil.disk_usage(probe).free
    except OSError:
        free = None
    if free is not None and needed and free < needed:
        return ('Not enough room: %d MB free, %d MB needed.'
                % (free >> 20, needed >> 20)), 'bad'
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
NETPLAY_SRC_SHA = '60b314889c5355be7fbca8f5981c00d9651e158a4f6dea4627c4995796dd7a1c'
# sha256 of the compiled DLL, so the patcher can tell its own build
# from an older one already installed.
NETPLAY_DLL_SHA = 'c4293bcc57e5b537f19bb18a3947c8f0103b177ffe18e13993098ff07e367bf2'
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
                        'under DISC, which installs and rips from it.',
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
                _note(self._why_unwritable(
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

    @staticmethod
    def _why_unwritable(folder, exc, name=None):
        return why_unwritable(folder, exc, name)

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

# The version is in the title because it is the only place a Windows
# user who double-clicked the exe can see it, and it is the first
# thing worth knowing about a bug report.
TITLE = 'Virtual-On patcher %s' % VERSION
MIN_CONTENT = 480               # px per column; narrower and hints wrap badly
GUTTER = 14                     # px between the two columns
NO_FILE = 'No file selected'

FILE_HINT = ('Installing above fills this in. Browse for it if the game is '
             'already on your disk.')

INSTALL_HINT = ('Installs the game and rips the soundtrack, from a disc '
                'image or a drive.')

INSTALL_TIP = ('Source\tThe .cue sheet beside the .bin files, not the .bin.\n'
               'Install game\tThe game folder, about 95 MB.\n'
               'Rip soundtrack\tmusic\\track02.wav onward, about 320 MB.\n'
               'Manual\tWhich readme and help file is copied. Every '
               'pressing carries one v_on.exe and it is English, so there '
               'is no translated game to install.')

INSTALL_PICK = 'Give the .cue sheet, not the .bin.'
INSTALL_NOT_CUE = 'That is a %s. Give the .cue sheet beside it.'
INSTALL_NEEDS_DEST = 'Choose where to install it.'
INSTALL_BUSY = 'Copying\u2026'
INSTALL_NEEDS_TARGET = 'Choose a folder above, or pick your v_on.exe.'
INSTALL_OK = 'Installed %d files to %s'
INSTALL_DRIVE_ONLY = 'A drive can only be ripped. Give a .cue to install.'
INSTALL_NO_PATH = 'There is nothing at that path.'
INSTALL_NO_DRIVE = 'No such device.'
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

ESSENTIAL_HINT = ('Always applied. Each of these fixes something that is '
                  'broken on a modern system, with nothing to weigh up.')
EXTRA_HINT = 'Optional. Untick what you do not want.'
# Granularity of the hint wrapping, in pixels. See _hint.
WRAP_STEP = 8

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

MUSIC_HINT = 'The soundtrack, to music\\ beside the game. About 320 MB.'

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
        """The quiet explanatory line under a section heading; most of the
        cards have one and they only differ in their text.

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

        last = [0]

        def fit(event=None):
            # The width comes off the event rather than from winfo_width:
            # tkinter has already unpacked it, and asking the holder again
            # is a round trip into Tcl on every step of a window drag.
            #
            # <Configure> also fires for position and height, and only a
            # width change can alter the wrapping, so the rest return here
            # rather than relaying the label out for nothing. The write
            # itself goes through the Tcl call directly - configure()
            # marshals a dict and re-reads the widget's options first, and
            # this runs for every hint on screen for every pixel dragged.
            width = event.width if event is not None else holder.winfo_width()
            if width <= 1:
                return
            # Rounded down to a whole step. Re-wrapping is the dearest thing
            # a resize does - Tk re-measures the text and lays the label out
            # again - and a drag delivers an event per pixel. Rounding down
            # rather than to nearest matters: the wrap is then never wider
            # than the space, so text is never clipped, only wrapped up to a
            # step early. One step is under a character.
            width = max(140, (width - 2) // WRAP_STEP * WRAP_STEP)
            if width != last[0]:
                last[0] = width
                label.tk.call(label._w, 'configure', '-wraplength', width)
        holder.bind('<Configure>', fit, add='+')
        label.bind('<Map>', lambda _e: fit(), add='+')   # a collapsed card
        #                          gets no Configure until it reopens
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
            self._corner_cache = {}
            self._bodies = []
            self._openers = {}
            self._rip_thread, self._rip_dir = None, None
            # What _fit last wrote, so a resize that changes nothing costs
            # nothing.
            self._last_width, self._last_region = 0, None
            self._last_status = (None, 0)
            self._cancel_rip = False
            root.title(TITLE)
            root.minsize(430, 0)
            root.maxsize(root.winfo_screenwidth() - 40,
                         root.winfo_screenheight() - 60)
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
            left, right = body
            self._section(left, '1  DISC', self._install_body)
            self._section(left, '2  GAME FILE', self._file_body)
            self._section(right, '3  ESSENTIAL PATCHES',
                          lambda p: self._patch_body(p, ESSENTIAL,
                                                     ESSENTIAL_HINT))
            self._section(right, '4  EXTRA PATCHES',
                          lambda p: self._patch_body(p, EXTRA, EXTRA_HINT))
            # Separate, because these are not patches: Apply never touches
            # them and they write files rather than bytes. Collapsed,
            # because open they push Apply below the fold.
            self._section(right, '5  ADD-ONS', self._addons_body,
                          expanded=False)
            self._section(right, 'LOG', self._log_body, expanded=False)
            # Left, and closed: the version and the link are reference rather
            # than patching, and the left column has the room. On the right
            # it fell below the fold the moment the log opened.
            self._section(left, 'ABOUT', self._about_body, expanded=False)

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
            # With two columns the window has to fit the taller of them, not
            # the sum: the grid puts them side by side and reqheight already
            # reports the taller, but only once both have been laid out.
            full = self.inner.winfo_reqheight()
            # Width has to come from here too, for the same reason: a
            # collapsed section still has to fit when it is opened, and the
            # canvas forces its content to the window width rather than
            # scrolling sideways, so anything wider is cut off. The CD music
            # row is the widest thing in the window and starts collapsed.
            # A floor as well as the content width: with the long sections
            # collapsed the window would otherwise shrink to the widest
            # checkbox, and every hint below it would wrap to three lines.
            wide = max(MIN_CONTENT * self.columns
                       + (GUTTER if self.columns > 1 else 0),
                       self.inner.winfo_reqwidth())
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
            screen. Returns the two column frames to build into."""
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
            # How tall the window may grow before the content scrolls
            # instead. The screen is the real limit; the line count is only
            # there to stop a very tall display giving a window nobody wants
            # to drag. Sized so that a 1600x900 screen at 100% shows every
            # section with the log open, which is the state people spend the
            # most time looking at.
            row = self.small.metrics('linespace')
            self.cap = min(max(360, parent.winfo_screenheight() - 150),
                           row * 56)
            self.inner.bind('<Configure>', self._fit)
            self.canvas.bind('<Configure>', self._fit)
            for seq in ('<MouseWheel>', '<Button-4>', '<Button-5>'):
                self.canvas.bind_all(seq, self._wheel)

            # Two columns need both of them at the readable width plus the
            # padding, the gutter and the scrollbar. Below that, one column
            # and the sections stack as before - a squeezed pair wraps every
            # hint to three lines and reads worse than scrolling.
            self.columns = 2 if (parent.winfo_screenwidth() - 80
                                 >= 2 * MIN_CONTENT + GUTTER + 40) else 1
            if self.columns == 1:
                self.left = self.right = self.inner
                return self.inner, self.inner
            self.inner.columnconfigure(0, weight=1, uniform='col')
            self.inner.columnconfigure(1, weight=1, uniform='col')
            self.left = ttk.Frame(self.inner, style='Ink.TFrame')
            self.left.grid(row=0, column=0, sticky='nsew',
                           padx=(0, GUTTER // 2))
            self.right = ttk.Frame(self.inner, style='Ink.TFrame')
            self.right.grid(row=0, column=1, sticky='nsew',
                            padx=(GUTTER // 2, 0))
            return self.left, self.right

        def _fit(self, _event=None):
            need = self.inner.winfo_reqheight()
            wide = self.canvas.winfo_width()
            # Setting the item width makes the inner frame resize, which
            # fires its own <Configure> and brings us straight back here.
            # Writing the same value again is what made every drag step cost
            # two full passes over the layout, so each write is guarded by
            # what it last wrote rather than repeated.
            if wide > 1 and wide != self._last_width:
                self._last_width = wide
                self.canvas.itemconfigure(self.window, width=wide)
            # Only the scroll extent. Height is settled at startup and left
            # alone: driving it from here resizes the window on every expand
            # and collapse.
            region = (0, 0, self.inner.winfo_reqwidth(), need)
            if region != self._last_region:
                self._last_region = region
                self.canvas.configure(scrollregion=region)
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
            Info(grid, 'DISC', INSTALL_TIP, self).btn.grid(
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
            ttk.Label(self.lang_row, text='Manual', style='Card.TLabel',
                      font=self.small, width=10, anchor='w').grid(
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
            self._music('')
            # Typing a path counts as picking one. The disc read opens files,
            # so it waits for a pause rather than running on every keystroke.
            self.disc_var.trace_add('write', self._disc_typed)
            self.dest_var.trace_add('write',
                                    lambda *_a: self._sync_buttons())

        def _disc_typed(self, *_args):
            pending = getattr(self, '_disc_after', None)
            if pending:
                self.root.after_cancel(pending)
            self._disc_after = self.root.after(
                400, lambda: self._check_disc(self.disc_var.get()))

        def _field(self, grid, line, label, var, browse):
            """One labelled path row. The three of them share a grid so the
            entries line up rather than each starting after its own word."""
            ttk.Label(grid, text=label, style='Card.TLabel', font=self.small,
                      width=10, anchor='w').grid(row=line, column=0,
                                                 sticky='w', padx=(0, 8),
                                                 pady=(0, 6))
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
            self._disc_bytes = 0
            self.disc_compare.show(None)
            self.lang_row.grid_forget()
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
                self.disc_compare.show(compare_report(
                    build['size'], build['md5'], build['why'],
                    'Rip soundtrack still works; only patching needs '
                    'the retail build.',
                    'warn'))
                self._disc_note('CANNOT INSTALL - %s build.' % build['name'],
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
            # A warning about the disc outranks the folder note: it is the
            # reason someone would not press Rip soundtrack.
            if self._audio_warning:
                self.music_note.config(text=self._audio_warning,
                                       foreground=PALETTE['amber'])
            else:
                # The prompt only helps once there is a disc to rip from;
                # before that it is a second cyan line saying nothing new.
                self._music(music_status(self._target())
                            if self.disc_var.get().strip() or self._target()
                            else '')

            self.install_btn.state(
                ['!disabled'] if self.disc_ok and path and level != 'bad'
                else ['disabled'])
            # Ripping needs a source the ripper can actually read and
            # somewhere to put the tracks. It does not care which build the
            # disc holds, or whether the game beside it can be patched - a
            # cue for another pressing still rips.
            can_rip = self.rip_ok and bool(self._target())
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
            self.install_btn.state(['disabled'])
            self.rip_btn.state(['disabled'])
            self._disc_note(INSTALL_BUSY, self.dim)
            self._log('install: reading %s' % source)
            self._installq = queue.Queue()
            self._install_dest = dest
            last = [-1]

            def progress(done, total):
                pct = done * 100 // max(total, 1)
                if pct != last[0]:
                    last[0] = pct
                    self._installq.put(('progress', pct))

            def finished(error, written):
                self._installq.put(('done', error, written))

            install_in_background(source, dest, language, progress, finished)
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
            if error is not None:
                self._disc_note(str(error), PALETTE['bad'])
                self._log('install: failed - %s' % error)
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
            if text == MUSIC_NEEDS_EXE:
                text, colour = INSTALL_NEEDS_TARGET, PALETTE['cyan']
            else:
                colour = self.dim
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
            if isinstance(error, RipCancelled):
                self._log('music: cancelled, the part-written track was '
                          'discarded')
            elif error is not None:
                self._log('music: failed - %s' % error)
                self.music_note.config(text=str(error),
                                       foreground=PALETTE['bad'])
                self._sync_buttons()
                return
            else:
                self._log('music: %d tracks written to %s'
                          % (len(files), outdir_for(self._rip_dir)))
            self._music(music_status(self._rip_dir))
            self._sync_buttons()

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
            _hint(parent, note, self.dim, self.small)
            _hint(parent, DDRAW_WINE, PALETTE['amber'], self.small,
                  pady=(4, 0))
            self.ddraw_note = _hint(parent, '', self.dim, self.small,
                                    pady=(4, 0))
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
            _hint(parent, NETPLAY_NOTE, self.dim, self.small)
            _hint(parent, NETPLAY_PORT, PALETTE['amber'], self.small,
                  pady=(4, 0))
            self.net_note = _hint(parent, '', self.dim, self.small,
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
                row.pack(fill='x', pady=3)
                var = tk.BooleanVar(value=state[key])
                self.vars[key] = var
                if key in ALWAYS:
                    # A permanently ticked box that cannot be clicked reads
                    # like something is broken. A plain line does not, and
                    # the card's own heading says these are always applied.
                    ttk.Label(row, text=label, style='Card.TLabel',
                              padding=(2, 3)).pack(side='left')
                else:
                    check = ttk.Checkbutton(row, text=label, variable=var,
                                            style='Card.TCheckbutton',
                                            command=self._retally)
                    check.state(['disabled'])
                    check.pack(side='left')
                    self.checks[key] = check
                Info(row, label, tip, self).btn.pack(side='right',
                                                     padx=(6, 2))


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
            # Seeded before the binding: the first <Configure> arrives while
            # the window is being built, and with nothing remembered it
            # would fit an empty string over the label's own text.
            self._status_text, self._status_font = NO_FILE, self.small
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

        def _fit_status(self, _event=None):
            """Trim the status line to the room it actually has.

            It used to cut at 52 characters, chosen for a 430px window. The
            window is twice that now, so the number is measured in the font
            against the label's own width instead - and on a wide window
            nothing is cut at all."""
            text = getattr(self, '_status_text', '')
            font = getattr(self, '_status_font', self.small)
            room = self.status.winfo_width()
            if (text, room) == self._last_status:
                return          # a drag sends one of these per pixel
            self._last_status = (text, room)
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
  vo-patch.py --install CUE DIR   copy the game out of a disc image into DIR
                                  (--language NAME picks the help files)
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
        print('languages: %s (default %s)' % (', '.join(info['languages']),
                                              info['default_language']))
    build = info['build']
    if not build['supported']:
        return ('This disc holds the %s build of v_on.exe (%s).\n%s\n'
                'The patches are written for the retail disc build, so '
                'installing this one gains nothing - but --rip still works.'
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
        return '\n%s' % exc
    print('\rInstalled %d files to %s' % (len(written), dest))
    return 0


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
