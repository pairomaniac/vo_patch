#!/usr/bin/env python3
"""Apply the patch tables to a real v_on.exe and report.

    python3 tools/selftest.py /path/to/v_on.exe

CI cannot do this - the game is not in the repository - so run it by hand
before tagging. It checks what nothing else can:

  * every 'original' byte string in the tables is really in the file
  * every combination of patches applies, not just the all-on case
  * the fully patched result still has the MD5 it had last time

The tables are the patcher. A wrong offset passes every other check in this
repository and corrupts somebody's game.
"""

import hashlib
import importlib.util
import itertools
import os
import random
import sys

# MD5 of the original with every patch applied. Update deliberately, and only
# when a patch actually changed.
# Everything ticked, per build: retail, the Japanese rerelease, the OEM.
EXPECTED_ALL = {
    'a464b0ff32d5bab499f265e45658504e': '27d62ca331a9af259965f2a7ec297239',
    'd19320bdc3381a48228990907910a391': '2b2faa549780a74a290c7401b8a55749',
    '4c70f780a7f0d98d74be62304fb99021': 'c6c4ccee8a7afb88a5e463630378af5d',
}

HERE = os.path.dirname(os.path.abspath(__file__))
PATCHER = os.path.join(os.path.dirname(HERE), 'vo_patch.py')


def pristine(path, vp):
    """The unmodified file, from `path` or from the backup beside it.

    A development copy of the game is usually patched, which is the whole
    point of having one, so fall back the way bannertest.py does."""
    for candidate in (path, path + '.bak'):
        try:
            with open(candidate, 'rb') as fh:
                data = fh.read()
        except OSError:
            continue
        if hashlib.md5(data).hexdigest() in vp.BUILDS:
            return data, candidate
    return None, None


def load_patcher():
    spec = importlib.util.spec_from_file_location('vopatch', PATCHER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)             # runs _check_table
    return module


def sections(data):
    """(file offset, size, virtual address) per section, from the headers.

    The two deltas the rest of the project uses - 0x400c00 for .text and
    .rdata, 0x305c400 for .rsrc - are these, worked out once. Reading them
    means the mapping is right for a section nobody has hardcoded yet."""
    pe = int.from_bytes(data[0x3c:0x40], 'little')
    count = int.from_bytes(data[pe + 6:pe + 8], 'little')
    optlen = int.from_bytes(data[pe + 20:pe + 22], 'little')
    base = int.from_bytes(data[pe + 24 + 28:pe + 24 + 32], 'little')
    out = []
    for i in range(count):
        h = pe + 24 + optlen + i * 40
        rva = int.from_bytes(data[h + 12:h + 16], 'little')
        size = int.from_bytes(data[h + 16:h + 20], 'little')
        raw = int.from_bytes(data[h + 20:h + 24], 'little')
        out.append((raw, size, base + rva))
    return out



def apply(vp, original, keys, build):
    """The patcher's own apply loop, so this tests what it ships.

    A skip is a failure here: the patcher tolerates dinput's signature going
    missing because a live install is better than none, but if a combination
    of patches can destroy that signature, that is what this run is for."""
    buf, _applied, skipped = vp.apply_selected(bytearray(original),
                                               dict.fromkeys(keys, True),
                                               build)
    skipped = [s for s in skipped
               if s != ('hires', 'not ready for this build yet')]
    if skipped:
        raise AssertionError('skipped %s: %s' % (skipped[0][0], skipped[0][1]))
    return buf


