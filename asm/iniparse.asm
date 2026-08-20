bits 32
; Twelve binds out of a v_on.ini line: find the key, and if the line is
; there parse its 48 hex chars - lowbyte-highbyte pairs, the format both
; "Keyboard Assign" and "Simple Assign" use - into a 24-byte bind block.
; The block is left alone when the line is missing. In: eax = key string,
; edi = block. Preserves edi; clobbers eax, ecx, edx, esi.

org 0x00601b0c          ; a run of zeros in .rdata

FINDLINE    equ 0x005b1871      ; (key) -> value text, 0 if absent

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
