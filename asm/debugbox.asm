bits 32
org 0x005f4e7c          ; the .text cave the F11 dialog patch drops this into
BASE        equ 0x005f4e7c      ; the org again, for the pins below
; The F11 Extras dialog: a window-procedure hook, the dialog procedure, and
; the Quit Program case that did not fit before the end of the second cave.
;
; Two addresses in here are named from outside this file and cannot move:
;
;   0x5f4e7c  the window procedure pointer at 0x1c4d7e is repointed here
;   0x5f4ed8  the hook passes the dialog procedure to DialogBoxIndirectParamA
;
; `times` pins the second one, so nasm fails rather than shifting it.
;
; The strings, the tables and the dialog template are data, built by
; asm/dialogs.py, which also emits the addresses and control ids below.

BOXLEN      equ 388             ; the cave from here to 0x5f5000. The zeros
                                ; run fourteen bytes further, to 0x5f500e,
                                ; but 0x5f5000 is a qword 0.0 that 0x401ce4
                                ; compares against and 0x5f5008 a qword 1.0
                                ; that three sites read. Zero is not free.

; USER32 and DLGBOXPROC, the two strings; CHECKS, one entry per check box;
; RATES, the frame rate list; TEMPLATE, the dialog itself; and CMD_QUIT and
; IDCANCEL, two of the control ids.
%include "dialogs.inc"

MOTION      equ 0x006c84d0      ; frame rate, 1 to 5
MODE        equ 0x01ae3594      ; game state; 4 is a match in progress
SUBMODE     equ 0x01ae3690      ; and its sub-state, 0x1f the ending
HWND        equ 0x01ae5f58      ; the game's window
ORIGWNDPROC equ 0x005c6857      ; the handler the hook falls through to

LOADLIB     equ 0x0365d504      ; LoadLibraryA
GETPROC     equ 0x0365d508      ; GetProcAddress
GETMODULE   equ 0x0365d4a0      ; GetModuleHandleA
SENDMSG     equ 0x0365d52c      ; SendMessageA
ENDDIALOG   equ 0x0365d538      ; EndDialog
CHECKDLGBTN equ 0x0365d544      ; CheckDlgButton
GETDLGITEM  equ 0x0365d54c      ; GetDlgItem
POSTMSG     equ 0x0365d56c      ; PostMessageA

WM_KEYDOWN  equ 0x0100
WM_INITDLG  equ 0x0110
WM_COMMAND  equ 0x0111
VK_F11      equ 0x7a
CB_ADDSTRING equ 0x143
CB_GETCURSEL equ 0x147
CB_SETCURSEL equ 0x14e

; The frame rate combo and the five commands behind it. Motion moved to the
; F5 page and the combo went with it, so no control carries IDC_RATE and
; neither branch below is reachable.
IDC_RATE    equ 0x3e8
CMD_RATE1   equ 0x9c55          ; the game's own command ids, 1/1 to 1/5

; nasm assembles `mov r32, r32` and `xor r32, r32` as 89 and 31; the code
; this replaces used the 8b and 33 encodings. The `db` lines below keep the
; blob byte-identical to the hex it was reconstructed from.

; ---------------------------------------------------------------- 0x5f4e7c
; Window procedure hook. Everything except F11 goes on to the original.
hook:
    push    ebp
    db      0x8b, 0xec                  ; mov ebp, esp
    push    ebx
    cmp     dword [ebp + 0x0c], WM_KEYDOWN
    jne     .pass
    cmp     dword [ebp + 0x10], strict dword VK_F11
    jne     .pass

    ; DialogBoxIndirectParamA is not in the import table, so it is fetched.
    push    USER32
    call    [LOADLIB]
    push    DLGBOXPROC
    push    eax
    call    [GETPROC]
    test    eax, eax
    je      .done
    db      0x8b, 0xd8                  ; mov ebx, eax

    push    0                           ; dwInitParam
    push    dlgproc
    push    dword [ebp + 8]             ; hWndParent
    push    TEMPLATE
    push    0
    call    [GETMODULE]
    push    eax                         ; hInstance
    call    ebx
