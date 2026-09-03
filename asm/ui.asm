bits 32
; Native widescreen: the 2D layer drawn into an offscreen canvas and
; composited onto the viewport; HUD polygons projected at 640x480 and
; rescaled at insert. Built into vo_patch.py by tools/uibuild.py, whose
; --check guards drift. docs/HIRES.md has the design and the porting
; record.
;
; 2D: four stubs replace the four 2D calls in 0x5c80df (viewport 1
; before and after the 3D flush, viewport 2 the same). The split flag
; is hidden from the 2D code while it draws, so split-screen viewports
; get the full-size layout. The canvas is pre-filled with the
; viewport's own pixels, sampled at the inverse of the 2D scale, and a
; copy is kept; after the game has drawn, only pixels that differ from
; the copy are composited back, nearest-neighbour, so translucent
; elements blend against the real background and untouched areas are
; left alone. The scaled canvas is centred on the viewport and clipped
; if larger. In the pre-3D phase (backdrops) of a 1P or single-viewport
; frame the canvas has the viewport's aspect and the game draws its
; 640-wide picture centred in it (D_XO); in split, and in the post
; phase (HUD), it is 4:3. The margins either side of the picture are
; drawn from the game's own plane B tile ring (margins): a tile row
; with no empty tile is a field - the encounter grid, the static - and
; continues into the margins at its 80-tile period; any other row
; takes the picture's top-row colour when that row is all one colour,
; else black, and only on 2D-only screens (the last 3D flush drew
; nothing), the 3D showing otherwise. The two-player screens' 496x384
; photo backdrops are rescaled over the whole canvas instead. The
; canvas has 480 guard rows above and below: the 2D code draws outside
; the viewport in split screen and expects frame memory there.
;
; HUD polygons: the functions that own the HUD projection setups are
; wrapped (hud_enter) so the pass depth is known; inside a pass the
; projection setups and the submit hooks install a 640x480 projection,
; and the insert hooks scale every polygon to the HUD frame on the
; viewport. Outside a pass everything is left alone.
;
; HUD spread: in a round on a viewport wider than 4:3, the timer (the
; 2D layer's rows above D_PINTH left of D_SPLITC, and the in-game HUD
; pass's polygons there) moves left by the frame's inset, keeping its
; 4:3 distance from the left edge; PLAYER/ENEMY and the bars move by
; D_BARDX units, which centres them as a group; the TOTAL time moves
; right by the inset (spread_row, rescale). Side by side is untouched.
;
; Split-screen HUD band: in side by side the 4:3 HUD frame sits centred
; in a taller viewport. In a match the rows of the frame above D_PINTH
; (timer, health bars) go to the viewport top instead: the insert hooks
; give a polygon whose lowest vertex is above the threshold the
; top-aligned offset, and the compositor places the canvas rows above it
; from the viewport top, the pre-fill sampling from the same rows. The
; band the slice leaves in the centred frame is not composited at all.
; Gated on split, a centred frame (D_OYH > 0) and the sub-states that
; draw the in-game HUD, so the machine-select hangar, a 3D scene inside
; a HUD pass, is not cut.
;
; Single viewport in a split game: the split only has a use in the
; sub-states that draw a round (D_SPLITST, the patcher's list). In every
; other frame - the machine select, the waiting card, the wipe, the
; encounter screen, and outside a match (MODE not 4: the boot splash,
; the title) - the frame is one full-screen viewport from one player's
; engine: the player whose sub-state is lower, P1 on a tie, which is
; the one still doing something while the other waits on a card.
; frame_setup runs the game's viewport setup with the split flag
; cleared, which gives both renderers the 1P geometry, then points
; viewport 2 at the same surface; the other renderer's flush runs with
; its draw-skip flag set (the list is still sorted and emptied) and its
; 2D layer is not composited. D_LAYOUT is what the rest of this file
; keys on: 0 1P or single, 1 side by side, 2 top/bottom.
;
; Data is ebx-relative after call/pop. The patcher writes D_MODEW/
; D_MODEH, D_ROWTAB, D_KSBS, D_SCALE/D_HUD, the float constants,
; D_CMOON, D_PINTH, D_SPLITC/D_BOTROW/D_BOTCOL, D_SPLITST, D_DEBUG and
; D_F4TAB; see the UI_ constants in vo_patch.py.
FB_PTR    equ 0x6bf5a8            ; locked surface pointer
FB_PITCH  equ 0x6bf5ac
FB_ROW    equ 0x6bf5b0            ; current row pointer
FB_W      equ 0x6bf5b8
FB_H      equ 0x6bf5bc
SPLIT     equ 0x6bc948
FLAGS     equ 0x6bf598
DRAWN     equ 0x6d0dc4          ; renderer pixel counter, cleared per frame
PROJ_A    equ 0x6db4c8          ; renderer A projection: 3D, aspect, HUD
ASPECT_A  equ 0x6bc1e8
PROJ_B    equ 0x708818          ; renderer B projection: 3D, aspect, HUD
ASPECT_B  equ 0x6c8b28
OFF_PITCH equ 2048              ; canvas up to 1024 px wide
D_BASE    equ 0x1c00            ; data block; code below, stubs at 0x1a70
D_PITCH   equ 0x1c04
D_W       equ 0x1c08
D_H       equ 0x1c0c
D_XSTEP   equ 0x1c10            ; canvas px per viewport px, 16.16
D_YSTEP   equ 0x1c14
D_Y       equ 0x1c18
D_BASEPTR equ 0x1c1c
D_MODEW   equ 0x1c20
D_MODEH   equ 0x1c24
D_LW      equ 0x1c28            ; canvas size
D_LH      equ 0x1c2c
D_DW      equ 0x1c30            ; composited size on the viewport (clipped)
D_XOFF    equ 0x1c34            ; where it starts on the viewport
D_PHASE   equ 0x1c38            ; 1 before the 3D flush
D_ROWTAB  equ 0x1c3c            ; relocated 2D row table
D_DH      equ 0x1c40
D_YOFF    equ 0x1c44
D_KSBS    equ 0x1c48            ; split FOV factors, read by the game code
D_3D      equ 0x1c50            ; pixels the last 3D flush drew
D_SPLIT   equ 0x1c54            ; saved split flag
D_YF      equ 0x1c58            ; canvas row position, 16.16
D_S       equ 0x1c5c            ; scale in use, 16.16
D_CX      equ 0x1c60            ; canvas x/y (16.16) at D_XOFF/D_YOFF
D_CY      equ 0x1c64
D_SCALE   equ 0x1c68            ; 2D scale 16.16: 1P, side by side, top/bottom
D_HUD     equ 0x1c74            ; the same as floats
D_HUDF    equ 0x1c80            ; float in use; read by the game's projection
D_CA      equ 0x1c9c            ; renderer A/B screen centre y, as the game set
D_CB      equ 0x1ca0            ; it this frame (captured in pre)
D_C65536  equ 0x1ca8            ; float constants, written by the patcher
D_C640    equ 0x1cac
D_C480    equ 0x1cb0
D_CHALF   equ 0x1cb4
D_CAX     equ 0x1cb8            ; screen centre x, as the game set it
D_CBX     equ 0x1cbc
D_PASS_A  equ 0x1cc0            ; 1 while a HUD pass is being submitted,
D_PASS_B  equ 0x1cc4            ; per renderer
D_S16     equ 0x1cc8            ; HUD scale, 16.16
D_OXH     equ 0x1ccc            ; HUD offset on the viewport, pixels
D_OYH     equ 0x1cd0
D_CXH     equ 0x1dc8            ; HUD-space centre: the viewport centre in
D_CYH     equ 0x1dcc            ; HUD units, whole, so culling sees the viewport
D_OFFX16  equ 0x1dd0            ; rescale offsets, 16.16: D_OXH less what the
D_OFFY16  equ 0x1dd4            ; centre already contributes
D_XMIN    equ 0x1cd4            ; scratch for the rescale
D_XMAX    equ 0x1db0
D_YMAX    equ 0x1db4
D_YMIN    equ 0x1cd8
D_SAVE_A  equ 0x1cdc            ; last world projection, renderer A (3)
D_SAVE_FA equ 0x1ce8            ; its focal length
D_VAR_A   equ 0x1cec            ; 1 if set by the variant without aspect
D_SAVE_B  equ 0x1cf0            ; the same for renderer B
D_SAVE_FB equ 0x1cfc
D_VAR_B   equ 0x1d00
D_DEPTH   equ 0x1d08            ; HUD pass nesting depth (see hud_enter)
D_RETS    equ 0x1d0c            ; saved return addresses, D_RETS_N deep,
D_RETS_N  equ 32                ; to 0x1d8c
D_RETD    equ 0x1ddc            ; and the depth to come back to for each
D_SP      equ 0x1dd8            ; how many are saved
D_DIM     equ 0x1e64            ; hangar: shade factor 16.16 for the mech being
                              ; drawn, 0 when none (see hangar_draw)
D_HDRET   equ 0x1e68            ; hangar_draw's return address
D_PINTH   equ 0x1e6c            ; HUD band: frame rows pinned to the top, 0 off
                              ; (patcher)
D_PINON   equ 0x1e70            ; 1 while the band applies (set in pre)
D_PINSUB  equ 0x1e74            ; D_OYH 16.16: centred less this is pinned
D_PINROWS equ 0x1e78            ; viewport rows the band covers; 0 outside
                              ; the HUD phase
D_OFFY    equ 0x1e7c            ; y rescale offset for the polygon in hand
D_YEND    equ 0x1e80            ; composite loop bound
D_YSAVE   equ 0x1e84            ; pre-fill: the centred frame's row start
D_SHOW    equ 0x1e88            ; 0 split as set, 1 viewport 1 full screen, 2
                              ; viewport 2 (frame_setup)
D_LAYOUT  equ 0x1e8c            ; 0 1P or single, 1 side by side, 2 top/bottom
D_SPLITST equ 0x1e90            ; 64-bit mask of the sub-states drawn split
                              ; (patcher)
D_DEBUG   equ 0x1e98            ; 1: print both machines' states on the frame
                              ; (patcher, HIRES_DEBUG_STATES)
D_DBGSTR  equ 0x1ea0            ; the text, 32 bytes: "MM SS MM SS SH
                              ; XXYYFF AAAA BBBB" fills 31
D_F4MODE  equ 0x1ec0            ; 0: the first size is in place, 1: the second
D_F4TAB   equ 0x1ec4            ; the F4 site table (patcher); see f4_toggle
D_F4WANT  equ 0x1ec8            ; the F5 Screen choice: 1 for the second size
D_XO      equ 0x1ecc            ; canvas x of the game's 640-wide picture in
                              ; the pre phase, 0 when the canvas is 4:3
D_MXT     equ 0x1ed0            ; margin tile columns each side
D_ERING   equ 0x1ed4            ; the engine's plane B, set per call (margins):
D_ESCRX   equ 0x1ed8            ; ring, scroll x/y, tile destination, tile
D_ESCRY   equ 0x1edc            ; blit, bank watermarks
D_EDEST   equ 0x1ee0
D_EBLIT   equ 0x1ee4
D_EWMA    equ 0x1ee8
D_EWMB    equ 0x1eec
D_MR      equ 0x1ef0            ; margins: tile row, scroll column/row, flat
D_MSX     equ 0x1ef4            ; colour, fine scroll
D_MSY     equ 0x1ef8
D_MCOL    equ 0x1efc
D_MFY     equ 0x1e5c
D_MSPLIT  equ 0x1e60            ; margins: the split flag, cleared while it runs
PRIMARY   equ 0x1ae5f40         ; the surface DRAW paints on, and the one
BACK      equ 0x1ae5f5c         ; about to be flipped over it
DRAW      equ 0x5c991c          ; GDI text (text, x, y, colour, flag), cdecl
LOOPMODE  equ 0x6bc94c          ; 1 two players, 2 network
FB_ROW2   equ 0x6bf5b4
MASKOFF   equ 0x7087a0          ; renderer B's coverage mask offset
SKIP_A    equ 0x6c84c8          ; flush draws nothing while set, per renderer
SKIP_B    equ 0x6c84cc
VIEWPORT  equ 0x5c8317          ; per-frame viewport and projection setup
FLUSH_A   equ 0x5d1db0
FLUSH_B   equ 0x5dcc80
ROWDRAW   equ 0x47f2e0          ; one glyph row: ecx dest, edx src; next
                              ; src in eax; esi kept
ROWS      equ 0x66c180          ; glyph rows per tile for the mode (8)
RECREATE  equ 0x5c56a2          ; release and create the surfaces (w, h,
                              ; bpp) cdecl, nonzero on success
MASKINIT  equ 0x5ce180          ; fills the coverage mask row table; keeps
                              ; every register
FONTS     equ 0x5c8ca0          ; builds the GDI fonts for a mode (1: 24px)
FONTSET   equ 0x6c866c          ; the mode they were last built for
F4TAIL    equ 0x5c755a          ; the F4 handler's tail, after the mode set
GRESUME   equ 0x5c680b          ; the resume after a dialog
STAGE     equ 0xbe4300          ; the F5 dialog's copy of FLAGS
DLG_INIT  equ 0x427ec9          ; after the dialog stages FLAGS
DLG_OK    equ 0x428241          ; after OK writes FLAGS back
INI_LOAD  equ 0x50bcc6          ; after the ini load has set FLAGS
INI_SAVE  equ 0x50c0cb          ; after the ini save has read FLAGS
INI_NEXT  equ 0x6a0240          ; the push the ini-load hook displaces
MODE      equ 0x1ae3594         ; 1P's game state, 4 in a match, and its
SUBMODE   equ 0x1ae3690         ; sub-state; 2P's own machine has a copy of
MODE2     equ 0x1ef8a90         ; both, same tables (0x5ff1c0 / 0x606fa0).
SUBMODE2  equ 0x1ef9eb0         ; The in-game HUD is drawn from sub-states
                              ; 9..0x0c, 0x14, 0x15 and 0x1b; 3 and 4 are
                              ; the machine select.
SCALE_A   equ 0x6bc1e4          ; the game's 3D scale, per renderer
SCALE_B   equ 0x6c8b24
CENTRE_AX equ 0x6db530
CENTRE_BX equ 0x708870
LIST_A    equ 0x7001d0          ; render list bucket heads
LIST_B    equ 0x725f50
CENTRE_A  equ 0x6db534
CENTRE_B  equ 0x708874
; Plane B of each 2D engine: the backdrop tile map the pre-3D call
; draws (0x4800d0 / 0x5670c0), an 82x62-word ring of which 80x60 show,
; scrolled by whole tiles through the two scroll words (bits 3..9; y bit
; 15 hides the plane); the tile destination helper (ecx column, edx
; row; eax the frame address or 0 to skip) and the tile blit (ecx tile,
; edx destination, a pushed row count after which the tile wraps to the
; frame top); and the loaded-tile watermarks of the two banks.
RING1     equ 0x1cc6700
SCRX1     equ 0x34155c8
SCRY1     equ 0x34155d0
DEST1     equ 0x480410
BLIT1     equ 0x4803d0
WMA1      equ 0xbf5f7c
WMB1      equ 0xbf5f78
RING2     equ 0x1ef1140
SCRX2     equ 0x1efb728
SCRY2     equ 0x1efb730
DEST2     equ 0x567400
BLIT2     equ 0x5673c0
WMA2      equ 0x1ad0034
WMB2      equ 0x1ad0030
PHOTO_X   equ 72                ; the 64x48-tile photo blocks: ring (9, 6),
PHOTO_Y   equ 48                ; picture px (72, 48); their last two tile
PHOTO_W   equ 496               ; columns are blank, so 496x384 of picture
PHOTO_H   equ 384
PHOTO_A   equ 6*164+9*2         ; ring bytes of their cells (0, 0),
PHOTO_B   equ 30*164+41*2       ; (32, 24) and (0, 47)
PHOTO_C   equ 53*164+9*2
PHOTO_N   equ 3
MATSCALE  equ 0x408790          ; scales the current matrix by (x, y, z), cdecl
FLASHATTR equ 0x791ad0          ; the damage flash tiles' attribute block
COMMIT    equ 0x514430          ; copies the current matrix for the next submit
D_CMOON   equ 0x1c84            ; credits moon card scale, float, written by
                                ; the patcher
D_ROUND   equ 0x1c88            ; 1 while this viewport's player is in a round
                                ; (set in pre); gates the fill in rescale
D_PSTEP   equ 0x1c8c            ; margins, photo backdrop: canvas px to
D_PX0     equ 0x1c90            ; source px, 16.16, and the source origin
D_PY0     equ 0x1c94
D_SPREAD  equ 0x1c98            ; HUD spread: viewport px the timer moves
                                ; left this call, the frame's inset (pre)
D_SPLITC  equ 0x1ca4            ; frame column the timer ends before
                                ; (patcher; 0 turns the spread off)
D_BOTROW  equ 0x1db8            ; the TOTAL time: frame rows from here and
D_BOTCOL  equ 0x1dbc            ; columns from here move right (patcher)
D_PASSFN  equ 0x1dc0            ; the outermost HUD pass: its stub's offset
                                ; from the first, 20 a function
D_RTHR    equ 0x1dc4            ; the composite row in hand: canvas
D_RDXL    equ 0x1d8c            ; columns below D_RTHR move D_RDXL px,
D_RDXR    equ 0x1d90            ; the rest D_RDXR (spread_row)
D_OFFX    equ 0x1d94            ; x rescale offset for the polygon in hand
STUBS     equ 0x1a70            ; the pass stubs (UI_PASS_STUBS), 20 bytes
STUB_LEN  equ 20                ; each, in UI_PASS_FUNCS order
HUD_PASSES equ 2                ; the first two are the in-game HUD
D_MSIGA   equ 0x1da0            ; margins: the two photo cells last read
D_MSIGB   equ 0x1da4            ; (for the debug readout)
D_BARDX   equ 0x1da8            ; HUD units PLAYER/ENEMY and the bars move,
                                ; signed: what centres them (patcher)
D_ROWLAST equ 0x1d98            ; composite: the canvas row last examined
D_ROWSAME equ 0x1d9c            ; and whether it was all copy (skipped)
D_STAGE   equ 0x1e3b00         ; the guard rows after the canvas: the
                                ; photo's 496x384, staged for the rescale
D_OFF     equ 0xf3b00          ; canvas; 480 guard rows either side
D_COPY    equ 0x2d3b00         ; copy of the pre-fill, canvas rows only
                              ; (layout: guard, canvas, guard, copy)

; Projection setup, replacing 0x51444d / 0x51448e / 0x5cc39d / 0x5cc3de
; (the originals jump here, and the HUD passes' setup calls come here
; directly): the original maths, plus a copy of the result and the
; focal length for the submit hooks below. cdecl, focal at [esp+4].
world_a:
    push ebx
    call world_a_here
world_a_here:
    pop ebx
    sub ebx, world_a_here
    mov eax, [esp+8]
    mov [ebx+D_SAVE_FA], eax
    mov dword [ebx+D_VAR_A], 0
    fld dword [SCALE_A]
    fmul dword [esp+8]
    fmul dword [ASPECT_A]
    fstp dword [ebx+D_SAVE_A]
    fld dword [SCALE_A]
    fmul dword [ASPECT_A]
    fstp dword [ebx+D_SAVE_A+4]
    jmp world_a_tail
world_a2:
    push ebx
    call world_a2_here
world_a2_here:
    pop ebx
    sub ebx, world_a2_here
    mov eax, [esp+8]
    mov [ebx+D_SAVE_FA], eax
    mov dword [ebx+D_VAR_A], 1
    fld dword [SCALE_A]
    fmul dword [esp+8]
    fmul dword [ASPECT_A]
    fstp dword [ebx+D_SAVE_A]
    mov eax, [SCALE_A]
    mov [ebx+D_SAVE_A+4], eax
world_a_tail:
    fld dword [SCALE_A]
    fmul dword [esp+8]
    fstp dword [ebx+D_SAVE_A+8]
    mov eax, [ebx+D_SAVE_A]
    mov [PROJ_A], eax
    mov eax, [ebx+D_SAVE_A+4]
    mov [PROJ_A+8], eax
    mov eax, [ebx+D_SAVE_A+8]
    mov [PROJ_A+12], eax
    cmp dword [ebx+D_DEPTH], 0
    je world_a_out
    ; inside a HUD pass: the paths that project directly at setup time
    ; (4-vertex primitives: frames, timer box, cursors) get the 640x480
    ; projection the quad submissions get, since they reach the same
    ; insert hook and are rescaled there: the aspect alone (1.0 for the
    ; variant) and the HUD-space centre
    mov eax, [ASPECT_A]
    cmp dword [ebx+D_VAR_A], 0
    je world_a_hv
    mov eax, 0x3f800000
world_a_hv:
    mov [PROJ_A+8], eax
    mov eax, [ebx+D_CXH]
    mov [CENTRE_AX], eax
    mov eax, [ebx+D_CYH]
    mov [CENTRE_A], eax
    mov dword [ebx+D_PASS_A], 1  ; so the insert hook rescales them
world_a_out:
    pop ebx
    ret
world_b:
    push ebx
    call world_b_here
world_b_here:
    pop ebx
    sub ebx, world_b_here
    mov eax, [esp+8]
    mov [ebx+D_SAVE_FB], eax
    mov dword [ebx+D_VAR_B], 0
    fld dword [ASPECT_B]
    fmul dword [esp+8]
    fmul dword [SCALE_B]
    fstp dword [ebx+D_SAVE_B]
    fld dword [ASPECT_B]
    fmul dword [SCALE_B]
    fstp dword [ebx+D_SAVE_B+4]
    jmp world_b_tail
world_b2:
    push ebx
    call world_b2_here
world_b2_here:
    pop ebx
    sub ebx, world_b2_here
    mov eax, [esp+8]
    mov [ebx+D_SAVE_FB], eax
    mov dword [ebx+D_VAR_B], 1
    fld dword [esp+8]
    fmul dword [ASPECT_B]
    fmul dword [SCALE_B]
    fstp dword [ebx+D_SAVE_B]
    mov eax, [SCALE_B]
    mov [ebx+D_SAVE_B+4], eax
world_b_tail:
    fld dword [esp+8]
    fmul dword [SCALE_B]
    fstp dword [ebx+D_SAVE_B+8]
    mov eax, [ebx+D_SAVE_B]
    mov [PROJ_B], eax
    mov eax, [ebx+D_SAVE_B+4]
    mov [PROJ_B+4], eax
    mov eax, [ebx+D_SAVE_B+8]
    mov [PROJ_B+8], eax
    cmp dword [ebx+D_DEPTH], 0
    je world_b_out
    mov eax, [ASPECT_B]
    cmp dword [ebx+D_VAR_B], 0
    je world_b_hv
    mov eax, 0x3f800000
world_b_hv:
    mov [PROJ_B+4], eax
    mov eax, [ebx+D_CXH]
    mov [CENTRE_BX], eax
    mov eax, [ebx+D_CYH]
    mov [CENTRE_B], eax
    mov dword [ebx+D_PASS_B], 1
world_b_out:
    pop ebx
    ret

; Submit hooks: 0x514576 / 0x5cc4c6 jump here. Inside a HUD pass (see
; hud_enter) the submission gets a 640x480 projection with the current
; focal, centred on the viewport's centre in HUD units (so the game's
; own clipping, which runs before the insert, sees the whole viewport:
; the machine-select hangar is a 3D scene drawn inside a pass), and the
; pass is marked for the insert hook; everything else gets the last
; world projection back. The insert rescale then puts the HUD frame's
; 320,240 on the viewport exactly where a 4:3 frame centred there is. Then the displaced prologue and a jump into the
; original.
submit_a:
    push ebx
    push eax
    call submit_a_here
submit_a_here:
    pop ebx
    sub ebx, submit_a_here
    cmp dword [ebx+D_DEPTH], 0
    je submit_a_world
submit_a_hud:
    fld dword [ebx+D_SAVE_FA]
    fmul dword [ASPECT_A]
    fstp dword [PROJ_A]
    mov eax, [ASPECT_A]
    cmp dword [ebx+D_VAR_A], 0
    je submit_a_v
    mov eax, 0x3f800000
submit_a_v:
    mov [PROJ_A+8], eax
    mov eax, [ebx+D_SAVE_FA]
    mov [PROJ_A+12], eax
    mov eax, [ebx+D_CXH]
    mov [CENTRE_AX], eax
    mov eax, [ebx+D_CYH]
    mov [CENTRE_A], eax
    mov dword [ebx+D_PASS_A], 1
    jmp submit_a_done
submit_a_world:
    mov eax, [ebx+D_SAVE_A]
    mov [PROJ_A], eax
    mov eax, [ebx+D_SAVE_A+4]
    mov [PROJ_A+8], eax
    mov eax, [ebx+D_SAVE_A+8]
    mov [PROJ_A+12], eax
    mov eax, [ebx+D_CAX]
    mov [CENTRE_AX], eax
    mov eax, [ebx+D_CA]
    mov [CENTRE_A], eax
    mov dword [ebx+D_PASS_A], 0
submit_a_done:
    pop eax
    pop ebx
    push ebp
    mov ebp, esp
    push ebx
    push esi
    push edi
    push 0x51457c
    ret
submit_b:
    push ebx
    push eax
    call submit_b_here
submit_b_here:
    pop ebx
    sub ebx, submit_b_here
    cmp dword [ebx+D_DEPTH], 0
    je submit_b_world
submit_b_hud:
    fld dword [ebx+D_SAVE_FB]
    fmul dword [ASPECT_B]
    fstp dword [PROJ_B]
    mov eax, [ASPECT_B]
    cmp dword [ebx+D_VAR_B], 0
    je submit_b_v
    mov eax, 0x3f800000
submit_b_v:
    mov [PROJ_B+4], eax
    mov eax, [ebx+D_SAVE_FB]
    mov [PROJ_B+8], eax
    mov eax, [ebx+D_CXH]
    mov [CENTRE_BX], eax
    mov eax, [ebx+D_CYH]
    mov [CENTRE_B], eax
    mov dword [ebx+D_PASS_B], 1
    jmp submit_b_done
submit_b_world:
    mov eax, [ebx+D_SAVE_B]
    mov [PROJ_B], eax
    mov eax, [ebx+D_SAVE_B+4]
    mov [PROJ_B+4], eax
    mov eax, [ebx+D_SAVE_B+8]
    mov [PROJ_B+8], eax
    mov eax, [ebx+D_CBX]
    mov [CENTRE_BX], eax
    mov eax, [ebx+D_CB]
    mov [CENTRE_B], eax
    mov dword [ebx+D_PASS_B], 0
submit_b_done:
    pop eax
    pop ebx
    push ebp
    mov ebp, esp
    push ebx
    push esi
    push edi
    push 0x5cc4cc
    ret

; HUD passes. The patcher puts a jump at the entry of every function that
; draws HUD elements (in-game HUD, machine select, cursors, menu frames:
; the functions that own the HUD projection setups) to a stub that calls
; hud_enter, runs the displaced prologue and jumps back. hud_enter swaps
; the function's return address for hud_leave and counts the depth;
; while it is above zero every submission is HUD. Nested passes and both
; renderers share the counter. Past D_RETS_N deep a function is left
; unwrapped.
hud_enter:                          ; a HUD pass: depth + 1
    push ebx
    push eax
    push ecx
    call hud_enter_here
hud_enter_here:
    pop ebx
    sub ebx, hud_enter_here
    mov eax, [ebx+D_SP]
    cmp eax, D_RETS_N
    jae enter_done
    test eax, eax                   ; outermost: which stub called
    jne enter_nested
    mov ecx, [esp+12]
    sub ecx, ebx
    sub ecx, STUBS+5
    mov [ebx+D_PASSFN], ecx
enter_nested:
    mov ecx, [ebx+D_DEPTH]
    mov [ebx+D_RETD+eax*4], ecx     ; depth to come back to
    inc ecx
    mov [ebx+D_DEPTH], ecx
    inc dword [ebx+D_SP]
    mov ecx, [esp+16]               ; the function's return address
    mov [ebx+D_RETS+eax*4], ecx
    lea ecx, [ebx+hud_leave]
    mov [esp+16], ecx
enter_done:
    pop ecx
    pop eax
    pop ebx
    ret
hud_leave:                          ; eax is the function's return value
    push ebx
    push eax
    call hud_leave_here
hud_leave_here:
    pop ebx
    sub ebx, hud_leave_here
    dec dword [ebx+D_SP]
    mov eax, [ebx+D_SP]
    push eax
    mov eax, [ebx+D_RETD+eax*4]
    mov [ebx+D_DEPTH], eax
    test eax, eax
    jne hud_leave_ret
    call world_state
hud_leave_ret:
    pop eax
    mov eax, [ebx+D_RETS+eax*4]
    xchg eax, [esp]                 ; [esp] = original return, eax back
    mov ebx, [esp+4]
    ret 4
; world_state: no pending pass for the insert hooks, and the last world
; projection, centres and frame size back in the globals. ebx = data.
world_state:
    push eax
    mov dword [ebx+D_PASS_A], 0
    mov dword [ebx+D_PASS_B], 0
    ; D_W/D_H are pre's measurements; on a frame pre stood down for (no
    ; surface yet - the JP boot warning) they are stale or zero, and
    ; zeroing the game's frame globals from here would be worse than
    ; leaving them. The projection saves below are safe either way: a
    ; pass implies its setups ran and filled them this frame.
    mov eax, [ebx+D_W]
    test eax, eax
    jz world_state_proj
    mov [FB_W], eax
    mov eax, [ebx+D_H]
    mov [FB_H], eax
