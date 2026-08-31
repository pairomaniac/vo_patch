# The resolution patch

The game renders at 640x480 and assumes it everywhere. The patch moves it
to any size (the window applies 1920x1080), by rewriting every place the
assumption is baked and adding a blob of new code (ui.asm) in an appended
section, .vohr. This is its design, the game structures it stands on, and
the record of porting it to the other builds - including why that port is
currently withheld.

## What is patched

Around 240 sites over the retail executable, in families:

### Mode and window

SetDisplayMode pushes (0x5c56a2), the mode selector 0x5c9404 (which
returns failure for modes it does not know - the original crash), the
EnumDisplayModes callback, CreateWindowExA, the availability flag the
enumeration sets, and the Screen=Normal window geometry.

### Viewport

0x5c8317 sets the picture globals per mode. The FB family lives at
0x6bf5a8..0x6bf5bc: locked surface pointer, pitch, current row pointer
(0x6bf5b0/b4), width, height. The row maths (y*240 lea/shl chains) become
imuls of the new height.

### Row tables

the rasterizers index per-row pointers through 0x6c75c8 (and 0x6c7d48);
the refs are rebased to a table in the section, filled by the blob.

### Coverage mask

the renderers track drawn spans through a pointer at 0x6c8ce8, advancing
by the row stride (0x50 packed into or/add immediates); every advance,
pack and stride site moves to the new stride. The blob blacks margins
only when the last 3D flush drew nothing (DRAWN 0x6d0dc4).

### Projection

0x51444d (renderer A) and 0x5cc39d (B) build X as sx*f and Y as sx*sy*f
from SCALE/ASPECT globals (0x6bc1e4/e8, 0x6c8b24/28). The scale comes
from the height, so widescreen keeps the vertical FOV and sees more at
the sides. In split, the stock game halves the scale because its
viewports were half-size; the FOV block (retail file 0x1c782d) is
replaced with a per-layout factor from section data.

### Render lists and pool

the polygon cap (cmp eax, 2500) and the pool tables 0x6db7e0/0x6f8ca0 are
raised and rebased (--polys); the flush walks bucket heads (0x6fdac0
family) which move with them; the list-insert loads (LIST_A 0x7001d0,
LIST_B 0x725f50) are hooked for HUD-pass vertex scaling.

### The 2D layer

the game has two near-identical engine copies; each is called around by
pre/post hooks (stubs call pre, the original (0x4800d0/0x4804f0,
0x5670c0/0x5674f0), then post).

pre redirects the 2D draw to a canvas in the section (480 guard rows
either side, since split draws outside the viewport) and post composites
every pixel that differs from a copy back onto the viewport,
nearest-neighbour, centred, so translucency blends against the real
background.

### HUD passes

twenty pass prologues (UI_PASS_FUNCS) are wrapped by 20-byte stubs that
count pass depth in and out; submissions inside a pass get the HUD
projection, and the insert hooks rescale their vertices to the HUD frame.

