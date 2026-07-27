#!/usr/bin/env python3
"""Virtual-On (PC, 1997) patcher. See README.md.

    python3 vo-patch.py

Uses GTK4 where it is available and Tk otherwise, so it runs on Linux
without XWayland and on Windows without installing anything. Force one
with VOPATCH_UI=gtk or VOPATCH_UI=tk.
"""

import hashlib
import os
import re
import shutil
import sys

EXE_SIZE = 6650880

ORIGINAL_MD5 = 'a464b0ff32d5bab499f265e45658504e'

# Each site: (offset, original, patched). The input is checksummed, so the
# original bytes are only an assertion against a typo in this table.

FEATURES = [
    ('sound_wait', 'Remove the SE playback wait',
     'Closer to arcade timing on rapid-fire weapons.', [
         (0x002bba60, '0f', '01')]),

    ('samplerate', 'Sound processing 22050 \u2192 44100 Hz',
     'Raises the DirectSound buffer rate. May misbehave on some cards.', [
         (0x00189546, '2256', '44ac'),
         # nAvgBytesPerSec, which VO_Patch leaves inconsistent
         (0x00189552, '88580100', '10b10200')]),

    ('noloading', 'Hide the "Now Loading . . ." text',
     'Cosmetic. Loads are instant on modern hardware anyway.', [
         (0x002c7678, '4e', '00')]),

    ('debugmenu', 'Always show the Debug menu',
     'Loads menu resource 101 regardless of what v_on.ini says.', [
         (0x001c4d57, '66', '65')]),

    ('feiyen', 'Enemy Fei-Yen hypermode sound',
     'Restores the 2P-side sound. Plays at the 1P pitch, unlike the arcade.', [
         (0x00058189, '01', '02'),
         (0x00170dc9, '01', '02')]),

    ('motion', 'Let v_on.ini set the Motion value',
     'The ini value is read and then overwritten by a later routine.\n'
     'This removes the overwrite, so Motion=1 gives full framerate.', [
         (0x001c8bc4, 'c705d0846c0002000000', '90909090909090909090'),
         (0x001c8bd3, 'c705d0846c0003000000', '90909090909090909090')]),

    ('timer', 'Raise the multimedia timer resolution',
     'The game never calls timeBeginPeriod, so it runs slow where the\n'
     'default timer period is coarse. Adds the call at startup. Not\n'
     'needed under Wine or Proton.', [
         (0x001f423e, '00' * 62,
          '68624e5f00ff1504d56503686c4e5f0050ff1508d5650385c074046a01ff'
          'd0e9ce2affff77696e6d6d2e646c6c0074696d65426567696e506572696f'
          '6400'),
         (0x000000a8, '30791e00', '3e4e1f00')]),
    ('continuefix', 'Fix the crash when you lose a round',
     'Dying as certain VRs crashes on Windows 2000 and later: ten\n'
     'continue-screen routines dereference a stack slot holding a float\n'
     'constant. Removes the blocks, which only undo a translation.', [
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
]

# SetCooperativeLevel: DISCL_FOREGROUND -> DISCL_BACKGROUND, found by
# signature so it survives other edits moving bytes around it.
DINPUT_KEY = 'dinput'
DINPUT_LABEL = 'Keep keyboard input after alt-tab'
DINPUT_NOTE = ('Without this, opening a dialog or alt-tabbing away kills\n'
               'keyboard input for the rest of the session.')
DI_FIND = re.compile(
    rb'\x6a\x06[\s\S]{0,20}?\xff(?:[\x50-\x57]\x34|[\x90-\x97]\x34\x00\x00\x00)')

# Patches that are not part of VO_Patch; the UI rules a line above them.
OURS = ('motion', 'timer', 'continuefix', 'dinput')

def apply_feature(buf, sites):
    for off, old, new in sites:
        old, new = bytes.fromhex(old), bytes.fromhex(new)
        assert bytes(buf[off:off + len(old)]) == old, hex(off)
        buf[off:off + len(new)] = new


def apply_dinput(buf):
    hits = list(DI_FIND.finditer(bytes(buf)))
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
            note += '  Already patched: restore %s first.' % (
                os.path.basename(path) + '.bak')
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
        for key, label, _tip, sites in FEATURES:
            if wanted.get(key):
                apply_feature(buf, sites)
                applied.append(label)
        if wanted.get(DINPUT_KEY):
            try:
                apply_dinput(buf)
                applied.append(DINPUT_LABEL)
            except ValueError as exc:
                log.append('alt-tab fix skipped: %s' % exc)

        if applied:
            self._backup(self.exe_path, log)
            try:
                with open(self.exe_path, 'wb') as fh:
                    fh.write(buf)
            except OSError as exc:
                return log + ['Write failed: %s' % exc]
            log += ['  %s' % name for name in applied]
            log.append('Wrote %s' % self.exe_path)
        else:
            log.append('No executable patches selected')

        return log

    @staticmethod
    def _backup(path, log):
        bak = path + '.bak'
        if not os.path.exists(bak):
            try:
                shutil.copy(path, bak)
                log.append('Backup: %s' % bak)
            except OSError as exc:
                log.append('Backup failed for %s: %s' % (path, exc))


TITLE = 'Virtual-On patcher'
INTRO = 'Select an unmodified v_on.exe. Hover a box for details.'


# ---------------------------------------------------------------- GTK4 front


def run_gtk():
    import gi
    gi.require_version('Gtk', '4.0')
    from gi.repository import Gtk, Gio, GLib

    class Window(Gtk.ApplicationWindow):

        def __init__(self, app):
            super().__init__(application=app, title=TITLE)
            self.set_default_size(520, 560)
            self.core = Patcher()
            self.boxes = {}

            root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            for setter in ('top', 'bottom', 'start', 'end'):
                getattr(root, 'set_margin_' + setter)(12)
            self.set_child(root)

            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            label = Gtk.Label(label='Game executable', xalign=0)
            label.set_size_request(120, -1)
            row.append(label)
            self.entry = Gtk.Entry(hexpand=True, editable=False,
                                   placeholder_text='not selected')
            row.append(self.entry)
            btn = Gtk.Button(label='Browse\u2026')
            btn.connect('clicked', self._pick)
            row.append(btn)
            root.append(row)

            self.status = self._dim('No file selected')
            root.append(self.status)

            root.append(self._exe_page())

            self.log_view = Gtk.TextView(editable=False, monospace=True,
                                         cursor_visible=False)
            scroll = Gtk.ScrolledWindow()
            scroll.set_child(self.log_view)
            scroll.set_size_request(-1, 96)
            root.append(scroll)

            bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            bar.set_halign(Gtk.Align.END)
            self.apply_btn = Gtk.Button(label='Apply')
            self.apply_btn.add_css_class('suggested-action')
            self.apply_btn.set_sensitive(False)
            self.apply_btn.connect('clicked', self._apply)
            bar.append(self.apply_btn)
            root.append(bar)

            self._log(INTRO)

        def _dim(self, text):
            lbl = Gtk.Label(label=text, xalign=0)
            lbl.add_css_class('dim-label')
            lbl.set_wrap(True)
            return lbl

        def _check(self, key, label, tip, store):
            cb = Gtk.CheckButton(label=label)
            cb.set_tooltip_text(tip)
            cb.set_sensitive(False)
            store[key] = cb
            return cb

        def _page(self, box):
            for setter in ('top', 'bottom', 'start', 'end'):
                getattr(box, 'set_margin_' + setter)(10)
            scroll = Gtk.ScrolledWindow(vexpand=True)
            scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            scroll.set_child(box)
            return scroll

        def _exe_page(self):
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            ruled = False
            for key, label, tip, _s in FEATURES:
                if key in OURS and not ruled:
                    box.append(Gtk.Separator())
                    ruled = True
                box.append(self._check(key, label, tip, self.boxes))
            box.append(self._check(DINPUT_KEY, DINPUT_LABEL, DINPUT_NOTE,
                                   self.boxes))
            return self._page(box)

        def _set_status(self, text, ok=None):
            for css in ('dim-label', 'success', 'error'):
                self.status.remove_css_class(css)
            self.status.add_css_class('dim-label' if ok is None
                                      else 'success' if ok else 'error')
            self.status.set_markup('<b>%s</b>' % GLib.markup_escape_text(text)
                                   if ok is not None else text)

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
            for cb in self.boxes.values():
                cb.set_sensitive(ok)
            self.apply_btn.set_sensitive(ok)
            if not ok:
                self._log(note)

        def _apply(self, _btn):
            wanted = {k: cb.get_active() for k, cb in self.boxes.items()}
            for line in self.core.apply(wanted):
                self._log(line)
            self.apply_btn.set_sensitive(False)
            for cb in self.boxes.values():
                cb.set_sensitive(False)
            self._set_status('Done. Restore the .bak to patch again.')

        def _log(self, text):
            buf = self.log_view.get_buffer()
            buf.insert(buf.get_end_iter(), text + '\n')
            mark = buf.create_mark(None, buf.get_end_iter(), False)
            self.log_view.scroll_mark_onscreen(mark)
            buf.delete_mark(mark)

    app = Gtk.Application(application_id='org.local.vopatch',
                          flags=Gio.ApplicationFlags.FLAGS_NONE)
    app.connect('activate', lambda a: Window(a).present())
    return app.run(None)


# ------------------------------------------------------------------ Tk front


def run_tk():
    import tkinter as tk
    from tkinter import ttk, filedialog

    class Tooltip:
        """Tk has none built in; this is the usual minimal version."""

        def __init__(self, widget, text):
            self.widget, self.text, self.win = widget, text, None
            widget.bind('<Enter>', self.show)
            widget.bind('<Leave>', self.hide)

        def show(self, _event=None):
            if self.win or not self.text:
                return
            x = self.widget.winfo_rootx() + 20
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 2
            self.win = tk.Toplevel(self.widget)
            self.win.wm_overrideredirect(True)
            self.win.wm_geometry('+%d+%d' % (x, y))
            tk.Label(self.win, text=self.text, justify='left',
                     background='#ffffe0', relief='solid', borderwidth=1,
                     padx=6, pady=4).pack()

        def hide(self, _event=None):
            if self.win:
                self.win.destroy()
                self.win = None

    class App:

        def __init__(self, root):
            self.root = root
            self.core = Patcher()
            self.vars, self.checks = {}, {}
            root.title(TITLE)
            root.geometry('560x620')

            top = ttk.Frame(root, padding=10)
            top.pack(fill='x')
            ttk.Label(top, text='Game executable', width=16).pack(side='left')
            self.path_var = tk.StringVar()
            ttk.Entry(top, textvariable=self.path_var, state='readonly'
                      ).pack(side='left', fill='x', expand=True, padx=6)
            ttk.Button(top, text='Browse\u2026', command=self._pick
                       ).pack(side='left')

            self.status = ttk.Label(root, text='No file selected',
                                    foreground='#777', padding=(10, 0))
            self.status.pack(fill='x')

            self._exe_page(root).pack(fill='both', expand=True)

            self.log_box = tk.Text(root, height=7, wrap='none', state='disabled')
            self.log_box.pack(fill='x', padx=10)

            bar = ttk.Frame(root, padding=10)
            bar.pack(fill='x')
            self.apply_btn = ttk.Button(bar, text='Apply', state='disabled',
                                        command=self._apply)
            self.apply_btn.pack(side='right')

            self._log(INTRO)

        def _add_check(self, parent, key, label, tip, vars_, checks):
            var = tk.BooleanVar(value=False)
            cb = ttk.Checkbutton(parent, text=label, variable=var)
            cb.state(['disabled'])
            cb.pack(anchor='w', pady=2)
            Tooltip(cb, tip)
            vars_[key], checks[key] = var, cb

        def _exe_page(self, parent):
            frame = ttk.Frame(parent, padding=10)
            ruled = False
            for key, label, tip, _s in FEATURES:
                if key in OURS and not ruled:
                    ttk.Separator(frame).pack(fill='x', pady=6)
                    ruled = True
                self._add_check(frame, key, label, tip, self.vars, self.checks)
            self._add_check(frame, DINPUT_KEY, DINPUT_LABEL, DINPUT_NOTE,
                            self.vars, self.checks)
            return frame

        def _set_status(self, text, ok=None):
            colour = '#777' if ok is None else '#1a7f37' if ok else '#c0392b'
            self.status.config(text=text, foreground=colour)

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
            for cb in self.checks.values():
                cb.state(['!disabled'] if ok else ['disabled'])
            self.apply_btn.state(['!disabled'] if ok else ['disabled'])
            if not ok:
                self._log(note)

        def _apply(self):
            wanted = {k: v.get() for k, v in self.vars.items()}
            for line in self.core.apply(wanted):
                self._log(line)
            self.apply_btn.set_sensitive(False)
            for cb in self.boxes.values():
                cb.set_sensitive(False)
            self._set_status('Done. Restore the .bak to patch again.')

        def _log(self, text):
            self.log_box.config(state='normal')
            self.log_box.insert('end', text + '\n')
            self.log_box.see('end')
            self.log_box.config(state='disabled')

    root = tk.Tk()
    App(root)
    root.mainloop()
    return 0


def main():
    choice = os.environ.get('VOPATCH_UI', '').lower()
    if choice not in ('gtk', 'tk'):
        choice = 'tk' if sys.platform.startswith('win') else 'gtk'
    if choice == 'gtk':
        try:
            return run_gtk()
        except (ImportError, ValueError) as exc:
            print('GTK unavailable (%s), using Tk' % exc, file=sys.stderr)
    return run_tk()


if __name__ == '__main__':
    sys.exit(main())
