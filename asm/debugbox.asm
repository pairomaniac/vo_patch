bits 32
; The F11 Extras dialog: a window-procedure hook, the dialog procedure, and
; the Credits case, out of line.
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

BOXLEN      equ 388             ; a bound on the blob: the dialog procedure
                                ; has outgrown its room before, unnoticed

; USER32 and DLGBOXPROC, the two strings; CHECKS, one entry per check box;
; TEMPLATE, the dialog itself; and CMD_QUIT, IDCANCEL and ID_DZ, three of
; the control ids.
%include "dialogs.inc"

extern MODE                     ; game state; 4 is a match in progress
extern SUBMODE                  ; and its sub-state, 0x1f the ending
extern HWND                     ; the game's window
extern ORIGWNDPROC              ; the handler the hook falls through to

extern LOADLIB                  ; LoadLibraryA
extern GETPROC                  ; GetProcAddress
extern GETMODULE                ; GetModuleHandleA
extern SENDMSG                  ; SendMessageA
extern GETDLGITEM               ; GetDlgItem
extern CHECKDLGBTN              ; CheckDlgButton
extern POSTMSG                  ; PostMessageA

WM_KEYDOWN  equ 0x0100
WM_SETTEXT  equ 0x000c
WM_INITDLG  equ 0x0110
WM_COMMAND  equ 0x0111

; The deadzone quads in asm/padxinput.asm's .data scratch: thresholds then
; digit pairs, 1P then 2P, both strides 4 so the loops below index them.
; iniall.asm seeds all four at launch when the gamepad patch is in; without
; it the boxes show empty and their values land in scratch nothing reads,
; which is harmless - the addresses are free in the stock executable
; whatever is installed.
extern DZTHR1
extern DZSTR1

extern F11CHECKS                ; asm/f11pause.asm's tail: the check boxes
ANNEXREL    equ 0xEAEAEAEA      ; a placeholder: the rel32 to asm/voxt.asm's
                                ; annex at the end of the .voxt section,
                                ; whose address only exists at apply time -
                                ; vo_patch.py computes and fills it
VK_F11      equ 0x7a
extern F11WRAP                  ; asm/f11pause.asm
extern GAMEMODE                 ; 2 during a network match, when every F-key
                                ; command handler returns before doing
                                ; anything; so does this

; nasm assembles `mov r32, r32` and `xor r32, r32` as 89 and 31; the code
; this replaces used the 8b and 33 encodings. The `db` lines below keep the
; blob byte-identical to the hex it was reconstructed from.

; ----------------------------------------------------------------
; Window procedure hook. Everything except F11 goes on to the original.
hook:
    push    ebp
    db      0x8b, 0xec                  ; mov ebp, esp
    push    ebx
    cmp     dword [ebp + 0x0c], WM_KEYDOWN
    jne     .pass
    cmp     dword [ebp + 0x10], strict dword VK_F11
    jne     .pass
    cmp     dword [GAMEMODE], 2         ; a network match: as F5 to F8
    je      .pass

    ; DialogBoxIndirectParamA is not in the import table, so it is fetched.
    push    USER32
    call    [LOADLIB]
    push    DLGBOXPROC
    push    eax
    call    [GETPROC]
    test    eax, eax
    je      .done
    db      0x8b, 0xd8                  ; mov ebx, eax
    call    F11WRAP                     ; asm/f11pause.asm: pause the game
.done:                                  ; and music, run the dialog, resume
    db      0x33, 0xc0                  ; xor eax, eax
    pop     ebx
    pop     ebp
    ret     0x10
.pass:
    pop     ebx
    pop     ebp
    jmp     ORIGWNDPROC

    times   0x5c - ($ - $$) db 0

; ----------------------------------------------------------------
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

    ; The check boxes are ticked in asm/f11pause.asm's tail, where the loop
    ; kept there for room.
    push    dword [ebp + 8]
    call    F11CHECKS

    ; Show both deadzone percents - the digit pairs iniall.asm keeps beside
    ; the thresholds, empty when the gamepad patch is out.
    xor     edi, edi
.dz:
    lea     eax, [edi + ID_DZ1]
    push    eax
    push    dword [ebp + 8]
    call    [GETDLGITEM]
    lea     edx, [edi*4 + DZSTR1]
    push    edx
    push    0
    push    WM_SETTEXT
    push    eax
    call    [SENDMSG]
    inc     edi
    cmp     edi, 2
    jb      .dz
    jmp     .handled

.command:
    cmp     eax, WM_COMMAND
    jne     .ignore
    movzx   ecx, word [ebp + 0x10]      ; control id, the low word
    lea     edx, [ecx - ID_DZ1]         ; the edits' notifications are the
    cmp     edx, 1                      ; dialog's own; do not post them
    jbe     .handled
    cmp     ecx, IDCANCEL
    je      .tail
    cmp     ecx, ID_DZDEF
    je      .tail
    cmp     ecx, CMD_QUIT
    jne     credits
.tail:
    ; Close and Quit are the long paths - the deadzone read, the ini save,
    ; the teardown order - and live in asm/voxt.asm at the end of the
    ; template's section, for room. The annex says what to do with its
    ; answer.
    mov     edx, dword [ebp + 8]
    db      0xe8                        ; call rel32; nasm would subtract the
    dd      ANNEXREL                    ; site from a plain call, and the
                                        ; placeholder has to survive verbatim
    test    eax, eax
    je      .handled                    ; else fall through: ecx is the
                                        ; command to post
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

; Credits is the one button with no menu item behind it, so it is handled
; here rather than posted; everything else is forwarded as the menu item
; it replaces. Out of line from when the dialog procedure ran to the end
; of the blob. 0x1f is the state that sets the ending up and steps to the
; credits itself; it only means that during a match, so pressing this
; anywhere else does nothing. The Quit case that lived above this is
; gone with its button - the window X quits the game the same way.
credits:
    cmp     ecx, CMD_CREDITS
    jne     .fwd
    cmp     dword [MODE], 4
    jne     .done
    push    0x1f                        ; two bytes shorter than the mov
    pop     dword [SUBMODE]
.done:
    jmp     dlgproc.handled
.fwd:
    jmp     dlgproc.post

%if ($ - $$) > BOXLEN
%error the dialog procedure has grown past BOXLEN
%endif