In side-by-side split the 4:3 frame is centred in a taller viewport; in
the sub-states that draw the in-game HUD (MODE 4, SUBMODE 9..0x0c, 0x14,
0x15, 0x1b; 2P's own machine for the second viewport) frame rows above
HIRES_HUD_BAND (110) are pinned to the viewport top instead: a polygon
whose lowest vertex is above the threshold gets the top-aligned y offset,
and the 2D canvas is composited in two slices, the pre-fill sampling each
from where it will land. The band the slice leaves in the centred frame
is not composited.

The state gate keeps the machine-select hangar, a 3D scene inside a HUD
pass, in one piece; top/bottom split and 1P have no centred frame and are
unaffected.

Measured against stock, the health bars sit inside their frames to within
a pixel in 640x480 terms, which is the precision the original geometry
was placed at; the remaining top/bottom asymmetry is a 0.7 px offset in
the frame position, tunable if it ever shows.

### Single viewport in a split game

the call to the viewport setup (0x5c811b) and the two flush calls
(0x5c8166, 0x5c8178) go through the section. The split is drawn only
while either player's machine is in a sub-state that draws a round
(HIRES_SPLIT_STATES: 9..0x0c, 0x14, 0x15, 0x1b); every other frame - the
machine select, the waiting card, the wipe, the encounter and continue
screens, and everything outside a match (MODE not 4) - is one full-screen
viewport from the engine of the player whose sub-state is lower, P1 on a
tie.

The setup runs with the split flag cleared, so both renderers get the 1P
geometry, then viewport 2's base (0x6bf5b0) and mask offset (0x7087a0)
are pointed at the whole surface; the other renderer's flush runs with
its draw-skip flag (0x6c84c8 / 0x6c84cc) set, which sorts and empties the
list without drawing, and its 2D layer is not composited.

The blob keys its layouts on D_LAYOUT (1P or single, Ver, Hor) rather
than the split flag. The polygons submitted in the frame a transition
happens in still carry the previous frame's HUD placement.
HIRES_DEBUG_STATES prints both machines' states on the frame through the
game's GDI text.

### GDI text

the draw at 0x5c991c centres on (x,y); the wrap routine 0x5c8da6 and the
loading-strip rects carry 640x480 bounds; the two 24px LOGFONTs (.data
0x2c7370/0x2c73f0) scale by h/480.

### F4 and the size switch

the handler (0x5c74ec, past the network-game guard) jumps to the blob's
f4_toggle. The sites are written for the first size; a table in the
section (UI_F4TAB_OFF, in the file) lists every site whose bytes differ
for the second size (HIRES_ALT, 1280x720) with both byte sets, plus the
size-derived data words and the idle-pass recreate's pushes in
asm/activate.asm.

The toggle copies the other set over the sites (.text is given the
writable flag; the buffers are sized for the larger of the two), reruns
the coverage-mask table init (0x5ce180) and the font build (0x5c8ca0,
cache 0x6c866c cleared), and calls the recreate (0x5c56a2) with the new
size; on failure the first set goes back and the idle pass recreates at
that.

The game's own low-resolution flag stays clear, so none of its halving
paths run. The direct 320x240 menu command (0x5c79aa) jumps to the
handler exit.

The F5 Screen radios (ids 0x420/0x421) become "720p"/"1080p" and drive
the same switch: the dialog stages FLAGS into 0xbe4300 at 0x427ec4 (hook:
bit 0 set from the mode word, so the radio shows the size in place), OK
writes it back at 0x42823c (hook: bit 0 stripped into D_F4WANT), and the
resume after DialogBoxParamA at 0x5c7494 (hook: switch if D_F4WANT
differs, not in a network game, then GRESUME).

The ini keeps the choice as ScrSize bit 0: the load's join at 0x50bcc1
(hook: strip it from FLAGS and apply the second size's sites before the
mode is set - the load runs in WM_CREATE) and the save's read at 0x50c0c6
(hook: bit 0 from the mode word).

FLAGS bit 0 itself - stock's Screen=Normal window - is never set at
runtime. The Screen Split radio labels become "Ver"/"Hor" and the
redundant Type2 is hidden.

### The hangar draw

The machine-select hangar draw (0x59e4ea) is wrapped to widen the
platform-mech angle window for the wider FOV, fading the outermost mech
to a silhouette where the game stops guaranteeing palettes.

## The blob

asm/ui.asm, assembled to UI_CODE by tools/uibuild.py: nasm, the same
assembler as the rest of asm/, but run here rather than by asm/build.py
because the blob is position independent (no org; every routine finds its
data by call/pop) and carries its own address list. Label offsets come
out of a dd table appended in a temporary copy of the source - appending
shifts nothing.

uibuild splices the blob and every derived offset constant into
vo_patch.py, and regenerates UI_REFS: every game-address dword in the
blob by exact position, from its own disassembly (capstone), excluding
call/jmp rel32s (those are fixed per site). `tools/uibuild.py --check` is
in the check pipeline; it reassembles when nasm is present - always, on
CI - and falls back to a recorded fingerprint of the source only where
nasm is missing.

Section layout: code (under 0x1660), pass stubs at 0x1660, data block at
0x1800 (D_*), split FOV factors at 0x1848, the F4 site table at 0x1b00,
then the canvas and its copy (UI_OFF/UI_OFF_SIZE), the mask, the row
table and the polygon pool.

The blob stands down for a frame whose saved surface base is null (pre
stores the flag, post honours it): the Japanese build runs its 2D layer
at boot, before any viewport setup, and the composite would read through
a null pointer.

## Porting to other builds

The machinery: tools/vomap.py maps retail onto another build at function
level; tools/votrans.py translates addresses through it;
tools/hiresport.py generates a per-build PORT table - every site offset,
the build's own original bytes, every named address (ADDR) and every blob
reference (UI_REFS), plus per-build pass prologue lengths - keyed by PE
timestamp. hires_install translates sites, retargets jumps into the
section from the moved sites, swaps the blob's addresses by position, and
checks everything against the build's own bytes.

Resolution tiers, strictest first:

1. the instruction map;
2. named-address consistency (a site that is also an ADDR entry keeps
   the same answer);
3. ordered byte patterns for code the map cannot match (the polygon-pool
   cmp/lea cluster, the render-list insert loads, the flush-bucket
   reads, the mask spans by their own bytes with the mask pointer
   swapped, the GDI 480 idiom in both modrm forms);
4. caller-vote function location with a prologue check;
5. boundary-aligned context windows inside a located function;
6. and a MANUAL dict for what only eyes can place.

Two invariants make generation fail rather than guess:

- the four 2D call targets must be pairwise distinct (they are derived
  from the build's own call instructions, because vomap deduplicates the
  two identical engine copies and once paired engine B's post with
  engine A's);
- no two sites may map to one build offset.

## What porting actually taught (the crash log record)

Every one of these shipped past byte verification and surfaced only at
runtime, which is the lesson in itself.

1. A name-based blob address swap is not enough. ui.asm assembles
   derived addresses - FB_PITCH-4 (the surface pointer), PROJ_A+8,
   hook returns like SUBMIT_A+6, the hangar callee, three IAT slots -
   that no equate names. Hence UI_REFS: positions from the blob's own
   disassembly, nothing dependent on naming discipline.
2. Identical code deduplicates. The two 2D engines are byte-twins, so
   the map collapsed them and mispaired pre/post across engines on both
   builds. Anything with a duplicate must be resolved from a
   disambiguating anchor - here, the call instructions at the stub
   sites.
3. Boot order differs between builds. JP shows a warning screen through
   the 2D layer before any viewport init; the null-surface guard exists
   because of it.
4. A fuzzy resolver that can start a pattern mid-instruction matches
   junk with junk. A whole-build 48-byte window placed the flush-bucket
   site 16 bytes off, inside two `and` instructions of JP's flush -
   corrupted list-type masks, blank screens, then a null bucket walk.
   That tier is deleted; windows align to instruction boundaries; and
   the boundary audit is the gate.
5. Identical code copies fool the map twice over. JP's mask sites all
   collided onto renderer B, and the explanation written first - that
   JP's renderer A was an MMX variant with no scalar mask code - was
   wrong: every build is MMX throughout (it is a start-up requirement),
   and the scalar mask-advance idiom is in all three, sixteen times
   each. The map had translated the mask pointer to a neighbouring
   global, so the span search found nothing and the fallbacks found
   renderer B, twice. The generator now derives the mask pointer from
   the build's own spans, and any site whose surroundings occur more
   than once in retail is paired by order of occurrence rather than by
   the map. JP generates clean with that; OEM's table did not change,
   so its rasterizer fault has another cause, still to be found. Both
   stay withheld until they boot.

## The scenes, as read off the game

Retail addresses; what the patch relies on and what was learnt around it.

### Picture geometry

0x5c88ac chooses the viewport per mode each frame: FLAGS (0x6bf598) bit 0
is the 496x384 Screen=Normal window, bit 1 the 320x240 window, else
640x480; it writes the picture origin and size (0x6bf578/7c, 0x6bf5b8/bc)
that overlay.asm reads for the credits prompt. ScrSize is staged at
0xbe4300/0xbe42fc before it reaches FLAGS. F4's handler is 0x5c74da
(guarded on split 0x6bc94c == 2), the direct 320x240 menu command lands
at 0x5c79aa, both exit through 0x5c7dfe; the mode selector 0x5c9404 knows
two modes and fails on any other.

### GDI text, at runtime

0x5c991c draws centred on (x, y) through GetTextExtentPoint32, halving
under FSFLAGS bit 4 in low resolution. Fonts are HFONTs at 0x6c8568/6c
built at 0x5c8ca0 from the LOGFONTs at 0x6c8570/0x6c85f0 (24px) and
0x6c85b0/0x6c8630 (16px, low resolution); the 24px pair is what the patch
scales, in .data at file 0x2c7370/0x2c73f0. The wrap routine is 0x5c8da6.

### F5 Graphic Settings

The dialog template sits in .rsrc around file 0x60be42, located by its
strings because the frame rate patch grows the template before it. Screen
Split radios are ids 0x42e/0x42f/0x431 (Type1/2/3), handler 0x427fa0;
Type2 duplicated Type1 under the new layouts, so it is hidden (style
0x50010009 -> 0x40010009).

### Machine select (the hangar)

A platform mech is drawn while its angle is within a window of the
camera's - 0x59e3a1 tests 31.57 degrees left and 28.43 right, .data
doubles at file 0x2213f0/0x2213f8, sized for a 4:3 view. The draw is the
call at 0x59e4ea to 0x59cb93, which the blob wraps (hangar_draw).

The game keeps palettes only for the selection and the previous one -
rows 1/3/5/7 and 9/11 of the colour planes - loaded asynchronously, so a
mech beyond 28.43 degrees right can come out in someone else's colours;
that is why the widened window fades the outermost mech to a silhouette
over the twelve degrees inside that edge instead of drawing it lit. The
PRESS BUTTON and MACHINE SELECT words on that screen are sprites, not
text.

### Ending

The title state machine's sub-state 0x20 handler 0x59081f runs phases
through 0x1ad0964: 0 the cutscene (0x58c1cc), 1 mission complete
(0x58e659), 2 the roll (0x58ecd0); anything past 2 falls to the tail that
stops the music and moves to name entry, which is what the skip writes.
The in-game machine's analogue is 0x44a523/0x4489d6. The roll's tile ring
buffer is 0x1cc18ea.

### Credits letterboxing, found and removed

During the ending (SUBMODE 0x20, the roll flag 0x66c190 set) the tail of
each engine's 2D post draw cleared frame rows 0..0x60 and 0x180..0x1e0 to
black, one row-memset (0x47e580, value 0) per row - 0x480c6c in engine 1,
0x567c7c in engine 2, halved under the low-resolution flag.

Those are the bands the roll passed behind, and they were load-bearing:
the roll's tile walkers (the windowed path of each engine's plane-A draw,
0x480520 / 0x567520) push a visible row count for the window's top tile
and for the entering tile at its foot, but the shared blit 0x47fee0
discards the argument and always draws all 8 glyph rows, so the top tile
redrew unscrolled at row 0 and the entering line popped in whole - which
the bands hid.

The patch skips both band blocks (jne -> jmp at 0x480c74 / 0x567c84) and
points 0x47fee0's entry at ui.asm roll_blit, which honours the count; the
top edge's push is re-encoded fine+8 (was 8-fine, ambiguous against the
foot's) so the blit can tell the edges apart. One glyph source row is 16
bytes in the full mode, the only mode left under this patch.

The window itself is 50 tile rows (400 lines, 0x480556, plus the partial
entering row), top-aligned, in a 64-row ring. The writer - the command
interpreter at 0x4cdab4, cursors 0xbf7758/0xbf775c, driven by the
hand-timed schedule in 0x58ecd0 - fills a ring row just as it reaches the
window's foot, with stale 512-line-old content beyond it. So lines
materialise at row 400 and there is nothing below to draw; a roll that
enters at row 480 means re-timing the authored schedule, and is not
attempted.

With the clip wired, a line slides in at the foot a glyph row at a time
and out through the real row 0, the scenery clean behind both edges.

The roll's pixels are strips loaded incrementally into two banks
(0x66c1a0/0x66c1a8, 565->555 converted at 0x480f30) and blitted as plain
copies (0x47f2e0); there is no per-pixel fade anywhere in the path. The
arcade's letterbox and shade call (0x4a70c6/0x4ff496 into
0x514629/0x5cc579, alpha 0x80 arguments) remains a stubbed no-op on PC -
it lands in bare sort flushes at 0x5d1a70/0x5dc940.