world_state_proj:
    mov eax, [ebx+D_SAVE_A]
    mov [PROJ_A], eax
    mov eax, [ebx+D_SAVE_A+4]
    mov [PROJ_A+8], eax
    mov eax, [ebx+D_SAVE_A+8]
    mov [PROJ_A+12], eax
    mov eax, [ebx+D_SAVE_B]
    mov [PROJ_B], eax
    mov eax, [ebx+D_SAVE_B+4]
    mov [PROJ_B+4], eax
    mov eax, [ebx+D_SAVE_B+8]
    mov [PROJ_B+8], eax
    mov eax, [ebx+D_CAX]
    mov [CENTRE_AX], eax
    mov eax, [ebx+D_CA]
    mov [CENTRE_A], eax
    mov eax, [ebx+D_CBX]
    mov [CENTRE_BX], eax
    mov eax, [ebx+D_CB]
    mov [CENTRE_B], eax
    pop eax
    ret

stub1:
    call set1pre
    call pre
stub1_call:                        ; UI_CALLS: rel32 fixed per install
    call 0x4800d0
    call post
    ret
stub2:
    call set1post
    call pre
stub2_call:                        ; UI_CALLS: rel32 fixed per install
    call 0x4804f0
    call post
    ret
stub3:
    call set2pre
    call pre
