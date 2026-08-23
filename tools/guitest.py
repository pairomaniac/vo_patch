#!/usr/bin/env python3
"""The window, driven headlessly.

Every check here is one a person found by clicking: a button that came back
when it should have stayed down, a heading that redrew as an empty box, a
column that ended level with nothing. None of them raise on their own - the
widgets are all present and the right size and the wrong thing is on screen -
so each one asserts the property rather than the absence of an exception.

Needs a display. Under CI that is xvfb; locally, run it inside

    xvfb-run -a -s "-screen 0 1600x1400x24" python3 tools/guitest.py

It skips with a message rather than failing when there is no display, so a
checkout on a headless box without xvfb still gets a clean run.

Most of it needs no copy of the game - the disc is built by disctest, with a
stand-in for v_on.exe. Pass a game folder (or set VO_GAME) and the checks
that need the patcher to accept the disc run as well:

    xvfb-run -a python3 tools/guitest.py /path/to/VIRTUAL-ON

The patching side itself is selftest.py's, and check.py's offsets, banner and
credit.
"""

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import disctest                                             # noqa: E402

FAILED = []


def check(label, condition, detail=''):
    print('%-4s %s%s' % ('ok' if condition else 'FAIL', label,
                         '' if condition else '   <- %s' % (detail,)))
    if not condition:
        FAILED.append(label)


def build_window(vp, tk):
    """Open the window without entering its loop, and hand back the root."""
    state = {}
    original = tk.Misc.mainloop

    def stop(self, n=0):
        state['root'] = self
        raise SystemExit(0)

    tk.Misc.mainloop = stop
    try:
        vp.run_tk()
    except SystemExit:
        pass
    finally:
        tk.Misc.mainloop = original
    return state['root']


def walk(widget, out=None):
    out = [] if out is None else out
    out.append(widget)
    for child in widget.winfo_children():
        walk(child, out)
    return out


def text_of(widget):
    try:
        return str(widget.cget('text'))
    except Exception:                                       # noqa: BLE001
        return ''


