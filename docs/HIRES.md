# The resolution patch

The game renders at 640x480 and assumes it everywhere. The patch moves it
to any size (the window applies 1920x1080), by rewriting every place the
assumption is baked and adding a blob of new code (ui.asm) in an appended
section, .vohr. This is its design, the game structures it stands on, and
the record of porting it to the other builds.

## What is patched

About 280 sites over the retail executable, in families:

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

The rasterizers index per-row pointers through 0x6c75c8 (and 0x6c7d48);
the refs are rebased to a table in the section, filled by the blob.

### Coverage mask

The renderers track drawn spans through a pointer at 0x6c8ce8, advancing
by the row stride (0x50 packed into or/add immediates); every advance,
pack and stride site moves to the new stride. The margins either side
of the 640-wide picture are under *The 2D layer* below.

### Projection

0x51444d (renderer A) and 0x5cc39d (B) build X as sx*f and Y as sx*sy*f
from SCALE/ASPECT globals (0x6bc1e4/e8, 0x6c8b24/28). The scale comes
from the height, so widescreen keeps the vertical FOV and sees more at
the sides. In split, the stock game halves the scale because its
viewports were half-size; the FOV block (retail file 0x1c782d) is
replaced with a per-layout factor from section data.

### Render lists and pool

Each renderer's polygon record pool, side array and flush list move to
the section and grow to HIRES_POLYS records: renderer A's (2500 at
0x6db7e0/0x6f8ca0/0x6fdabc, cap in two insert paths and the flush) and
renderer B's (2000 at 0x708a90/0x720190/0x72400c, the same three caps,
the flush's at 2500). The bucket heads (LIST_A 0x7001d0, LIST_B
0x725f50) stay where they are; the list-insert loads are hooked for
HUD-pass vertex scaling.

### The 2D layer

The game has two near-identical copies of its 2D engine, called four
times a frame: 0x4800d0/0x4804f0 for viewport 1 before and after the 3D
flush, 0x5670c0/0x5674f0 for viewport 2. Each call is redirected to a
stub that runs pre, then the original, then post.

pre redirects the 2D draw to a canvas in the section (480 guard rows
either side, since split draws outside the viewport) and post composites
every pixel that differs from a copy back onto the viewport,
nearest-neighbour, centred, so translucency blends against the real
background. A canvas row that is all copy - most rows of the HUD phase -
is found with one compare and every viewport row sampling it is skipped.

In the pre-3D call of a 1P or single-viewport frame the canvas has the
viewport's aspect (rounded to an even width) and the game's surface
pointer is offset by D_XO, so its 80-tile picture lands in the middle
and the margins either side are the blob's to draw (ui.asm margins,
run at the start of post while the game's globals still address the
canvas). They are drawn from the game's own plane B - the
82x62-word tile ring the pre-3D call walks, 80x60 shown, scrolled by
whole tiles - through the game's own destination and blit helpers:
margin tile column c shows what the picture shows at c mod 80, with
the scroll and the ring's wrap applied the way the walker applies
them, so the seams are the walker's own. A ring row is continued only
when none of its 80 tiles is empty: the encounter grid (a 2-by-4-tile
pattern) and the static (a 40-by-8-tile block, jittered by random
whole-tile scrolls) are fields and continue in step, holes included;
any other row - art on a backdrop, the plane hidden - takes the
picture's top-row colour when that row is all one colour and black
otherwise, on 2D-only screens (the last 3D flush drew nothing, DRAWN
0x6d0dc4). With 3D behind, the margins are the 3D. Each engine has its
own ring, scroll words, watermarks and helpers (RING1.. / RING2.. in
ui.asm); the port tool reads them off the build's own walker rather
than the map, which pairs the twin engines.

The destination helper halves its coordinates whenever the split flag
0x6bc948 is set (the `test al, 2` before its two `sar`s is dead code,
so any two-player game halves), which is why pre hides the flag from
the game's walker; margins hides it the same way, since post has
restored it by then. Seen on video first: every margin tile at half
scale, the right-hand ones over the picture.

Limits of the canvas: OFF_PITCH is 2048 bytes, so the pre-3D canvas is
capped at 1024 px wide (2.13:1; a wider viewport gets a centred canvas
and its outer columns untouched), and the patcher refuses widths over
2040 because the coverage-mask stride is an 8-bit immediate. Neither
has been exercised past 16:9; see *Queued work*.

