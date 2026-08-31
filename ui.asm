; 2D layer drawn into an offscreen canvas, then composited onto the
; Built into vo_patch.py by tools/uibuild.py; --check guards drift.
; docs/HIRES.md documents the design and the porting record.
; viewport; HUD polygons projected at 640x480 and rescaled at insert.
;
; 2D: four stubs replace the four 2D calls in 0x5c80df (viewport 1
; before/after the 3D flush, viewport 2 the same). The canvas is 640x480
; and the split flag is hidden from the 2D code while it draws, so
; split-screen viewports get the full-size layout. The canvas is
; pre-filled with the viewport's own pixels, sampled at the inverse of
; the 2D scale, and a copy is kept; after the game has drawn, only
; pixels that differ from the copy are composited back, so translucent
; elements blend against the real background and untouched areas are
; left alone. The composite is nearest, or bilinear when D_FILTER is set,
; in the surface's own 555/565 layout. The scaled canvas is centred on
; the viewport and clipped if larger. In the pre-3D phase (backdrops)
; the canvas has the viewport's own aspect, but if nothing was painted
; beyond the 4:3 width (logos, title, menus are fixed-width) it is
; treated as 4:3 after all; in split it is always 4:3. The post phase
; (HUD) is 4:3. Margins are blacked in the pre-3D phase when the last 3D
; flush drew nothing (2D-only screens). The canvas has 480 guard rows
; above and below: the 2D code draws outside the viewport in split
; screen and expects frame memory there.
;
; HUD polygons: the functions that own the HUD projection setups are
; wrapped (hud_enter) so the pass depth is known; inside a pass the
; projection setups and the submit hooks install a 640x480 projection,
; and the insert hooks scale every polygon to the HUD frame on the
; viewport. Outside a pass everything is left alone.
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
; encounter screen, and outside a match (MODE not 4: the boot splash, the
; title) - the frame is one full-screen viewport from one player's
; engine: the player whose sub-state is lower, P1 on a tie, which is the
; one still doing something while the other waits on a card. frame_setup runs the game's viewport
; setup with the split flag cleared, which gives both renderers the 1P
; geometry, then points viewport 2 at the same surface; the other
; renderer's flush runs with its draw-skip flag set (the list is still
; sorted and emptied) and its 2D layer is not composited. D_LAYOUT is
; what the rest of this file keys on: 0 1P or single, 1 side by side,
; 2 top/bottom.
;
; Data is ebx-relative after call/pop. D_MODEW/D_MODEH, D_ROWTAB, the
; split factors, D_SCALE/D_HUD, D_FILTER, D_SHIFTX/Y, D_PINTH, D_SPLITST
; are written by the patcher.
FB_PTR    = 0x6bf5a8            ; locked surface pointer
FB_PITCH  = 0x6bf5ac
FB_ROW    = 0x6bf5b0            ; current row pointer
FB_W      = 0x6bf5b8
FB_H      = 0x6bf5bc
SPLIT     = 0x6bc948
FLAGS     = 0x6bf598
DRAWN     = 0x6d0dc4          ; renderer pixel counter, cleared per frame
PROJ_A    = 0x6db4c8          ; renderer A projection: 3D, aspect, HUD
ASPECT_A  = 0x6bc1e8
PROJ_B    = 0x708818          ; renderer B projection: 3D, aspect, HUD
ASPECT_B  = 0x6c8b28
OFF_PITCH = 2048              ; canvas up to 1024 px wide
D_BASE    = 0x1800            ; data block; code below, stubs at 0x1600
D_PITCH   = 0x1804
D_W       = 0x1808
D_H       = 0x180c
D_XSTEP   = 0x1810            ; canvas px per viewport px, 16.16
D_YSTEP   = 0x1814
D_Y       = 0x1818
D_BASEPTR = 0x181c
D_MODEW   = 0x1820
D_MODEH   = 0x1824
D_LW      = 0x1828            ; canvas size
D_LH      = 0x182c
D_DW      = 0x1830            ; composited size on the viewport (clipped)
D_XOFF    = 0x1834            ; where it starts on the viewport
D_PHASE   = 0x1838            ; 1 before the 3D flush
D_ROWTAB  = 0x183c            ; relocated 2D row table
D_DH      = 0x1840
D_YOFF    = 0x1844
D_KSBS    = 0x1848            ; split FOV factors, read by the game code
D_3D      = 0x1850            ; pixels the last 3D flush drew
D_SPLIT   = 0x1854            ; saved split flag
D_YF      = 0x1858            ; canvas row position, 16.16
D_S       = 0x185c            ; scale in use, 16.16
D_CX      = 0x1860            ; canvas x/y (16.16) at D_XOFF/D_YOFF
D_CY      = 0x1864
D_SCALE   = 0x1868            ; 2D scale 16.16: 1P, side by side, top/bottom
D_HUD     = 0x1874            ; the same as floats
D_HUDF    = 0x1880            ; float in use; read by the game's projection
D_FILTER  = 0x1884            ; 1: bilinear composite (patcher)
D_ROW1    = 0x1888            ; scratch for the bilinear loop
D_FY      = 0x188c
D_ACC     = 0x1890
D_CA      = 0x189c            ; renderer A/B screen centre y, as the game set
D_CB      = 0x18a0            ; it this frame (captured in pre)
D_C65536  = 0x18a8            ; float constants, written by the patcher
D_C640    = 0x18ac
D_C480    = 0x18b0
D_CHALF   = 0x18b4
D_CAX     = 0x18b8            ; screen centre x, as the game set it
D_CBX     = 0x18bc
D_PASS_A  = 0x18c0            ; 1 while a HUD pass is being submitted,
D_PASS_B  = 0x18c4            ; per renderer
D_S16     = 0x18c8            ; HUD scale, 16.16
D_OXH     = 0x18cc            ; HUD offset on the viewport, pixels
D_OYH     = 0x18d0
D_CXH     = 0x19c8            ; HUD-space centre: the viewport centre in
D_CYH     = 0x19cc            ; HUD units, whole, so culling sees the viewport
D_OFFX16  = 0x19d0            ; rescale offsets, 16.16: D_OXH less what the
D_OFFY16  = 0x19d4            ; centre already contributes
D_XMIN    = 0x18d4            ; scratch for the rescale
D_XMAX    = 0x19b0
D_YMAX    = 0x19b4
D_SHIFTX  = 0x19b8            ; HUD polygon x shift on the viewport, pixels,
                              ; per layout (patcher)