Two engine facts came out of chasing the residue the bands' removal
exposed, both verified by running the patched walkers under Unicorn
against a synthetic ring and canvas.

First, the window's foot is not the walker's row count but its
destination helper: 0x480930 rejects tile rows past 0x31, so the roll
never reached below line ~392-400 - consistent with the writer feeding at
the foot.

Rather than stretch the window down into the writer's workspace, the
patch moves the whole window down thirteen rows: plane A's walker starts
thirteen ring rows earlier ((coarse - 13) mod 64 in place of the plain
coarse row), draws 60 rows, and the cap rises to 0x3d.

The feed actually lands about two rows inside the old window, at its 0x31
cap (measured on video: a fed line arrives ~0.6 s after its ring row
would have entered at ten rows of shift, showing the row's stale
512-line-old content in the meantime), so the bottom row (start+47)
trails the feed (start+48) by a full row and a line is complete before it
slides in at line 480.

Thirteen exactly, not more: the writer composes an entering line over
ring rows cursor..cursor+2, congruent to start-16..start-14 - at fourteen
rows of shift the third compose row was the top display row, and its
glyph bottoms flashed at the screen's top edge for a few frames per line
(caught at 60 fps on video). At thirteen the window excludes all three
scratch rows, and a history row keeps three rows of margin before the
feed comes around to rewrite it.

