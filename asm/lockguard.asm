bits 32
; The per-frame back buffer lock, guarded. The game releases its surfaces
; when it loses the window and recreates them when it gets it back, and a
; frame can run in between: the lock wrapper then reads the vtable of a
; null surface and dies. Under cnc-ddraw that is what ALT+TAB does to the
; OEM and Japanese builds, and to retail without it. With no surface the
; lock reports lost, which the wrapper already turns into "no frame".
;
; In: the wrapper's frame, [ebp + 8] the surface, its five Lock arguments
; pushed. Reached by a jmp from the site; leaves by one.

extern LOCKBACK                 ; the wrapper's test of the result

DDERR_SURFACELOST equ 0x887601c2

lockguard:
    mov     eax, [ebp + 8]
    test    eax, eax
    jz      .none
    mov     eax, [eax]
    call    [eax + 0x64]        ; IDirectDrawSurface::Lock, stdcall
    jmp     LOCKBACK
.none:
    add     esp, 0x14           ; the five arguments Lock would have eaten
    mov     eax, DDERR_SURFACELOST
    jmp     LOCKBACK

; The same frame's flip of the primary surface, a few instructions on.
; The wrapper's failure is not checked by its caller, so with both surfaces
; gone the flip dereferences the second one. Same answer: with no surface
; the flip fails, and the frame ends where a failed flip ends it.
;
; In: the frame routine, its two Flip arguments pushed.

extern PRIMARY                  ; the surface the frame flips
extern FLIPBACK                 ; the frame's test of the flip's result

flipguard:
    mov     eax, [PRIMARY]
    test    eax, eax
    jz      .none
    mov     eax, [eax]
    call    [eax + 0x2c]        ; IDirectDrawSurface::Flip, stdcall
    jmp     FLIPBACK
.none:
    add     esp, 8              ; the two arguments Flip would have eaten
    mov     eax, DDERR_SURFACELOST
    jmp     FLIPBACK