Rules tried at pixel level before the map was read, on record: per-row
edge pixels streaked the SEGA wipe, the static and the grid; the first
canvas column past 4:3 assumed the game clears the wide canvas, which
the title and splash do not, and gave black; a margin-wide mirrored
corner block reached the title logo and the splash panel, and
mirroring broke the grid's spacing at the seams; fixed tiles and a
per-frame period search drifted against what was measured on video as
a 48.33-px period. That period was real and was the compositor's:
fit computed 1/s as (2^24 / s) << 8, which drops the low byte of the
16.16 reciprocal - 0.68% short at s = 2.25 - so the 2D layer was
stretched by that much, its last four columns and three rows never
shown, and every 2D element drifted right and down with its position
(1.7 px at the health labels, 2 at the weapon names). The reciprocal
is now 2^32 / s rounded up, and the composite is verified against an
exact nearest-neighbour model at 1080p and 720p, every canvas pixel
shown once.

The photo backdrops of the two-player screens - the machine select and
the network waiting cards - are 64x48-tile blocks placed at ring (9, 6),
the low-resolution window: 496x384 of picture in the 640x480 (the
blocks' last two tile columns are blank), framed in black even at 4:3. margins recognises one by the differences between
three of its cells - (32, 24) and (0, 47) against (0, 0), the same in
every build - since the game rebases a block's tile indices to wherever
its tiles were loaded (0x2380 read back as 0x154e on the select); with
the plane shown and unscrolled it stages the 496x384 into the guard rows
after the canvas and fills the whole canvas from it, nearest-neighbour,
keeping its aspect: the width is filled and the rows outside the height
are cropped equally top and bottom (53 of 384 each side at 16:9, 6 at
4:3). The margin row loop is skipped. Side by side gets the same, each
engine over its own 4:3 canvas, so a waiting card fills its half's
width; top/bottom is left alone, since
the frame rows its viewport shows (HIRES_HUD_TB_ROWS, 48..432) are
exactly the photo's.
Plane A is the post-3D call's, so the text on those screens is not
scaled. See *The photo backdrops* below.

The ending roll is the 2D layer's too: its bands, window, edge clipping
and cut are under *Credits letterboxing, found and removed* below.

### HUD passes

Twenty pass prologues (UI_PASS_FUNCS) are wrapped by 20-byte stubs that
count pass depth in and out; submissions inside a pass get the HUD
projection, and the insert hooks rescale their vertices to the HUD frame.

In side-by-side split the 4:3 frame is centred in a taller viewport.
While a round's HUD is on screen (MODE 4, SUBMODE 9..0x0c, 0x14, 0x15,
0x1b; the second viewport reads 2P's own machine), the frame's top rows
- everything above HIRES_HUD_BAND, row 110: the timer and health bars -
are pinned to the top of the viewport instead of riding with the centred
frame. A polygon belongs to the top region when its lowest vertex is
above the band, and gets the top-aligned y offset. The 2D canvas is
composited in two slices to match, the pre-fill sampling each slice from
where it will land; the gap the top slice leaves in the centred frame is
simply not composited.

The state gate keeps the machine-select hangar, a 3D scene inside a HUD
pass, in one piece; top/bottom split and 1P have no centred frame and are
unaffected.

The HUD spread, in a round (D_ROUND less sub-states 0xd/0xe). At 4:3
the timer box sits 82 px from the left edge; a centred frame carries
it towards the middle. The post-3D 2D layer's band rows (above
HIRES_HUD_BAND) are composited with the columns left of
HIRES_HUD_SPREAD's split column (230: the timer and its digits) moved
left by the frame's inset, so the timer keeps its 4:3 distance from
the left edge; the columns right of it - PLAYER/ENEMY and the bars,
which in stock span 233..523 - move by 320 - HIRES_HUD_BARS units
(-58), which centres them as a group. The pre-fill samples each column
from where it lands (spread_row). The TOTAL time - 2D rows from 380,
columns from 420 - moves right by the inset. Polygons get the same
offsets in rescale when the outermost HUD pass is the in-game HUD
(PASS0/PASS1: the bars, their frames and the timer box; hud_enter
records the calling stub in D_PASSFN) and the polygon lies wholly in
the band and wholly on one side of the split column; the reticle and
the weapon strips are other passes and stay, and in a round the enemy
marker is drawn outside the passes. The inset is the smaller of the
frame's two margins inside the viewport (D_SPREAD), computed per call;
at 4:3 and in side-by-side split it is 0 and nothing moves.

