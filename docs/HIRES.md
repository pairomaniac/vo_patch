# The resolution patch

The game renders at 640x480 and assumes it everywhere. The patch moves it
to any size (the window applies 1920x1080), by rewriting every place the
assumption is baked and adding a blob of new code (ui.asm) in an appended
section, .vohr. This is its design, the game structures it stands on, and
the record of porting it to the other builds - including why that port is
currently withheld.

## What is patched

Around 196 sites over the retail executable, in families:

- Mode and window: SetDisplayMode pushes (0x5c56a2), the mode selector
  0x5c9404 (which returns failure for modes it does not know - the
  original crash), the EnumDisplayModes callback, CreateWindowExA, the
  availability flag the enumeration sets, and the Screen=Normal window
  geometry.
- Viewport: 0x5c8317 sets the picture globals per mode. The FB family
  lives at 0x6bf5a8..0x6bf5bc: locked surface pointer, pitch, current row
  pointer (0x6bf5b0/b4), width, height. The row maths (y*240 lea/shl
  chains) become imuls of the new height.
- Row tables: the rasterizers index per-row pointers through 0x6c75c8
  (and 0x6c7d48); the refs are rebased to a table in the section, filled
  by the blob.
- Coverage mask: the renderers track drawn spans through a pointer at
  0x6c8ce8, advancing by the row stride (0x50 packed into or/add
  immediates); every advance, pack and stride site moves to the new
  stride. The blob blacks margins only when the last 3D flush drew
  nothing (DRAWN 0x6d0dc4).
- Projection: 0x51444d (renderer A) and 0x5cc39d (B) build X as sx*f and
  Y as sx*sy*f from SCALE/ASPECT globals (0x6bc1e4/e8, 0x6c8b24/28). The
  scale comes from the height, so widescreen keeps the vertical FOV and
  sees more at the sides. In split, the stock game halves the scale
  because its viewports were half-size; the FOV block (retail file
  0x1c782d) is replaced with a per-layout factor from section data.
- Render lists and pool: the polygon cap (cmp eax, 2500) and the pool
  tables 0x6db7e0/0x6f8ca0 are raised and rebased (--polys); the flush
  walks bucket heads (0x6fdac0 family) which move with them; the
  list-insert loads (LIST_A 0x7001d0, LIST_B 0x725f50) are hooked for
  HUD-pass vertex scaling.
- 2D layer: the game has two near-identical engine copies; each is
  called around by pre/post hooks (stubs call pre, the original
  (0x4800d0/0x4804f0, 0x5670c0/0x5674f0), then post). pre redirects the
  2D draw to a canvas in the section (480 guard rows either side, since
  split draws outside the viewport) and post composites every pixel that
  differs from a copy back onto the viewport, nearest-neighbour, centred,
  so translucency blends against the real background.
- HUD passes: twenty pass prologues (UI_PASS_FUNCS) are wrapped by
  20-byte stubs that count pass depth in and out; submissions inside a
  pass get the HUD projection, and the insert hooks rescale their
  vertices to the HUD frame. In side-by-side split the 4:3 frame is
  centred in a taller viewport; in the sub-states that draw the in-game HUD
  (MODE 4, SUBMODE 9..0x0c, 0x14, 0x15, 0x1b; 2P's own machine for the
  second viewport) frame rows above HIRES_HUD_BAND (110) are pinned to the
  viewport top instead: a polygon whose lowest vertex is above the
  threshold gets the top-aligned y offset, and the 2D canvas is
  composited in two slices, the pre-fill sampling each from where it
  will land. The band the slice leaves in the centred frame is not
  composited. The state gate keeps the machine-select hangar, a 3D
  scene inside a HUD pass, in one piece; top/bottom split and 1P have
  no centred frame and are unaffected. Measured against stock, the health bars sit
  inside their frames to within a pixel in 640x480 terms, which is the
  precision the original geometry was placed at; the remaining top/bottom
  asymmetry is a 0.7 px offset in the frame position, tunable if it ever
  shows.
- Single viewport in a split game: the call to the viewport setup
  (0x5c811b) and the two flush calls (0x5c8166, 0x5c8178) go through
  the section. While either player's machine is in a sub-state listed
  in HIRES_SINGLE_STATES (3 and 4, the machine select, whose portrait
  grid is the same in both halves) the frame is one full-screen
  viewport from that player's engine, P1 first: the setup runs with
  the split flag cleared, so both renderers get the 1P geometry, then
  viewport 2's base (0x6bf5b0) and mask offset (0x7087a0) are pointed
  at the whole surface; the other renderer's flush runs with its
  draw-skip flag (0x6c84c8 / 0x6c84cc) set, which sorts and empties
  the list without drawing, and its 2D layer is not composited. The
  blob keys its layouts on D_LAYOUT (1P or single, Ver, Hor) rather
  than the split flag. The polygons submitted in the frame a transition
  happens in still carry the previous frame's HUD placement.
