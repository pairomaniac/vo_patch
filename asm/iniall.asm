bits 32
; The ini loader runs one section per player by saved device, so
; asm/iniload.asm - hooked into the padtype section - only ran for a
; player saved on Simple. Any other saved device left both keyboard-page
; blocks at their shipped sets, and the next commit of either profile
; would then save those defaults over the customs in v_on.ini. This runs
; the loader for both players at the load loop's normal exit, whatever
; the saved devices; it is idempotent, so the in-section pass for a
; Simple player doing the same work first is harmless.

org 0x005fb144          ; a run of zeros in .rdata

LOADSIMPLE  equ 0x0060702c      ; asm/iniload.asm, cdecl (player, flag)

iniall:
    push    1
    push    0
    call    LOADSIMPLE
    add     esp, 8
    push    1
    push    1
    call    LOADSIMPLE
    add     esp, 8
    ret                         ; the hook is a call; this resumes the
                                ; epilogue the replaced jmp targeted
