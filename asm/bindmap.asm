bits 32
; The restore half of asm/bindlist.asm: mapping a saved bind byte back to a
; combo index walks the same list, in a routine too far from the other two
; to share their cave. Same rules: pinned slots, device picked by the side
; being configured. The startup defaults writer rides along at the end,
; there being no room for it beside its subject in asm/kbpage.asm.

org 0x005fd904          ; a run of zeros in .rdata

CURPLAYER   equ 0x00bf6bac
PENDING     equ 0x00bf6838      ; + player * 0x70, see asm/bindlist.asm
SIMPLE      equ 3
KEYLIST     equ 0x0066d438
PADLIST     equ 0x00624843
KEYCOUNT    equ 0x21
PADCOUNT    equ 0x10
MAPDONE     equ 0x004980d9      ; where the search loop's jge went

; ---------------------------------------------------------------- 0x5fd904
devcur:
    push    eax
    mov     eax, [CURPLAYER]
    imul    eax, eax, 0x70
    cmp     dword [eax + PENDING], SIMPLE
    pop     eax
    ret

    times   0x18 - ($ - devcur) db 0x90

; ---------------------------------------------------------------- 0x5fd91c
; The search loop's `cmp [ebp-0xc], count` and the jge after it.
mapcount:
    call    devcur
    je      .simple
    cmp     dword [ebp - 0xc], PADCOUNT
    jmp     .test
.simple:
    cmp     dword [ebp - 0xc], KEYCOUNT
.test:
    jl      .stay
    add     esp, 4
    jmp     MAPDONE
.stay:
    ret

    times   0x38 - ($ - devcur) db 0x90

; ---------------------------------------------------------------- 0x5fd93c
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

; ---------------------------------------------------------------- 0x5fd954
; Startup fills every profile's block in turn. The call that filled +0x38
; with 2 Joysticks defaults lands here instead, and the block gets Keyboard
; (Simple)'s shipped set. The live-refresh flag is dropped: the shared live
; table is seeded by the device apply, out of whichever block the saved
; device owns.
%include "padtables.inc"    ; SIMPLEDEF, the shipped sets it builds
BLOCKS      equ 0x00bf6838
MEMCPY      equ 0x005e6030

    times   0x50 - ($ - devcur) db 0x90

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
