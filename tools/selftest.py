#!/usr/bin/env python3
"""Apply the patch tables to a real v_on.exe and report.

    python3 tools/selftest.py /path/to/v_on.exe

CI cannot do this - the game is not in the repository - so run it by hand
before tagging. It checks four things nothing else can:

  * every 'original' byte string in the tables is really in the file
  * no blob has outgrown its cave and run onto data the game reads
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
EXPECTED_ALL = '1dc7fac847b9eddd238a705056448fb4'

HERE = os.path.dirname(os.path.abspath(__file__))
PATCHER = os.path.join(os.path.dirname(HERE), 'vo-patch.py')


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
        if (len(data) == vp.EXE_SIZE
                and hashlib.md5(data).hexdigest() == vp.ORIGINAL_MD5):
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


def to_va(secs, off):
    for raw, size, va in secs:
        if raw <= off < raw + size:
            return va + off - raw
    return None


def cave_writes(vp):
    """Every site that fills a run of zeros, as (key, offset, length).

    A byte being zero is not the same as a byte being free. These are the
    sites where the 'original' column proves nothing, so they are the only
    ones worth scanning."""
    for key, (_label, _tip, sites) in vp.BY_KEY.items():
        for off, old, _new in sites or ():
            blob = bytes.fromhex(old)
            if len(blob) >= 8 and not any(blob):
                yield key, off, len(blob)


def check_caves(vp, original):
    """Does any cave write land on an address the game still reads?

    A blob that outgrows its cave writes into whatever follows it, and if
    that is zeroed data the site check passes and the patch ships. The
    debugbox procedure did exactly this: two bytes over the end and onto a
    qword 0.0 that a projection routine compares depth against.

    So every dword in the file is resolved as an address and checked against
    the write. Two kinds of hit, reported apart because they are not worth
    the same: a dword preceded by a disp32 modrm byte is an instruction
    operand and the game reads it, while a bare dword that happens to fall
    in range is almost always a coincidence in tile or model data - the
    caves in use here collect fifteen of those between them and not one
    survives a disassembly. Operands fail; bare dwords are printed and left
    alone.

    Two limits worth knowing. It cannot see an address reached by pointer
    arithmetic, and it only judges the original file, so a cave one patch
    hands to another is out of scope."""
    secs = sections(original)
    spans = []
    for key, off, length in sorted(cave_writes(vp), key=lambda w: w[1]):
        va = to_va(secs, off)
        if va is not None:
            spans.append((key, va, length))
    lo = min(va for _k, va, _n in spans)
    hi = max(va + n for _k, va, n in spans)

    operands, bare = {}, {}
    for i in range(1, len(original) - 4):
        va = int.from_bytes(original[i:i + 4], 'little')
        if not lo <= va < hi:
            continue
        # mod 00, r/m 101 is the disp32 form, so the four bytes are the
        # absolute address of an operand rather than data that looks like one.
        table = operands if original[i - 1] & 0xC7 == 0x05 else bare
        table.setdefault(va, []).append(i)

    bad, loose = 0, 0
    for key, va, length in spans:
        span = range(va, va + length)
        hit = [(a, r) for a in span for r in operands.get(a, ())]
        loose += sum(len(bare.get(a, ())) for a in span)
        if hit:
            bad += 1
            print('  OVERRUN %s writes 0x%06x..0x%06x'
                  % (key, va, va + length))
            for addr, ref in hit:
                print('    VA 0x%06x is a disp32 operand, modrm at VA 0x%06x'
                      % (addr, to_va(secs, ref - 1)))
    print('cave check: %d write(s) into a run of zeros, %d overrun, '
          '%d bare dword(s) in range and ignored'
          % (len(spans), bad, loose))
    return bad


def apply(vp, original, keys):
    """The patcher's own apply loop, so this tests what it ships.

    A skip is a failure here: the patcher tolerates dinput's signature going
    missing because a live install is better than none, but if a combination
    of patches can destroy that signature, that is what this run is for."""
    buf, _applied, skipped = vp.apply_selected(bytearray(original),
                                               dict.fromkeys(keys, True))
    if skipped:
        raise AssertionError('skipped %s: %s' % (skipped[0][0], skipped[0][1]))
    return buf


def main(path):
    vp = load_patcher()
    original, read = pristine(path, vp)
    if original is None:
        try:
            with open(path, 'rb') as fh:
                data = fh.read()
        except OSError as err:
            return str(err)
        if len(data) != vp.EXE_SIZE:
            return ('%s is %d bytes, expected %d, and there is no %s.bak '
                    'holding the original'
                    % (path, len(data), vp.EXE_SIZE, path))
        return ('%s has MD5 %s, expected %s, and there is no %s.bak holding '
                'the original' % (path, hashlib.md5(data).hexdigest(),
                                  vp.ORIGINAL_MD5, path))
    if read != path:
        print('note: read %s, not the patched file beside it' % read)
    print('original: %d bytes, MD5 %s'
          % (len(original), hashlib.md5(original).hexdigest()))

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

    bad += check_caves(vp, original)

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
        sys.exit('Usage: python3 tools/selftest.py /path/to/v_on.exe')
    sys.exit(main(sys.argv[1]))
