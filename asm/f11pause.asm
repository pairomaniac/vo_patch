bits 32
; The F11 Extras dialog's runner. The built-in F-key dialogs pause the
; game and the music around their DialogBox and resume after; this gives
; F11 the same manners. Called from asm/debugbox.asm's hook with ebx =
; DialogBoxIndirectParamA and the hook's frame live ([ebp + 8] is the
; parent window); it pushes the dialog's five arguments itself.
;
; The tail is the dialog's check-box init, evicted from debugbox.asm's
; cave when the second deadzone box needed the room. (hwnd), stdcall;
; esi and edi are the caller's to lose, which the dialog procedure
; refills after the call.

org 0x0063bf24          ; a run of zeros in .rdata

%include "dialogs.inc"      ; TEMPLATE, CHECKS

GPAUSE      equ 0x005c67c5      ; the built-in dialogs' pause, arg 0
GRESUME     equ 0x005c680b      ; and their resume
GETMODULE   equ 0x0365d4a0      ; GetModuleHandleA
CHECKDLGBTN equ 0x0365d544      ; CheckDlgButton
DLGPROC     equ 0x005f4ed8      ; asm/debugbox.asm's dialog procedure
BASE        equ 0x0063bf24      ; the org, for the pin below

f11wrap:
    push    0
    call    GPAUSE
    add     esp, 4
    push    0                           ; dwInitParam
    push    DLGPROC
    push    dword [ebp + 8]             ; hWndParent
    push    TEMPLATE
    push    0
    call    [GETMODULE]
    push    eax                         ; hInstance
    call    ebx                         ; stdcall eats the five arguments
    call    GRESUME
    ret

; CHECKS is one (flag, id) pair per check box; each box is ticked from the
; game's own flag, so what the dialog shows is what is on.
    times   (0x0063bf50 - BASE) - ($ - $$) db 0
f11checks:
    mov     esi, CHECKS
    push    3
    pop     edi
.c:
    mov     eax, [esi]
    mov     eax, [eax]
    xor     ecx, ecx
    cmp     eax, 1
    sete    cl
    push    ecx
    push    dword [esi + 4]
    push    dword [esp + 12]            ; the hwnd argument, past two pushes
    call    [CHECKDLGBTN]
    add     esi, 8
    dec     edi
    jne     .c
    ret     4