Top/bottom split draws the HUD at the side-by-side scale rather than
the 4:3 that fits its half-height viewport (1.406 instead of 1.125 at
1080p: HIRES_HUD_TB_ROWS caps it so frame rows 48..432 stay on
screen), so both layouts show the HUD at one size. The frame's empty
top and bottom rows fall outside the viewport: fit clips the
composite, the pre-fill zero-fills the canvas rows it cannot sample,
and the HUD projection's centre (D_CXH/D_CYH) is the viewport's, so
the game culls what falls outside.

The damage flash is the unit quad drawn as a grid of tiles (4 by 3 at
16:9) through attribute block 0x791ad0, sized a fifth over the 4:3 frame,
so the outer tiles stopped 80 px short of each edge at 1080p. It is an
ordinary list-A polygon set; it hid from the insert log because no single
tile is wide, and from the pixel watch until tools/vo-dbg.sh recovered the
record from the flush frame. ui.asm fill, run by both insert hooks after
the pass rescale, pushes the edge vertices of a polygon of that attribute
block to x = 0 and x = W-1 in a round (D_ROUND: MODE 4 and a sub-state
in HIRES_SPLIT_STATES, per viewport). 0x791ad0 is the game's flat red,
used from forty-odd sites, among them the enemy marker's arrow and
triangle (56 units, below), whose slide ends with the arrow's tip on the
inset; a polygon narrower than 120 HUD units (a tile is about 195) is
left alone. Nothing else is touched.

The rescale and the 2D composite agree to the pixel: a HUD vertex at
frame x lands at round(x * s + offset), and a canvas column at
floor(X / s). They did not until the compositor's reciprocal was fixed
(*The 2D layer* above); the 3 px polygon shift that used to compensate
is gone.

### Single viewport in a split game

The call to the viewport setup (0x5c811b) and the two flush calls
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
than the split flag. When the layout changes, polygons already submitted
that frame keep the old placement for that one frame.
HIRES_DEBUG_STATES prints both machines' states on the frame through the
game's GDI text: MODE SUBMODE, MODE2 SUBMODE2, D_SHOW, then margins'
tile scroll x, y and fine y and the two photo cells it read, in hex.

### GDI text

The draw at 0x5c991c centres on (x,y); the wrap routine 0x5c8da6 and the
loading-strip rects carry 640x480 bounds; the two 24px LOGFONTs (.data
0x2c7370/0x2c73f0) scale by h/480.

### F4 and the size switch

The handler (0x5c74ec, past the network-game guard) jumps to the blob's
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
the same switch, through three hooks on the dialog's own path:

- opening, where the dialog stages FLAGS into 0xbe4300 (0x427ec4): the
  hook sets bit 0 from the mode word, so the radio shows the size the
  game is in;
- OK, where the dialog writes FLAGS back (0x42823c): the hook strips
  bit 0 out into D_F4WANT instead;
- the resume after DialogBoxParamA (0x5c7494): the hook runs the switch
  if D_F4WANT differs from the mode - never in a network game - then
  falls into GRESUME.

The ini keeps the choice as ScrSize bit 0, through two more hooks: the
load's join (0x50bcc1) strips the bit from FLAGS and applies the second
size's sites right there - the load runs in WM_CREATE, before the mode
is set - and the save's read (0x50c0c6) supplies bit 0 from the mode
word.

FLAGS bit 0 itself - stock's Screen=Normal window - is never set at
runtime. The Screen Split radio labels become "Ver"/"Hor" and the
redundant Type2 is hidden.

### The enemy marker

The lock brackets and their off-screen arrow (0x5485c0 renderer A,
0x475930 B) share a 4:3 on-screen test: the enemy's projected x within
256 in focal-600 space, and the bearing within 0x1500 of the camera
before the edge arrow takes over. The x bounds are scaled by the
visible width and the bearing window widened by the extra half field of
view, both computed from the size; the y bounds stay with the vertical
view. Sized for the 1P view, as the hangar window is: in a split
viewport the arrow comes a few degrees late.