stub3_call:                        ; UI_CALLS: rel32 fixed per install
    call 0x5670c0
    call post
    ret
stub4:
    call set2post
    call pre
stub4_call:                        ; UI_CALLS: rel32 fixed per install
    call 0x5674f0
    call post
    ret

set1pre:
    push ebx
    push eax
    call s1p_here
s1p_here:
    pop ebx
    sub ebx, s1p_here
    mov dword [ebx+D_BASEPTR], FB_PTR
    mov dword [ebx+D_PHASE], 1
    ; the projection centres as the game has just set them (0x5c8317
    ; runs right before this hook); captured here only, since the HUD
    ; pass can run between the two hooks and would have nudged them
    mov eax, [CENTRE_A]
    mov [ebx+D_CA], eax
    mov eax, [CENTRE_B]
    mov [ebx+D_CB], eax
    mov eax, [CENTRE_AX]
    mov [ebx+D_CAX], eax
    mov eax, [CENTRE_BX]
    mov [ebx+D_CBX], eax
    pop eax
    pop ebx
    ret
set1post:
    push ebx
    push eax
    call s1q_here
s1q_here:
    pop ebx
    sub ebx, s1q_here
    mov dword [ebx+D_BASEPTR], FB_PTR
    mov dword [ebx+D_PHASE], 0
    mov eax, [DRAWN]
    mov [ebx+D_3D], eax
    pop eax
    pop ebx
    ret
set2pre:
    push ebx
    call s2p_here
s2p_here:
    pop ebx
    sub ebx, s2p_here
    mov dword [ebx+D_BASEPTR], FB_ROW
    mov dword [ebx+D_PHASE], 1
    pop ebx
    ret
set2post:
    push ebx
    call s2q_here
s2q_here:
    pop ebx
    sub ebx, s2q_here
    mov dword [ebx+D_BASEPTR], FB_ROW
    mov dword [ebx+D_PHASE], 0
    pop ebx
    ret

