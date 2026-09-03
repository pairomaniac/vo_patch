#!/usr/bin/env python3
"""Run the resolution blob under Unicorn, on the retail build.

    python3 tools/uiemu.py /path/to/v_on.exe     # or its .bak

Patches the exe at 1920x1080 in memory, maps the image, and calls the
blob's routines with the globals a frame would have. Nothing here proves
the game feeds the blob what it assumes - only video does that - but it
pins what the blob does with what it is given:

  photo    the game's own plane B walker plus the blob, with a photo block
           in the ring the way the loader leaves it (indices rebased): the
           picture fills a 16:9 viewport and a side-by-side half
  spread   the HUD spread at 1080p: 2D markers and HUD quads land where
           the timer, bars and TOTAL should, the pre-fill samples the
           viewport from where each column lands, and an untouched canvas
           composites nothing
  layouts  D_LAYOUT and the inset per layout, top/bottom's frame cut to
           the viewport, side by side's band pin

Needs nasm (for the label offsets) and python3-unicorn; without either it
says so and passes, like the gui check without a display.
"""

import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

RETAIL_MD5 = 'a464b0ff32d5bab499f265e45658504e'
W, H = 1920, 1080
SURF, STACK, RET, TILES, REC = (0x10000000, 0x20000000, 0x30000000,
                                0x40000000, 0x50000000)
# blob data offsets (asm/ui.asm D_*) the tests read or write
D = dict(BASEPTR=0x1c1c, LW=0x1c28, XOFF=0x1c34, PHASE=0x1c38, S=0x1c5c,
         ROUND=0x1c88, SPREAD=0x1c98, S16=0x1cc8, CXH=0x1dc8, CYH=0x1dcc,
         OFFX16=0x1dd0, OFFY16=0x1dd4, PASSFN=0x1dc0, PINON=0x1e70,
         PINSUB=0x1e74, SHOW=0x1e88, LAYOUT=0x1e8c, OFF=0xf3b00)
OFF_PITCH = 2048
# game globals the blob and the walker read (retail)
G = dict(FB_PTR=0x6bf5a8, FB_PITCH=0x6bf5ac, FB_ROW=0x6bf5b0,
         FB_ROW2=0x6bf5b4, FB_W=0x6bf5b8, FB_H=0x6bf5bc, SPLIT=0x6bc948,
         FLAGS=0x6bf598, MODE=0x1ae3594, SUBMODE=0x1ae3690, DRAWN=0x6d0dc4,
         RING1=0x1cc6700, SCRX1=0x34155c8, SCRY1=0x34155d0,
         RING2=0x1ef1140, SCRX2=0x1efb728, SCRY2=0x1efb730,
         BANKA=0x66c1a0, BANKB=0x66c1a8, WMA1=0xbf5f7c, WMB1=0xbf5f78,
         BANKA2=0x6bc9dc, BANKB2=0x6bc9e4, WMA2=0x1ad0034, WMB2=0x1ad0030,
         MODEHOOK=0x66c1ac, MODEHOOK2=0x6bc9e8,
         UPDATE1=0x48d5b0, UPDATE2=0x5b54c0,
         PHOTO2=0x941968)
PHOTO_W, PHOTO_H = 496, 384
REBASE = 0xe32                  # what the select's loader took off block 2