D_SHIFTY  = 0x19c4
D_YMIN    = 0x18d8
D_SAVE_A  = 0x18dc            ; last world projection, renderer A (3)
D_SAVE_FA = 0x18e8            ; its focal length
D_VAR_A   = 0x18ec            ; 1 if set by the variant without aspect
D_SAVE_B  = 0x18f0            ; the same for renderer B
D_SAVE_FB = 0x18fc
D_VAR_B   = 0x1900
D_DEPTH   = 0x1908            ; HUD pass nesting depth (see hud_enter)
D_RETS    = 0x190c            ; saved return addresses, D_RETS_N deep
D_RETS_N  = 32
D_SP      = 0x19d8            ; how many are saved
D_DIM     = 0x1a64            ; hangar: shade factor 16.16 for the mech being
                              ; drawn, 0 when none (see hangar_draw)
D_HDRET   = 0x1a68            ; hangar_draw's return address
D_PINTH   = 0x1a6c            ; HUD band: frame rows pinned to the top, 0 off
                              ; (patcher)
D_PINON   = 0x1a70            ; 1 while the band applies (set in pre)
D_PINSUB  = 0x1a74            ; D_OYH 16.16: centred less this is pinned
D_PINROWS = 0x1a78            ; viewport rows the band covers; 0 outside
                              ; the HUD phase
D_OFFY    = 0x1a7c            ; y rescale offset for the polygon in hand
D_YEND    = 0x1a80            ; composite loop bound
D_YSAVE   = 0x1a84            ; pre-fill: the centred frame's row start
D_SHOW    = 0x1a88            ; 0 split as set, 1 viewport 1 full screen, 2
                              ; viewport 2 (frame_setup)
D_LAYOUT  = 0x1a8c            ; 0 1P or single, 1 side by side, 2 top/bottom
D_SPLITST = 0x1a90            ; 64-bit mask of the sub-states drawn split
                              ; (patcher)
D_DEBUG   = 0x1a98            ; 1: print both machines' states on the frame
                              ; (patcher, HIRES_DEBUG_STATES)
D_DBGSTR  = 0x1aa0            ; the text, 32 bytes
D_F4MODE  = 0x1ac0            ; 0: the first size is in place, 1: the second
D_F4TAB   = 0x1ac4            ; the F4 site table (patcher); see f4_toggle
D_F4WANT  = 0x1ac8            ; the F5 Screen choice: 1 for the second size
PRIMARY   = 0x1ae5f40         ; the surface DRAW paints on, and the one
BACK      = 0x1ae5f5c         ; about to be flipped over it
DRAW      = 0x5c991c          ; GDI text (text, x, y, colour, flag), cdecl
LOOPMODE  = 0x6bc94c          ; 1 two players, 2 network
FB_ROW2   = 0x6bf5b4
MASKOFF   = 0x7087a0          ; renderer B's coverage mask offset
SKIP_A    = 0x6c84c8          ; flush draws nothing while set, per renderer
SKIP_B    = 0x6c84cc
VIEWPORT  = 0x5c8317          ; per-frame viewport and projection setup
FLUSH_A   = 0x5d1db0
FLUSH_B   = 0x5dcc80
RECREATE  = 0x5c56a2          ; release and create the surfaces (w, h,
                              ; bpp) cdecl, nonzero on success
MASKINIT  = 0x5ce180          ; fills the coverage mask row table; keeps
                              ; every register
FONTS     = 0x5c8ca0          ; builds the GDI fonts for a mode (1: 24px)
FONTSET   = 0x6c866c          ; the mode they were last built for
F4TAIL    = 0x5c755a          ; the F4 handler's tail, after the mode set
GRESUME   = 0x5c680b          ; the resume after a dialog
STAGE     = 0xbe4300          ; the F5 dialog's copy of FLAGS
DLG_INIT  = 0x427ec9          ; after the dialog stages FLAGS
DLG_OK    = 0x428241          ; after OK writes FLAGS back
INI_LOAD  = 0x50bcc6          ; after the ini load has set FLAGS
INI_SAVE  = 0x50c0cb          ; after the ini save has read FLAGS
INI_NEXT  = 0x6a0240          ; the push the ini-load hook displaces
MODE      = 0x1ae3594         ; 1P's game state, 4 in a match, and its
SUBMODE   = 0x1ae3690         ; sub-state; 2P's own machine has a copy of
MODE2     = 0x1ef8a90         ; both, same tables (0x5ff1c0 / 0x606fa0).
SUBMODE2  = 0x1ef9eb0         ; The in-game HUD is drawn from sub-states
                              ; 9..0x0c, 0x14, 0x15 and 0x1b; 3 and 4 are
                              ; the machine select.