; fit: place D_LW x D_LH scaled by the layout's scale, centred on the
; viewport, clipped. Sets D_S, D_XSTEP/D_YSTEP (canvas px per viewport
; px), D_XOFF/D_YOFF, D_DW/D_DH, D_CX/D_CY.
fit:
    mov eax, [ebx+D_LAYOUT]
    mov eax, [ebx+eax*4+D_SCALE]
    mov [ebx+D_S], eax
    xor eax, eax                    ; 1/s, 16.16: 2^32 / s, rounded up,
    mov edx, 1                      ; so that a source column boundary
    div dword [ebx+D_S]             ; that lands exactly on a destination
    test edx, edx                   ; column is not undershot by the
    jz fit_step                     ; accumulated error. (2^24 / s << 8
    inc eax                         ; lost the low byte: 0.68% at 2.25,
fit_step:                           ; a stretch that showed as the
                                    ; "48.33 px" grid and 4 columns lost)
    mov [ebx+D_XSTEP], eax
    mov [ebx+D_YSTEP], eax
    ; x
    mov ecx, [ebx+D_LW]
    imul ecx, [ebx+D_S]
    shr ecx, 16                     ; scaled width
    mov eax, [ebx+D_W]
    sub eax, ecx
    sar eax, 1                      ; offset, may be negative
    mov dword [ebx+D_CX], 0
    test eax, eax
    jns fit_x_in
    add ecx, eax                    ; width inside the viewport
    neg eax
    imul eax, [ebx+D_XSTEP]
    mov [ebx+D_CX], eax
    xor eax, eax
fit_x_in:
    mov [ebx+D_XOFF], eax
    mov edx, [ebx+D_W]
    sub edx, eax
    cmp ecx, edx
    jle fit_x_dw
    mov ecx, edx
fit_x_dw:
    mov [ebx+D_DW], ecx
    ; y
    mov ecx, [ebx+D_LH]
    imul ecx, [ebx+D_S]
    shr ecx, 16
    mov eax, [ebx+D_H]
    sub eax, ecx
    sar eax, 1
    mov dword [ebx+D_CY], 0
    test eax, eax
    jns fit_y_in
    add ecx, eax
    neg eax
    imul eax, [ebx+D_YSTEP]
    mov [ebx+D_CY], eax
    xor eax, eax
fit_y_in:
    mov [ebx+D_YOFF], eax
    mov edx, [ebx+D_H]
    sub edx, eax
    cmp ecx, edx
    jle fit_y_dh
    mov ecx, edx
fit_y_dh:
    mov [ebx+D_DH], ecx
    ret

pre:
    pushad
    call pre_here
pre_here:
    pop ebx
    sub ebx, pre_here
    mov esi, [ebx+D_BASEPTR]
    mov eax, [esi]
    mov [ebx+D_BASE], eax
    test eax, eax                   ; no surface yet (JP boot warning):
    jnz pre_live                    ; leave this frame to the stock path
    popad
    ret
pre_live:
    mov eax, [FB_PITCH]
    mov [ebx+D_PITCH], eax
    mov eax, [FB_W]
    mov [ebx+D_W], eax
    mov eax, [FB_H]
    mov [ebx+D_H], eax
    mov eax, [SPLIT]
    mov [ebx+D_SPLIT], eax
    mov dword [SPLIT], 0
    xor ecx, ecx
    test eax, eax
    je pre_layout
    cmp dword [ebx+D_SHOW], 0
    jne pre_layout                  ; single: 1P geometry
    inc ecx
    test byte [FLAGS], 3
    je pre_layout
    inc ecx
pre_layout:
    mov [ebx+D_LAYOUT], ecx
    ; HUD projection factor for this layout, for the game's 0x51444d
    mov ecx, [ebx+ecx*4+D_HUD]
    mov [ebx+D_HUDF], ecx
    fld dword [ebx+D_HUDF]
    fmul dword [ebx+D_C65536]
    fistp dword [ebx+D_S16]
    fild dword [ebx+D_W]
    fld dword [ebx+D_HUDF]
    fmul dword [ebx+D_C640]
    fsubp st1, st0
    fmul dword [ebx+D_CHALF]
    fistp dword [ebx+D_OXH]
    fild dword [ebx+D_H]
    fld dword [ebx+D_HUDF]
    fmul dword [ebx+D_C480]
    fsubp st1, st0
    fmul dword [ebx+D_CHALF]
    fistp dword [ebx+D_OYH]
    ; centre of the viewport in HUD units, and the rescale offsets that
    ; put the HUD frame's 320,240 back on OXH/OYH exactly
    fild dword [ebx+D_W]
    fmul dword [ebx+D_CHALF]
    fdiv dword [ebx+D_HUDF]
    fistp dword [ebx+D_CXH]
    fild dword [ebx+D_H]
    fmul dword [ebx+D_CHALF]
    fdiv dword [ebx+D_HUDF]
    fistp dword [ebx+D_CYH]
    mov eax, [ebx+D_CXH]
    sub eax, 320
    imul eax, [ebx+D_S16]
    mov ecx, [ebx+D_OXH]
    shl ecx, 16
    sub ecx, eax
    mov [ebx+D_OFFX16], ecx
    mov eax, [ebx+D_CYH]
    sub eax, 240
    imul eax, [ebx+D_S16]
    mov ecx, [ebx+D_OYH]
    shl ecx, 16
    sub ecx, eax
    mov [ebx+D_OFFY16], ecx
    mov dword [ebx+D_LH], 480
    mov eax, 480
    cmp dword [ebx+D_PHASE], 0
    je pre_43
    cmp dword [ebx+D_LAYOUT], 0
    jne pre_43
    imul eax, [ebx+D_W]
    xor edx, edx
    div dword [ebx+D_H]
    inc eax                         ; even, so the picture centres on a
    and eax, -2                     ; whole canvas column
    cmp eax, 1024
    jle pre_lw
    mov eax, 1024
    jmp pre_lw
pre_43:
    shl eax, 2
    xor edx, edx
    mov ecx, 3
    div ecx
pre_lw:
    mov [ebx+D_LW], eax
    sub eax, 640
    sar eax, 1
    mov [ebx+D_XO], eax
    add eax, 7
    sar eax, 3
    mov [ebx+D_MXT], eax
    call fit
    ; in a round: MODE 4 and a sub-state that draws one, for the
    ; viewport's own player. The HUD band and the fill in rescale key
    ; on it.
    mov dword [ebx+D_ROUND], 0
    mov ecx, [MODE]
    mov edx, [SUBMODE]
    cmp dword [ebx+D_BASEPTR], FB_PTR
    je pre_round_1p
    mov ecx, [MODE2]
    mov edx, [SUBMODE2]
pre_round_1p:
    cmp ecx, 4
    jne pre_round
    call split_state                ; the patcher's list; edx kept
    jz pre_round
    mov dword [ebx+D_ROUND], 1
pre_round:
    ; the HUD band, for the insert hooks and for this composite
    mov dword [ebx+D_PINON], 0
    mov dword [ebx+D_PINROWS], 0
    mov eax, [ebx+D_PINTH]
    test eax, eax
    je pre_pin
    cmp dword [ebx+D_LAYOUT], 0
    je pre_pin
    cmp dword [ebx+D_OYH], 0
    jle pre_pin
    cmp dword [ebx+D_ROUND], 0
    je pre_pin
    cmp edx, 0xd                    ; the band's own list stops short of
    je pre_pin                      ; these two
    cmp edx, 0xe
    je pre_pin
    mov dword [ebx+D_PINON], 1
    mov ecx, [ebx+D_OYH]
    shl ecx, 16
    mov [ebx+D_PINSUB], ecx
    cmp dword [ebx+D_PHASE], 0
    jne pre_pin
    imul eax, [ebx+D_S]
    shr eax, 16
    mov [ebx+D_PINROWS], eax
pre_pin:
    ; the HUD spread: in a round, the post-3D call; the timer moves by
    ; as many px as the frame has to either side inside the viewport
    mov dword [ebx+D_SPREAD], 0
    cmp dword [ebx+D_SPLITC], 0
    je pre_spread_done
    cmp dword [ebx+D_PHASE], 0
    jne pre_spread_done
    cmp dword [ebx+D_ROUND], 0
    je pre_spread_done
    cmp edx, 0xd
    je pre_spread_done
    cmp edx, 0xe
    je pre_spread_done
    mov eax, [ebx+D_XOFF]
    mov ecx, [ebx+D_W]
    sub ecx, [ebx+D_XOFF]
    sub ecx, [ebx+D_DW]
    cmp eax, ecx
    jle pre_spread_min
    mov eax, ecx
pre_spread_min:
    test eax, eax
    jle pre_spread_done
    mov [ebx+D_SPREAD], eax
pre_spread_done:
    ; pre-fill: canvas (cx, cy) from frame (fx0 + cx*s, fy0 + cy*s),
    ; fx0 = xoff - cx0*s; outside the viewport reads as 0.
    mov dword [ebx+D_Y], 0
    lea edi, [ebx+D_OFF]
    mov ebp, [ebx+D_YOFF]
    shl ebp, 16
    mov eax, [ebx+D_CY]
    shr eax, 16
    imul eax, [ebx+D_S]
    sub ebp, eax
    mov [ebx+D_YSAVE], ebp
    cmp dword [ebx+D_PINROWS], 0
    je pre_fill_row
    xor ebp, ebp                    ; the band, from the viewport top
pre_fill_row:
    mov eax, ebp
    sar eax, 16
    js pre_fill_skiprow
    cmp eax, [ebx+D_H]
    jge pre_fill_skiprow
    imul eax, [ebx+D_PITCH]
    add eax, [ebx+D_BASE]
    mov esi, eax
    mov eax, [ebx+D_Y]
    call spread_row
    mov edx, [ebx+D_XOFF]
    shl edx, 16
    mov eax, [ebx+D_CX]
    shr eax, 16
    imul eax, [ebx+D_S]
    sub edx, eax
    xor ecx, ecx
pre_fill_px:
    mov eax, [ebx+D_RDXL]           ; the spread moves this column
    cmp ecx, [ebx+D_RTHR]
    jl pre_fill_dx
    mov eax, [ebx+D_RDXR]
pre_fill_dx:
    shl eax, 16
    add eax, edx
    sar eax, 16
    js pre_fill_zero
    cmp eax, [ebx+D_W]
    jge pre_fill_zero
    mov ax, [esi+eax*2]
    jmp pre_fill_store
pre_fill_zero:
    xor eax, eax
pre_fill_store:
    mov [edi+ecx*2], ax
    mov [edi+ecx*2+D_COPY-D_OFF], ax
    add edx, [ebx+D_S]
    inc ecx
    cmp ecx, [ebx+D_LW]
    jl pre_fill_px
    jmp pre_fill_next
pre_fill_skiprow:
    push edi
    xor eax, eax
    mov ecx, [ebx+D_LW]
    db 0xf3                         ; rep, by hand: nasm 3.x writes the
    stosw                           ; 66 before the f3, 2.x after, and
                                    ; the blob must not depend on which
    pop edi
    push edi
    add edi, D_COPY-D_OFF
    mov ecx, [ebx+D_LW]
    db 0xf3                         ; rep, pinned; see above
    stosw
    pop edi
