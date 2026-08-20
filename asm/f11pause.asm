bits 32
; The F11 Extras dialog's runner. The built-in F-key dialogs pause the
; game and the music around their DialogBox and resume after; this gives
; F11 the same manners. Called from asm/debugbox.asm's hook with ebx =
; DialogBoxIndirectParamA and the hook's frame live ([ebp + 8] is the
; parent window); it pushes the dialog's five arguments itself.

org 0x0063bf24          ; a run of zeros in .rdata

%include "dialogs.inc"      ; TEMPLATE

GPAUSE      equ 0x005c67c5      ; the built-in dialogs' pause, arg 0
GRESUME     equ 0x005c680b      ; and their resume
GETMODULE   equ 0x0365d4a0      ; GetModuleHandleA
DLGPROC     equ 0x005f4ed8      ; asm/debugbox.asm's dialog procedure

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
