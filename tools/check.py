#!/usr/bin/env python3
"""Run every check in the project.

    python3 tools/check.py                       # everything that needs no game
    python3 tools/check.py /path/to/VIRTUAL-ON   # and the ones that do
    VO_GAME=/path/to/VIRTUAL-ON python3 tools/check.py

The game is not in the repository, so CI can only run the first form. The
checks that need a real copy are skipped rather than failed when no game is
given: selftest.py, the only thing that catches a wrong offset, and the
banner and credit tests, the only proof that those read back as written.
They are why the manual step before tagging still exists.

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
     [PY, 'vo-patch.py', '--selfcheck'], False, False),
    ('asm', 'asm/ sources match the committed blobs',
     [PY, 'asm/build.py', '--check'], False, False),
    ('net', 'netplay blob matches net/dpctrl.c',
     [PY, 'net/build.py', '--check'], False, False),
    ('disc', 'the disc reader, on images built for the test',
     [PY, 'tools/disctest.py'], False, False),
    ('lint', 'pyflakes',
     [PY, '-m', 'pyflakes', 'vo-patch.py', 'asm/build.py', 'asm/layout.py',
      'asm/padtables.py', 'asm/dialogs.py', 'net/build.py',
      'tools/selftest.py', 'tools/bannertest.py', 'tools/vonbanner.py',
      'tools/credittest.py', 'tools/vocredits.py', 'tools/disctest.py',
      'tools/check.py'], False, False),
    ('tree', 'no uncommitted generated files',
     ['git', 'diff', '--exit-code', '--stat'], False, True),
    ('offsets', 'every patch against a real v_on.exe',
     [PY, 'tools/selftest.py', '{exe}'], True, False),
    ('banner', 'the title prompt reads back as written',
     [PY, 'tools/bannertest.py', '{game}'], True, False),
    ('credit', 'the credit line reads back out of the roll',
     [PY, 'tools/credittest.py', '{game}'], True, False),
]


def find_game(arg):
    """The game folder, from a folder or from any file inside one.

    Naming a file is convenient and every checker wants the folder anyway -
    the banner needs escrgame.bin as well - so the file name itself is not
    used. The folder that was resolved gets printed, because otherwise
    pointing at the wrong copy looks exactly like pointing at the right one."""
    game = arg or os.environ.get('VO_GAME')
    if not game:
        return None, None
    if os.path.isfile(game):
        game = os.path.dirname(game) or '.'
    exe = os.path.join(game, 'v_on.exe')
    return (game, exe) if os.path.exists(exe) else (game, None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('game', nargs='?', help='the VIRTUAL-ON folder')
    ap.add_argument('--only', help='comma-separated names from --list')
    ap.add_argument('--list', action='store_true')
    ap.add_argument('--colour', choices=['auto', 'always', 'never'],
                    default='auto')
    args = ap.parse_args()

    c = colours({'auto': None, 'always': True, 'never': False}[args.colour])
    if args.list:
        for name, what, _cmd, needs, ci in CHECKS:
            note = 'needs the game' if needs else ('CI only' if ci else '')
            print('  %-9s %-46s %s' % (name, what, note))
        return 0

    wanted = set(args.only.split(',')) if args.only else None
    game, exe = find_game(args.game)
    if args.game and not exe:
        print('%sno v_on.exe in %s%s' % (c['bad'], game, c['off']))
        return 2

    if exe:
        print('%sgame%s %s' % (c['dim'], c['off'], os.path.abspath(game)))
    in_ci = os.environ.get('CI') == 'true'
    os.chdir(ROOT)
    results = []
    for name, what, cmd, needs_game, ci_only in CHECKS:
        if wanted and name not in wanted:
            continue
        if ci_only and not (in_ci or wanted):
            # Fails on any uncommitted change, which is every moment of
            # working on something. It answers "was a regenerated blob
            # committed", which is a question for the push, not the edit.
            print('  %s%-9s SKIP%s  %s %s(runs in CI)%s'
                  % (c['dim'], name, c['off'], what, c['dim'], c['off']))
            continue
        if needs_game and not exe:
            print('  %s%-9s SKIP%s  %s %s(pass the game folder)%s'
                  % (c['warn'], name, c['off'], what, c['dim'], c['off']))
            results.append((name, None))
            continue
        run = [a.replace('{exe}', exe or '').replace('{game}', game or '')
               for a in cmd]
        start = time.time()
        proc = subprocess.run(run, capture_output=True, text=True)
        took = time.time() - start
        good = proc.returncode == 0
        results.append((name, good))
        tag = ('%sOK  %s' % (c['ok'], c['off'])) if good else \
              ('%sFAIL%s' % (c['bad'], c['off']))
        print('  %s%-9s%s %s  %-46s %s%.1fs%s'
              % (c['bold'], name, c['off'], tag, what, c['dim'], took,
                 c['off']))
        out = (proc.stdout + proc.stderr).strip().split('\n')
        if good:
            # A check can say something worth hearing even when it passes -
            # which file it actually read, say. Anything it marks as a note
            # is surfaced; the rest is only shown when something failed.
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
        print('%s%d skipped: %s - rerun with the game folder before tagging%s'
              % (c['warn'], len(skipped), ' '.join(skipped), c['off']))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