class Emu:
    def __init__(self, exe):
        from unicorn import Uc, UC_ARCH_X86, UC_MODE_32
        import vo_patch as vp
        import uibuild
        self.vp = vp
        buf = bytearray(exe)
        vp.hires_install(buf, W, H)
        src = open(os.path.join(os.path.dirname(HERE), 'asm', 'ui.asm')).read()
        blob, self.offs = uibuild.assemble(src)
        if blob != vp.UI_CODE:
            raise SystemExit('asm/ui.asm does not match the committed blob')
        pe = vp._PE(buf)
        self.mu = mu = Uc(UC_ARCH_X86, UC_MODE_32)
        end = max(s['vaddr'] + max(s['vsize'], s['rsize'])
                  for s in pe.sections)
        mu.mem_map(pe.base, (end + 0xfff) & ~0xfff)
        for s in pe.sections:
            mu.mem_write(pe.base + s['vaddr'],
                         bytes(buf[s['raddr']:s['raddr'] + s['rsize']]))
        self.blob = pe.base + pe.sections[-1]['vaddr']
        for addr, size in ((SURF, 0x800000), (STACK, 0x10000),
                           (RET, 0x1000), (TILES, 0x400000), (REC, 0x1000)):
            mu.mem_map(addr, size)
        mu.mem_write(RET, b'\xf4')
        # one tile bank: every tile a flat colour of its index
        bank = b''.join(struct.pack('<64H', *[((t * 37 + 1) & 0x7fff)] * 64)
                        for t in range(0x4000))
        mu.mem_write(TILES, bank)
        for k, v in dict(BANKA=TILES, BANKB=TILES, BANKA2=TILES, BANKB2=TILES,
                         WMA1=0x4000, WMB1=0x4000, WMA2=0x4000, WMB2=0x4000,
                         MODEHOOK=-1, MODEHOOK2=-1, FB_PTR=SURF, FB_ROW=SURF,
                         FB_ROW2=SURF, MODE=4, SUBMODE=9).items():
            self.w32(G[k], v)
        # the walkers' first call is a sprite painter; nothing to paint
        mu.mem_write(G['UPDATE1'], b'\xc3')
        mu.mem_write(G['UPDATE2'], b'\xc3')
        off = self.file_off(pe, G['PHOTO2'])
        self.photo = exe[off:off + 64 * 48 * 2]

    @staticmethod
    def file_off(pe, va):
        for s in pe.sections:
            if s['vaddr'] <= va - pe.base < s['vaddr'] + s['rsize']:
                return va - pe.base - s['vaddr'] + s['raddr']
        raise ValueError(hex(va))

    def w32(self, addr, v):
        self.mu.mem_write(addr, struct.pack('<I', v & 0xffffffff))

    def r32(self, off):
        return struct.unpack('<i', self.mu.mem_read(self.blob + off, 4))[0]

    def d(self, name, v):
        self.w32(self.blob + D[name], v)

    def viewport(self, w, h, split=0, flags=0):
        for k, v in dict(FB_PITCH=W * 2, FB_W=w, FB_H=h, SPLIT=split,
                         FLAGS=flags, DRAWN=0).items():
            self.w32(G[k], v)

    def call(self, off, ebx=None, edx=None):
        from unicorn.x86_const import UC_X86_REG_ESP, UC_X86_REG_EBX, \
            UC_X86_REG_EDX
        mu = self.mu
        mu.reg_write(UC_X86_REG_ESP, STACK + 0x8000 - 4)
        mu.mem_write(STACK + 0x8000 - 4, struct.pack('<I', RET))
        if ebx is not None:
            mu.reg_write(UC_X86_REG_EBX, ebx)
        if edx is not None:
            mu.reg_write(UC_X86_REG_EDX, edx)
        mu.emu_start(self.blob + off, RET)

    def routine(self, name, **regs):
        self.call(self.offs[name], **regs)

    def surface(self, x, y):
        return struct.unpack('<H', self.mu.mem_read(SURF + (y * W + x) * 2,
                                                    2))[0]

    def fill_surface(self, f):
        rows = [struct.pack('<%dH' % W, *[f(x, y) for x in range(W)])
                for y in range(H)]
        self.mu.mem_write(SURF, b''.join(rows))

    def canvas_write(self, x, y, v):
        self.mu.mem_write(self.blob + D['OFF'] + y * OFF_PITCH + x * 2,
                          struct.pack('<H', v))

    def load_photo(self, ring, scrx, scry, rebase):
        words = struct.unpack('<%dH' % (64 * 48), self.photo)
        block = [(w - rebase) & 0xffff for w in words]
        rows = bytearray(82 * 62 * 2)
        for r in range(48):
            rows[(6 + r) * 164 + 18:(6 + r) * 164 + 146] = \
                struct.pack('<64H', *block[r * 64:(r + 1) * 64])
        self.mu.mem_write(G[ring], bytes(rows))
        self.mu.mem_write(G[scrx], b'\0\0')
        self.mu.mem_write(G[scry], struct.pack('<H', 0x4000))
        self.block = block

    def tile(self, r, c):
        return (self.block[r * 64 + c] * 37 + 1) & 0x7fff


