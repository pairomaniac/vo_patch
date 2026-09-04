# -*- mode: python ; coding: utf-8 -*-
"""Windows build for the patcher.

    pip install pyinstaller
    pyinstaller vo_patch.spec

Everything the build needs is here, so the CI workflow is one command. The
version is read out of vo_patch.py's VERSION line, which the workflow stamps
from the tag before this runs; an unstamped source tree builds as 'dev'.
"""

import pathlib
import re

SOURCE = 'vo_patch.py'
VERSION = re.search(r"^VERSION = '(.*)'$",
                    pathlib.Path(SOURCE).read_text(encoding='utf-8'),
                    re.M).group(1)

# Windows file properties. Without this the exe carries no version information
# at all, which looks broken in the properties dialog. Each component is
# packed into 16 bits, so anything that is not a dotted number in that range
# gets zeros - which covers 'dev' and also an all-digit short SHA, which
# otherwise looks like a version number and overflows the field.
_digits = VERSION.split('.')
if len(_digits) > 4 or not all(p.isdigit() and int(p) < 1 << 16
                               for p in _digits):
    _digits = []
_parts = tuple(int(n) for n in (_digits + ['0'] * 4)[:4])
_version_file = pathlib.Path('build') / 'file-version.txt'
_version_file.parent.mkdir(exist_ok=True)
_version_file.write_text("""VSVersionInfo(
  ffi=FixedFileInfo(filevers=%(parts)s, prodvers=%(parts)s),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'pairomaniac'),
      StringStruct('FileDescription', 'vo_patch - Virtual-On (PC, 1997)'),
      StringStruct('FileVersion', '%(version)s'),
      StringStruct('InternalName', 'vo_patch'),
      StringStruct('OriginalFilename', 'vo_patch-%(version)s.exe'),
      StringStruct('ProductName', 'vo_patch'),
      StringStruct('ProductVersion', '%(version)s'),
    ])]),
    VarFileInfo([VarStruct('Translation', [0x0409, 1200])]),
  ]
)
""" % {'parts': _parts, 'version': VERSION}, encoding='utf-8')

# Stdlib packages this script never touches which come in transitively.
# email and http are not among them: urllib.request imports both, and
# excluding them builds an exe that dies at its own import line. The bundle
# check in the workflow is there to catch that happening again.
EXCLUDES = ['unittest', 'pydoc', 'xml', 'lib2to3']

a = Analysis(
    [SOURCE],
    pathex=[],
    binaries=[],
    datas=[('net/dpctrl.dll', '.')],   # netplay DLL, read at install time
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
    optimize=0,
)
# Tcl's time zone tables and the Tcl/Tk message catalogues (4 MB on disk)
# are not used by a tkinter window; the catalogues only translate Tk's own
# dialogs, which fall back to English. The encodings stay: Tcl loads the
# system code page from them at startup.
TRIM = ('_tcl_data/tzdata', '_tcl_data/msgs', '_tk_data/msgs')
a.datas = [d for d in a.datas
           if not d[0].replace('\\', '/').startswith(TRIM)]

pyz = PYZ(a.pure)

# A one-dir build: the exe is the bootloader and the script; the runtime,
# the libraries and the netplay DLL sit beside it in _internal/. Nothing is
# unpacked at startup, and fewer scanners object to it than to a one-file
# exe.
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='vo_patch-%s' % VERSION,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,                  # no console window behind the GUI
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=str(_version_file),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='vo_patch',
)