pre_fill_next:
    add edi, OFF_PITCH
    add ebp, [ebx+D_S]
    inc dword [ebx+D_Y]
    mov eax, [ebx+D_Y]
    cmp dword [ebx+D_PINROWS], 0
    je pre_fill_more
    cmp eax, [ebx+D_PINTH]
    jne pre_fill_more
    mov ebp, [ebx+D_YSAVE]          ; below the band: the centred frame
    imul eax, [ebx+D_S]
    add ebp, eax
    mov eax, [ebx+D_Y]
pre_fill_more:
    cmp eax, [ebx+D_LH]
    jl pre_fill_row
    ; globals for the 2D code; the picture centred on the canvas
    mov eax, [ebx+D_XO]
    lea eax, [ebx+eax*2+D_OFF]
    mov esi, [ebx+D_BASEPTR]
    mov [esi], eax
    mov dword [FB_PITCH], OFF_PITCH
    mov eax, [ebx+D_LW]
    mov [FB_W], eax
    mov eax, [ebx+D_LH]
    mov [FB_H], eax
    mov edi, [ebx+D_ROWTAB]
    xor eax, eax
    xor ecx, ecx
pre_rows:
    mov [edi+ecx*4], eax
    add eax, OFF_PITCH
    inc ecx
    cmp ecx, 480
    jl pre_rows
    popad
    ret

post:
    pushad
    call post_here
post_here:
    pop ebx
    sub ebx, post_here
    mov eax, [ebx+D_SPLIT]
    mov [SPLIT], eax
    mov esi, [ebx+D_BASEPTR]
    mov eax, [ebx+D_BASE]
    test eax, eax                   ; the frame pre stood down for
    jnz post_live
    popad
    ret
post_live:
    call margins
    mov [esi], eax
    mov eax, [ebx+D_PITCH]
    mov [FB_PITCH], eax
    mov eax, [ebx+D_W]
    mov [FB_W], eax
    mov eax, [ebx+D_H]
    mov [FB_H], eax
    mov edi, [ebx+D_ROWTAB]
    xor eax, eax
    xor ecx, ecx
post_rows:
    mov [edi+ecx*4], eax
    add eax, [ebx+D_PITCH]
    inc ecx
    cmp ecx, [ebx+D_H]
    jl post_rows
    ; single viewport: the other engine's layer stays in the canvas
    mov eax, [ebx+D_SHOW]
    test eax, eax
    je post_shown
    cmp dword [ebx+D_BASEPTR], FB_PTR
    jne post_engine_b
    cmp eax, 2
    je post_done
    jmp post_shown
post_engine_b:
    cmp eax, 1
    je post_done
post_shown:
post_fit:
    call fit
    mov eax, [ebx+D_DH]
    mov [ebx+D_YEND], eax
    mov eax, [ebx+D_YOFF]
    cmp dword [ebx+D_PINROWS], 0
    je post_start
    mov eax, [ebx+D_PINROWS]        ; the band first, at the viewport top
    mov [ebx+D_YEND], eax
    xor eax, eax
post_start:
    imul eax, [ebx+D_PITCH]
    add eax, [ebx+D_BASE]
    mov edi, [ebx+D_XOFF]
    lea edi, [eax+edi*2]
    mov eax, [ebx+D_CY]
    mov [ebx+D_YF], eax
    mov dword [ebx+D_Y], 0
    mov dword [ebx+D_ROWLAST], -1
post_yloop:
    ; a new canvas row: its spread, and whether it differs from the copy
    ; at all - most HUD-phase rows do not, and every viewport row that
    ; samples such a row is skipped whole
    mov esi, [ebx+D_YF]
    shr esi, 16
    cmp esi, [ebx+D_ROWLAST]
    je post_rowknown
    mov [ebx+D_ROWLAST], esi
    mov eax, esi
    call spread_row
    push edi
    imul esi, esi, OFF_PITCH
    lea esi, [ebx+esi+D_OFF]
    lea edi, [esi+D_COPY-D_OFF]
    mov ecx, [ebx+D_LW]
    shr ecx, 1
    repe cmpsd
    pop edi
    mov dword [ebx+D_ROWSAME], 0
    jne post_rowknown
    mov dword [ebx+D_ROWSAME], 1
post_rowknown:
    cmp dword [ebx+D_ROWSAME], 0
    jne post_rownext
    mov esi, [ebx+D_YF]
    shr esi, 16
    imul esi, esi, OFF_PITCH
    lea esi, [ebx+esi+D_OFF]
    mov edx, [ebx+D_CX]
    xor ecx, ecx
post_xloop:
    mov ebp, edx
    shr ebp, 16
    mov ax, [esi+ebp*2]
    cmp ax, [esi+ebp*2+D_COPY-D_OFF]
    je post_skip
    cmp ebp, [ebx+D_RTHR]           ; the spread moves this column
    mov ebp, [ebx+D_RDXL]
    jl post_put
    mov ebp, [ebx+D_RDXR]
post_put:
    add ebp, ecx
    mov [edi+ebp*2], ax
post_skip:
    add edx, [ebx+D_XSTEP]
    inc ecx
    cmp ecx, [ebx+D_DW]
    jl post_xloop
post_rownext:
    add edi, [ebx+D_PITCH]
    mov eax, [ebx+D_YSTEP]
    add [ebx+D_YF], eax
    inc dword [ebx+D_Y]
    mov eax, [ebx+D_Y]
    cmp eax, [ebx+D_YEND]
    jl post_yloop
    cmp eax, [ebx+D_DH]
    jge post_done
    mov eax, [ebx+D_DH]             ; the rest of the frame, centred; the
    mov [ebx+D_YEND], eax           ; rows the band left are not touched
    mov eax, [ebx+D_Y]
    add eax, [ebx+D_YOFF]
    imul eax, [ebx+D_PITCH]
    add eax, [ebx+D_BASE]
    mov edi, [ebx+D_XOFF]
    lea edi, [eax+edi*2]
    jmp post_yloop
post_done:
    cmp dword [ebx+D_DEBUG], 0
    je post_out
    cmp dword [ebx+D_PHASE], 0
    jne post_out
    cmp dword [ebx+D_BASEPTR], FB_PTR
    jne post_out
    call dbg_draw
post_out:
    popad
    ret

; The HUD spread. At 4:3 the timer sits 82 px from the left edge; on a
; wider viewport the 4:3 frame is centred and it drifts towards the
; middle. In a round, on a viewport with an inset (D_SPREAD, viewport
; px: 1P and top/bottom split; 0 at 4:3 and side by side, where nothing
; moves), the 2D layer's band rows (above D_PINTH) and the in-game HUD
; pass's polygons in them that lie left of D_SPLITC - the timer box and
; its digits - are moved left by the inset, so the timer keeps its
; stock distance from the left edge; what lies right of D_SPLITC -
; PLAYER/ENEMY and the bars, off-centre as a group in stock - moves by
; D_BARDX units, which centres it. The TOTAL time, 2D layer rows from
; D_BOTROW and columns from D_BOTCOL, goes right by the inset.
;
; spread_row: eax is a canvas row; sets D_RTHR/D_RDXL/D_RDXR for the
; composite and the pre-fill. Clobbers ecx, edx.
spread_row:
    mov dword [ebx+D_RTHR], 0x7fffffff
    mov dword [ebx+D_RDXL], 0
    mov dword [ebx+D_RDXR], 0
    mov ecx, [ebx+D_SPREAD]
    test ecx, ecx
    je spread_row_ret
    cmp eax, [ebx+D_PINTH]
    jge spread_row_low
    mov edx, [ebx+D_SPLITC]
    mov [ebx+D_RTHR], edx
    neg ecx
    mov [ebx+D_RDXL], ecx
    mov ecx, [ebx+D_BARDX]          ; HUD units to viewport px
    imul ecx, [ebx+D_S]
    sar ecx, 16
    mov [ebx+D_RDXR], ecx
    ret
spread_row_low:
    cmp eax, [ebx+D_BOTROW]
    jl spread_row_ret
    mov edx, [ebx+D_BOTCOL]
    mov [ebx+D_RTHR], edx
    mov [ebx+D_RDXR], ecx
spread_row_ret:
    ret

