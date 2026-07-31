#!/usr/bin/env python3
"""Virtual-On (PC, 1997) patcher. See README.md.

    python3 vo-patch.py

GTK4 where it is available, Tk otherwise. VOPATCH_UI=gtk or =tk forces one.

Version 0.5.0
https://github.com/pairomaniac/vo_patch
"""

import hashlib
import os
import re
import shutil
import sys

EXE_SIZE = 6650880

ORIGINAL_MD5 = 'a464b0ff32d5bab499f265e45658504e'

# Each site: (offset, original, patched).

FEATURES = [
    ('sound_wait', 'Arcade sound effect timing',
     'Drops the built-in delay before each sound effect, so rapid-fire\n'
     'weapons sound like the arcade.', [
         (0x002bba60, '0f', '01')]),

    ('samplerate', 'Increase sound output frequency',
     '22050 to 44100 Hz. Subtle, since the samples are 8-bit either way.\n'
     'May misbehave on some cards.', [
         (0x00189546, '2256', '44ac'),
         (0x00189552, '88580100', '10b10200')]),

    ('noloading', 'Hide loading screen text',
     'Hides "Now Loading . . .". Loads are instant on modern hardware.', [
         (0x002c7678, '4e', '00')]),


    ('feiyen', 'Enemy Fei-Yen hypermode sound fix',
     'Restores the sound an enemy Fei-Yen makes going hypermode. A bug\n'
     'left it silent.', [
         (0x00058189, '01', '02'),
         (0x00170dc9, '01', '02')]),

    ('nocd', 'Disable disc check',
     'Skips the "Please insert VIRTUAL ON CD" prompt. The drive is still\n'
     'checked, so mount the image anyway if you want the CD music.', [
         (0x001c76d4, '0f840a000000', '909090909090')]),
    ('nocpucheck', 'Skip processor check',
     'The same as ProcessorCheck=Off in v_on.ini, without editing the\n'
     'ini. Takes the MMX, Pentium and vendor checks with it.', [
         (0x00107930, '830dc884bf0001', '90909090909090')]),
    ('motion', 'Allow v_on.ini to save Motion value',
     'Motion is an FPS divisor, and anything but 1/1 flickers. The game\n'
     'read Motion= and then overwrote it, so it never stuck. Now it\n'
     'does, and a missing or out of range value falls back to 1/1.\n'
     '1/1\tevery frame, the fallback now\n'
     '1/3\tone frame in three, the old fallback', [
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

    ('timer', 'Raise multimedia timer resolution',
     'Stops the game running in slow motion on Windows 2000 and later.\n'
     'Not needed under Wine.', [
         (0x001f423e, '00' * 62,
          '68624e5f00ff1504d56503686c4e5f0050ff1508d5650385c074046a01ff'
          'd0e9ce2affff77696e6d6d2e646c6c0074696d65426567696e506572696f'
          '6400'),
         (0x000000a8, '30791e00', '3e4e1f00')]),
    ('debugbox', 'Disable menu bar (Extras menu on F11)',
     'The menu bar was only ever a strip across the top, so it goes. F11\n'
     'opens a dialog in its place holding the Debug options: Motion, No\n'
     'shot, SE, CD, Kill, Scorekeeping and Quit Program. Every other\n'
     'menu was always on a key as well.\n'
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
         (0x006036b0, '0000000010002600470061006d00650000000000439c26005200650073007400'
          '6100720074002000470061006d006500090041006c0074002b00460032000000'
          '0000449c2600500061007500730065002000470061006d006500090046003300'
          '00000000469c44006900730063006f006e006e00650063007400200026004e00'
          '6500740077006f0072006b0009004600390000000000679c260042006f006f00'
          '6b004b0065006500700069006e00670020002e002e002e000000000000000000'
          '8000419c450026007800690074002000470061006d006500090041006c007400'
          '2b00460034000000100026004400650062007500670000001000440069007300'
          '7000260046006c006f006f00720000000000499c26004600690065006c006400'
          '000000004a9c260057006100740065007200000000004b9c260053006b007900'
          '000000004c9c26004f00750074005300690064006500000080004d9c46006900'
          '6c006c002600420047000000100026004d006f00740069006f006e0000000000'
          '559c31002f002600310000000000569c31002f002600320000000000579c3100'
          '2f002600330000000000589c31002f002600340000008000599c31002f002600'
          '35000000100026004b006900',
          'c000c880000000000a0000000000d40082000000000045007800740072006100'
          '7300000008004d0053002000530061006e007300200053006500720069006600'
          '00000000030001500000000010000e0038000c00479cffff80004e006f002000'
          '730068006f00740000000000030001500000000050000e0024000c005b9cffff'
          '80005300450000000000000003000150000000007c000e0024000c005c9cffff'
          '800043004400000000000000000000500000000010002c0028000a00ffffffff'
          '82004d006f00740069006f006e0000000000000003000150000000003c002a00'
          '3c005a00e803ffff850000000000000000000150000000001000460032000e00'
          '619cffff80004b0069006c006c00200031005000000000000000015000000000'
          '4600460032000e00629cffff80004b0069006c006c0020003200500000000000'
          '00000150000000007c00460048000e00679cffff8000530063006f0072006500'
          '6b0065006500700069006e006700000000000000000001500000000010006e00'
          '48000e00419cffff800051007500690074002000500072006f00670072006100'
          '6d00000000000000000001500000000092006e0032000e000200ffff80004300'
          '6c006f007300650000000000')]),
    ('continuefix', 'Fix crash on round loss',
     'Stops the crash when you lose a round as Temjin, Viper II,\n'
     'Apharmd or Raiden.', [
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
     'Adds a Gamepad (XInput) device profile that binds all twelve\n'
     'actions from the F7 screen, for both players. Pad 1 drives 1P and\n'
     'pad 2 drives 2P; the keyboard keeps working alongside it.\n'
     'A is accept, Select is camera, Start is pause.\n'
     'Retires v_on.ini, which the game then rebuilds.', [
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
         # device list: Keyboard only, Gamepad (XInput)
         (0x0026c218, 'ecd36600d4d36600c0d36600b4d3660090d3660080d3660068d3660058d36600',
                      'ed4b6200fb4b6200000000000000000000000000000000000000000000000000'),
         # condition table, input list, strings, then the routine itself
         (0x0022411b, '0000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000',
                      '02000000001000000200000000200000020000000040000002000000008000000200000000010000020000000002000003000200400000000300030040000000010006001027000000000600f0d8ffff00000400f0d8ffff010004001027000001000a001027000000000a00f0d8ffff00000800f0d8ffff0100080010270000'),
         (0x00223c43, '0000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000',
                      '9b4b6200e00000009d4b6200e10000009f4b6200e2000000a14b6200e3000000a34b6200e4000000a64b6200e5000000a94b6200e6000000ac4b6200e7000000af4b6200e8000000b54b6200e9000000bd4b6200ea000000c54b6200eb000000ce4b6200ec000000d44b6200ed000000dc4b6200ee000000e44b6200ef000000'),
         (0x00223f9b, '0000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000',
                      '41004200580059004c42005242004c54005254004c53205570004c5320446f776e004c53204c656674004c5320526967687400525320557000525320446f776e005253204c656674005253205269676874004b6579626f617264206f6e6c790047616d65706164202858496e7075742900'),
         (0x00207460, '0000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000',
                      '6822836000e91a00000083c404e952aee3ff684a836000e91200000083c404e9d34cfbffe8d0000000e9dcffffffe8c6000000e9e4ffffff609ca140cb650383f8010f86a900000031f683fe020f839e0000006870cb650356ff1540cb650385c00f85840000000fb70574cb6503a9100000000f95c00fb6c08d9684cb65030fb60a39c80f841f000000880285c00f84150000006a006a726800010000a1585fae0150ff156cd565030fb70574cb6503a9001000000f95c00fb6c08d9688cb65030fb60a39c80f841f000000880285c00f84150000006a006a206800010000a1585fae0150ff156cd5650346e959ffffff9d61ff2590d565035589e583ec045356578b5d08c745fc00000000a140cb650385c00f840e00000083f8010f84a1000000e95400000031f683fe030f833a0000008b04b50783600050ff1504d5650385c00f850600000046e9dbffffff681383600050ff1508d5650385c00f840a000000a340cb6503e90f000000c70540cb650301000000e9480000006844cb6503ff33ffd085c00f8537000000c745fc010000000fb70548cb6503a9001000000f84060000008b5318c602800fb70548cb6503a9200000000f84060000008b5324c602808b4320ffd0837dfc000f84d000000031f683fe0c0f83c50000008b53040fb604722de00000000f82ad00000083f8100f83a40000008d3cc51b4d62000fb6070fb757028b4f0483f8000f842600000083f8010f843100000083f8020f843c0000000fb68248cb650339c80f8741000000e9640000000fbf8248cb650339c80f8c2d000000e9500000000fbf8248cb650339c80f8f19000000e93c0000000fb70548cb650385c80f8505000000e9280000008b53100fb60c32f7d18b53080fb70221c86689028b53140fb60c32f7d18b530c0fb70221c866890246e932ffffff5f5e5bc9c372836000808360008e83600058496e7075744765745374617465000000000070146503c414cb01c614cb01903665009d3665008104bf0060cb6503743044005704bf000100000088146503e43eee01e63eee0108eb6b0015eb6b00b10dad0161cb6503edce5b00940dad0178696e707574315f342e646c6c0078696e707574315f332e646c6c0078696e707574395f315f302e646c6c00')]),
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
    'keyboard input for the rest of the session.', None)

# Display order. Essential fixes what is broken on modern systems, extra is
# taste; both start ticked.
ESSENTIAL = ('nocpucheck', 'timer', 'motion', 'continuefix', 'dinput')
EXTRA = ('debugbox', 'padxinput', 'nocd', 'sound_wait', 'samplerate',
         'feiyen', 'noloading')

def _check_table():
    """Fail at import, not half way through somebody's executable.

    A length mismatch would patch silently and wrongly."""
    if set(BY_KEY) != set(ESSENTIAL) | set(EXTRA):
        raise AssertionError('patch list and display order disagree')
    owner = {}
    for key in BY_KEY:
        for off, old, new in BY_KEY[key][2] or ():
            if len(old) != len(new):
                raise AssertionError('%s at 0x%08x: %d bytes replaced by %d'
                                     % (key, off, len(old) // 2,
                                        len(new) // 2))
            for byte in range(off, off + len(old) // 2):
                if owner.setdefault(byte, key) != key:
                    raise AssertionError('%s and %s both patch 0x%08x'
                                         % (owner[byte], key, byte))


_check_table()


def default_state():
    return {key: True for key in BY_KEY}


def apply_feature(buf, sites):
    for off, old, new in sites:
        old, new = bytes.fromhex(old), bytes.fromhex(new)
        if buf[off:off + len(old)] != old:
            raise ValueError('unexpected bytes at 0x%08x' % off)
        buf[off:off + len(old)] = new


def apply_dinput(buf):
    hits = list(DI_FIND.finditer(buf))
    if len(hits) != 1:
        raise ValueError('expected one call site, found %d' % len(hits))
    buf[hits[0].start() + 1] = 0x0A


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
        """Patch a clean original in place. Returns a list of log lines."""
        log = []
        try:
            with open(self.exe_path, 'rb') as fh:
                buf = bytearray(fh.read())
        except OSError as exc:
            return ['Could not read the executable: %s' % exc]

        applied = []
        for key in ESSENTIAL + EXTRA:
            if not wanted.get(key):
                continue
            label, _tip, sites = BY_KEY[key]
            try:
                apply_dinput(buf) if sites is None else apply_feature(buf,
                                                                      sites)
            except ValueError as exc:
                if sites is None:            # signature miss, not fatal
                    log.append('Skipped %s: %s' % (label, exc))
                    continue
                return ['%s: %s' % (label, exc), 'Nothing written.']
            applied.append(label)

        if applied:
            self._backup(self.exe_path, log)
            try:
                with open(self.exe_path, 'wb') as fh:
                    fh.write(buf)
            except OSError as exc:
                return log + ['Write failed: %s' % exc]
            log += ['  %s' % name for name in applied]
            log.append('Wrote %s' % self.exe_path)
            if wanted.get('padxinput'):
                self._retire_ini(log)
        else:
            log.append('No executable patches selected')

        return log

    def can_restore(self):
        return bool(self.exe_path) and os.path.exists(self.exe_path + '.bak')

    def restore(self):
        """Copy the .bak back over the exe. Returns a log line."""
        bak = self.exe_path + '.bak'
        try:
            shutil.copy(bak, self.exe_path)
        except OSError as exc:
            return 'Restore failed: %s' % exc
        ini = os.path.join(os.path.dirname(self.exe_path), 'v_on.ini')
        if os.path.exists(ini + '.bak') and not os.path.exists(ini):
            try:
                shutil.move(ini + '.bak', ini)
            except OSError:
                pass
        return 'Restored %s from the backup' % os.path.basename(self.exe_path)

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
    """Split a description into prose and any 'key<TAB>meaning' rows."""
    prose, rows = [], []
    for line in text.split('\n'):
        if '\t' in line:
            key, _, meaning = line.partition('\t')
            rows.append((key.strip(), meaning.strip()))
        elif line.strip():
            prose.append(line.strip())
    return ' '.join(prose), rows


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

TITLE = 'Virtual-On patcher'
INTRO = 'Select an unmodified v_on.exe.'
NO_FILE = 'No file selected'
ESSENTIAL_HINT = ('These fix things that are broken on modern systems. '
                  'Keep them enabled unless you have a reason not to.')
EXTRA_HINT = 'Optional changes. Untick anything you would rather not have.'
DONE = 'Done. Restore the original to change your selection.'


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
            if theme is None or theme.has_icon(icon):
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
                return
            self._set_status(note, ok)
            state = default_state()
            for key, check in self.boxes.items():
                check.set_active(state[key] if ok else False)
                check.set_sensitive(ok)
            self.apply_btn.set_sensitive(ok)
            self.restore_btn.set_sensitive(self.core.can_restore())
            if not ok:
                self._log(note)

        def _apply(self, _btn):
            wanted = {k: cb.get_active() for k, cb in self.boxes.items()}
            for line in self.core.apply(wanted):
                self._log(line)
            self.apply_btn.set_sensitive(False)
            for check in self.boxes.values():
                check.set_sensitive(False)
            self.restore_btn.set_sensitive(self.core.can_restore())
            self._set_status(DONE)

        def _restore(self, _btn):
            self._log(self.core.restore())
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
                return
            self._set_status(note, ok)
            state = default_state()
            for key, check in self.checks.items():
                self.vars[key].set(state[key] if ok else False)
                check.state(['!disabled'] if ok else ['disabled'])
            self.apply_btn.state(['!disabled'] if ok else ['disabled'])
            self.restore_btn.state(
                ['!disabled'] if self.core.can_restore() else ['disabled'])
            if not ok:
                self._log(note)

        def _apply(self):
            wanted = {k: v.get() for k, v in self.vars.items()}
            for line in self.core.apply(wanted):
                self._log(line)
            self.apply_btn.state(['disabled'])
            for check in self.checks.values():
                check.state(['disabled'])
            self.restore_btn.state(
                ['!disabled'] if self.core.can_restore() else ['disabled'])
            self._set_status(DONE)

        def _restore(self):
            self._log(self.core.restore())
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


def main():
    """GTK4 if it is there, Tk if not. VOPATCH_UI=gtk or =tk forces one."""
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
