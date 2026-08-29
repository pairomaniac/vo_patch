bits 32
; The restore half of asm/bindlist.asm: mapping a saved bind byte back to a
; combo index walks the same list, in a routine too far from the other two
; to share a blob. Same rule: device picked by the side being configured.
; The startup defaults writer is at the end.

extern CURPLAYER
extern BLOCKS                   ; + player * 0x70, see asm/bindlist.asm
SIMPLE      equ 3
extern KEYLIST
extern PADLIST
KEYCOUNT    equ 0x21
PADCOUNT    equ 0x10
extern SELSET                  ; where the search loop's jge went
%include "frames.inc"      ; the caller's locals, by name; the offset
                            ; is the retail build's, and build.py finds
                            ; each use so a build can move it
                            ; SELIDX: the preselect loop counter

; ----------------------------------------------------------------
devcur:
    push    eax
    mov     eax, [CURPLAYER]
    imul    eax, eax, 0x70
    cmp     dword [eax + BLOCKS], SIMPLE
    pop     eax
    ret

; ----------------------------------------------------------------
; The search loop's `cmp [ebp-0xc], count` and the jge after it.
mapcount:
    call    devcur
    je      .simple
    cmp     dword [ebp + SELIDX], PADCOUNT
    jmp     .test
.simple:
    cmp     dword [ebp + SELIDX], KEYCOUNT
.test:
    jl      .stay
    add     esp, 4
    jmp     SELSET
.stay:
    ret

; ----------------------------------------------------------------
; The search's `cmp [eax*8 + list + 4], ecx`. The jne after it stays at the
; site and reads the flags this leaves.
mapid:
    call    devcur
    je      .simple
    cmp     [eax*8 + PADLIST + 4], ecx
    ret
.simple:
    cmp     [eax*8 + KEYLIST + 4], ecx
    ret

; ----------------------------------------------------------------
; Startup fills every profile's block in turn. The call that filled +0x38
; with 2 Joysticks defaults lands here instead, and the block gets Keyboard
; (Simple)'s shipped set. The live-refresh flag is dropped: the shared live
; table is seeded by the device apply, out of whichever block the saved
; device owns.
%include "padtables.inc"    ; SIMPLEDEF, the shipped sets it builds
extern BLOCKS
extern MEMCPY

simple_defaults:                ; cdecl (player, flag)
    mov     eax, [esp + 4]
    imul    ecx, eax, 0x70
    add     ecx, BLOCKS + 0x38
    imul    eax, eax, 0x18
    add     eax, SIMPLEDEF
    push    0x18
    push    eax
    push    ecx
    call    MEMCPY
    add     esp, 12
    ret
