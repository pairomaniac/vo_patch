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

# Each site: (offset, original, patched).

FEATURES = [
    ('sound_wait', 'Remove the SE playback wait',
     'Closer to arcade timing on rapid-fire weapons.', [
         (0x002bba60, '0f', '01')]),

    ('samplerate', 'Sound processing 22050 \u2192 44100 Hz',
     'Doubles the audio output rate. May misbehave on some cards.', [
         (0x00189546, '2256', '44ac'),
         (0x00189552, '88580100', '10b10200')]),

    ('noloading', 'Hide the "Now Loading . . ." text',
     'Cosmetic. Loads are instant on modern hardware anyway.', [
         (0x002c7678, '4e', '00')]),


    ('feiyen', 'Enemy Fei-Yen hypermode sound',
     'Restores the sound an enemy Fei-Yen makes going hypermode.', [
         (0x00058189, '01', '02'),
         (0x00170dc9, '01', '02')]),

    ('nocpucheck', 'Skip the processor check',
     'Same as ProcessorCheck=Off in v_on.ini, without editing the ini.\n'
     'Also skips the MMX, Pentium and vendor checks behind it.', [
         (0x00107930, '830dc884bf0001', '90909090909090')]),
    ('motion', 'Let v_on.ini set the Motion value',
     'The ini value is read and then overwritten by four routines, one of\n'
     'which fires on resolution and view changes. Removing them lets\n'
     'Motion= stand. Missing or out of range now falls back to 1/1\n'
     'rather than 1/3.', [
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

    ('timer', 'Raise the multimedia timer resolution',
     'Stops the game running slow on Windows 2000 and later.\n'
     'Not needed under Wine.', [
         (0x001f423e, '00' * 62,
          '68624e5f00ff1504d56503686c4e5f0050ff1508d5650385c074046a01ff'
          'd0e9ce2affff77696e6d6d2e646c6c0074696d65426567696e506572696f'
          '6400'),
         (0x000000a8, '30791e00', '3e4e1f00')]),
    ('debugbox', 'Remove the menu bar, Extras dialog on F11',
     'Removes the menu bar and gives F11 a settings box with the Debug\n'
     'options that are not already in Graphic Settings.', [
         (0x001c4d42, '0f850c000000', '909090909090'),
         (0x001c4d4b, '65000000', '00000000'),
         (0x001c4d7e, '57685c00', '7c4e5f00'),
         (0x001f427c, '00000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000',
          '558bec53817d0c000100007547817d107a000000753e68e8e86300ff1504d565'
          '0368f3e8630050ff1508d5650385c0741c8bd86a0068d84e5f00ff7508685886'
          '66036a00ff15a0d4650350ffd333c05b5dc210005b5de98019fdff'),
         (0x001f42d8, '00000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000',
          '558bec5356578b450c3d10010000756fbe0ce96300bf030000008b068b0033c9'
          '83f8010f94c151ff7604ff7508ff1544d5650383c6084f75e168e8030000ff75'
          '08ff154cd565038bd8be24e96300bf05000000566a00684301000053ff152cd5'
          '650383c6044f75eba1d0846c00486a0050684e01000053ff152cd56503eb683d'
          '1101000075688b45100fb7c8c1e81081f9e8030000752a48755468e8030000ff'
          '7508ff154cd565036a006a00684701000050ff152cd5650305559c00008bc8eb'
          '1283f902750d6a00ff7508ff1538d56503eb146a00516811010000ff35585fae'
          '01ff156cd56503b801000000eb0233c05f5e5b5dc21000'),
         (0x0023dce8, '0000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000',
          '5553455233322e444c4c004469616c6f67426f78496e64697265637450617261'
          '6d410000d82f6500479c00004ccc6b005b9c000030f463005c9c0000312f3100'
          '312f3200312f3300312f3400312f3500'),
         (0x0060c258, '000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000',
          'c000c88000000000090000000000d40082000000000045007800740072006100'
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
          '6b0065006500700069006e006700000000000000000001500000000050006e00'
          '32000e000200ffff800043006c006f007300650000000000')]),
    ('continuefix', 'Fix the crash when you lose a round',
     'Stops the crash when you lose as Temjin, Viper II, Apharmd\n'
     'or Raiden.', [
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

# SetCooperativeLevel: DISCL_FOREGROUND -> DISCL_BACKGROUND.
DINPUT_KEY = 'dinput'
DINPUT_LABEL = 'Keep keyboard input after alt-tab'
DINPUT_NOTE = ('Without this, alt-tabbing or opening an F-key dialog\n'
               'kills keyboard input for the rest of the session.')
DI_FIND = re.compile(
    rb'\x6a\x06[\s\S]{0,20}?\xff(?:[\x50-\x57]\x34|[\x90-\x97]\x34\x00\x00\x00)')

# Not part of VO_Patch; the UI rules a line above these.
OURS = ('nocpucheck', 'motion', 'timer', 'debugbox', 'continuefix',
        'dinput')


def apply_feature(buf, sites):
    for off, old, new in sites:
        old, new = bytes.fromhex(old), bytes.fromhex(new)
        if bytes(buf[off:off + len(old)]) != old:
            raise ValueError('unexpected bytes at 0x%08x' % off)
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
                try:
                    apply_feature(buf, sites)
                except ValueError as exc:
                    return ['%s: %s' % (label, exc), 'Nothing written.']
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
            self.set_default_size(540, 480)
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

            self.advanced = Gtk.Expander(label='Advanced \u2014 choose patches')
            self.advanced.set_child(self._exe_page())
            root.append(self.advanced)

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
            cb.set_margin_start(10)
            store[key] = cb
            return cb

        def _heading(self, text, first=False):
            lbl = Gtk.Label(xalign=0)
            lbl.set_markup('<b>%s</b>' % text)
            lbl.set_margin_top(0 if first else 10)
            lbl.set_margin_bottom(2)
            return lbl

        def _page(self, box):
            for setter in ('top', 'bottom', 'start', 'end'):
                getattr(box, 'set_margin_' + setter)(10)
            scroll = Gtk.ScrolledWindow(vexpand=True)
            scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            scroll.set_child(box)
            return scroll

        def _exe_page(self):
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            box.append(self._heading('VO_Patch', first=True))
            ruled = False
            for key, label, tip, _s in FEATURES:
                if key in OURS and not ruled:
                    box.append(self._heading('Fixes and additions'))
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
                cb.set_active(ok)
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
    import tkinter.font as tkfont
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

        PAD = 12

        def __init__(self, root):
            self.root = root
            self.core = Patcher()
            self.vars, self.checks = {}, {}
            root.title(TITLE)
            root.geometry('560x520')
            root.minsize(460, 340)

            bold = tkfont.nametofont('TkDefaultFont').copy()
            bold.configure(weight='bold')
            self.bold = bold

            outer = ttk.Frame(root, padding=self.PAD)
            outer.pack(fill='both', expand=True)
            outer.columnconfigure(1, weight=1)

            ttk.Label(outer, text='Game executable').grid(
                row=0, column=0, sticky='w', padx=(0, 8))
            self.path_var = tk.StringVar()
            ttk.Entry(outer, textvariable=self.path_var, state='readonly').grid(
                row=0, column=1, sticky='ew')
            ttk.Button(outer, text='Browse\u2026', command=self._pick).grid(
                row=0, column=2, sticky='e', padx=(8, 0))

            self.status = ttk.Label(outer, text='No file selected',
                                    foreground='#777', wraplength=520,
                                    justify='left')
            self.status.grid(row=1, column=0, columnspan=3, sticky='w',
                             pady=(8, 0))

            self.adv_open = False
            self.adv_btn = ttk.Button(
                outer, text='\u25b8  Advanced \u2014 choose patches',
                command=self._toggle_advanced)
            self.adv_btn.grid(row=2, column=0, columnspan=3, sticky='ew',
                              pady=(12, 0))

            self.adv_frame = self._exe_page(outer)      # gridded on demand
            outer.rowconfigure(3, weight=1)

            logwrap = ttk.Frame(outer, relief='sunken', borderwidth=1)
            logwrap.grid(row=4, column=0, columnspan=3, sticky='nsew',
                         pady=(12, 0))
            logwrap.columnconfigure(0, weight=1)
            logwrap.rowconfigure(0, weight=1)
            self.log_box = tk.Text(logwrap, height=6, wrap='word',
                                   state='disabled', relief='flat',
                                   highlightthickness=0, padx=6, pady=4)
            self.log_box.grid(row=0, column=0, sticky='nsew')
            bar = ttk.Scrollbar(logwrap, orient='vertical',
                                command=self.log_box.yview)
            bar.grid(row=0, column=1, sticky='ns')
            self.log_box.configure(yscrollcommand=bar.set)

            self.apply_btn = ttk.Button(outer, text='Apply', state='disabled',
                                        command=self._apply)
            self.apply_btn.grid(row=5, column=2, sticky='e', pady=(12, 0))

            self._log(INTRO)

        def _add_check(self, parent, key, label, tip, vars_, checks):
            var = tk.BooleanVar(value=False)
            cb = ttk.Checkbutton(parent, text=label, variable=var)
            cb.state(['disabled'])
            cb.pack(anchor='w', pady=1, padx=(16, 0))
            Tooltip(cb, tip)
            vars_[key], checks[key] = var, cb

        def _add_heading(self, parent, text, first=False):
            ttk.Label(parent, text=text, font=self.bold).pack(
                anchor='w', pady=(0 if first else 12, 3))

        def _exe_page(self, parent):
            """The patch list, in a canvas so it scrolls instead of clipping."""
            wrap = ttk.Frame(parent)
            wrap.columnconfigure(0, weight=1)
            wrap.rowconfigure(0, weight=1)
            canvas = tk.Canvas(wrap, highlightthickness=0, borderwidth=0,
                               height=200)
            canvas.grid(row=0, column=0, sticky='nsew')
            bar = ttk.Scrollbar(wrap, orient='vertical', command=canvas.yview)
            bar.grid(row=0, column=1, sticky='ns')
            canvas.configure(yscrollcommand=bar.set)

            frame = ttk.Frame(canvas, padding=(2, 4))
            window = canvas.create_window((0, 0), window=frame, anchor='nw')

            def resized(_event=None):
                canvas.configure(scrollregion=canvas.bbox('all'))
                canvas.itemconfigure(window, width=canvas.winfo_width())
            frame.bind('<Configure>', resized)
            canvas.bind('<Configure>', resized)

            def wheel(event):
                step = -1 if getattr(event, 'num', 0) == 4 or event.delta > 0 else 1
                canvas.yview_scroll(step, 'units')
            for seq in ('<MouseWheel>', '<Button-4>', '<Button-5>'):
                canvas.bind_all(seq, lambda e, w=wheel: self.adv_open and w(e))

            self._add_heading(frame, 'VO_Patch', first=True)
            ruled = False
            for key, label, tip, _s in FEATURES:
                if key in OURS and not ruled:
                    self._add_heading(frame, 'Fixes and additions')
                    ruled = True
                self._add_check(frame, key, label, tip, self.vars, self.checks)
            self._add_check(frame, DINPUT_KEY, DINPUT_LABEL, DINPUT_NOTE,
                            self.vars, self.checks)
            return wrap

        def _toggle_advanced(self):
            self.adv_open = not self.adv_open
            arrow = '\u25be' if self.adv_open else '\u25b8'
            self.adv_btn.config(text='%s  Advanced \u2014 choose patches' % arrow)
            if self.adv_open:
                self.adv_frame.grid(row=3, column=0, columnspan=3,
                                    sticky='nsew', pady=(6, 0))
            else:
                self.adv_frame.grid_remove()

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
            for key, cb in self.checks.items():
                self.vars[key].set(ok)
                cb.state(['!disabled'] if ok else ['disabled'])
            self.apply_btn.state(['!disabled'] if ok else ['disabled'])
            if not ok:
                self._log(note)

        def _apply(self):
            wanted = {k: v.get() for k, v in self.vars.items()}
            for line in self.core.apply(wanted):
                self._log(line)
            self.apply_btn.state(['disabled'])
            for cb in self.checks.values():
                cb.state(['disabled'])
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