def f4_check(vp, original, keys, build, result):
    """The resolution patch's F4 table: its first byte set is what the
    file holds at every site, and the file with the second set copied in
    is a build made for the other size, outside the table itself."""
    import struct
    if not vp.hires_supported(bytearray(original)):
        return 'no table for this build'
    pe = vp._PE(result)
    sec = [x for x in pe.sections if x['name'] == b'.vohr']
    if not sec:
        return 'no .vohr section'
    sec_va, raw = pe.base + sec[0]['vaddr'], sec[0]['raddr']
    tab = struct.unpack_from('<I', result, raw + vp.UI_F4TAB)[0]
    swapped, o, n = bytearray(result), raw + (tab - sec_va), 0
    while True:
        va, ln = struct.unpack_from('<II', result, o)
        if va == 0:
            break
        f = pe.off(va - pe.base)
        if result[f:f + ln] != result[o + 8:o + 8 + ln]:
            return 'first set differs from the file at 0x%x' % va
        swapped[f:f + ln] = result[o + 8 + ln:o + 8 + 2 * ln]
        o += 8 + 2 * ln
        n += 1
    w, hh = struct.unpack_from('<II', result, raw + vp.UI_MODEW)
    other = apply(vp, original, keys - {'hires'}, build)
    vp.hires_install(other, *vp.HIRES_ALT, alt=(w, hh))
    lo, hi = raw + vp.UI_F4TAB_OFF, raw + vp.UI_OFF
    if len(other) != len(swapped) or any(
            swapped[i] != other[i] for i in range(len(other))
            if not lo <= i < hi):
        return 'second set is not a %dx%d build' % vp.HIRES_ALT
    return '%d entries' % n


def main(path):
    vp = load_patcher()
    original, read = pristine(path, vp)
    if original is None:
        try:
            with open(path, 'rb') as fh:
                data = fh.read()
        except OSError as err:
            return str(err)
        known = ', '.join('%s (%d bytes, MD5 %s)' % (b.name, b.size, b.md5)
                          for b in vp.BUILDS.values())
        return ('%s is %d bytes with MD5 %s, and there is no %s.bak holding '
                'an original. Known: %s'
                % (path, len(data), hashlib.md5(data).hexdigest(), path,
                   known))
    if read != path:
        print('note: read %s, not the patched file beside it' % read)
    digest = hashlib.md5(original).hexdigest()
    build = vp.BUILDS[digest]
    table = vp.by_key(build)
    print('original: %d bytes, MD5 %s (%s)' % (len(original), digest,
                                              build.name))

    # Every 'original' column against the untouched file. Sites that overlap
    # an earlier site in the same patch are skipped: they expect what that
    # site wrote, not what is in the file.
    bad = 0
    for key, (label, _tip, sites) in table.items():
        seen = set()
        for off, old, _new in sites or ():
            if off >= len(original):        # the annex, appended at apply
                continue
            span = range(off, off + len(old) // 2)
            if not seen.isdisjoint(span):
                seen.update(span)
                continue
            seen.update(span)
            if original[off:off + len(old) // 2] != bytes.fromhex(old):
                print('  MISMATCH %s at 0x%08x' % (key, off))
                bad += 1
    print('site check: %d mismatches' % bad)


    hits = len(list(vp.DI_FIND.finditer(bytearray(original))))
    print('dinput signature: %d hit(s)' % hits)
    if hits != 1:
        bad += 1

    keys = list(table)
    failures, tested = [], 0
    trials = [set(c) for r in (1, 2) for c in itertools.combinations(keys, r)]
    random.seed(1)
    trials += [set(random.sample(keys, random.randint(3, len(keys) - 1)))
               for _ in range(300)]
    trials.append(set(keys))
    for sel in trials:
        tested += 1
        try:
            result = apply(vp, original, sel, build)
        except Exception as exc:                # noqa: BLE001
            failures.append((sorted(sel), exc))
            continue
        if sel == set(keys):
            digest = hashlib.md5(bytes(result)).hexdigest()
            print('all patches: %d bytes, MD5 %s' % (len(result), digest))
            note = f4_check(vp, original, sel, build, result)
            print('F4 table: %s' % note)
            if not note.endswith('entries') and 'no table' not in note:
                bad += 1
            if EXPECTED_ALL.get(build.md5) is None:
                print('  not pinned for this build yet')
            elif digest != EXPECTED_ALL[build.md5]:
                print('  CHANGED - expected %s' % EXPECTED_ALL[build.md5])
                bad += 1
    print('combinations: %d tested, %d failed' % (tested, len(failures)))
    for sel, exc in failures[:10]:
        print('  %s: %s' % (' '.join(sel), exc))

    if bad or failures:
        return 'FAILED'
    print('OK')
    return None


if __name__ == '__main__':
    if len(sys.argv) != 2:
        sys.exit('Usage: python3 tools/selftest.py /path/to/v_on.exe')
    sys.exit(main(sys.argv[1]))