D_RETD    = 0x19dc            ; and the depth to come back to for each
SCALE_A   = 0x6bc1e4          ; the game's 3D scale, per renderer
SCALE_B   = 0x6c8b24
CENTRE_AX = 0x6db530
CENTRE_BX = 0x708870
LIST_A    = 0x7001d0          ; render list bucket heads
LIST_B    = 0x725f50
CENTRE_A  = 0x6db534
CENTRE_B  = 0x708874
PIXFMT    = 0x33cd5f4         ; 0x22b when the surface is 555
D_OFF     = 0xf3b00          ; canvas; 480 guard rows either side
D_COPY    = 0x2d3b00         ; copy of the pre-fill, canvas rows only
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
    mov eax, [esp+8]
    mov [ebx+D_SAVE_FA], eax
    mov dword ptr [ebx+D_VAR_A], 0
    fld dword ptr [SCALE_A]
    fmul dword ptr [esp+8]
    fmul dword ptr [ASPECT_A]
    fstp dword ptr [ebx+D_SAVE_A]
    fld dword ptr [SCALE_A]
    fmul dword ptr [ASPECT_A]
    fstp dword ptr [ebx+D_SAVE_A+4]
    jmp world_a_tail
world_a2:
    push ebx
    call world_a2_here
world_a2_here:
    pop ebx
    sub ebx, world_a2_here
    mov eax, [esp+8]
    mov eax, [esp+8]
    mov [ebx+D_SAVE_FA], eax
    mov dword ptr [ebx+D_VAR_A], 1
    fld dword ptr [SCALE_A]
    fmul dword ptr [esp+8]
    fmul dword ptr [ASPECT_A]
    fstp dword ptr [ebx+D_SAVE_A]
    mov eax, [SCALE_A]
    mov [ebx+D_SAVE_A+4], eax
world_a_tail:
    fld dword ptr [SCALE_A]
    fmul dword ptr [esp+8]
    fstp dword ptr [ebx+D_SAVE_A+8]
    mov eax, [ebx+D_SAVE_A]
    mov [PROJ_A], eax
    mov eax, [ebx+D_SAVE_A+4]
    mov [PROJ_A+8], eax
    mov eax, [ebx+D_SAVE_A+8]
    mov [PROJ_A+12], eax
    cmp dword ptr [ebx+D_DEPTH], 0
    je world_a_out
    ; inside a HUD pass: the paths that project directly at setup time
    ; (4-vertex primitives: frames, timer box, cursors) get the 640x480
    ; projection the quad submissions get, since they reach the same
    ; insert hook and are rescaled there: the aspect alone (1.0 for the
    ; variant) and the HUD-space centre
    mov eax, [ASPECT_A]
    cmp dword ptr [ebx+D_VAR_A], 0
    je world_a_hv
    mov eax, 0x3f800000
world_a_hv:
    mov [PROJ_A+8], eax
    mov eax, [ebx+D_CXH]
    mov [CENTRE_AX], eax
    mov eax, [ebx+D_CYH]
    mov [CENTRE_A], eax
    mov dword ptr [ebx+D_PASS_A], 1  ; so the insert hook rescales them
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
    mov eax, [esp+8]
    mov [ebx+D_SAVE_FB], eax
    mov dword ptr [ebx+D_VAR_B], 0
    fld dword ptr [ASPECT_B]
    fmul dword ptr [esp+8]
    fmul dword ptr [SCALE_B]
    fstp dword ptr [ebx+D_SAVE_B]
    fld dword ptr [ASPECT_B]
    fmul dword ptr [SCALE_B]
    fstp dword ptr [ebx+D_SAVE_B+4]
    jmp world_b_tail
world_b2:
    push ebx
    call world_b2_here
world_b2_here:
    pop ebx
    sub ebx, world_b2_here
    mov eax, [esp+8]
    mov eax, [esp+8]
    mov [ebx+D_SAVE_FB], eax
    mov dword ptr [ebx+D_VAR_B], 1
    fld dword ptr [esp+8]
    fmul dword ptr [ASPECT_B]
    fmul dword ptr [SCALE_B]
    fstp dword ptr [ebx+D_SAVE_B]
    mov eax, [SCALE_B]
    mov [ebx+D_SAVE_B+4], eax
world_b_tail:
    fld dword ptr [esp+8]
    fmul dword ptr [SCALE_B]
    fstp dword ptr [ebx+D_SAVE_B+8]
    mov eax, [ebx+D_SAVE_B]
    mov [PROJ_B], eax
    mov eax, [ebx+D_SAVE_B+4]
    mov [PROJ_B+4], eax
    mov eax, [ebx+D_SAVE_B+8]
    mov [PROJ_B+8], eax
    cmp dword ptr [ebx+D_DEPTH], 0
    je world_b_out
    mov eax, [ASPECT_B]
    cmp dword ptr [ebx+D_VAR_B], 0
    je world_b_hv
    mov eax, 0x3f800000
world_b_hv:
    mov [PROJ_B+4], eax
    mov eax, [ebx+D_CXH]
    mov [CENTRE_BX], eax
    mov eax, [ebx+D_CYH]
    mov [CENTRE_B], eax
    mov dword ptr [ebx+D_PASS_B], 1
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
    cmp dword ptr [ebx+D_DEPTH], 0
    je submit_a_world