### The lock-on line

The flat grey band that flashes across the picture as the enemy comes
on from the side is not the resolution patch's: it is stock's, and it
has its own Essential patch now (**Fix the lock-on line**, asm/
lockline.asm). Found while chasing it here, with tools/vo-dbg.sh wedge,
submit and clip in turn: the 2D quad submits (0x5d79a0 renderer A,
0x5e2a80 B) project their z = 1.0 vertices with the aspect slot of the
projection, and the clipper re-projects what it clips or subdivides
with the 3D slot, focal length included, 600 times the size. The
marker's leader line to the distance readout is the quad that gets long
enough. Two dead ends on the way, both reverted: dropping polygons with
a vertex under z = 4 took a piece of the hangar floor; saturating
overflowed 16-bit vertex words changed nothing.

### Stage objects: the see-through switch

Each stage object has a solid model and a meshed twin (tables 0x64bab0
and 0x64bcc0, 12 bytes an entry; 0x64bed0/0x64bfc0 on the eight-way
stages), and the object drawer (0x5be1d0 renderer A, 0x422f20 B;
0x5bed0d/0x5bf07c and 0x423a5d/0x423dcc are uncalled copies) picks one
per object per frame: the object's authored direction code for the
player's grid cell (2 bits per object in the stage's cell table at
0x623d04; 3 bits, 45-degree steps, on the eight-way stages at
0x623d10) is compared with the camera yaw (0x1ae35fa), and the solid
model is used while the yaw is within 0x4800 (101 degrees; 0x3800
eight-way) of the code's direction, the meshed one beyond - the object
stands between the camera and the machine. 0x1ae35e4 set forces the
mesh.

The codes are 90-degree quantised, so an object beside the machine
meshes once the yaw is 11 degrees past square to it: at 4:3 that is at
the picture's edge, at 16:9 while it is whole on screen. Both windows
get 22.5 degrees more (0x1000 on the add, twice that on its compare,
six pairs a renderer); an object behind the machine is 135 degrees or
more off and still meshes. Record flag 0x1000 is the flush's edge
expansion (0x5d2102), not the mesh; the mesh is attribute bit 0x2000
of the twin's own polygons.

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

Section layout: code (under 0x1a70), pass stubs at 0x1a70, data block at
0x1c00 (D_*), split FOV factors at 0x1c48, the F4 site table at 0x1f00,
then the canvas and its copy (UI_OFF/UI_OFF_SIZE), the mask, the row
table and the polygon pool. Immediates in the game's address range
(0x401000..0x3660000) are read as addresses by the port tool, so sizes
in 16.16 are shifted at runtime rather than written as constants.

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
timestamp. hires_install translates sites through port_sites, which
retargets jumps into the section from the moved sites and redoes the
rewrites that depend on the build's own bytes - the mask spans'
interleaved instructions, the imul register, the roll's lea register,
the credits jne-to-jmp displacements, and the FOV block, which goes out
of line to UI_FOV when the build's is shorter - swaps the blob's
addresses by position, builds the F4 table from the translated sites,
and checks everything against the build's own bytes.

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
   the map. JP generated clean with that; OEM's table did not change,
   so its rasterizer fault had another cause.
6. Bytes the patch writes can carry retail addresses of their own. The
   rasterizer fault on both builds was four of the MMX mask spans: their
   interleaved instruction is `mov ecx, [FB_PITCH]` (or the row
   pointer), and build_sites wrote retail's `between` bytes verbatim, so
   the ported span loaded ecx - the next span's row pointer - from a
   retail address. apply() only checked that the build's load, add and
   store were in the span, so it passed. port_sites now rebuilds every
   mask span from the build's own bytes, and the port record above is
   a reminder that a site's new bytes need reading for addresses as
   well as its old ones.