Verified under emulation: continuous coverage of lines 0..479 at every
fine value, wrap across the ring boundary included.

One consequence surfaced on screen: the strip loaders' availability
watermarks (0xbf5f7c/0xbf5f78, 24 stores across the 12 loader variants)
round the load cursor up, so a tile with only part of its 128 bytes read
counts as loaded - invisible while fed rows sat below line 400, but drawn
half-read once they enter at 480, which garbled the streamed name strips
at the moment of entry.

The rounding is floored; a partial tile is culled for the frame it takes
to finish, and the strips are 128-aligned so no final tile is stranded.

Second, the roll uses a second tile plane (rings 0x1cc3e00/0x1cc41ea, its
own scroll word 0x34155c6, 60 rows), whose destination helpers (0x480a52,
0x480eb0 and kin) index the 2D row table with rows past either end -
stock's deliberate vertical wrap into the second table right behind it,
which relocating the table broke: the neighbours are the mask now, so the
plane's over-the-edge rows sprayed at garbage offsets, the corruption
seen over the top line once the top band was gone.

The ten unbounded loads go through ui.asm rowsafe: in range it is the
plain table load, out of range it wraps by the frame height as stock's
adjacency did, and during the roll it parks the write on the canvas guard
instead, since the wrapped sliver was only ever hidden by the bands.

