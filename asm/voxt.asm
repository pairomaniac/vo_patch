bits 32
; The F11 dialog's long tails, riding at the end of the .voxt section after
; the template: the close-time deadzone read and save, and the Quit case.
; The dialog procedure in asm/debugbox.asm stays a dispatcher and calls here
; through a relative placeholder that vo_patch.py fills once the section's
; address exists.
;
; Position independent: the section lands wherever the headers put it, so
; every call to fixed code goes through a register and there is no org.
; In: ecx = the command id (IDCANCEL or CMD_QUIT), edx = the dialog hwnd.
; Out: eax = 0 for handled, 1 to post - with ecx the command to post.
; Runs inside the dialog procedure's frame, which saved ebx, esi and edi.

%include "dialogs.inc"      ; ID_DZ1, ID_DZDEF, CMD_QUIT, IDCANCEL

extern SENDMSG                  ; SendMessageA
extern GETDLGITEM               ; GetDlgItem
extern ENDDIALOG                ; EndDialog
extern DZSEED                   ; asm/pagesel.asm's tail: (cl, ebx)
extern DZSAVE                   ; asm/iniparse.asm's tail: the ini lines
extern DZSTR1                   ; the digit pairs; see asm/padxinput.asm
WM_GETTEXT  equ 0x000d
WM_SETTEXT  equ 0x000c

annex:
    mov     ebx, edx            ; the hwnd; stdcall keeps ebx, not edx
    cmp     ecx, CMD_QUIT
    je      .quit
    cmp     ecx, ID_DZDEF
    je      .defs

    ; Closing: read both boxes back. Digits only (ES_NUMBER) and the length
    ; asked for stops at two of them, so the parse is two subtractions.
    ; 5 to 95 goes through the seed; anything else re-seeds the percent
    ; already in force, so an entry that did not take neither lingers nor
    ; blanks the box - it snaps back to the truth.
    push    1                   ; 2P first; the order is free
    pop     edi
.rd:
    lea     eax, [edi + ID_DZ1]
    push    eax
    push    ebx
    call    [GETDLGITEM]
    lea     esi, [edi*4 + DZSTR1]
    push    esi
    push    3                   ; two digits and the terminator
    push    WM_GETTEXT
    push    eax
    call    [SENDMSG]
    movzx   eax, byte [esi]
    sub     eax, '0'
    cmp     eax, 9
    ja      .force              ; empty or not a digit
    movzx   edx, byte [esi + 1]
    sub     edx, '0'
    cmp     edx, 9
    ja      .got                ; one digit
    imul    eax, eax, 10
    add     eax, edx
.got:
    lea     edx, [eax - 5]
    cmp     edx, 90
    jbe     .seed
.force:
    movzx   eax, byte [esi + 3] ; rejected: the percent in force
.seed:
    mov     edx, DZSEED         ; its first byte says whether the gamepad
    cmp     byte [edx], 0       ; patch is in; zero is the stock run and
    je      .next               ; there is nothing to call
    push    ebx
    mov     ecx, eax
    mov     ebx, edi            ; the player, for the seed
    call    edx
    pop     ebx
.next:
    dec     edi
    jns     .rd

    mov     eax, DZSAVE         ; same test, same reason
    cmp     byte [eax], 0
    je      .close
    call    eax
.close:
    push    0
    push    ebx
    call    [ENDDIALOG]
    xor     eax, eax            ; handled
    ret

    ; Defaults: both players back to 40, boxes refreshed, dialog stays
    ; open - the value is live at once and the ini lines follow on close.
    ; Without the gamepad patch there is nothing to seed and the empty
    ; boxes stay empty.
.defs:
    mov     edx, DZSEED
    cmp     byte [edx], 0
    je      .done
    push    1
    pop     edi
.d:
    push    ebx
    push    40
    pop     ecx
    mov     ebx, edi            ; the player, for the seed
    call    edx
    pop     ebx
    dec     edi
    jns     .d
    push    1
    pop     edi
.t:
    lea     eax, [edi + ID_DZ1]
    push    eax
    push    ebx
    call    [GETDLGITEM]
    lea     edx, [edi*4 + DZSTR1]
    push    edx
    push    0
    push    WM_SETTEXT
    push    eax
    call    [SENDMSG]
    dec     edi
    jns     .t
.done:
    xor     eax, eax            ; handled
    ret

    ; Quit closes the dialog before the command is posted, because the
    ; game tears the window down under it.
.quit:
    push    0
    push    ebx
    call    [ENDDIALOG]
    push    CMD_QUIT
    pop     ecx
    push    1                   ; post it
    pop     eax
    ret
