#!/usr/bin/env python3
"""Run every check in the project.

    python3 tools/check.py                       # everything that needs no game
    python3 tools/check.py /path/to/VIRTUAL-ON   # and the ones that do
    python3 tools/check.py RETAIL/ OEM/ JP/ JPORIG/      # once per build
    VO_GAME=/path/to/VIRTUAL-ON python3 tools/check.py

The game is not in the repository, so CI can only run the first form. The
checks that need a real copy are skipped rather than failed when no game is
given: selftest.py, the only thing that catches a wrong offset, and the
banner and credit tests, the only proof that those read back as written.
They are why the manual step before tagging still exists. Give every
build's folder and each of those runs once per build, named; the patcher
has tables for three, and a table can only be wrong on the build it is for.

Each check stays a script of its own; this only decides what to run and
reports the result. --list prints them without running anything.
"""

import argparse
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PY = sys.executable or 'python3'


def colours(force=None):
    on = force if force is not None else (
        sys.stdout.isatty() and os.environ.get('NO_COLOR') is None
        and os.environ.get('TERM') != 'dumb')
    if not on:
        return {k: '' for k in
                ('ok', 'bad', 'warn', 'dim', 'bold', 'off')}
    return {'ok': '\033[32m', 'bad': '\033[31m', 'warn': '\033[33m',
            'dim': '\033[90m', 'bold': '\033[1m', 'off': '\033[0m'}


CHECKS = [
    ('tables', 'patch tables, blobs and the banner bitmap',
     [PY, 'vo_patch.py', '--selfcheck'], False, False),
    ('asm', 'asm/ sources match the committed blobs',
     [PY, 'asm/build.py', '--check'], False, False),
    ('ui', 'asm/ui.asm matches the committed resolution blob',
     [PY, 'tools/uibuild.py', '--check'], False, False),
    ('net', 'net/dpctrl.dll matches net/dpctrl.c',
     [PY, 'net/build.py', '--check'], False, False),
    ('disc', 'the disc reader, on images built for the test',
     [PY, 'tools/disctest.py'], False, False),
    ('gui', 'the window, driven headlessly',
     [PY, 'tools/guitest.py', '{game}'], False, False),
    ('lint', 'pyflakes',
     [PY, '-m', 'pyflakes', 'vo_patch.py', 'asm/build.py', 'asm/layout.py',
      'asm/padtables.py', 'asm/dialogs.py', 'net/build.py',
      'tools/selftest.py', 'tools/bannertest.py', 'tools/vonbanner.py',
      'tools/credittest.py', 'tools/vocredits.py', 'tools/disctest.py',
      'tools/guitest.py', 'tools/check.py', 'tools/buildsites.py',
      'tools/vomap.py', 'tools/votrans.py', 'tools/whereis.py',
      'tools/uibuild.py', 'tools/hiresport.py', 'tools/vo_patch_hires.py',
      'tools/rvload.py', 'tools/uiemu.py', 'net/rendezvous.py'], False,
     False),
    ('tree', 'no uncommitted generated files',
     ['git', 'diff', '--exit-code', '--stat'], False, True),
    ('offsets', 'every patch against a real v_on.exe',
     [PY, 'tools/selftest.py', '{exe}'], True, False),
    ('banner', 'the title prompt reads back as written',
     [PY, 'tools/bannertest.py', '{game}'], True, False),
    ('credit', 'the credit line reads back out of the roll',
     [PY, 'tools/credittest.py', '{game}'], True, False),
    ('uiemu', 'the resolution blob run under Unicorn',
     [PY, 'tools/uiemu.py', '{exe}'], True, False, ('retail',)),
]


def find_games(args):
    """The game folders, from folders or from any file inside one, each
    with its v_on.exe and the name of the build it holds.

    Naming a file is convenient and every checker wants the folder anyway -
    the banner needs the artwork as well - so the file name itself is not
    used. The folder that was resolved gets printed, because otherwise
    pointing at the wrong copy looks exactly like pointing at the right one."""
    given = args or ([os.environ['VO_GAME']] if os.environ.get('VO_GAME')
                     else [])
    out = []
    for game in given:
        if os.path.isfile(game):
            game = os.path.dirname(game) or '.'
        exe = os.path.join(game, 'v_on.exe')
        name, short = build_name(exe)
        out.append((game, exe if os.path.exists(exe) else None, name, short))
    return out