def main():
    try:
        import tkinter as tk
        from tkinter import ttk
    except ImportError:
        print('no tkinter; skipped')
        return 0
    try:
        tk.Tk().destroy()
    except tk.TclError as exc:
        print('no display (%s); skipped - run under xvfb-run' % exc)
        return 0

    vp = disctest.load_patcher()
    tmp = tempfile.mkdtemp(prefix='vo-guitest-')
    here = os.path.join(tmp, 'disc')
    os.makedirs(here)

    # A stand-in reads as an unsupported build, which is correct and is what
    # the refusal checks want; the real one lets the copy run.
    game = (sys.argv[1] if len(sys.argv) > 1 else '') or os.environ.get(
        'VO_GAME', '')
    real = os.path.join(game, 'v_on.exe') if game else ''
    if real and os.path.exists(real):
        with open(real, 'rb') as fh:
            exe = fh.read()
    else:
        exe, real = b'M' * vp.EXE_SIZE, ''
    tree = disctest.retail_tree(exe)
    # Padded so the copy lasts longer than the checks that run during it.
    # A real disc is 95 MB; without this the exe alone copies faster than
    # one turn of the event loop and there is no "during" to test.
    tree['v_on']['escrgame.bin'] = b'E' * (24 << 20)
    cue = disctest.write_disc(here, 'RETAIL', disctest.build_iso(tree),
                              'MODE1/2352', audio=26)

    root = build_window(vp, tk)
    root.overrideredirect(True)        # no window manager under xvfb
    root.geometry('+0+0')
    root.update()
    everything = walk(root)

    canvas = [w for w in everything if isinstance(w, tk.Canvas)][0]
    inner = canvas.winfo_children()[0]
    entries = [w for w in everything
               if isinstance(w, ttk.Entry) and not isinstance(w, ttk.Combobox)]
    buttons = {}
    for w in everything:
        if isinstance(w, ttk.Button):
            buttons.setdefault(text_of(w), []).append(w)
    heads = {text_of(w): w for w in everything
             if isinstance(w, ttk.Label)
             and text_of(w) in ('INSTALL', 'GAME FILE', 'ESSENTIAL PATCHES',
                                'EXTRA PATCHES', 'ADD-ONS', 'LOG', 'ABOUT')}

    def pump(ms=0):
        root.update_idletasks()
        root.update()
        if ms:
            end = root.tk.call('clock', 'milliseconds') + ms
            while root.tk.call('clock', 'milliseconds') < end:
                root.update()

    def enabled(name):
        return 'disabled' not in buttons[name][0].state()

    def card_of(head):
        return head.master.master

    def is_open(label):
        card = card_of(heads[label])
        return bool(card.winfo_children()[-1].winfo_manager())

    def set_open(label, want):
        # Driven to a state rather than toggled: the log opens itself on the
        # first line it is given, so what is open at this point depends on
        # what has happened above.
        if is_open(label) != want:
            heads[label].event_generate('<Button-1>')
            pump()

    def set_entry(entry, value):
        root.setvar(entry.cget('textvariable'), value)

    # ---- the source decides what is offered --------------------------
    check('nothing offered for an empty source',
          not enabled('Install game') and not enabled('Rip soundtrack'))
    set_entry(entries[0], os.path.join(tmp, 'no-such-file.cue'))
    pump(200)
    check('nothing offered for a path that is not there',
          not enabled('Install game') and not enabled('Rip soundtrack'))
    set_entry(entries[0], cue)
    set_entry(entries[1], os.path.join(tmp, 'game'))
    for _ in range(200):                     # the source is read on a timer
        pump(20)
        if enabled('Rip soundtrack'):
            break
    check('the rip is offered for a readable image', enabled('Rip soundtrack'),
          'it needs a destination as well as a source')
    if not real:
        check('a stand-in build is refused for the install',
              not enabled('Install game'),
              'only the retail build may be installed')
    else:
        check('the install is offered for a retail disc',
              enabled('Install game'))

        # ---- a long job holds the buttons down -----------------------
        buttons['Install game'][0].invoke()
        pump(40)
        running = not enabled('Install game')
        check('a copy disables both buttons',
              running and not enabled('Rip soundtrack'))
        set_entry(entries[1], os.path.join(tmp, 'elsewhere'))
        pump(60)
        check('editing a path mid-copy does not re-arm them',
              not running or (not enabled('Install game')
                              and not enabled('Rip soundtrack')),
              'a second copy could start on top of the first')
        for _ in range(600):
            pump(20)
            if enabled('Install game'):
                break
        check('and they come back when it finishes', enabled('Install game'))

    # ---- the layout --------------------------------------------------
    if inner.grid_size()[0] >= 2:
        columns = [c for c in inner.winfo_children() if isinstance(c, ttk.Frame)]
        left, right = columns[0], columns[1]

        def bottom(column):
            card = column.winfo_children()[-1]
            return card.winfo_rooty() + card.winfo_height()

        set_open('ADD-ONS', True)
        set_open('LOG', True)
        set_open('ABOUT', False)
        check('the columns end level whatever is open',
              abs(bottom(left) - bottom(right)) <= 1,
              (bottom(left), bottom(right)))
        check('the two foot headings line up',
              heads['LOG'].winfo_rooty() == heads['ABOUT'].winfo_rooty())
        log_card, about_card = card_of(heads['LOG']), card_of(heads['ABOUT'])
        check('an open card does not inflate a closed one beside it',
              log_card.winfo_height() > about_card.winfo_height(),
              'a heading stretched into an empty box')
        set_open('ABOUT', True)
        check('two open cards match each other',
              log_card.winfo_height() == about_card.winfo_height(),
              (log_card.winfo_height(), about_card.winfo_height()))
        set_open('LOG', False)
        check('and a closed one shrinks back to its heading',
              about_card.winfo_height() > log_card.winfo_height())

    # ---- nothing is wrapped wider than the space it has ---------------
    widest = 0
    for label in everything:
        if not isinstance(label, ttk.Label) or not label.winfo_ismapped():
            continue
        try:
            wrap = int(label.cget('wraplength'))
        except Exception:                                   # noqa: BLE001
            continue
        if wrap:
            widest = max(widest, wrap - (label.master.winfo_width() - 2))
    check('no hint wraps wider than its card', widest <= 0, '%d px over' % widest)

    # ---- the window can still be resized ------------------------------
    low, high = root.minsize(), root.maxsize()
    check('the minimum is not the maximum', low[0] < high[0],
          'the window could not be dragged at all')
    check('the window opens no smaller than its minimum',
          root.winfo_width() >= low[0], (root.winfo_width(), low[0]))

    print()
    if FAILED:
        print('FAILED: %s' % ', '.join(FAILED))
        return 1
    print('window OK')
    return 0


if __name__ == '__main__':
    sys.exit(main())