submit_a_hud:
    fld dword ptr [ebx+D_SAVE_FA]
    fmul dword ptr [ASPECT_A]
    fstp dword ptr [PROJ_A]
    mov eax, [ASPECT_A]
    cmp dword ptr [ebx+D_VAR_A], 0
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
    mov dword ptr [ebx+D_PASS_A], 1
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
    mov dword ptr [ebx+D_PASS_A], 0
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
    cmp dword ptr [ebx+D_DEPTH], 0
    je submit_b_world
submit_b_hud:
    fld dword ptr [ebx+D_SAVE_FB]
    fmul dword ptr [ASPECT_B]
    fstp dword ptr [PROJ_B]
    mov eax, [ASPECT_B]
    cmp dword ptr [ebx+D_VAR_B], 0
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
    mov dword ptr [ebx+D_PASS_B], 1
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
    mov dword ptr [ebx+D_PASS_B], 0
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
    mov ecx, [ebx+D_DEPTH]
    mov [ebx+D_RETD+eax*4], ecx     ; depth to come back to
    inc ecx
    mov [ebx+D_DEPTH], ecx
    inc dword ptr [ebx+D_SP]
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
    dec dword ptr [ebx+D_SP]
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
    mov dword ptr [ebx+D_PASS_A], 0
    mov dword ptr [ebx+D_PASS_B], 0
    mov eax, [ebx+D_W]
    mov [FB_W], eax
    mov eax, [ebx+D_H]
    mov [FB_H], eax
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
    call 0x4800d0
    call post
    ret
stub2:
    call set1post
    call pre
    call 0x4804f0
    call post
    ret
stub3:
    call set2pre
    call pre
    call 0x5670c0
    call post
    ret
stub4:
    call set2post
    call pre
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
    mov dword ptr [ebx+D_BASEPTR], FB_PTR
    mov dword ptr [ebx+D_PHASE], 1
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
    mov dword ptr [ebx+D_BASEPTR], FB_PTR
    mov dword ptr [ebx+D_PHASE], 0
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
    mov dword ptr [ebx+D_BASEPTR], FB_ROW
    mov dword ptr [ebx+D_PHASE], 1
    pop ebx
    ret
set2post:
    push ebx
    call s2q_here
s2q_here:
    pop ebx
    sub ebx, s2q_here
    mov dword ptr [ebx+D_BASEPTR], FB_ROW
    mov dword ptr [ebx+D_PHASE], 0
    pop ebx
    ret

; fit: place D_LW x D_LH scaled by the layout's scale, centred on the
; viewport, clipped. Sets D_S, D_XSTEP/D_YSTEP (canvas px per viewport
; px), D_XOFF/D_YOFF, D_DW/D_DH, D_CX/D_CY.
fit:
    mov eax, [ebx+D_LAYOUT]
    mov eax, [ebx+eax*4+D_SCALE]
    mov [ebx+D_S], eax
    mov eax, 0x10000
    shl eax, 8
    xor edx, edx
    div dword ptr [ebx+D_S]
    shl eax, 8                      ; 1/s, 16.16
    test edx, edx                   ; rounded up, so that a source
    jz fit_step                     ; column boundary that lands exactly
    inc eax                         ; on a destination column is not
fit_step:                           ; undershot by the accumulated error
    mov [ebx+D_XSTEP], eax
    mov [ebx+D_YSTEP], eax
    ; x
    mov ecx, [ebx+D_LW]
    imul ecx, [ebx+D_S]
    shr ecx, 16                     ; scaled width
    mov eax, [ebx+D_W]
    sub eax, ecx
    sar eax, 1                      ; offset, may be negative
    mov dword ptr [ebx+D_CX], 0
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
    mov dword ptr [ebx+D_CY], 0
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
    mov dword ptr [SPLIT], 0
    xor ecx, ecx
    test eax, eax
    je pre_layout
    cmp dword ptr [ebx+D_SHOW], 0
    jne pre_layout                  ; single: 1P geometry
    inc ecx
    test byte ptr [FLAGS], 3
    je pre_layout
    inc ecx
pre_layout:
    mov [ebx+D_LAYOUT], ecx
    ; HUD projection factor for this layout, for the game's 0x51444d
    mov ecx, [ebx+ecx*4+D_HUD]
    mov [ebx+D_HUDF], ecx
    fld dword ptr [ebx+D_HUDF]
    fmul dword ptr [ebx+D_C65536]
    fistp dword ptr [ebx+D_S16]
    fild dword ptr [ebx+D_W]
    fld dword ptr [ebx+D_HUDF]
    fmul dword ptr [ebx+D_C640]
    fsubp st(1), st(0)
    fmul dword ptr [ebx+D_CHALF]
    fistp dword ptr [ebx+D_OXH]
    mov eax, [ebx+D_LAYOUT]
    mov eax, [ebx+eax*4+D_SHIFTX]
    add [ebx+D_OXH], eax
    fild dword ptr [ebx+D_H]
    fld dword ptr [ebx+D_HUDF]
    fmul dword ptr [ebx+D_C480]
    fsubp st(1), st(0)
    fmul dword ptr [ebx+D_CHALF]
    fistp dword ptr [ebx+D_OYH]
    mov eax, [ebx+D_SHIFTY]
    add [ebx+D_OYH], eax
    ; centre of the viewport in HUD units, and the rescale offsets that
    ; put the HUD frame's 320,240 back on OXH/OYH exactly
    fild dword ptr [ebx+D_W]
    fmul dword ptr [ebx+D_CHALF]
    fdiv dword ptr [ebx+D_HUDF]
    fistp dword ptr [ebx+D_CXH]
    fild dword ptr [ebx+D_H]
    fmul dword ptr [ebx+D_CHALF]
    fdiv dword ptr [ebx+D_HUDF]
    fistp dword ptr [ebx+D_CYH]
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
    mov dword ptr [ebx+D_LH], 480
    mov eax, 480
    cmp dword ptr [ebx+D_PHASE], 0
    je pre_43
    cmp dword ptr [ebx+D_LAYOUT], 0
    jne pre_43
    imul eax, [ebx+D_W]
    xor edx, edx
    div dword ptr [ebx+D_H]
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
    call fit
    ; the HUD band, for the insert hooks and for this composite
    mov dword ptr [ebx+D_PINON], 0
    mov dword ptr [ebx+D_PINROWS], 0
    mov eax, [ebx+D_PINTH]
    test eax, eax
    je pre_pin
    cmp dword ptr [ebx+D_LAYOUT], 0
    je pre_pin
    cmp dword ptr [ebx+D_OYH], 0
    jle pre_pin
    mov ecx, [MODE]                 ; the viewport's own player's state
    mov edx, [SUBMODE]
    cmp dword ptr [ebx+D_BASEPTR], FB_PTR
    je pre_pin_1p
    mov ecx, [MODE2]
    mov edx, [SUBMODE2]
