bits 32
; The preselect half of asm/pagesec.asm: on the gamepad page the letter
; and digit matching is skipped and a list hit is the combo index itself.

org 0x00601bd4          ; a run of zeros in .rdata

DEVCUR      equ 0x005fd7e4      ; ZF set when the pending device is Simple

; The preselect's letter loop entry.
selsec:
    call    DEVCUR
    jne     .skip
    cmp     dword [ebp - 0xc], 0x1a
    jge     .letters_done
    ret
.letters_done:
    add     esp, 4
    jmp     0x00498059          ; the digit loop
.skip:
    add     esp, 4
    jmp     0x0049809d          ; the list loop

; A list hit's combo index: past letters and digits on Simple's page,
; bare on the gamepad's.
selidx:
    call    DEVCUR
    jne     .flat
    add     dword [ebp - 0xc], 0x24
.flat:
    add     esp, 4
    jmp     0x004980d9          ; set the selection
