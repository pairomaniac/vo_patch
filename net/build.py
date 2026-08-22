#!/usr/bin/env python3
"""Compile net/dpctrl.c and bake it into vo-patch.py.

Same idea as asm/build.py: the patcher ships as one file, so it cannot read
net/ at runtime and the DLL rides along as text between marker comments.

    python3 net/build.py            compile, compress, write the blob
    python3 net/build.py --check    is the blob current? writes nothing

--check compares a hash of dpctrl.c against the one recorded beside the
blob, rather than recompiling and comparing bytes. Two mingw versions do
not produce identical output from identical source, so a byte comparison
would fail on any machine but the one that last ran this. The hash answers
the question that actually matters: was the blob made from this source?

Never edit the blob by hand. The next run discards it.
"""

import base64
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(HERE, 'dpctrl.c')
DEF = os.path.join(HERE, 'dpctrl.def')
TARGET = os.path.join(ROOT, 'vo-patch.py')

CC = 'i686-w64-mingw32-gcc'

# Left to itself the linker stamps a build time and picks an image base, so
# two runs over unchanged source give different bytes and a 40 KB blob turns
# up in the diff for nothing. Pinned, the same mingw produces the same file.
# Stripping is done by the linker: a separate strip pass re-stamps the time.
IMAGE_BASE = 0x6c540000

BEGIN = '# --- netplay blob: written by net/build.py, do not edit ---'
END = '# --- end netplay blob ---'

EXPORTS = ('InitialDirectPlay', 'DestroyDirectPlay', 'SendDirectPlay',
           'SendDirectPlayWaitMessage', 'ReceiveDirectPlay',
           'SWDataSendReceive', 'CloseProvider')


def source_hash():
    with open(SRC, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()


def compile_dll(workdir):
    """Build a stripped 32-bit DLL and return its bytes."""
    if not shutil.which(CC):
        sys.exit('%s not found. Install gcc-mingw-w64-i686.' % CC)

    out = os.path.join(workdir, 'dpctrl.dll')
    cmd = [CC, '-O2', '-shared', '-s', '-o', out, SRC, DEF,
           '-lws2_32', '-lwinmm', '-Wl,--enable-stdcall-fixup',
           '-Wl,--no-insert-timestamp', '-Wl,--image-base=%#x' % IMAGE_BASE]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode:
        sys.stderr.write(proc.stderr)
        sys.exit('compile failed')

    with open(out, 'rb') as f:
        blob = f.read()

    if blob[:2] != b'MZ':
        sys.exit('output is not a PE file')
    for name in EXPORTS:
        if name.encode() not in blob:
            sys.exit('export missing from the build: %s' % name)
    return blob


def render(blob, sha):
    """The blob as it appears in vo-patch.py."""
    packed = base64.b64encode(zlib.compress(blob, 9)).decode()
    lines = [packed[i:i + 72] for i in range(0, len(packed), 72)]
    body = '\n'.join("    '%s'" % line for line in lines)
    dll_sha = hashlib.sha256(blob).hexdigest()
    return (
        "%s\n"
        "# Source: net/dpctrl.c, compiled by net/build.py.\n"
        "NETPLAY_SRC_SHA = '%s'\n"
        "# sha256 of the compiled DLL, so the patcher can tell its own build\n"
        "# from an older one already installed.\n"
        "NETPLAY_DLL_SHA = '%s'\n"
        "NETPLAY_DLL_Z = (\n"
        "%s\n"
        ")\n"
        "%s" % (BEGIN, sha, dll_sha, body, END))


def splice(text, block):
    start = text.index(BEGIN)
    stop = text.index(END) + len(END)
    return text[:start] + block + text[stop:]


def recorded_hash(text):
    for line in text.splitlines():
        if line.startswith('NETPLAY_SRC_SHA'):
            return line.split("'")[1]
    return None


def main(argv):
    check = '--check' in argv

    with open(TARGET, encoding='utf-8') as f:
        text = f.read()
    if BEGIN not in text or END not in text:
        sys.exit('markers not found in vo-patch.py')

    sha = source_hash()

    if check:
        have = recorded_hash(text)
        if have != sha:
            print('net/dpctrl.c and the baked DLL disagree.')
            print('  source: %s' % sha)
            print('  blob:   %s' % (have or 'none'))
            print('Run: python3 net/build.py')
            return 1
        print('netplay blob matches net/dpctrl.c (%s)' % sha[:12])
        return 0

    with tempfile.TemporaryDirectory() as workdir:
        blob = compile_dll(workdir)

    with open(TARGET, 'w', encoding='utf-8') as f:
        f.write(splice(text, render(blob, sha)))

    print('baked %d bytes of DLL (%s)' % (len(blob), sha[:12]))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