pre_pin_1p:
    cmp ecx, 4
    jne pre_pin
    cmp edx, 0x1b
    je pre_pin_on
    cmp edx, 0x14
    je pre_pin_on
    cmp edx, 0x15
    je pre_pin_on
    sub edx, 9
    cmp edx, 3
    ja pre_pin
pre_pin_on:
    mov dword ptr [ebx+D_PINON], 1
    mov ecx, [ebx+D_OYH]
    shl ecx, 16
    mov [ebx+D_PINSUB], ecx
    cmp dword ptr [ebx+D_PHASE], 0
    jne pre_pin
    imul eax, [ebx+D_S]
    shr eax, 16
    mov [ebx+D_PINROWS], eax
pre_pin:
    ; pre-fill: canvas (cx, cy) from frame (fx0 + cx*s, fy0 + cy*s),
    ; fx0 = xoff - cx0*s; outside the viewport reads as 0.
    mov dword ptr [ebx+D_Y], 0
    lea edi, [ebx+D_OFF]
    mov ebp, [ebx+D_YOFF]
    shl ebp, 16
    mov eax, [ebx+D_CY]
    shr eax, 16
    imul eax, [ebx+D_S]
    sub ebp, eax
    mov [ebx+D_YSAVE], ebp
    cmp dword ptr [ebx+D_PINROWS], 0
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
    mov edx, [ebx+D_XOFF]
    shl edx, 16
    mov eax, [ebx+D_CX]
    shr eax, 16
    imul eax, [ebx+D_S]
    sub edx, eax
    xor ecx, ecx
pre_fill_px:
    mov eax, edx
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
    rep stosw
    pop edi
    push edi
    add edi, D_COPY-D_OFF
    mov ecx, [ebx+D_LW]
    rep stosw
    pop edi
pre_fill_next:
    add edi, OFF_PITCH
    add ebp, [ebx+D_S]
    inc dword ptr [ebx+D_Y]
    mov eax, [ebx+D_Y]
    cmp dword ptr [ebx+D_PINROWS], 0
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
    ; globals for the 2D code
    lea eax, [ebx+D_OFF]
    mov esi, [ebx+D_BASEPTR]
    mov [esi], eax
    mov dword ptr [FB_PITCH], OFF_PITCH
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
    cmp dword ptr [ebx+D_BASEPTR], FB_PTR
    jne post_engine_b
    cmp eax, 2
    je post_done
    jmp post_shown
post_engine_b:
    cmp eax, 1
    je post_done
post_shown:
    ; pre phase: anything painted beyond the 4:3 width?
    cmp dword ptr [ebx+D_PHASE], 0
    je post_fit
    mov eax, [ebx+D_LH]
    shl eax, 2
    xor edx, edx
    mov ecx, 3
    div ecx
    cmp eax, [ebx+D_LW]
    jge post_fit
    mov ecx, eax
    lea esi, [ebx+D_OFF]
    xor edx, edx
post_scan_row:
    push ecx
post_scan_px:
    mov ax, [esi+ecx*2]
    cmp ax, [esi+ecx*2+D_COPY-D_OFF]
    jne post_scan_painted
    inc ecx
    cmp ecx, [ebx+D_LW]
    jl post_scan_px
    pop ecx
    add esi, OFF_PITCH
    inc edx
    cmp edx, [ebx+D_LH]
    jl post_scan_row
    mov eax, [ebx+D_LH]
    shl eax, 2
    xor edx, edx
    mov ecx, 3
    div ecx
    mov [ebx+D_LW], eax
    jmp post_fit
post_scan_painted:
    pop ecx
post_fit:
    call fit
    mov eax, [ebx+D_DH]
    mov [ebx+D_YEND], eax
    mov eax, [ebx+D_YOFF]
    cmp dword ptr [ebx+D_PINROWS], 0
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
    mov dword ptr [ebx+D_Y], 0
post_yloop:
    mov esi, [ebx+D_YF]
    mov eax, esi
    and eax, 0xffff
    shr eax, 8
    mov [ebx+D_FY], eax             ; y fraction, 0..255
    shr esi, 16
    mov eax, esi
    inc eax
    cmp eax, [ebx+D_LH]
    jl post_row1
    dec eax                         ; clamp the second row