- GDI text: the draw at 0x5c991c centres on (x,y); the wrap routine
  0x5c8da6 and the loading-strip rects carry 640x480 bounds; the two
  24px LOGFONTs (.data 0x2c7370/0x2c73f0) scale by h/480.
- F4 and the 320x240 mode are defused (0x5c74da falls through to its
  exit, the direct menu command jumps there); no baked scale covers that
  mode. The F5 Screen Split radio labels become "Ver"/"Hor" and the
  redundant Type2 is hidden.
- The machine-select hangar draw (0x59e4ea) is wrapped to widen the
  platform-mech angle window for the wider FOV, fading the outermost
  mech to a silhouette where the game stops guaranteeing palettes.

## The blob

ui.asm, assembled to UI_CODE by tools/uibuild.py: keystone, with equates
substituted, comments stripped, and each .long emitted as a unique
4-byte marker overwritten with the value afterwards (keystone has no
data directives). Label offsets are read back by appending a jmp to
every wanted label - appending shifts nothing - and decoding the rel32s;
uibuild splices the blob and every derived offset constant into
vo_patch.py, and regenerates UI_REFS: every game-address dword in the
blob by exact position, from its own disassembly, excluding call/jmp
rel32s (those are fixed per site). `tools/uibuild.py --check` is in the
check pipeline, by fingerprint when keystone is not installed, so ui.asm
and the committed blob cannot drift.

Section layout: code (under 0x1600), pass stubs at 0x1600, data block at
0x1800 (D_*), split FOV factors at 0x1848, then the row table, the mask,
the canvas and its copy (UI_OFF/UI_OFF_SIZE).

The blob stands down for a frame whose saved surface base is null
(pre stores the flag, post honours it): the Japanese build runs its 2D
layer at boot, before any viewport setup, and the composite would read
through a null pointer.

## Porting to other builds

The machinery: tools/vomap.py maps retail onto another build at function
level; tools/votrans.py translates addresses through it;
tools/hiresport.py generates a per-build PORT table - every site offset,
the build's own original bytes, every named address (ADDR) and every
blob reference (UI_REFS), plus per-build pass prologue lengths - keyed
by PE timestamp. hires_install translates sites, retargets jumps into
the section from the moved sites, swaps the blob's addresses by
position, and checks everything against the build's own bytes.

Resolution tiers, strictest first: the instruction map; named-address
consistency (a site that is also an ADDR entry keeps the same answer);
ordered byte patterns for code the map cannot match (the polygon-pool
cmp/lea cluster, the render-list insert loads, the flush-bucket reads,
the mask spans by their own bytes with the mask pointer swapped, the GDI
480 idiom in both modrm forms); caller-vote function location with a
prologue check; boundary-aligned context windows inside a located
function; and a MANUAL dict for what only eyes can place. Two
invariants make generation fail rather than guess: the four 2D call
targets must be pairwise distinct (they are derived from the build's own
call instructions, because vomap deduplicates the two identical engine
copies and once paired engine B's post with engine A's), and no two
sites may map to one build offset.

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

**Picture geometry.** 0x5c88ac chooses the viewport per mode each frame:
FLAGS (0x6bf598) bit 0 is the 496x384 Screen=Normal window, bit 1 the
320x240 window, else 640x480; it writes the picture origin and size
(0x6bf578/7c, 0x6bf5b8/bc) that overlay.asm reads for the credits
prompt. ScrSize is staged at 0xbe4300/0xbe42fc before it reaches FLAGS.
F4's handler is 0x5c74da (guarded on split 0x6bc94c == 2), the direct
320x240 menu command lands at 0x5c79aa, both exit through 0x5c7dfe; the
mode selector 0x5c9404 knows two modes and fails on any other.

**GDI text.** 0x5c991c draws centred on (x, y) through
GetTextExtentPoint32, halving under FSFLAGS bit 4 in low resolution.
Fonts are HFONTs at 0x6c8568/6c built at 0x5c8ca0 from the LOGFONTs at
0x6c8570/0x6c85f0 (24px) and 0x6c85b0/0x6c8630 (16px, low resolution);
the 24px pair is what the patch scales, in .data at file
0x2c7370/0x2c73f0. The wrap routine is 0x5c8da6.