def build_name(exe):
    """Which build a v_on.exe, or the original beside it, is."""
    import hashlib
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        'vopatch', os.path.join(ROOT, 'vo_patch.py'))
    vp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(vp)
    for candidate in (exe, exe + '.bak'):
        try:
            with open(candidate, 'rb') as fh:
                build = vp.BUILDS.get(hashlib.md5(fh.read()).hexdigest())
        except OSError:
            continue
        if build:
            return build.name, build.short
    return 'no build the patcher knows', 'unknown'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('game', nargs='*', help='a VIRTUAL-ON folder per build')
    ap.add_argument('--only', help='comma-separated names from --list')
    ap.add_argument('--list', action='store_true')
    ap.add_argument('--colour', choices=['auto', 'always', 'never'],
                    default='auto')
    args = ap.parse_args()

    c = colours({'auto': None, 'always': True, 'never': False}[args.colour])
    if args.list:
        for name, what, _cmd, needs, ci, *_rest in CHECKS:
            note = 'needs the game' if needs else ('CI only' if ci else '')
            print('  %-13s %-46s %s' % (name, what, note))
        return 0

    wanted = set(args.only.split(',')) if args.only else None
    games = find_games(args.game)
    for game, exe, _name, _short in games:
        if not exe:
            print('%sno v_on.exe in %s%s' % (c['bad'], game, c['off']))
            return 2
    for game, _exe, name, _short in games:
        print('%sgame%s %s  %s(%s)%s'
              % (c['dim'], c['off'], os.path.abspath(game), c['dim'], name,
                 c['off']))
    in_ci = os.environ.get('CI') == 'true'
    os.chdir(ROOT)
    results = []
    for name, what, cmd, needs_game, ci_only, *rest in CHECKS:
        # a sixth field names the builds the check knows; the rest run on
        # every folder given
        builds = rest[0] if rest else None
        if wanted and name not in wanted:
            continue
        if ci_only and not (in_ci or wanted):
            # Fails on any uncommitted change, which is every moment of
            # working on something. It answers "was a regenerated blob
            # committed", which is a question for the push, not the edit.
            print('  %s%-9s SKIP%s  %s %s(runs in CI)%s'
                  % (c['dim'], name, c['off'], what, c['dim'], c['off']))
            continue
        if needs_game and not games:
            print('  %s%-9s SKIP%s  %s %s(pass the game folders)%s'
                  % (c['warn'], name, c['off'], what, c['dim'], c['off']))
            results.append((name, None))
            continue
        # a game-dependent check runs once per build given; the rest once,
        # against the first folder if one is wanted for its {game}
        runs = games if needs_game else games[:1] or [(None,) * 4]
        if builds:
            runs = [r for r in runs if r[3] in builds]
            if not runs:
                print('  %s%-9s SKIP%s  %s %s(pass the %s folder)%s'
                      % (c['warn'], name, c['off'], what, c['dim'],
                         ' or '.join(builds), c['off']))
                results.append((name, None))
                continue
        for game, exe, build, short in runs:
            run = [a.replace('{exe}', exe or '').replace('{game}', game or '')
                   for a in cmd]
            start = time.time()
            proc = subprocess.run(run, capture_output=True, text=True)
            took = time.time() - start
            good = proc.returncode == 0
            label = name if not needs_game or len(games) == 1 else \
                '%s/%s' % (name, short)
            results.append((label, good))
            tag = ('%sOK  %s' % (c['ok'], c['off'])) if good else \
                  ('%sFAIL%s' % (c['bad'], c['off']))
            print('  %s%-13s%s %s  %-46s %s%.1fs%s'
                  % (c['bold'], label, c['off'], tag, what, c['dim'], took,
                     c['off']))
            out = (proc.stdout + proc.stderr).strip().split('\n')
            if good:
                # A check can say something worth hearing even when it
                # passes - which file it actually read, say. Anything it
                # marks as a note is surfaced; the rest only on failure.
                for line in out:
                    if line.startswith('note: '):
                        print('      %s%s%s' % (c['dim'], line[6:], c['off']))
            else:
                for line in out[-12:]:
                    print('      %s%s%s' % (c['dim'], line, c['off']))

    ran = [r for _n, r in results if r is not None]
    failed = [n for n, r in results if r is False]
    skipped = [n for n, r in results if r is None]
    print()
    if failed:
        print('%s%d of %d failed: %s%s'
              % (c['bad'], len(failed), len(ran), ' '.join(failed), c['off']))
    else:
        print('%sall %d passed%s' % (c['ok'], len(ran), c['off']))
    if skipped:
        print('%s%d skipped: %s - rerun with the game folders before tagging%s'
              % (c['warn'], len(skipped), ' '.join(skipped), c['off']))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