post_row1:
    imul eax, eax, OFF_PITCH
    lea eax, [ebx+eax+D_OFF]
    mov [ebx+D_ROW1], eax
    imul esi, esi, OFF_PITCH
    lea esi, [ebx+esi+D_OFF]
    mov edx, [ebx+D_CX]
    xor ecx, ecx
post_xloop:
    mov ebp, edx
    shr ebp, 16
    cmp dword ptr [ebx+D_FILTER], 0
    jne post_bilinear
    mov ax, [esi+ebp*2]
    cmp ax, [esi+ebp*2+D_COPY-D_OFF]
    je post_skip
    mov [edi+ecx*2], ax
    jmp post_skip
post_bilinear:
    mov ax, [esi+ebp*2]
    cmp ax, [esi+ebp*2+D_COPY-D_OFF]
    jne post_blend
    mov ax, [esi+ebp*2+2]
    cmp ax, [esi+ebp*2+2+D_COPY-D_OFF]
    jne post_blend
    push esi
    mov esi, [ebx+D_ROW1]
    mov ax, [esi+ebp*2]
    cmp ax, [esi+ebp*2+D_COPY-D_OFF]
    jne post_blend_pop
    mov ax, [esi+ebp*2+2]
    cmp ax, [esi+ebp*2+2+D_COPY-D_OFF]
    jne post_blend_pop
    pop esi
    jmp post_skip
post_blend_pop:
    pop esi
post_blend:
    push ecx
    push edx
    mov eax, ebp
    inc eax
    cmp eax, [ebx+D_LW]
    jl post_x1
    dec eax
post_x1:
    push eax                        ; [esp] = x1
    mov ecx, edx
    and ecx, 0xffff
    shr ecx, 8                      ; fx 0..255
    movzx eax, word ptr [esi+ebp*2]
    call unpack
    mov edx, 256
    sub edx, ecx
    call scale_acc
    push dword ptr [ebx+D_ACC]
    push dword ptr [ebx+D_ACC+4]
    push dword ptr [ebx+D_ACC+8]
    mov eax, [esp+12]
    movzx eax, word ptr [esi+eax*2]
    call unpack
    mov edx, ecx
    call scale_acc
    pop eax
    add [ebx+D_ACC+8], eax
    pop eax
    add [ebx+D_ACC+4], eax
    pop eax
    add [ebx+D_ACC], eax
    mov edx, 256
    sub edx, [ebx+D_FY]
    call scale_acc
    push dword ptr [ebx+D_ACC]
    push dword ptr [ebx+D_ACC+4]
    push dword ptr [ebx+D_ACC+8]
    push esi
    mov esi, [ebx+D_ROW1]
    movzx eax, word ptr [esi+ebp*2]
    call unpack
    mov edx, 256
    sub edx, ecx
    call scale_acc
    push dword ptr [ebx+D_ACC]
    push dword ptr [ebx+D_ACC+4]
    push dword ptr [ebx+D_ACC+8]
    mov eax, [esp+28]
    movzx eax, word ptr [esi+eax*2]
    call unpack
    mov edx, ecx
    call scale_acc
    pop eax
    add [ebx+D_ACC+8], eax
    pop eax
    add [ebx+D_ACC+4], eax
    pop eax
    add [ebx+D_ACC], eax
    mov edx, [ebx+D_FY]
    call scale_acc
    pop esi
    pop eax
    add [ebx+D_ACC+8], eax
    pop eax
    add [ebx+D_ACC+4], eax
    pop eax
    add [ebx+D_ACC], eax
    pop eax
    pop edx
    pop ecx
    call pack
    mov [edi+ecx*2], ax
post_skip:
    add edx, [ebx+D_XSTEP]
    inc ecx
    cmp ecx, [ebx+D_DW]
    jl post_xloop
    add edi, [ebx+D_PITCH]
    mov eax, [ebx+D_YSTEP]
    add [ebx+D_YF], eax
    inc dword ptr [ebx+D_Y]
    mov eax, [ebx+D_Y]
    cmp eax, [ebx+D_YEND]
    jl post_yloop
    cmp eax, [ebx+D_DH]
    jge post_margins
    mov eax, [ebx+D_DH]             ; the rest of the frame, centred; the
    mov [ebx+D_YEND], eax           ; rows the band left are not touched
    mov eax, [ebx+D_Y]
    add eax, [ebx+D_YOFF]
    imul eax, [ebx+D_PITCH]
    add eax, [ebx+D_BASE]
    mov edi, [ebx+D_XOFF]
    lea edi, [eax+edi*2]
    jmp post_yloop
post_margins:
    cmp dword ptr [ebx+D_PHASE], 0
    je post_done
    cmp dword ptr [ebx+D_3D], 0
    jne post_done
    cmp dword ptr [ebx+D_XOFF], 0
    je post_done
    mov edi, [ebx+D_BASE]
    mov dword ptr [ebx+D_Y], 0
    xor eax, eax
post_mloop:
    push edi
    mov ecx, [ebx+D_XOFF]
    rep stosw
    mov ecx, [ebx+D_DW]
    lea edi, [edi+ecx*2]
    mov ecx, [ebx+D_W]
    sub ecx, [ebx+D_DW]
    sub ecx, [ebx+D_XOFF]
    rep stosw
    pop edi
    add edi, [ebx+D_PITCH]
    inc dword ptr [ebx+D_Y]
    mov ecx, [ebx+D_Y]
    cmp ecx, [ebx+D_H]
    jl post_mloop