; margins: the pre phase of a 1P or single-viewport frame, called before
; post restores the game's globals, so the 2D code's destination and
; blit helpers still address the canvas. The game's plane B walker has
; drawn tile columns 0..79 at canvas x D_XO; this draws columns
; -D_MXT..-1 and 80..79+D_MXT from the same ring through the same
; helpers, column c showing what the picture shows at c mod 80 (the
; scroll and the ring's 82-word wrap as the walker applies them, so the
; seams are the walker's own), for ring rows with no empty tile: the
; encounter grid and the static are 80 tiles of pattern and continue
; in step; the picture's holes continue as holes. Any other
; row - art placed on a backdrop, the plane hidden - gets the flat
; colour on the rows its tiles would cover (the last tile row wraps to
; the frame top, as the blit does) when no 3D is behind. Rules tried
; and dropped are on record in docs/HIRES.md.
;
; The photo backdrops of the two-player screens (the machine select
; and the network waiting cards) are 64x48-tile blocks at ring (9, 6),
; 496x384 of picture (the last two tile columns are blank) in the
; 640x480, so they sat framed in black even at
; 4:3. The game rebases a block's tile indices to wherever its tiles
; were loaded, so a block is recognised by the differences between
; three of its cells - (32, 24) and (0, 47) against (0, 0) - which
; survive the rebase (photo_sig; the same in every build). With the
; plane shown and unscrolled, the block is rescaled over the whole
; canvas keeping its aspect - the sides fill the width and the rows
; outside the canvas's height are cropped equally top and bottom -
; nearest-neighbour from a staged copy, and the row loop is skipped.
; At 4:3, and in side by side, where each engine's canvas is 4:3 and
; margins otherwise has nothing to do, that is the only thing this
; does; in top/bottom the frame's rows 48..432 - the photo's own - are
; what the viewport shows already.
margins:
    pushad
    cmp dword [ebx+D_PHASE], 0
    je margins_ret
    mov eax, [ebx+D_LAYOUT]
    cmp eax, 2                      ; top/bottom: the frame is already cut
    je margins_ret                  ; to the viewport's rows; leave it
    test eax, eax
    jne margins_engine              ; side by side: both engines draw, each
    mov eax, [ebx+D_SHOW]           ; its own 4:3 canvas; a photo fills it
    test eax, eax                   ; not this engine's frame
    je margins_engine
    cmp dword [ebx+D_BASEPTR], FB_PTR
    jne margins_show2
    cmp eax, 2
    je margins_ret
    jmp margins_engine
margins_show2:
    cmp eax, 1
    je margins_ret
margins_engine:
    mov eax, [SPLIT]                ; the destination helper halves under
    mov [ebx+D_MSPLIT], eax         ; split; pre hides the flag from the
    mov dword [SPLIT], 0            ; walker, and so does this
    mov dword [ebx+D_ERING], RING1
    mov dword [ebx+D_ESCRX], SCRX1
    mov dword [ebx+D_ESCRY], SCRY1
    mov dword [ebx+D_EDEST], DEST1
    mov dword [ebx+D_EBLIT], BLIT1
    mov dword [ebx+D_EWMA], WMA1
    mov dword [ebx+D_EWMB], WMB1
    cmp dword [ebx+D_BASEPTR], FB_PTR
    je margins_colour
    mov dword [ebx+D_ERING], RING2
    mov dword [ebx+D_ESCRX], SCRX2
    mov dword [ebx+D_ESCRY], SCRY2
    mov dword [ebx+D_EDEST], DEST2
    mov dword [ebx+D_EBLIT], BLIT2
    mov dword [ebx+D_EWMA], WMA2
    mov dword [ebx+D_EWMB], WMB2
margins_colour:
    ; the flat colour: the picture's top row if all one colour, else black
    mov eax, [ebx+D_XO]
    lea esi, [ebx+eax*2+D_OFF]
    movzx eax, word [esi]
    mov ecx, 640
margins_crow:
    cmp ax, [esi]
    jne margins_cblack
    add esi, 2
    dec ecx
    jnz margins_crow
    jmp margins_ccol
margins_cblack:
    xor eax, eax
margins_ccol:
    mov [ebx+D_MCOL], eax
    ; the scroll, as the walker reads it
    mov eax, [ebx+D_ESCRY]
    movzx eax, word [eax]
    mov ecx, eax
    and ecx, 7
    mov [ebx+D_MFY], ecx
    shr eax, 3
    and eax, 0x7f
    cmp eax, 62
    jb margins_sy
    sub eax, 62
margins_sy:
    mov [ebx+D_MSY], eax
    mov eax, [ebx+D_ESCRX]
    movzx eax, word [eax]
    shr eax, 3
    and eax, 0x7f
    mov [ebx+D_MSX], eax
    ; a photo backdrop: shown, unscrolled, and its two cells in place
    test eax, eax
    jne margins_rows
    cmp dword [ebx+D_MSY], 0
    jne margins_rows
    cmp dword [ebx+D_MFY], 0
    jne margins_rows
    mov eax, [ebx+D_ESCRY]
    test byte [eax+1], 0x80
    jne margins_rows
    mov esi, [ebx+D_ERING]
    movzx eax, word [esi+PHOTO_A]
    movzx ecx, word [esi+PHOTO_B]
    mov [ebx+D_MSIGA], eax
    mov [ebx+D_MSIGB], ecx
    sub ecx, eax
    and ecx, 0x3fff                 ; tile index bits
    movzx edi, word [esi+PHOTO_C]
    sub edi, eax
    and edi, 0x3fff
    mov eax, edi
    lea edx, [ebx+photo_sig]
    mov edi, PHOTO_N
margins_sig:
    cmp cx, [edx]
    jne margins_signext
    cmp ax, [edx+2]
    je margins_photo
margins_signext:
    add edx, 4
    dec edi
    jnz margins_sig
margins_rows:
    cmp dword [ebx+D_XO], 0         ; 4:3: no margins
    je margins_done
    mov dword [ebx+D_MR], 0
margins_row:
    mov eax, [ebx+D_MR]             ; the ring row shown on this tile row
    sub eax, [ebx+D_MSY]
    jns margins_ring
    add eax, 62
margins_ring:
    imul eax, eax, 164
    add eax, [ebx+D_ERING]
    mov esi, eax
    xor edi, edi                    ; rows before the last tile row wraps
    cmp dword [ebx+D_MR], 59
    jne margins_full
    mov edi, [ebx+D_MFY]
    test edi, edi
    je margins_full
    neg edi
    add edi, 8
margins_full:
    mov eax, [ebx+D_ESCRY]          ; shown, and no empty tile in the row
    test byte [eax+1], 0x80
    jne margins_flat
    mov ecx, 80
    mov edx, esi
margins_fullcol:
    test word [edx], 0x3fff
    je margins_flat
    add edx, 2
    dec ecx
    jnz margins_fullcol
    mov ebp, [ebx+D_MXT]
    neg ebp
margins_tile:
    mov ecx, ebp                    ; the picture column this one repeats
    test ecx, ecx
    jns margins_right
    add ecx, 80
    jmp margins_index
margins_right:
    sub ecx, 80
margins_index:
    mov eax, [ebx+D_MSX]            ; its ring word: 82-sx+c before the
    cmp ecx, eax                    ; walker's reset to the row start,
    jae margins_unwrapped           ; c-sx after
    add ecx, 82
margins_unwrapped:
    sub ecx, eax
    movzx ecx, word [esi+ecx*2]
    test ecx, 0x3fff
    je margins_next
    mov eax, ecx
    and eax, 0x3fff
    mov edx, [ebx+D_EWMA]
    test ecx, 0x4000
    je margins_loaded
    mov edx, [ebx+D_EWMB]
margins_loaded:
    cmp eax, [edx]
    jae margins_next
    push ecx
    mov ecx, ebp
    mov edx, [ebx+D_MR]
    call [ebx+D_EDEST]
    pop ecx
    test eax, eax
    je margins_next
    mov edx, eax
    push edi
    call [ebx+D_EBLIT]
margins_next:
    inc ebp
    jnz margins_more
    mov ebp, 80
margins_more:
    mov eax, [ebx+D_MXT]
    add eax, 80
    cmp ebp, eax
    jl margins_tile
    jmp margins_rownext
margins_flat:
    cmp dword [ebx+D_3D], 0
    jne margins_rownext
    mov eax, [ebx+D_MR]             ; the canvas rows this tile row covers
    shl eax, 3
    add eax, [ebx+D_MFY]
    mov ecx, 8
margins_frow:
    cmp eax, 480
    jl margins_fin
    sub eax, 480
margins_fin:
    push eax
    push ecx
    imul eax, eax, OFF_PITCH
    lea edx, [ebx+eax+D_OFF]
    mov eax, [ebx+D_MCOL]
    mov edi, edx
    mov ecx, [ebx+D_XO]
    db 0xf3                         ; rep, pinned; see above
    stosw
    mov ecx, [ebx+D_XO]
    lea edi, [edx+ecx*2+1280]
    mov ecx, [ebx+D_LW]
    sub ecx, [ebx+D_XO]
    sub ecx, 640
    db 0xf3                         ; rep, pinned; see above
    stosw
    pop ecx
    pop eax
    inc eax
    dec ecx
    jnz margins_frow
margins_rownext:
    inc dword [ebx+D_MR]
    cmp dword [ebx+D_MR], 60
    jl margins_row
margins_done:
    mov eax, [ebx+D_MSPLIT]
    mov [SPLIT], eax
margins_ret:
    popad
    ret

; the photo: stage its 496x384 off the canvas, then fill the canvas
; from the stage. step is source px per canvas px, the smaller of the
; two axes' so the picture covers the canvas; the source origin centres
; what is cropped.
margins_photo:
    mov eax, [ebx+D_XO]
    lea esi, [ebx+eax*2+D_OFF+PHOTO_Y*OFF_PITCH+PHOTO_X*2]
    lea edi, [ebx+D_STAGE]
    mov edx, PHOTO_H
margins_stage:
    push esi
    push edi
    mov ecx, PHOTO_W/2
    rep movsd
    pop edi
    pop esi
    add esi, OFF_PITCH
    add edi, OFF_PITCH
    dec edx
    jnz margins_stage
    mov eax, PHOTO_W                ; shifted, not immediates: the 16.16
    shl eax, 16                     ; sizes read as game addresses to the
    xor edx, edx                    ; port tool
    div dword [ebx+D_LW]
    cmp eax, (PHOTO_H << 16) / 480
    jbe margins_step
    mov eax, (PHOTO_H << 16) / 480
margins_step:
    mov [ebx+D_PSTEP], eax
    mov ecx, [ebx+D_LW]
    imul ecx, eax
    mov edx, PHOTO_W
    shl edx, 16
    sub edx, ecx
    sar edx, 1
    mov [ebx+D_PX0], edx
    imul ecx, eax, 480
    mov edx, PHOTO_H
    shl edx, 16
    sub edx, ecx
    sar edx, 1
    mov [ebx+D_PY0], edx
    lea edi, [ebx+D_OFF]
    mov ebp, 480
margins_prow:
    mov eax, [ebx+D_PY0]
    sar eax, 16
    imul eax, eax, OFF_PITCH
    lea esi, [ebx+eax+D_STAGE]
    push edi
    mov edx, [ebx+D_PX0]
    mov ecx, [ebx+D_LW]
margins_ppx:
    mov eax, edx
    sar eax, 16
    mov ax, [esi+eax*2]
    stosw
    add edx, [ebx+D_PSTEP]
    dec ecx
    jnz margins_ppx
    pop edi
    add edi, OFF_PITCH
    mov eax, [ebx+D_PSTEP]
    add [ebx+D_PY0], eax
    dec ebp
    jnz margins_prow
    jmp margins_done

; the three photo blocks: cells (32, 24) and (0, 47) less cell (0, 0)
photo_sig:
    dw 0x00c3, 0x0402               ; 0x940168
    dw 0x0202, 0x05b9               ; 0x92f8ba
    dw 0x076b, 0x0b0c               ; 0x941968

; dbg_draw: "MODE SUBMODE  MODE2 SUBMODE2  SHOW", hex, through the game's
; GDI text on the frame about to be shown (as asm/overlay.asm does).
dbg_draw:
    lea edi, [ebx+D_DBGSTR]
    mov eax, [MODE]
    call dbg_hex
    mov byte [edi], 0x20
    inc edi
    mov eax, [SUBMODE]
    call dbg_hex
    mov byte [edi], 0x20
    inc edi
    mov eax, [MODE2]
    call dbg_hex
    mov byte [edi], 0x20
    inc edi
    mov eax, [SUBMODE2]
    call dbg_hex
    mov byte [edi], 0x20
    inc edi
    mov eax, [ebx+D_SHOW]
    call dbg_hex
    mov byte [edi], 0x20             ; margins: tile scroll x, y, fine y,
    inc edi                          ; and the two photo cells
    mov eax, [ebx+D_MSX]
    call dbg_hex
    mov eax, [ebx+D_MSY]
    call dbg_hex
    mov eax, [ebx+D_MFY]
    call dbg_hex
    mov byte [edi], 0x20
    inc edi
    mov eax, [ebx+D_MSIGA]
    call dbg_word
    mov byte [edi], 0x20
    inc edi
    mov eax, [ebx+D_MSIGB]
    call dbg_word
    mov byte [edi], 0
    mov ecx, [PRIMARY]
    push ecx
    mov ecx, [BACK]
    mov [PRIMARY], ecx
    push 1
    push 0x0000ff00
    push 40
    push 300
    lea eax, [ebx+D_DBGSTR]
    push eax
    mov eax, DRAW
    call eax
    add esp, 20
    pop ecx
    mov [PRIMARY], ecx
    ret
dbg_word:                           ; low word of eax as four hex digits
    push eax
    shr eax, 8
    call dbg_hex
    pop eax
dbg_hex:                            ; low byte of eax as two hex digits
    push eax
    shr eax, 4
    call dbg_nib
    pop eax
dbg_nib:
    and eax, 15
    add al, 0x30
    cmp al, 0x39
    jbe dbg_put
    add al, 7
dbg_put:
    mov [edi], al
    inc edi
    ret

; Render-list insert hooks (0x5d4628, 0x5d5360 for A; 0x5e02b0, 0x5df538
; for B: two record writers a renderer, both filing four packed vertex
; positions at +0x10). In a HUD pass the four positions, whole 640x480
; pixels, are scaled to the HUD scale and offset to the viewport. The renderer fills inclusively, so a vertex on the low side
; of its axis maps to x*s and one on the high side to (x+1)*s-1; a
; degenerate axis (the frame lines are zero-width quads) is opened to
; the same s px, so a line covers the same scaled column as a fill edge
; on that column. Then the displaced
; instruction runs. edx is the record.
insert_a:
    pushad
    call ins_a_here
ins_a_here:
    pop ebx
    sub ebx, ins_a_here
    mov eax, [ebx+D_DIM]            ; hangar: shade the mech being drawn
    test eax, eax
    je ins_a_nodim
    imul eax, [edx+0xc]
    shr eax, 16
    mov [edx+0xc], eax
ins_a_nodim:
    cmp dword [ebx+D_PASS_A], 0
    je ins_a_done
    call rescale
ins_a_done:
    call fill
    popad
    mov esi, [ebx*4+LIST_A]
    ret
insert_b:
    pushad
    call ins_b_here
ins_b_here:
    pop ebx
    sub ebx, ins_b_here
    cmp dword [ebx+D_PASS_B], 0
    je ins_b_done
    call rescale
ins_b_done:
    call fill
    popad
    mov esi, [ebx*4+LIST_B]
    ret
; fill: the damage flash. It is the unit quad drawn as a grid of tiles
; (4 by 3 at 16:9) through attribute block FLASHATTR, sized a fifth over
; the 4:3 frame, so the outer tiles stopped 80 px short of each edge at
; 1080p. In a round, a polygon of that attribute block has every vertex
; on the frame's left inset pushed to x = 0 and every one on its right
; inset to x = W-1; the inner tiles and the middle of the outer ones are
; untouched. Viewport pixels, after any pass rescale. edx is the record.
; FLASHATTR is the game's flat red, used from forty-odd sites; the enemy
; marker's arrow and triangle (56 units) are drawn with it too, and the
; arrow's slide ends with its tip on the inset. A tile is about 195 HUD
; units wide, so anything under FILL_MIN is left alone.
FILL_MIN  equ 120               ; HUD units
fill:
    cmp dword [ebx+D_ROUND], 0
    je fill_ret
    cmp dword [edx+4], FLASHATTR
    jne fill_ret
    call extents
    mov eax, [ebx+D_S16]
    imul eax, FILL_MIN
    shr eax, 16
    mov ecx, [ebx+D_XMAX]
    sub ecx, [ebx+D_XMIN]
    cmp ecx, eax
    jl fill_ret
    mov eax, [ebx+D_OXH]
    add eax, 8                      ; at or left of this: the left edge
    mov ecx, [ebx+D_W]
    sub ecx, [ebx+D_OXH]
    sub ecx, 9                      ; at or right of this: the right edge
    lea esi, [edx+0x10]
    mov edi, 4
fill_v:
    movsx ebp, word [esi]
    cmp ebp, eax
    jg fill_notleft
    mov word [esi], 0
    jmp fill_next
fill_notleft:
    cmp ebp, ecx
    jl fill_next
    mov ebp, [ebx+D_W]
    dec ebp
    mov [esi], bp
fill_next:
    add esi, 4
    dec edi
    jnz fill_v
fill_ret:
    ret

; extents: D_XMIN/XMAX/YMIN/YMAX over the record's four vertices (edx)
extents:
    mov dword [ebx+D_XMIN], 0x7fffffff
    mov dword [ebx+D_YMIN], 0x7fffffff
    mov dword [ebx+D_XMAX], -0x7fffffff
    mov dword [ebx+D_YMAX], -0x7fffffff
    lea esi, [edx+0x10]
    mov ecx, 4
extents_v:
    movsx eax, word [esi]
    cmp eax, [ebx+D_XMIN]
    jge extents_x2
    mov [ebx+D_XMIN], eax
extents_x2:
    cmp eax, [ebx+D_XMAX]
    jle extents_y
    mov [ebx+D_XMAX], eax
extents_y:
    movsx eax, word [esi+2]
    cmp eax, [ebx+D_YMIN]
    jge extents_y2
    mov [ebx+D_YMIN], eax
extents_y2:
    cmp eax, [ebx+D_YMAX]
    jle extents_next
    mov [ebx+D_YMAX], eax
extents_next:
    add esi, 4
    dec ecx
    jnz extents_v
    ret
rescale:
    call extents
    mov ebp, [ebx+D_YMAX]
    sub ebp, [ebx+D_CYH]
    add ebp, 240                    ; HUD frame row of the lowest vertex
    cmp ebp, [ebx+D_PINTH]
    setl cl                         ; cl: wholly inside the band
    ; y offset: the top-aligned one for a polygon inside the band
    mov eax, [ebx+D_OFFY16]
    cmp dword [ebx+D_PINON], 0
    je rescale_offy
    test cl, cl
    je rescale_offy
    sub eax, [ebx+D_PINSUB]
rescale_offy:
    mov [ebx+D_OFFY], eax
    ; x offset: the spread, for the in-game HUD pass's polygons in the
    ; band (PASS0/PASS1): the timer box left of the split column moves
    ; left by the inset, the bars and their frames right of it by
    ; D_BARDX units
    mov eax, [ebx+D_OFFX16]
    cmp dword [ebx+D_SPREAD], 0
    je rescale_offx
    cmp dword [ebx+D_PASSFN], HUD_PASSES*STUB_LEN
    jae rescale_offx
    test cl, cl
    je rescale_offx
    mov edi, [ebx+D_XMAX]
    sub edi, [ebx+D_CXH]
    add edi, 320                    ; HUD frame column of the rightmost
    cmp edi, [ebx+D_SPLITC]
    jg rescale_bars
    mov ecx, [ebx+D_SPREAD]
    shl ecx, 16
    sub eax, ecx
    jmp rescale_offx
rescale_bars:
    mov edi, [ebx+D_XMIN]
    sub edi, [ebx+D_CXH]
    add edi, 320
    cmp edi, [ebx+D_SPLITC]
    jl rescale_offx
    mov ecx, [ebx+D_BARDX]
    imul ecx, [ebx+D_S16]
    add eax, ecx
rescale_offx:
    mov [ebx+D_OFFX], eax
    lea esi, [edx+0x10]
    xor ecx, ecx                    ; vertex index
rescale_loop:
    ; x: low side unless above the minimum; a degenerate axis (a 1 px
    ; line) becomes s px thick, with vertices 1 and 2 on the high side
    movsx eax, word [esi]
    cmp eax, [ebx+D_XMIN]
    jne rescale_x_high
    mov edi, [ebx+D_XMAX]
    cmp edi, [ebx+D_XMIN]
    jne rescale_x_low
    lea edi, [ecx-1]
    cmp edi, 2
    jae rescale_x_low
rescale_x_high:
    inc eax
    imul eax, [ebx+D_S16]
    add eax, [ebx+D_OFFX]
    add eax, 0x8000
    sar eax, 16
    dec eax
    jmp rescale_x_done
rescale_x_low:
    imul eax, [ebx+D_S16]
    add eax, [ebx+D_OFFX]
    add eax, 0x8000
    sar eax, 16
rescale_x_done:
    movsx edi, word [esi+2]
    cmp edi, [ebx+D_YMIN]
    jne rescale_y_high
    mov ebp, [ebx+D_YMAX]
    cmp ebp, [ebx+D_YMIN]
    jne rescale_y_low
    lea ebp, [ecx-1]
    cmp ebp, 2
    jae rescale_y_low
rescale_y_high:
    inc edi
    imul edi, [ebx+D_S16]
    add edi, [ebx+D_OFFY]
    add edi, 0x8000
    sar edi, 16
    dec edi
    jmp rescale_y_done
rescale_y_low:
    imul edi, [ebx+D_S16]
    add edi, [ebx+D_OFFY]
    add edi, 0x8000
    sar edi, 16
rescale_y_done:
rescale_pack:
    and eax, 0xffff
    shl edi, 16
    or eax, edi
    mov [esi], eax
    add esi, 4
    inc ecx
    cmp ecx, 4
    jl rescale_loop
    ret


; frame_setup: the call to the viewport setup at 0x5c811b comes here,
; with its argument. Decides D_SHOW for the frame; see the header.
frame_setup:
    push ebx
    push ecx
    push edx
    call frame_here
frame_here:
    pop ebx
    sub ebx, frame_here
    mov dword [ebx+D_SHOW], 0
    cmp dword [SPLIT], 0
    je frame_go
    mov ecx, 1
    cmp dword [MODE], 4         ; not a match (boot, title): one view
    jne frame_single
    mov edx, [SUBMODE]
    call split_state
    jne frame_go                    ; a round on P1's side
    cmp dword [MODE2], 4
    jne frame_single
    mov edx, [SUBMODE2]
    call split_state
    jne frame_go                    ; or on P2's
    cmp edx, [SUBMODE]              ; neither: the lower sub-state's player
    jae frame_single
    mov ecx, 2
frame_single:
    mov [ebx+D_SHOW], ecx
    push dword [SPLIT]
    mov dword [SPLIT], 0
    push dword [esp+20]
    mov eax, VIEWPORT
    call eax
    add esp, 4
    pop dword [SPLIT]
    mov eax, [FB_PTR]               ; viewport 2 on the same surface
    mov [FB_ROW], eax
    mov dword [MASKOFF], 0
    jmp frame_out
frame_go:
    push dword [esp+16]
    mov eax, VIEWPORT
    call eax
    add esp, 4
frame_out:
    pop edx
    pop ecx
    pop ebx
    ret
; split_state: ZF clear when sub-state edx is in D_SPLITST; edx kept
split_state:
    cmp edx, 64
    jae split_no
    push ecx
    mov ecx, edx
    and ecx, 31
    mov eax, 1
    shl eax, cl
    mov ecx, edx
    shr ecx, 5
    test [ebx+ecx*4+D_SPLITST], eax
    pop ecx
    ret
split_no:
    xor eax, eax
    ret

; flush_a / flush_b: the flush calls at 0x5c8166 / 0x5c8178 come here.
; The renderer not shown flushes with its draw-skip flag set.
flush_a:
    push ebx
    push eax
    call flush_a_here
flush_a_here:
    pop ebx
    sub ebx, flush_a_here
    mov eax, FLUSH_A
    cmp dword [ebx+D_SHOW], 2
    jne flush_a_go
    push dword [SKIP_A]
    mov dword [SKIP_A], 1
    call eax
    pop dword [SKIP_A]
    pop eax
    pop ebx
    ret
flush_a_go:
    call eax
    pop eax
    pop ebx
    ret
flush_b:
    push ebx
    push eax
    call flush_b_here
flush_b_here:
    pop ebx
    sub ebx, flush_b_here
    mov eax, FLUSH_B
    cmp dword [ebx+D_SHOW], 1
    jne flush_b_go
    push dword [SKIP_B]
    mov dword [SKIP_B], 1
    call eax
    pop dword [SKIP_B]
    pop eax
    pop ebx
    ret
flush_b_go:
    call eax
    pop eax
    pop ebx
    ret

; Machine-select hangar: the idle platform draw of a mech (0x59e4ea,
; "call 0x59cb93") comes here. The game keeps palettes for the selection
; and the previous selection only, and loads them asynchronously, so a
; mech far enough right can be drawn in someone else's colours. Past
; 28.43 degrees right of the camera - stock's draw bound, where colours
; stop being guaranteed - the shade is scaled to black at insert (see
; insert_a), fading over the twelve degrees inside that edge: the
; outermost mech is a silhouette that lights up as it turns in. The
; factor is computed signed, so the centre and the whole left side
; land at 1 or more and are left alone.
hangar_draw:
    push ebx
    call hangar_draw_here
hangar_draw_here:
    pop ebx
    sub ebx, hangar_draw_here
    push eax
    push edx
    mov eax, [ebp+8]
    lea eax, [eax+eax*4]
    mov edx, [ebp+8]
    lea eax, [edx+eax*4]
    fld dword [eax*4+0x345bd58]     ; camera angle
    mov eax, [ebp+0xc]
    lea eax, [eax+eax*4]
    mov edx, [ebp+0xc]
    lea eax, [edx+eax*4]
    fsub dword [eax*4+0x345b2c8]     ; less the entity's: negative
                                         ; to the right of the camera
    fadd dword [ebx+hd_inner]        ; degrees short of the edge,
    fmul dword [ebx+hd_slope]        ; over the fade range
    fcom dword [ebx+hd_floor]
    fnstsw ax
    test ah, 1                          ; below the floor: the floor
    je hd_lo_ok                         ; (0 would mean no scaling)
    fstp st0
    fld dword [ebx+hd_floor]
hd_lo_ok:
    fld1
    fcomp st1
    fnstsw ax
    test ah, 1                          ; 1 or more: left alone
    jne hd_full
    fmul dword [ebx+D_C65536]
    fistp dword [ebx+D_DIM]
    jmp hd_go
hd_full:
    fstp st0
    mov dword [ebx+D_DIM], 0
hd_go:
    pop edx
    pop eax
    push eax
    mov eax, [esp+8]                    ; the return address, kept aside
    mov [ebx+D_HDRET], eax
    lea eax, [ebx+hangar_draw_back]
    mov [esp+8], eax                    ; the drawer returns to _back
    pop eax
    pop ebx
    push 0x59cb93
    ret
hangar_draw_back:
    push ebx
    call hangar_draw_back_here
hangar_draw_back_here:
    pop ebx
    sub ebx, hangar_draw_back_here
    mov dword [ebx+D_DIM], 0
    push dword [ebx+D_HDRET]
    mov ebx, [esp+4]
    add esp, 8
    jmp dword [esp-8]
hd_inner:
    dd 0x41e370a4                    ; 28.43
hd_slope:
    dd 0x3daaaaab                    ; 1/12
hd_floor:
    dd 0x37800000                    ; 1/65536

; f4_toggle: F4 (0x5c74ec, after the network-game guard) comes here.
; Every site the patcher writes for the second size differently from
; the first is in the table at D_F4TAB: dword address, dword length,
; the first size's bytes, the second's, ended by a zero address. The
; other set is copied over the sites (.text and .rdata are writable),
; the coverage mask table and the fonts are rebuilt, and the surfaces
; are recreated at the new size. If that fails the first set goes back
; and the idle pass (asm/activate.asm) recreates at that size.
f4_toggle:
    pushad
    call f4_here
f4_here:
    pop ebx
    sub ebx, f4_here
    call f4_switch
    popad
    push F4TAIL
    ret
; f4_switch: the other size, in place and on the screen; ebx set
f4_switch:
    call f4_apply
    push 0x10
    push dword [ebx+D_MODEH]
    push dword [ebx+D_MODEW]
    mov eax, RECREATE
    call eax
    add esp, 12
    test eax, eax
    jnz f4_done
    call f4_apply
f4_done:
    ret
f4_apply:
    mov eax, [ebx+D_F4MODE]
    xor eax, 1
    mov [ebx+D_F4MODE], eax
    mov esi, [ebx+D_F4TAB]
f4_next:
    mov edi, [esi]
    test edi, edi
    jz f4_swapped
    mov ecx, [esi+4]
    add esi, 8
    push esi
    test eax, eax
    jz f4_copy
    add esi, ecx
f4_copy:
    mov edx, ecx
    rep movsb
    pop esi
    lea esi, [esi+edx*2]
    jmp f4_next
f4_swapped:
    mov eax, MASKINIT
    call eax
    mov dword [FONTSET], 0
    push 1
    mov eax, FONTS
    call eax
    add esp, 4
    ret

; The F5 Screen row, relabelled 720p / 1080p by the patcher, drives the
; same switch. Its bit (FLAGS bit 0, stock's Screen=Normal window) is
; kept out of FLAGS: the dialog stages FLAGS with the bit set for the
; second size, OK strips it into D_F4WANT, and the switch runs once
; the dialog has closed, before the resume. The ini keeps the bit, so
; the load applies the size before the mode is set, and the save
; writes it.
dlg_init:
    push ebx
    call dlg_init_here
dlg_init_here:
    pop ebx
    sub ebx, dlg_init_here
    or eax, [ebx+D_F4MODE]
    mov [STAGE], eax
    push dword [ebx+D_F4MODE]
    pop dword [ebx+D_F4WANT]
    pop ebx
    push DLG_INIT
    ret
dlg_ok:
    push ebx
    call dlg_ok_here
dlg_ok_here:
    pop ebx
    sub ebx, dlg_ok_here
    mov [ebx+D_F4WANT], eax
    and dword [ebx+D_F4WANT], 1
    and eax, 0xfffffffe
    mov [FLAGS], eax
    pop ebx
    push DLG_OK
    ret
dlg_done:
    pushad
    call dlg_done_here
dlg_done_here:
    pop ebx
    sub ebx, dlg_done_here
    cmp dword [LOOPMODE], 2         ; not in a network game
    je dlg_resume
    mov eax, [ebx+D_F4WANT]
    cmp eax, [ebx+D_F4MODE]
    je dlg_resume
    call f4_switch
dlg_resume:
    popad
    push GRESUME
    ret
ini_load:
    pushad
    call ini_load_here
ini_load_here:
    pop ebx
    sub ebx, ini_load_here
    mov eax, [FLAGS]
    test al, 1
    jz ini_load_out
    and eax, 0xfffffffe
    mov [FLAGS], eax
    cmp dword [ebx+D_F4MODE], 0
    jne ini_load_out
    call f4_apply
ini_load_out:
    popad
    push INI_NEXT
    push INI_LOAD
    ret
ini_save:
    push ebx
    call ini_save_here
ini_save_here:
    pop ebx
    sub ebx, ini_save_here
    mov eax, [FLAGS]
    or eax, [ebx+D_F4MODE]
    pop ebx
    push INI_SAVE
    ret

; roll_blit: the tile blit 0x47fee0, with its rows argument honoured.
; ecx dest, edx src, one stack arg. Stock's roll walkers push a visible
; row count for the window's edge tiles and 0 elsewhere, but the blit
; discarded it and always drew all 8 glyph rows: the top tile redrew
; unscrolled at row 0 and the entering tile popped in whole. The
; patcher re-encodes the top edge's push as fine+8 (it was 8-fine), so:
; 0 draws [ROWS] rows as stock; 1..7 is the entering tile, its first n
; rows; above 8 is the top tile, skipping fine = n-8 source rows (16
; bytes each in the full mode, the only one left under this patch) and
; drawing the rest at the frame top. esi and edi kept, ret 4.
roll_blit:
    push esi
    push edi
    push ebx
    mov esi, ecx
    mov edi, [esp+16]
    test edi, edi
    jne roll_clip
    mov eax, [ROWS]
    test eax, eax
    je roll_done
    jmp roll_rows
roll_clip:
    cmp edi, 8
    jbe roll_rows_edi
    lea ecx, [edi-8]
    shl ecx, 4
    add edx, ecx
    mov eax, 16
    sub eax, edi
    je roll_done
    jmp roll_rows
roll_rows_edi:
    mov eax, edi
roll_rows:
    mov ebx, eax
roll_row:
    mov ecx, esi
    mov eax, ROWDRAW
    call eax
    mov edx, eax
    add esi, [0x6bf5ac]
    dec ebx
    jne roll_row
roll_done:
    pop ebx
    pop edi
    pop esi
    ret 4

; rowsafe: the 2D row table, indexed with the row bounded. The tile
; planes' destination helpers index the table with rows past either
; end - stock landed in the second table right behind it, a deliberate
; vertical wrap, but the patch relocated the table and the neighbours
; are the coverage mask. Out-of-range rows wrap by the frame height as
; stock's adjacency did; during the credits roll (the second plane's
; window pokes past both edges every frame) they park on the canvas
; guard instead, where the write lands invisibly. edx row in, offset
; out; everything else kept.
rowsafe:
    push ebx
    push eax
    call rowsafe_here
rowsafe_here:
    pop ebx
    sub ebx, rowsafe_here
    mov eax, [FB_H]
rowsafe_wrap:
    cmp edx, eax
    jb rowsafe_ok
    cmp dword [0x66c190], 0
    jne rowsafe_park
    test edx, edx
    js rowsafe_neg
    sub edx, eax
    jmp rowsafe_wrap
rowsafe_neg:
    add edx, eax
    jmp rowsafe_wrap
rowsafe_park:
    mov edx, 0xf0000
    jmp rowsafe_out
rowsafe_ok:
    mov eax, [ebx+D_ROWTAB]
    mov edx, [eax+edx*4]
rowsafe_out:
    pop eax
    pop ebx
    ret

; Credits: the moon. 0x58f4ce draws it as a 26x52 card at z=80, sized for
; the 640x288 band the stock roll sits behind; with the band gone
; (roll_blit above) the card ends 100 px short of the top and bottom at
; 1080p. Runs in place of the `call COMMIT` before the card's submit and
; scales the matrix by D_CMOON first. The card is not fixed up for the
; width: it sits behind the moon's own edge.
credits_moon:
    push ebx
    call moon_here
moon_here:
    pop ebx
    sub ebx, moon_here
    push dword [ebx+D_CMOON]
    push dword [ebx+D_CMOON]
    push dword [ebx+D_CMOON]
    mov eax, MATSCALE
    call eax
    add esp, 12
    pop ebx
    mov eax, COMMIT
    jmp eax
