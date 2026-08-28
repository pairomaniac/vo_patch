bits 32
; The bind page serves two devices now. Its fill and store routines walk a
; (name, id) list whose address and length were immediates; these shims pick
; the pair by the device of the side being configured. Slots are pinned: each
; site names an address here, and nothing downstream would catch a drift.

extern CURPLAYER                ; the side being configured, 0 or 1
extern BLOCKS                   ; + player * 0x70: the device picked on the
                                ; F7 screen, live before OK commits it to
                                ; 0x3651540 - the page opens against this
SIMPLE      equ 3               ; Keyboard (Simple)'s slot
extern KEYLIST                  ; the game's 33 named keys
extern PADLIST                  ; the 16 pad inputs, asm/padtables.py
KEYCOUNT    equ 0x21
PADCOUNT    equ 0x10
extern FILLDONE                 ; where the fill loop's jge went
%include "frames.inc"      ; the caller's locals, by name; the offset
                            ; is the retail build's, and build.py finds
                            ; each use so a build can move it
                            ; FILLIDX: the fill loop counter

; ---------------------------------------------------------------- 0x5fd7e4
; ZF set when the side being configured is on Keyboard (Simple).
devcur:
    push    eax
    mov     eax, [CURPLAYER]
    imul    eax, eax, 0x70
    cmp     dword [eax + BLOCKS], SIMPLE
    pop     eax
    ret

    times   0x18 - ($ - devcur) db 0x90

; ---------------------------------------------------------------- 0x5fd7fc
; Replaces the fill loop's `cmp [ebp-8], count` and the jge after it. The
; not-taken path returns into the loop body; the taken one leaves through
; the jge's own target, dropping the return address first.
fillcount:
    call    devcur
    je      .simple
    cmp     dword [ebp + FILLIDX], PADCOUNT
    jmp     .test
.simple:
    cmp     dword [ebp + FILLIDX], KEYCOUNT
.test:
    jl      .stay
    add     esp, 4
    jmp     FILLDONE
.stay:
    ret

    times   0x38 - ($ - devcur) db 0x90

; ---------------------------------------------------------------- 0x5fd81c
; The fill loop's `mov eax, [eax*8 + list]`, an entry's name.
fillname:
    call    devcur
    je      .simple
    mov     eax, [eax*8 + PADLIST]
    ret
.simple:
    mov     eax, [eax*8 + KEYLIST]
    ret

    times   0x50 - ($ - devcur) db 0x90

; ---------------------------------------------------------------- 0x5fd834
; The store routine's `mov al, [eax*8 + list + 4]`, an entry's bind byte.
storeid:
    call    devcur
    je      .simple
    mov     al, [eax*8 + PADLIST + 4]
    ret
.simple:
    mov     al, [eax*8 + KEYLIST + 4]
    ret
