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
    '6a016a00e8dfbe000083c4086a016a01e8d3be000083c408536a015b6bc30c05'
    '344c620050e80367fbff5a6a285985c0741f668b00662d30303c09771480fc09'
    '770f86c4d50a3c5f77073c0572030fb6c8e86e6a00004b79c35bc3'
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
         (0x001fa544, '00' * len(INIALL_CODE), INIALL_CODE.hex()),
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
         (0x000958aa, 'e900000000', 'e8954c1600'),
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
NETPLAY_SRC_SHA = 'b9ea7baeb85dac65e1effc52992ab898f2d07bc4a654e2a521a365838cb78897'
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


def _netplay_is_ours(path):
    try:
        with open(path, 'rb') as fh:
            return NETPLAY_MARK in fh.read()
    except OSError:
        return False


def netplay_status(gamedir):
    """'ours', 'stock', or None if there is nothing to look at.

    The file itself is read rather than the .stock copy taken as proof:
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