7. A shorter block is not a matching block. JP dropped the 0.95
   projection case, so its FOV block is 54 bytes to retail's 82; the
   82-byte replacement ran 28 bytes into the viewport function's
   pointer stores, which is why JP's surface pointer was always null
   and the blob stood down every frame. The old-bytes check passed
   because the table's old bytes are read from the build. port_sites
   now cuts the block at the build's last fstp [ASPECT_B] and, when the
   replacement does not fit, writes a call to a copy in the section
   (UI_FOV). tools/portaudit.py compares the instruction shapes covering
   every site between retail and the build, gate or no gate; read its
   list rather than counting it - a function vomap does not know
   decodes as nothing, and the imul and roll sites differ by register
   on purpose.
8. A jump's new bytes are retail's until proven otherwise. The credits
   jne-to-jmp sites (0x080074, 0x167084) carry retail's displacement,
   and port_sites rebased every e8/e9 from the retail site, so the
   ported jmp landed on retail's target inside JP's engine - mid
   instruction, three bytes into a mov. port_sites now takes the
   displacement from the build's own jcc, and only rebases jumps whose
   target is in the appended sections; the F4 exit is built from A()
   and already right.

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
in low resolution under FSFLAGS bit 2 - the test byte, 4 at 0x5c9a98,
the same bit the docs call low resolution everywhere else. Fonts are
HFONTs at 0x6c8568/6c built at 0x5c8ca0 from the LOGFONTs at
0x6c8570/0x6c85f0 (24px) and 0x6c85b0/0x6c8630 (16px, low resolution);
the 24px pair is what the patch scales, in .data at file
0x2c7370/0x2c73f0. The wrap routine is 0x5c8da6.

### F5 Graphic Settings

The dialog template sits in .rsrc around file 0x60be42, located by its
strings because the frame rate patch grows the template before it. Screen
Split radios are ids 0x42e/0x42f/0x431 (Type1/2/3), handler 0x427fa0;
Type2 duplicated Type1 under the new layouts, so it is hidden (style
0x50010009 -> 0x40010009).

### The encounter screen

A frame-scripted 2D sequence at 0x4d1fa8 (counter 0x34155e4, run
every frame from 0x4cd2bc), on plane B of engine 1 unless said:

- frames 1..8 (and 0x9d..0xa4 on the way out): a 40x8-tile block
  (0x9390c0, tiles 0x3680..0x36cf) written twice across, 8 rows a
  frame, through 0x4cf430 at the cursor 0x4cd8c3 sets (column -9 is
  ring column 0);
- frames 9..0x20 and 0x9e..0xba: the scroll words 0x34155c8 (x, snapped
  to whole tiles) and 0x34155d0 set from 0x540454 each frame - that is
  the static;
- frame 0x21 zeroes the scrolls; 0x22 writes 60 rows of a 40-tile strip
  from 0x93ac30 + (row & 3) * 128, twice across: the grid, period 2
  tiles by 4 rows;
- 0x23 and 0x31: 0x4d30f3 prints the machine's data (plane A, names
  from 0x600fd8); 0x25..0x32: 0x4d1e06; 0x23..0x9b: 21 sprite entries
  at 0x345d270 (tile data 0x6083e0, colours 0x1df / 0x7fe0 by frame
  bit) are the wireframe mech; 0x38 plays the machine's name (0x601608).

Plane B's ring is 0x1cc6700, 82 words a row, the low-resolution window
at ring (9, 6) = 0x1cc6aea, which the cursor's (0, 0) is; the walker
reads the tile column (scroll x bits 3..9) as the number of columns
drawn from ring word 82 - sx before it resets to the row start, the
tile row likewise against 62. Title (0x4d1408, a 62x34 block from
0x66ae60 at ring (9, 6)) and splash (0x4d053b, 64x48 from 0x93e968) are
plane B blocks narrower than the picture; tile 0 is skipped by the
blit, so their outer columns are untouched frame. The same static
block is used at 0x482353, 0x533cdc and 0x53440b.

### The photo backdrops

