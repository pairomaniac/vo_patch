bits 32
; The line from the enemy to the distance readout.
;
; The 2D quad submits (QUADA/QUADB: the marker's lines and triangles, the
; HUD frame lines) project their vertices, which carry z = 1.0, with the
; aspect slot of the renderer's projection. The clipper they then hand
; the quad to re-projects from the same vertices whenever it clips
; against the picture's edges or splits an edge longer than the
; subdivision threshold, and does so with the 3D slot, focal length
; included: 600 times the size, drawn last. The marker's leader line is
; the one quad that gets long enough, once the enemy is far enough from
; the readout - the flat grey band that flashes across the picture as
; the enemy comes on from the side.
;
; quad2d_a/b run in place of the submits' seven-byte prologues and raise
; the renderer's flag; walk_a/b do the same at the mesh walkers (WALKA/
; WALKB, the only other callers of the clippers) and drop it; the
; clippers' eleven `fdivr dword [PROJ]` sites per renderer call
; clipproj_a/b, which divide by the aspect slot while the flag is up.
; st0 is z on entry and scale/z on return, as at the sites.

extern PROJA                    ; renderer A projection: 3D scale
extern PROJA2D                  ; its aspect slot, the 2D quads' scale
extern PROJB
extern PROJB2D

; The prologue the four hooked functions share: push ebp; mov ebp, esp;
; push eax; push ebx; push ecx; push edx. The site is a call, so [esp] is
; site+5; the flag is set and the prologue replayed, ending at site+7.
%macro prologue 2               ; flag, value
    xchg    ebp, [esp]          ; the push ebp; ebp: site+5
    push    eax
    push    ebx
    push    ecx
    push    edx
    mov     dword [%1], %2
    lea     eax, [ebp + 2]      ; past the two nops after the call
    mov     ebp, esp
    add     ebp, 16             ; mov ebp, esp, as after the push
    push    eax
    mov     eax, [esp + 16]
    ret
%endmacro

quad2d_a:
    prologue flag_a, 1
quad2d_b:
    prologue flag_b, 1
walk_a:
    prologue flag_a, 0
walk_b:
    prologue flag_b, 0

clipproj_a:
    cmp     dword [flag_a], 0
    jne     .flat
    fdivr   dword [PROJA]
    ret
.flat:
    fdivr   dword [PROJA2D]
    ret
clipproj_b:
    cmp     dword [flag_b], 0
    jne     .flat
    fdivr   dword [PROJB]
    ret
.flat:
    fdivr   dword [PROJB2D]
    ret

flag_a: dd 0
flag_b: dd 0