post_done:
    cmp dword ptr [ebx+D_DEBUG], 0
    je post_out
    cmp dword ptr [ebx+D_PHASE], 0
    jne post_out
    cmp dword ptr [ebx+D_BASEPTR], FB_PTR
    jne post_out
    call dbg_draw
post_out:
    popad
    ret

; dbg_draw: "MODE SUBMODE  MODE2 SUBMODE2  SHOW", hex, through the game's
; GDI text on the frame about to be shown (as asm/overlay.asm does).
dbg_draw:
    lea edi, [ebx+D_DBGSTR]
    mov eax, [MODE]
    call dbg_hex
    mov byte ptr [edi], 0x20
    inc edi
    mov eax, [SUBMODE]
    call dbg_hex
    mov word ptr [edi], 0x2020
    add edi, 2
    mov eax, [MODE2]
    call dbg_hex
    mov byte ptr [edi], 0x20
    inc edi
    mov eax, [SUBMODE2]
    call dbg_hex
    mov word ptr [edi], 0x2020
    add edi, 2
    mov eax, [ebx+D_SHOW]
    call dbg_hex
    mov byte ptr [edi], 0
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

; unpack: ax (surface pixel) -> D_ACC r, D_ACC+4 g, D_ACC+8 b, 0..255
unpack:
    push ecx
    push edx
    mov edx, eax
    cmp dword ptr [PIXFMT], 0x22b
    je unpack_555
    shr edx, 11
    shl edx, 3
    mov [ebx+D_ACC], edx
    mov edx, eax
    shr edx, 5
    and edx, 63
    shl edx, 2
    mov [ebx+D_ACC+4], edx
    and eax, 31
    shl eax, 3
    mov [ebx+D_ACC+8], eax
    pop edx
    pop ecx
    ret
unpack_555:
    shr edx, 10
    and edx, 31
    shl edx, 3
    mov [ebx+D_ACC], edx
    mov edx, eax
    shr edx, 5
    and edx, 31
    shl edx, 3
    mov [ebx+D_ACC+4], edx
    and eax, 31
    shl eax, 3
    mov [ebx+D_ACC+8], eax
    pop edx
    pop ecx
    ret
; scale_acc: D_ACC channels *= edx
scale_acc:
    push eax
    mov eax, [ebx+D_ACC]
    imul eax, edx
    mov [ebx+D_ACC], eax
    mov eax, [ebx+D_ACC+4]
    imul eax, edx
    mov [ebx+D_ACC+4], eax
    mov eax, [ebx+D_ACC+8]
    imul eax, edx
    mov [ebx+D_ACC+8], eax
    pop eax
    ret
; pack: D_ACC channels (*65536, 0..255) -> ax in the surface format
pack:
    push edx
    mov eax, [ebx+D_ACC]
    shr eax, 16
    mov edx, [ebx+D_ACC+4]
    shr edx, 16
    cmp dword ptr [PIXFMT], 0x22b
    je pack_555
    shr eax, 3
    shl eax, 11
    shr edx, 2
    shl edx, 5
    or eax, edx
    mov edx, [ebx+D_ACC+8]
    shr edx, 16
    shr edx, 3
    or eax, edx
    pop edx
    ret
pack_555:
    shr eax, 3
    shl eax, 10
    shr edx, 3
    shl edx, 5
    or eax, edx
    mov edx, [ebx+D_ACC+8]
    shr edx, 16
    shr edx, 3
    or eax, edx
    pop edx
    ret

; Render-list insert hooks (0x5d4628, 0x5d5360 for A; 0x5e02b0, 0x5df538
; for B). In a HUD pass the record's four packed vertex positions, whole
; 640x480 pixels, are scaled to the HUD scale and offset to the
; viewport. The renderer fills inclusively, so a vertex on the low side
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
ins_a_nolog:
    mov eax, [ebx+D_DIM]            ; hangar: shade the mech being drawn
    test eax, eax
    je ins_a_nodim
    imul eax, [edx+0xc]
    shr eax, 16
    mov [edx+0xc], eax
ins_a_nodim:
    cmp dword ptr [ebx+D_PASS_A], 0
    je ins_a_done
    call rescale
ins_a_done:
    popad
    mov esi, [ebx*4+LIST_A]
    ret
insert_b:
    pushad
    call ins_b_here
ins_b_here:
    pop ebx
    sub ebx, ins_b_here
ins_b_nolog:
    cmp dword ptr [ebx+D_PASS_B], 0
    je ins_b_done
    call rescale
ins_b_done:
    popad
    mov esi, [ebx*4+LIST_B]
    ret
rescale:
    ; minima and maxima over the four vertices
    mov dword ptr [ebx+D_XMIN], 0x7fffffff
    mov dword ptr [ebx+D_YMIN], 0x7fffffff
    mov dword ptr [ebx+D_XMAX], -0x7fffffff
    mov dword ptr [ebx+D_YMAX], -0x7fffffff
    lea esi, [edx+0x10]
    mov ecx, 4
rescale_min:
    movsx eax, word ptr [esi]
    cmp eax, [ebx+D_XMIN]
    jge rescale_min_x2
    mov [ebx+D_XMIN], eax
rescale_min_x2:
    cmp eax, [ebx+D_XMAX]
    jle rescale_min_y
    mov [ebx+D_XMAX], eax
rescale_min_y:
    movsx eax, word ptr [esi+2]
    cmp eax, [ebx+D_YMIN]
    jge rescale_min_y2
    mov [ebx+D_YMIN], eax