**F5 Graphic Settings.** The dialog template sits in .rsrc around file
0x60be42, located by its strings because the frame rate patch grows the
template before it. Screen Split radios are ids 0x42e/0x42f/0x431
(Type1/2/3), handler 0x427fa0; Type2 duplicated Type1 under the new
layouts, so it is hidden (style 0x50010009 -> 0x40010009).

**Machine select (the hangar).** A platform mech is drawn while its
angle is within a window of the camera's - 0x59e3a1 tests 31.57 degrees
left and 28.43 right, .data doubles at file 0x2213f0/0x2213f8, sized for
a 4:3 view. The draw is the call at 0x59e4ea to 0x59cb93, which the blob
wraps (hangar_draw). The game keeps palettes only for the selection and
the previous one - rows 1/3/5/7 and 9/11 of the colour planes - loaded
asynchronously, so a mech beyond 28.43 degrees right can come out in
someone else's colours; that is why the widened window fades the
outermost mech to a silhouette over the twelve degrees inside that
edge instead of drawing it lit. The PRESS BUTTON and MACHINE SELECT
words on that screen are sprites, not text.

**Ending.** The title state machine's sub-state 0x20 handler 0x59081f
runs phases through 0x1ad0964: 0 the cutscene (0x58c1cc), 1 mission
complete (0x58e659), 2 the roll (0x58ecd0); anything past 2 falls to
the tail that stops the music and moves to name entry, which is what
the skip writes. The in-game machine's analogue is 0x44a523/0x4489d6.
The roll's tile ring buffer is 0x1cc18ea.

**Credits letterboxing, an open lead.** The roll's text fades out near
the top and bottom edges (visible band about rows 90..385 of 480), the
3D scenery behind it unaffected, so the fade is applied inside the
tile/scroll-layer draw rather than as a screen overlay; the source has
not been found. The arcade's letterbox and shade call
(0x4a70c6/0x4ff496 into 0x514629/0x5cc579, alpha 0x80 arguments) is a
stubbed no-op on PC - it lands in bare sort flushes at
0x5d1a70/0x5dc940 - so it is not that. Next place to look: the scroll
blit path (the 0x47f0c0 family) and the ring buffer above.

## Queued work

- **F4 as a real 1080/720 toggle.** Every baked scale (2D, HUD,
  projection, split geometry, row maths) becomes a pair selected at
  runtime on the flag. A rework of its own; the game's own plumbing
  only offers the exact half (960x540) for free, since every low-mode
  path is a halving.
- **Credits fade removal**, once the lead above lands.
- **The JP and OEM port.** JP's table now generates clean and needs a
  boot; OEM's rasterizer fault is unexplained - see lesson 5.

## Per-build facts worth keeping

Japanese rerelease (stamp 345107fa): FSFLAGS 0x6bb2b0, FB family from
0x6bb2c0, mask pointer 0x6c4a78 (derived from its spans; the map's
vote said 0x6c4938, which is not it), SCALE_A 0x6b7f44, FOV block at file
0x1c2093; the 0.95 hardware projection case was dropped in this
revision (only the 1.0 store remains, at 0x1c1fef); PASS8/PASS9 were
restructured (0x4cde03, 0x52e2ad) with 5-byte prologues; 2D targets
pre1 0x47ec80, post1 0x47f0a0, pre2 0x562400, post2 0x562830; the GDI
wrap function stores its 480 with an ebp-disp32 form (c785).

USA OEM (stamp 3317246a): renderer A's row maths uses eax where retail
uses ecx (the imul rewrite derives its register from the build's own
lea/shl chain); post2 is 0x567060 (its collapse was latent, caught by
the distinctness invariant); mask pointer 0x6c8ca8 family per its own
spans.

Both: prologue frame layouts and instruction encodings differ from
retail even inside "exact" stream matches, so linear placement inside a
matched function is only trustworthy with a byte or shape check at the
landing spot.

## Rebuilding

    python3 tools/uibuild.py            # ui.asm -> UI_CODE + constants
    sh tools/maps.sh RETAIL.exe JAPAN.exe OEM.exe
                                        # maps/*.pkl and maps/*_port.txt
    # splice a clean table between the PORT TABLES markers in vo_patch.py

tools/vo_patch_hires.py is the import shim the tools use to read the
tables out of vo_patch.py. tools/uibuild.py --check runs in
tools/check.py. Site or blob changes move the pinned all-patches MD5s
in tools/selftest.py for every build they apply to.
