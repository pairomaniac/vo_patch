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
NETPLAY_SRC_SHA = '4c9ba0556c003ef654b0f749f21183638987912f2bc3583a36128fa551ff7ee4'
NETPLAY_DLL_Z = (
    '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'
    'sBmZpfUIB89AnHsUG3W9zbSpk+Mew+bJkKQBHocr7EtEaB0ii0B/ZDP9N8G0kplVJRUGvWRH'
    '09gBfv+3IGP/CTcez/+VMKumZGa61HY8zymvqqyUdndiTILn/x2YU6Ov6+Po6b/Tf/9D/jDB'
    'QIeejdRepC3XztZ6tMu0q7TXaV/Uvql9V/uh9hutWneu7mIsoHfoqnQRXYtuo+5x3XO6fbq3'
    'dCr9KP0P9Qv0lxjKDdMNDYaAIWxoNqwy3G54yPCU4TlDl+EfBl44U/iecLmwXtgsaIwTjEuN'
    'zxrLTD7THPNL5lfMfzPPtdxped/ykUUpqkSfuFRcJq4Xfy3uFrvFA+Lb4p/FD8VPxC/EM6zf'
    't1qsxdZS61xrxNpu3WDdYf2N9WXrUetFNpvNbSu3+WxX2G6z3WW7z7bNtt922PYX26e2f9oU'
    '9rPs59qr7I32K+w32J+277G/ZT9mz3b8yBF1POgoKBQKWwvfKXzM+Xvn684/O2mCh+ZcdNqn'
    'tSFdq+563d26X+p26t7V/V03TH+u/kK9Qz9Lv1S/XL9Wf73+Vv3P9Vv1j+m79W/p/6Q/ov9A'
    'n2u4wHCt4TbDC4aXDO8bLEK5sFCIC1uElwWd0WYsNc4zJo2rjLcaf2l80fh747vG48Z/GL8x'
    '5pg0ptmmDtOrJt58hvk88xzzUvNq8x3m+827zR+bvzSPtoy3iJZCy3zLQ5YnLM9a3rP81cKL'
    'o0SLWCSWi4vEoHideLN4h7hFfAic2yn+XnxT/DfwLN96gdVmLbFOt86zNlqXW9dYr7febP25'
    '9X7rY9ad1j3W/dZXre9Y/2z9yPqlNct2vk1jG2cTbJW2hG2lbTU4+bBtu+0N25n279svsgv2'
    'QnuZvdY+315nX2Zfb7/T/qD91+DmX+0Ox0zHHMdljphjnWOj42bH7Y6tjscdzzr2Og45vnAs'
    'AH+vKbyn8LHCnYV7Cg8Uvl34XuE/C5XOs52is9o5zxlwLnOucm5w3ui803mv81Hndufzzr1O'
    'mmSjb/L5Wg1O47BqnZDM6dqF2gbtcu0d2q3aR7Q7tL/R7tO+oX1He1z7pfYM3Ujd+brxkFG7'
    'bppuhu5y3VrdtbrbdHfpHoas7tZ16/5N941ODVmdqLfqp+lr9Yv0jfqrUIvb9c/oD+o/1X8D'
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