def check_photo(e, label, vw, split, show, stub, ring, layout, scale,
                yoff):
    """The walker and the blob over a viewport vw x H; a photo fills it."""
    e.viewport(vw, H, split)
    e.load_photo(ring, ring.replace('RING', 'SCRX'),
                 ring.replace('RING', 'SCRY'), REBASE)
    e.d('SHOW', show)
    e.mu.mem_write(SURF, b'\0' * (W * H * 2))
    e.call(e.offs[stub])
    lw = e.r32(D['LW'])
    step = min((PHOTO_W << 16) // lw, (PHOTO_H << 16) // 480)
    x0 = ((PHOTO_W << 16) - lw * step) >> 1
    y0 = ((PHOTO_H << 16) - 480 * step) >> 1
    bad = 0
    for vy in range(yoff, H - yoff, 37):
        for vx in range(0, vw, 41):
            cx, cy = int(vx / scale), int((vy - yoff) / scale)
            sx, sy = (x0 + cx * step) >> 16, (y0 + cy * step) >> 16
            got = e.surface(vx, vy)
            near = {e.tile(min(47, max(0, sy // 8 + dy)),
                           min(61, max(0, sx // 8 + dx)))
                    for dy in (-1, 0, 1) for dx in (-1, 0, 1)}
            if got != e.tile(sy // 8, sx // 8) and got not in near:
                bad += 1
    edge = e.surface(vw - 1, H // 2) != 0
    ok = bad == 0 and edge and e.r32(D['LAYOUT']) == layout
    print('  photo %-12s %s  (%d off-model samples, right edge %s)'
          % (label, 'ok' if ok else 'FAIL', bad, 'drawn' if edge else 'black'))
    return ok


def check_spread(e):
    """1P at 1080p, the HUD phase: markers, quads, pre-fill, no-op."""
    e.viewport(W, H)
    e.d('BASEPTR', G['FB_PTR'])
    e.d('PHASE', 0)
    e.d('SHOW', 0)
    e.fill_surface(lambda x, y: ((x * 3 + y * 5) & 0x7fff) | 1)
    e.routine('pre')
    s, spread = e.r32(D['S']) / 65536, e.r32(D['SPREAD'])
    xoff = e.r32(D['XOFF'])
    bars = (320 - e.vp.HIRES_HUD_BARS) * e.r32(D['S16']) >> 16
    splitc, botrow, botcol = e.vp.HIRES_HUD_SPREAD
    band = e.vp.HIRES_HUD_BAND
    ok = spread == xoff == 240 and e.r32(D['ROUND']) == 1

    def dx(c, r):
        if r < band:
            return -spread if c < splitc else bars
        return spread if r >= botrow and c >= botcol else 0
    # the pre-fill sampled each column from where it will land
    canvas = e.mu.mem_read(e.blob + D['OFF'], OFF_PITCH * 480)
    bad = 0
    for r in range(0, 480, 7):
        for c in range(0, 640, 5):
            vx, vy = int(xoff + c * s) + dx(c, r), int(r * s)
            got = struct.unpack_from('<H', canvas, r * OFF_PITCH + c * 2)[0]
            bad += got != (((vx * 3 + vy * 5) & 0x7fff) | 1)
    print('  spread pre-fill     %s  (%d mismatches)'
          % ('ok' if not bad else 'FAIL', bad))
    ok &= bad == 0
    # nothing drawn: nothing composited
    before = e.mu.mem_read(SURF, W * H * 2)
    e.routine('post')
    same = bytes(e.mu.mem_read(SURF, W * H * 2)) == bytes(before)
    print('  spread no-op        %s' % ('ok' if same else 'FAIL'))
    ok &= same
    # markers: timer digits, PLAYER label, TOTAL, weapon name, band edge
    marks = [(100, 70, 0x1111), (300, 70, 0x2222), (500, 400, 0x3333),
             (300, 200, 0x4444), (100, band, 0x5555)]
    for x, y, v in marks:
        e.canvas_write(x, y, v)
    e.routine('post')
    for x, y, v in marks:
        want = int(xoff + x * s) + dx(x, y)
        rows = range(int(y * s), int((y + 1) * s) + 1)
        hit = sorted({vx for vy in rows for vx in range(W)
                      if e.surface(vx, vy) == v})
        good = hit and abs(hit[0] - want) <= 1
        print('  spread 2D (%3d,%3d)  %s  -> x %s, expected %d'
              % (x, y, 'ok' if good else 'FAIL', hit[:1] or '-', want))
        ok &= bool(good)
    # HUD quads through rescale: timer left, bars centred, weapon put
    cxh, cyh, s16 = e.r32(D['CXH']), e.r32(D['CYH']), e.r32(D['S16'])
    offx = e.r32(D['OFFX16'])
    for name, passfn, (x0, x1, y0, y1), shift in (
            ('timer', 0, (82, 226, 62, 89), -spread << 16),
            ('bars', 20, (288, 523, 62, 92),
             (320 - e.vp.HIRES_HUD_BARS) * s16),
            ('weapon', 0, (146, 200, 300, 320), 0),
            ('reticle pass', 8 * 20, (82, 226, 62, 89), 0)):
        e.d('PASSFN', passfn)
        for i, (x, y) in enumerate([(x0, y0), (x1, y0), (x1, y1), (x0, y1)]):
            e.mu.mem_write(REC + 0x10 + i * 4,
                           struct.pack('<hh', x - 320 + cxh, y - 240 + cyh))
        e.routine('rescale', ebx=e.blob, edx=REC)
        got = struct.unpack('<hh', e.mu.mem_read(REC + 0x10, 4))[0]
        want = ((x0 - 320 + cxh) * s16 + offx + shift + 0x8000) >> 16
        good = got == want
        print('  spread quad %-12s %s  x0 %d, expected %d'
              % (name, 'ok' if good else 'FAIL', got, want))
        ok &= good
    return ok


def check_layouts(e):
    want = {  # (w, h, split, flags): layout, xoff, spread, oyh
        'ver': ((960, H, 1, 0), (1, 0, 0)),
        'hor': ((W, 540, 1, 1), (2, 510, 510)),
        '1P': ((W, H, 0, 0), (0, 240, 240)),
        '4:3': ((1440, H, 0, 0), (0, 0, 0)),
    }
    ok = True
    for name, ((w, h, split, flags), (layout, xoff, spread)) in want.items():
        e.viewport(w, h, split, flags)
        e.d('BASEPTR', G['FB_PTR'])
        e.d('PHASE', 0)
        e.d('SHOW', 0)
        e.routine('pre')
        got = (e.r32(D['LAYOUT']), e.r32(D['XOFF']), e.r32(D['SPREAD']))
        e.routine('post')               # pre redirects the surface pointer
        good = got == (layout, xoff, spread)
        print('  layout %-4s          %s  layout %d, inset %d, spread %d'
              % (name, 'ok' if good else 'FAIL', *got))
        ok &= good
    # top/bottom: frame rows 48..432 fill the 540 rows, the rest are cut
    e.viewport(W, 540, 1, 1)
    e.fill_surface(lambda x, y: 0)
    e.routine('pre')
    top, bottom = e.vp.HIRES_HUD_TB_ROWS
    marks = [(300, top - 1, 0x1111), (300, top + 1, 0x2222),
             (300, bottom - 1, 0x3333), (300, bottom, 0x4444)]
    for x, y, v in marks:
        e.canvas_write(x, y, v)
    e.routine('post')
    surf = e.mu.mem_read(SURF, W * 540 * 2)
    seen = {v: struct.pack('<H', v) in surf for _x, _y, v in marks}
    good = [seen[v] for _x, _y, v in marks] == [False, True, True, False]
    print('  layout hor rows      %s  rows %d..%d shown, outside cut'
          % ('ok' if good else 'FAIL', top + 1, bottom - 1))
    ok &= good
    # side by side: a quad in the band takes the top-aligned y, one
    # below it the centred frame's
    e.viewport(960, H, 1, 0)
    e.routine('pre')
    e.routine('post')
    cxh, cyh, s16 = e.r32(D['CXH']), e.r32(D['CYH']), e.r32(D['S16'])
    offy, pinsub = e.r32(D['OFFY16']), e.r32(D['PINSUB'])
    for name, (y0, y1), sub in (('band', (62, 92), pinsub),
                                ('below', (300, 320), 0)):
        for i, (x, y) in enumerate([(100, y0), (200, y0), (200, y1),
                                    (100, y1)]):
            e.mu.mem_write(REC + 0x10 + i * 4,
                           struct.pack('<hh', x - 320 + cxh, y - 240 + cyh))
        e.d('PASSFN', 0)
        e.routine('rescale', ebx=e.blob, edx=REC)
        got = struct.unpack('<hh', e.mu.mem_read(REC + 0x10, 4))[1]
        want = ((y0 - 240 + cyh) * s16 + offy - sub + 0x8000) >> 16
        good = got == want and e.r32(D['PINON']) == 1
        print('  layout ver pin %-5s %s  y0 %d, expected %d'
              % (name, 'ok' if good else 'FAIL', got, want))
        ok &= good
    return ok


def main(path):
    try:
        __import__('unicorn')
    except ImportError:
        print('no python3-unicorn, so the blob was not run')
        return 0
    from credittest import pristine
    from uibuild import have_nasm
    if not have_nasm():
        print('no nasm, so the blob was not run')
        return 0
    exe, read = pristine(path, RETAIL_MD5)
    if exe is None:
        print('%s is not the retail build; the harness knows only its '
              'addresses' % path)
        return 0
    e = Emu(exe)
    ok = check_layouts(e)
    ok &= check_spread(e)
    # stub1/stub3: the pre-3D 2D call of engine 1 / engine 2
    ok &= check_photo(e, '1P 16:9', W, 0, 0, 'stub1', 'RING1', 0, 2.25, 0)
    ok &= check_photo(e, 'single, P1', W, 1, 1, 'stub1', 'RING1', 0, 2.25, 0)
    ok &= check_photo(e, 'single, P2', W, 1, 2, 'stub3', 'RING2', 0, 2.25, 0)
    ok &= check_photo(e, 'side by side', 960, 1, 0, 'stub1', 'RING1', 1,
                      1.5, 180)
    print('OK' if ok else 'FAILED')
    return 0 if ok else 1


if __name__ == '__main__':
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1]))
