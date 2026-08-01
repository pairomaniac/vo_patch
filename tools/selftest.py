#!/usr/bin/env python3
"""Apply the patch tables to a real v_on.exe and report.

    python3 tools/selftest.py /path/to/v_on.exe

CI cannot do this - the game is not in the repository - so run it by hand
before tagging. It checks three things nothing else can:

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
EXPECTED_ALL = 'fabd0713ff447ac0d64aa29e2c88da06'

HERE = os.path.dirname(os.path.abspath(__file__))
PATCHER = os.path.join(os.path.dirname(HERE), 'vo-patch.py')


def load_patcher():
    spec = importlib.util.spec_from_file_location('vopatch', PATCHER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)             # runs _check_table
    return module


def apply(vp, original, keys):
    buf = bytearray(original)
    for key in vp.apply_order():
        if key not in keys:
            continue
        sites = vp.BY_KEY[key][2]
        if sites is not None:
            vp.apply_feature(buf, sites)
        else:
            vp.apply_dinput(buf)
        if key == 'nodisc':
            buf = vp.apply_cdaudio(buf)
    return buf


def main(path):
    vp = load_patcher()
    with open(path, 'rb') as fh:
        original = fh.read()

    if len(original) != vp.EXE_SIZE:
        return '%s is %d bytes, expected %d' % (path, len(original),
                                                vp.EXE_SIZE)
    got = hashlib.md5(original).hexdigest()
    if got != vp.ORIGINAL_MD5:
        return '%s has MD5 %s, expected %s' % (path, got, vp.ORIGINAL_MD5)
    print('original: %d bytes, MD5 %s' % (len(original), got))

    # Every 'original' column against the untouched file. Sites that overlap
    # an earlier site in the same patch are skipped: they expect what that
    # site wrote, not what is in the file.
    bad = 0
    for key, (label, _tip, sites) in vp.BY_KEY.items():
        seen = set()
        for off, old, _new in sites or ():
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

    keys = list(vp.BY_KEY)
    failures, tested = [], 0
    trials = [set(c) for r in (1, 2) for c in itertools.combinations(keys, r)]
    random.seed(1)
    trials += [set(random.sample(keys, random.randint(3, len(keys) - 1)))
               for _ in range(300)]
    trials.append(set(keys))
    for sel in trials:
        tested += 1
        try:
            result = apply(vp, original, sel)
        except Exception as exc:                # noqa: BLE001
            failures.append((sorted(sel), exc))
            continue
        if sel == set(keys):
            digest = hashlib.md5(bytes(result)).hexdigest()
            print('all patches: %d bytes, MD5 %s' % (len(result), digest))
            if digest != EXPECTED_ALL:
                print('  CHANGED - expected %s' % EXPECTED_ALL)
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
        sys.exit(__doc__.strip().splitlines()[2].strip())
    sys.exit(main(sys.argv[1]))