.done:
    db      0x33, 0xc0                  ; xor eax, eax
    pop     ebx
    pop     ebp
    ret     0x10
.pass:
    pop     ebx
    pop     ebp
    jmp     ORIGWNDPROC

    times   (0x005f4ed8 - BASE) - ($ - $$) db 0

; ---------------------------------------------------------------- 0x5f4ed8
; Dialog procedure. Control ids are the game's own command ids, so a click
; is posted to the main window as the menu item it replaces.
dlgproc:
    push    ebp
    db      0x8b, 0xec                  ; mov ebp, esp
    push    ebx
    push    esi
    push    edi
    mov     eax, dword [ebp + 0x0c]
    cmp     eax, WM_INITDLG
    jne     .command

    ; Tick each box from the flag it reflects.
    mov     esi, CHECKS
    mov     edi, 3
.checks:
    mov     eax, dword [esi]
    mov     eax, dword [eax]
    db      0x33, 0xc9                  ; xor ecx, ecx
    cmp     eax, 1
    sete    cl
    push    ecx
    push    dword [esi + 4]
    push    dword [ebp + 8]
    call    [CHECKDLGBTN]
    add     esi, 8
    dec     edi
    jne     .checks

    ; Fill the frame rate list and select the current one.
    push    IDC_RATE
    push    dword [ebp + 8]
    call    [GETDLGITEM]
    db      0x8b, 0xd8                  ; mov ebx, eax
    mov     esi, RATES
    mov     edi, 5
.rates:
    push    esi
    push    0
    push    CB_ADDSTRING
    push    ebx
    call    [SENDMSG]
    add     esi, 4
    dec     edi
    jne     .rates
    mov     eax, dword [MOTION]
    dec     eax
    push    0
    push    eax
    push    CB_SETCURSEL
    push    ebx
    call    [SENDMSG]
    jmp     .handled

.command:
    cmp     eax, WM_COMMAND
    jne     .ignore
    mov     eax, dword [ebp + 0x10]
    movzx   ecx, ax                     ; control id
    shr     eax, 0x10                   ; notification
    cmp     ecx, IDC_RATE
    jne     .button
    dec     eax                         ; CBN_SELCHANGE
    jne     .ignore
    push    IDC_RATE
    push    dword [ebp + 8]
    call    [GETDLGITEM]
    push    0
    push    0
    push    CB_GETCURSEL
    push    eax
    call    [SENDMSG]
    add     eax, CMD_RATE1
    db      0x8b, 0xc8                  ; mov ecx, eax
    jmp     .post

.button:
    cmp     ecx, IDCANCEL
    jne     quit
    push    0
    push    dword [ebp + 8]
    call    [ENDDIALOG]
    jmp     .handled

.post:
    push    0
    push    ecx
    push    WM_COMMAND
    push    dword [HWND]
    call    [POSTMSG]
.handled:
    mov     eax, 1
    jmp     .ret
.ignore:
    db      0x33, 0xc0                  ; xor eax, eax
.ret:
    pop     edi
    pop     esi
    pop     ebx
    pop     ebp
    ret     0x10

; ---------------------------------------------------------------- 0x5f4fcf
; Quit Program closes the dialog before the command is posted, because the
; game tears the window down under it. Out of line only because the dialog
; procedure ran to the end of its cave.
quit:
    cmp     ecx, CMD_QUIT
    jne     .credits
    db      0x89, 0xcb                  ; mov ebx, ecx
    push    0
    push    dword [ebp + 8]
    call    [ENDDIALOG]
    db      0x89, 0xd9                  ; mov ecx, ebx
.fwd:
    jmp     dlgproc.post

; Credits is the one button with no menu item behind it, so it is handled
; here rather than posted. 0x1f is the state that sets the ending up and
; steps to the credits itself; it only means that during a match, so
; pressing this anywhere else does nothing.
.credits:
    cmp     ecx, CMD_CREDITS
    jne     .fwd
    cmp     dword [MODE], 4
    jne     .done
    push    0x1f                        ; two bytes shorter than the mov, and
    pop     dword [SUBMODE]             ; the cave has exactly two to spare
.done:
    jmp     dlgproc.handled

    times   BOXLEN - ($ - $$) db 0