Three 64x48-tile blocks in .data - 0x940168, 0x92f8ba, 0x941968 - copied
raw into plane B at the low-resolution window (0x1cc6aea, ring (9, 6))
by 0x4d1328(n) through the row copy 0x4d056c, which also serves the
title (0x4d1408) and splash (0x4d053b). Callers: the two-player machine
select 0x5a1cf6 (block 2; the 1P sub-state 3 handler 0x5a21fc branches
to it on 0x6bea88/0x6bea8c), sub-states 2 and 6 (0x4b1b87, 0x4b1f99,
block 1) and the network cards 0x4d54c1 and its neighbours, which pick
one of the three at random. 0x4d1328 leaves plane B's scroll y at
0x4000 (tile row 0). Each block has 1088..1804 distinct tiles; the
splash has 287. The maps are identical in the JP and OEM builds, which
is why the recognition is by cell content rather than by address. The
copy is a plain memcpy, yet the ring reads back with every index
lowered by one constant (0xe32 on the select, another on the encounter
card): the tile loader rebases the block to its bank slot after the
copy, so the recognition uses differences between cells, which the
rebase preserves. HIRES_DEBUG_STATES shows the two cells it reads.

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

### The match HUD and the enemy marker

Angles in the game code are 16-bit binary angles (0x10000 a turn).
0x401bfa is atan2: fpatan, scaled by 0x63f41c, result in ax. 0x41d730
and 0x41d750 are sin and cos of one. A machine's camera yaw is the word
at +0x184, its own at +0x84, its target pointer at +0x74, the horizontal
distance to it at +0x7c; position is floats at +8/+0xc/+0x10.

The in-game HUD passes (0x5b5f2e A, 0x4c468e B) draw only the bars,
frames and timer, through the sprite helpers 0x5b6dfe/0x4c555e, plus the
distance digits (0x4cf5d4, tile text via the 0xbf7758 cursor) and the
low-armour tile warning (0x4d05c9). The reticle is its own pass:
0x4d0280 (A) and 0x531f6a (B) draw the ring and the 21 tick marks from
the table at 0x600ed8 (angle, texture, palette per entry).

The enemy marker and the off-screen arrow are 0x5485c0 (A) and 0x475930
(B), called from the six frame drivers of each renderer, gated on scene
0x1ae3690 == 0xa and reading their own float pools (0x606928.. and
0x5fc090..). Each transforms the target into camera space (0x44efd0),
scales by 600/z (the double in the pool), and tests x within +-256 and
y within 192 (block one) or +-192 (block two) - the marker's window is
smaller than the picture on purpose. Block one draws the red triangle
above the enemy when the machine's +0x1b2 is under 5, and the edge
arrow once the bearing less the camera yaw is outside +-0x1500: the
sign picks a rotation of 0x4000 or 0xc000 through 0x408940 and a
translate of +-30. Block two draws the lock brackets, the lock text and
the double-lock cross (0x7ee23c), timed by 0x1acfe10/0x1acfe14. The
tutorial's own words for the arrow: "JUMP TO REGAIN VISUAL CONTACT OF
ENEMY. THE ARROW POINTS THE ENEMY!" (strings at file 0x285fcc).

Not the marker, though they read the same way: 0x4c3f40/0x4c4231 (B)
and 0x5806bc (A) are the chase camera, with windows of 0x1b80/0x1800
and 0x2000 and the atan2 of the target's height; 0x57858c walks the four
arena walls (0x1ad0148, 0x2c apart) for a bearing per quadrant;
0x4c5772's 0x1000/0x2000 buckets at 0x4c692f quantise a heading into
stick directions; 0x57f1b0 and 0x5829c3 are the demo and tutorial frame
drivers, whose four 0x4a729f quads are the iris wipe, not a marker.

### Ending

The title state machine's sub-state 0x20 handler 0x59081f runs phases
through 0x1ad0964: 0 is the cutscene (0x58c1cc), 1 mission complete
(0x58e659), 2 the roll (0x58ecd0), and anything past 2 falls to the tail
that stops the music and moves on to name entry. The skip patch works by
writing a phase past 2.
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

The window itself is 50 tile rows tall - 400 lines, the 0x32 stored at
0x480568 (its branch twin at 0x480556 is the 44-row low-resolution
window), plus the partial entering row - top-aligned in a 64-row ring.
The writer (the
command interpreter at 0x4cdab4, cursors 0xbf7758/0xbf775c, driven by
the hand-timed schedule in 0x58ecd0) fills each ring row just as it
scrolls up to the window's foot; below that the ring holds stale content
from 512 lines ago. The upshot: lines appear at row 400 with nothing
real below them, and making them enter at row 480 by feeding earlier
would mean re-timing the authored schedule, which is not attempted.

