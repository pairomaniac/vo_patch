bits 32
; Twelve binds out of a v_on.ini line: find the key, and if the line is
; there parse its 48 hex chars - lowbyte-highbyte pairs, the format both
; "Keyboard Assign" and "Simple Assign" use - into a 24-byte bind block.
; The block is left alone when the line is missing. In: eax = key string,
; edi = block. Preserves edi; clobbers eax, ecx, edx, esi.
;
; The tail is the deadzone write-back: both players' digit pairs out to
; their "1P Deadzone" and "2P Deadzone" lines, through the game's own
; line writer. The F11 dialog calls it when it closes, behind a byte
; test that proves this blob is in place at all.


%include "padtables.inc"    ; DZKEYS

extern FINDLINE                 ; (key) -> value text, 0 if absent
extern WRITELINE                ; (key, value): one v_on.ini line
extern DZSTR1                   ; the digit pairs; see asm/padxinput.asm

parse12:
    push    eax
    call    FINDLINE
    add     esp, 4
    test    eax, eax
    je      .done
    mov     esi, eax
    xor     ecx, ecx
.byte:
    call    nibble
    shl     al, 4
    mov     dl, al
    call    nibble
    or      al, dl
    mov     [edi + ecx], al
    inc     ecx
    cmp     ecx, 0x18
    jl      .byte
.done:
    ret

nibble:                         ; hex char at [esi++] -> al
    movzx   eax, byte [esi]
    inc     esi
    sub     al, '0'
    cmp     al, 9
    jbe     .ok
    sub     al, 'a' - '0' - 10
.ok:
    ret

    times   (0x00601b48 - 0x00601b0c) - ($ - $$) db 0
dzsave:
    push    1                   ; 2P first; the order is free
    pop     esi
.s:
    lea     eax, [esi*4 + DZSTR1]
    push    eax
    imul    eax, esi, 12        ; the two key strings, 12 bytes apiece
    add     eax, DZKEYS
    push    eax
    call    WRITELINE
    add     esp, 8
    dec     esi
    jns     .s
    ret
