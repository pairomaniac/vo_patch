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
NETPLAY_SRC_SHA = 'e94dcd75b80e7e8bb472946763f25e5433465558b308ec7b0495c05910f0162c'
NETPLAY_DLL_Z = (
    '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'
    'xHerGcwEJpv5f9v8+0/4+q3M'
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
                'DirectPlay layer with plain UDP: the host gets a code, the '
                'other player types it in, and nobody forwards a port. The '
                'original dpctrl.dll is kept as dpctrl.dll.stock.')
NETPLAY_PORT = 'Direct IP is still there for LAN play; the host forwards UDP 47624.'
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
