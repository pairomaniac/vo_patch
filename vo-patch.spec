# -*- mode: python ; coding: utf-8 -*-
"""Windows build for the patcher.

    pip install pyinstaller
    pyinstaller vo-patch.spec

Everything the build needs is here, so the CI workflow is one command. The
version is read out of vo-patch.py's docstring, which is the only place it is
written down.
"""

import pathlib
import re

SOURCE = 'vo-patch.py'
VERSION = re.search(r'^Version (\S+)',
                    pathlib.Path(SOURCE).read_text(encoding='utf-8'),
                    re.M).group(1)

# Windows file properties. Without this the exe carries no version information
# at all, which looks broken in the properties dialog.
_parts = tuple(int(n) for n in (VERSION.split('.') + ['0'] * 4)[:4])
_version_file = pathlib.Path('build') / 'file-version.txt'
_version_file.parent.mkdir(exist_ok=True)
_version_file.write_text("""VSVersionInfo(
  ffi=FixedFileInfo(filevers=%(parts)s, prodvers=%(parts)s),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'pairomaniac'),
      StringStruct('FileDescription', 'Virtual-On (PC, 1997) patcher'),
      StringStruct('FileVersion', '%(version)s'),
      StringStruct('InternalName', 'vo-patch'),
      StringStruct('OriginalFilename', 'vo-patch-%(version)s.exe'),
      StringStruct('ProductName', 'vo_patch'),
      StringStruct('ProductVersion', '%(version)s'),
    ])]),
    VarFileInfo([VarStruct('Translation', [0x0409, 1200])]),
  ]
)
""" % {'parts': _parts, 'version': VERSION}, encoding='utf-8')

# gi is imported inside probe_gtk, and the analysis picks up imports in
# function bodies, so it has to be named here or it gets collected wherever
# PyGObject happens to be installed. The rest are stdlib packages this script
# never touches which come in transitively; together they are about 9 MB.
EXCLUDES = ['gi', 'unittest', 'pydoc', 'email', 'http', 'xml', 'lib2to3']

a = Analysis(
    [SOURCE],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

# No COLLECT, so this is a one-file build: everything lands in the exe and is
# unpacked to a temporary directory at startup.
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='vo-patch-%s' % VERSION,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,                  # no console window behind the GUI
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=str(_version_file),
)
