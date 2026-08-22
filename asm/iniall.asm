bits 32
; The ini loader runs one section per player by saved device, so
; asm/iniload.asm - hooked into the padtype section - only ran for a
; player saved on Simple. Any other saved device left both keyboard-page
; blocks at their shipped sets, and the next commit of either profile
; would then save those defaults over the customs in v_on.ini. This runs
; the loader for both players at the load loop's normal exit, whatever
; the saved devices; it is idempotent, so the in-section pass for a
; Simple player doing the same work first is harmless.
;
; The tail seeds the stick deadzones: 40% per player unless that
; player's "1P Deadzone=" or "2P Deadzone=" v_on.ini line says otherwise
; - two digits, 05 to 95; anything else keeps 40. asm/pagesel.asm's
; tail, which this cave had no room for, writes the threshold for the
; tick and the digit pair for that player's F11 box; the boxes write
; the lines back when the dialog closes, through asm/iniparse.asm's
; tail.

org 0x0063c5f4          ; a run of zeros in .rdata with nothing pointing
                        ; into it and no table ending against it

LOADSIMPLE  equ 0x0060702c      ; asm/iniload.asm, cdecl (player, flag)
FINDLINE    equ 0x005b1871      ; (key) -> value text, 0 if absent
DZSEED      equ 0x00601c08      ; asm/pagesel.asm's tail: (cl, ebx)

%include "padtables.inc"    ; DZKEYS

iniall:
    push    1                  ; cdecl leaves the arguments, so both calls
    push    0                  ; are made before either is cleaned up
    call    LOADSIMPLE
    push    1
    push    1
    call    LOADSIMPLE
    add     esp, 16

    push    ebx                 ; callee-saved: the hooked function's
    push    1                   ; caller may hold it live. 2P first; the
    pop     ebx                 ; order is free.
.dz:
    imul    eax, ebx, 12        ; the two key strings, 12 bytes apiece
    add     eax, DZKEYS
    push    eax
    call    FINDLINE
    pop     edx                 ; balances the push
    push    40                  ; the default - after the call, which as
    pop     ecx                 ; cdecl owns ecx and may not give it back
    test    eax, eax
    je      .have
    mov     ax, [eax]           ; al first digit, ah second
    sub     ax, 0x3030
    cmp     al, 9
    ja      .have
    cmp     ah, 9
    ja      .have
    xchg    al, ah
    aad                         ; al = tens * 10 + ones
    cmp     al, 95
    ja      .have
    cmp     al, 5
    jb      .have
    movzx   ecx, al
.have:
    call    DZSEED              ; (cl, ebx): that player's pair
    dec     ebx
    jns     .dz
    pop     ebx
    ret                         ; the hook is a call; this resumes the
                                ; epilogue the replaced jmp targeted