rescale_min_y2:
    cmp eax, [ebx+D_YMAX]
    jle rescale_min_next
    mov [ebx+D_YMAX], eax
rescale_min_next:
    add esi, 4
    dec ecx
    jnz rescale_min
    ; y offset: the top-aligned one for a polygon wholly inside the band
    mov eax, [ebx+D_OFFY16]
    cmp dword ptr [ebx+D_PINON], 0
    je rescale_offy
    mov edi, [ebx+D_YMAX]
    sub edi, [ebx+D_CYH]
    add edi, 240                    ; HUD frame row of the lowest vertex
    cmp edi, [ebx+D_PINTH]
    jge rescale_offy
    sub eax, [ebx+D_PINSUB]
rescale_offy:
    mov [ebx+D_OFFY], eax
    lea esi, [edx+0x10]
    xor ecx, ecx                    ; vertex index
rescale_loop:
    ; x: low side unless above the minimum; a degenerate axis (a 1 px
    ; line) becomes s px thick, with vertices 1 and 2 on the high side
    movsx eax, word ptr [esi]
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
    add eax, [ebx+D_OFFX16]
    add eax, 0x8000
    sar eax, 16
    dec eax
    jmp rescale_x_done
rescale_x_low:
    imul eax, [ebx+D_S16]
    add eax, [ebx+D_OFFX16]
    add eax, 0x8000
    sar eax, 16
rescale_x_done:
    movsx edi, word ptr [esi+2]
    cmp edi, [ebx+D_YMIN]
    jne rescale_y_high
    push eax
    mov eax, [ebx+D_YMAX]
    cmp eax, [ebx+D_YMIN]
    pop eax
    jne rescale_y_low
    push eax
    lea eax, [ecx-1]
    cmp eax, 2
    pop eax
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
    mov dword ptr [ebx+D_SHOW], 0
    cmp dword ptr [SPLIT], 0
    je frame_go
    mov ecx, 1
    cmp dword ptr [MODE], 4         ; not a match (boot, title): one view
    jne frame_single
    mov edx, [SUBMODE]
    call split_state
    jne frame_go                    ; a round on P1's side
    cmp dword ptr [MODE2], 4
    jne frame_single
    mov edx, [SUBMODE2]
    call split_state
    jne frame_go                    ; or on P2's
    cmp edx, [SUBMODE]              ; neither: the lower sub-state's player
    jae frame_single
    mov ecx, 2
frame_single:
    mov [ebx+D_SHOW], ecx
    push dword ptr [SPLIT]
    mov dword ptr [SPLIT], 0
    push dword ptr [esp+20]
    mov eax, VIEWPORT
    call eax
    add esp, 4
    pop dword ptr [SPLIT]
    mov eax, [FB_PTR]               ; viewport 2 on the same surface
    mov [FB_ROW], eax
    mov dword ptr [MASKOFF], 0
    jmp frame_out
frame_go:
    push dword ptr [esp+16]
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
    cmp dword ptr [ebx+D_SHOW], 2
    jne flush_a_go
    push dword ptr [SKIP_A]
    mov dword ptr [SKIP_A], 1
    call eax
    pop dword ptr [SKIP_A]
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
    cmp dword ptr [ebx+D_SHOW], 1
    jne flush_b_go
    push dword ptr [SKIP_B]
    mov dword ptr [SKIP_B], 1
    call eax
    pop dword ptr [SKIP_B]
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
    fld dword ptr [eax*4+0x345bd58]     ; camera angle
    mov eax, [ebp+0xc]
    lea eax, [eax+eax*4]
    mov edx, [ebp+0xc]
    lea eax, [edx+eax*4]
    fsub dword ptr [eax*4+0x345b2c8]     ; less the entity's: negative
                                         ; to the right of the camera
    fadd dword ptr [ebx+hd_inner]        ; degrees short of the edge,
    fmul dword ptr [ebx+hd_slope]        ; over the fade range
    fcom dword ptr [ebx+hd_floor]
    fnstsw ax
    test ah, 1                          ; below the floor: the floor
    je hd_lo_ok                         ; (0 would mean no scaling)
    fstp st(0)
    fld dword ptr [ebx+hd_floor]
hd_lo_ok:
    fld1
    fcomp st(1)
    fnstsw ax
    test ah, 1                          ; 1 or more: left alone
    jne hd_full
    fmul dword ptr [ebx+D_C65536]
    fistp dword ptr [ebx+D_DIM]
    jmp hd_go
hd_full:
    fstp st(0)
    mov dword ptr [ebx+D_DIM], 0
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
    mov dword ptr [ebx+D_DIM], 0
    push [ebx+D_HDRET]
    mov ebx, [esp+4]
    add esp, 8
    jmp dword ptr [esp-8]
hd_inner:
    .long 0x41e370a4                    ; 28.43
hd_slope:
    .long 0x3daaaaab                    ; 1/12
hd_floor:
    .long 0x37800000                    ; 1/65536

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
    push dword ptr [ebx+D_MODEH]
    push dword ptr [ebx+D_MODEW]
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
    mov dword ptr [FONTSET], 0
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
    push dword ptr [ebx+D_F4MODE]
    pop dword ptr [ebx+D_F4WANT]
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
    and dword ptr [ebx+D_F4WANT], 1
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
    cmp dword ptr [LOOPMODE], 2         ; not in a network game
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
    cmp dword ptr [ebx+D_F4MODE], 0
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