With the clip wired, a line slides in at the foot a glyph row at a time
and out through the real row 0, the scenery clean behind both edges.

The roll's end is timed for the band too: the ending driver (0x58ecd0,
frame counter 0x1ad09f0) scrolls plane A one line a frame from frame
0x116 and, at 0x10e2, zeroes the scroll word and hides the plane
(0x4d4504) - with the last line still 72 lines short of row 0, under
the band in stock. The schedule (0x6bcd48, 188 entries of strip,
width, rows; feed frame 0x116 + 8 * rows so far) has the last text
entry, 155, fed at 0x0f2e into ring rows 51..53, followed by blank
entries through 0x1136. Row 53 is off the top at 0x112e; rows 51..53
come round to the foot from 0x1136, and a blank entry clears only
columns 9..59 of a row (51 words at +0x12, 0x58f12a), so a line's
right-hand tiles survive it - the flash of white at the bottom right
seen with a 96-frame extension. Both thresholds therefore move 80
frames, to 0x1132; the scene change at 0x1490 is untouched.

The roll's pixels are strips loaded incrementally into two banks
(0x66c1a0/0x66c1a8, 565->555 converted at 0x480f30) and blitted as plain
copies (0x47f2e0); there is no per-pixel fade anywhere in the path. The
arcade's letterbox and shade call (0x4a70c6/0x4ff496 into
0x514629/0x5cc579, alpha 0x80 arguments) remains a stubbed no-op on PC -
it lands in bare sort flushes at 0x5d1a70/0x5dc940.

Removing the bands exposed garbage they had been hiding, and chasing it
down produced two facts about the engine. Both were verified by running
the patched walkers under Unicorn - the actual machine code, executed in
an emulator against a fabricated tile ring and canvas, its output
checked line by line.

First, the window's foot is not the walker's row count but its
destination helper: 0x480930 rejects tile rows past 0x31, so the roll
never reached below line ~392-400 - consistent with the writer feeding at
the foot.

Rather than stretch the window down into the writer's workspace, the
patch moves the whole window down thirteen rows: plane A's walker starts
thirteen ring rows earlier ((coarse - 13) mod 64 in place of the plain
coarse row), draws 60 rows, and the cap rises to 0x3d.