## Queued work

- **The JP and OEM port.** JP's table now generates clean and needs a
  boot; OEM's rasterizer fault is unexplained - see lesson 5.

## Per-build facts worth keeping

Japanese rerelease (stamp 345107fa):

- FSFLAGS 0x6bb2b0, FB family from 0x6bb2c0, SCALE_A 0x6b7f44, FOV
  block at file 0x1c2093
- mask pointer 0x6c4a78, derived from its spans; the map's vote said
  0x6c4938, which is not it
- the 0.95 hardware projection case was dropped in this revision; only
  the 1.0 store remains, at 0x1c1fef
- PASS8/PASS9 were restructured (0x4cde03, 0x52e2ad) with 5-byte
  prologues
- 2D targets pre1 0x47ec80, post1 0x47f0a0, pre2 0x562400, post2
  0x562830
- the GDI wrap function stores its 480 with an ebp-disp32 form (c785)

USA OEM (stamp 3317246a):

- renderer A's row maths uses eax where retail uses ecx; the imul
  rewrite derives its register from the build's own lea/shl chain
- post2 is 0x567060; its collapse was latent, caught by the
  distinctness invariant
- mask pointer 0x6c8ca8 family per its own spans

Both: prologue frame layouts and instruction encodings differ from retail
even inside "exact" stream matches, so linear placement inside a matched
function is only trustworthy with a byte or shape check at the landing
spot.

## Rebuilding

    python3 tools/uibuild.py            # asm/ui.asm -> UI_CODE + constants
    sh tools/maps.sh RETAIL.exe JAPAN.exe OEM.exe
                                        # maps/*.pkl and maps/*_port.txt
    # splice a clean table between the PORT TABLES markers in vo_patch.py

tools/vo_patch_hires.py is the import shim the tools use to read the
tables out of vo_patch.py. tools/uibuild.py --check runs in
tools/check.py. Site or blob changes move the pinned all-patches MD5s in
tools/selftest.py for every build they apply to.