The feed turns out to land about two rows inside the old window, at the
0x31 cap. (Measured on video: at ten rows of shift a fed line arrived
~0.6 s after its ring row had already entered the window, showing the
row's stale 512-line-old content in the meantime.) With thirteen rows of
shift the window's bottom row, start+47, trails the feed at start+48 by
a full row - so every line is complete before it slides in at line
480.

Why thirteen exactly, and not more: the writer composes each entering
line in three scratch rows of the ring, cursor..cursor+2, which sit at
start-16..start-14. At fourteen rows of shift the third scratch row and
the top display row were the same row, so glyph bottoms flashed at the
screen's top edge for a few frames per line - caught on 60 fps video. At
thirteen the window clears all three scratch rows, and a row that has
scrolled off the top keeps three rows of margin before the feed comes
around to rewrite it.

Verified under emulation: continuous coverage of lines 0..479 at every
fine value, wrap across the ring boundary included.

The shift moves everything on plane A, not only the roll: the SEGA card
that follows it (0x58a570 at frame 0x1490, seven rows of 17 tiles
written through the writer at cursor row 22 = ring row 28, which the
walker shows at line 224 once the cut has zeroed the scroll) came out
104 lines low, at 328 - 13 rows, measured on a 1080p capture as 234
px. It is written at cursor row 9 instead (the `push 0x16` at
0x58a576), so it lands at line 224 as in stock. The in-game copy of
the driver (0x4489d6, card writer 0x4443b0) is left alone, as its cut
is.

Making lines enter at 480 surfaced one consequence on screen. The strip
loaders keep availability watermarks (0xbf5f7c/0xbf5f78, 24 stores
across the 12 loader variants) that round the load cursor up, so a tile
with only part of its 128 bytes read counted as loaded. Harmless while
fed rows sat below line 400 - nothing drew them - but entering at 480
they were drawn half-read, which garbled the streamed name strips at the
moment of entry. The fix floors the rounding: a partial tile is culled
for the one frame it takes to finish, and the strips are 128-aligned so
no final tile is stranded.

Second, the roll uses a second tile plane (rings 0x1cc3e00/0x1cc41ea,
its own scroll word 0x34155c6, 60 rows). Its destination helpers
(0x480a52, 0x480eb0 and kin) index the 2D row table with rows past
either end - deliberately, in stock: a second table sat right behind
this one in memory, so an out-of-range row landed in it and wrapped the
plane vertically. Relocating the row table broke that arrangement. The
new neighbour is the coverage mask, so the plane's over-the-edge rows
sprayed writes at garbage offsets - the corruption seen over the top
line once the top band was gone.

The ten unbounded loads now go through ui.asm rowsafe. In range it is
the plain table load; out of range it wraps by the frame height, the way
stock's table adjacency did; and during the roll it parks the write on
the canvas guard instead, since the wrapped sliver was only ever hidden
by the bands anyway.

### Credits assets sized for the band

With the bands gone, two things the roll draws show their edges. The
star field is not a backdrop: 0x58f59c tiles one star quad from the
ending's asset file (heap slot 0xa, fld_bosn.bin) in 1800-unit columns
from -9000 up to a limit that steps up through the roll, and drifts the
grid 0.9 units a frame; at 4:3 the first column reaches the edge just
as the roll ends, at 16:9 it runs out 36 s early. The start column
moves left by the extra width in columns plus one (imm32 at file
0x18ea14; the limit side ends behind the moon and is left alone). The
moon is a 26x52 card at z=80 (0x58f4ce) whose projected half height is
0.325 focal lengths against the frame's 0.399, so the disc stopped 100
px short of the top and bottom at 1080p; its commit (0x58f549) goes
through credits_moon, which scales the matrix by UI_CMOON (1.25) first.
Neither depends on the size, only on the aspect and the missing band,
so the F4 table has no entries for them.

Left as is: the star texture is undersampled at 640x480 - about 2.5
texels a pixel - so stock shows a sparser field of single pixels that
twinkle as the grid drifts under the sampling; at 1080p every texel
shows and nothing pops. Reproducing that would mean rasterising those
tiles at the 640 step, which is not attempted. Also open: in the SEGA
card phase after the roll both the stars and the moon are cut at about
row 950 of 1080, under a band in stock.

## Queued work

- **The rest of the HUD at 16:9.** The spread moves the top band and
  the TOTAL time; the weapon strips and the distance digits keep their
  4:3 places.
- **A per-layout marker window.** The x bounds and the bearing window
  are baked for the 1P view; split viewports want their own, keyed on
  D_LAYOUT like the split FOV factors. Needs the two compare pairs to
  read section data instead of immediates.
- **JP and OEM on video.** Both tables ship after lessons 6 and 7;
  neither has had the retail build's hours of play yet.
- **Wider than 16:9.** Untested and partly blocked: the width limit of
  2040 (the mask stride immediates), the 1024-px canvas (OFF_PITCH and
  the cap in pre; the guard/copy layout in UI_OFF_SIZE follows it), the
  1440p-and-up sizes generally, and on the game's side the enemy
  marker window and the hangar's angle window, sized from the width
  but only checked at 16:9. The credits star grid takes its column
  count from the aspect and is untested past 16:9.

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
- the FOV block is 54 bytes; the replacement goes through UI_FOV
- engine B's roll keeps its count in edi (0x161da3), engine A's in ebx

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
    python3 tools/portaudit.py maps/jp.pkl
                                        # instruction shapes at every site
    # splice maps/jp_port.txt and maps/oem_port.txt between the PORT
    # TABLES markers in vo_patch.py, replacing what is there
    python3 tools/selftest.py RETAIL.exe   # and JAPAN, OEM: the digests
    # go into EXPECTED_ALL in tools/selftest.py

The port tables must be regenerated after any change to ui.asm: they
list every blob reference by position (UI_REFS), and the positions
move. An immediate in the game's address range comes out as a FAIL in
maps/*_port.txt; shift or add such values at runtime instead.

tools/vo_patch_hires.py is the import shim the tools use to read the
tables out of vo_patch.py. tools/uibuild.py --check runs in
tools/check.py.
