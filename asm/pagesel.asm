bits 32
; The preselect half of asm/pagesec.asm: on the gamepad page the letter
; and digit matching is skipped and a list hit is the combo index itself.
; The tail is unrelated lodging: the deadzone digits formatter, which
; asm/iniall.asm's cave had no room for.

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

; The deadzone seed: percent in cl, player in ebx, into that player's
; threshold, digit pair (tens first) and the percent itself, kept in the
; digit pair's spare fourth byte so a rejected F11 entry can be re-seeded
; to what is actually in force. asm/iniall.asm and the dialog call this
; at the address the times pins; the digits' third byte stays the
; loader's zero, so the text is terminated. Clobbers eax only.
DZTHR1      equ 0x0365cb8c      ; see asm/padxinput.asm
DZSTR1      equ 0x0365cb94

    times   (0x00601c08 - 0x00601bd4) - ($ - $$) db 0
dzseed:
    imul    eax, ecx, 327       ; 327 is 32767/100 to 0.06%
    mov     [ebx*4 + DZTHR1], eax
    mov     [ebx*4 + DZSTR1 + 3], cl
    mov     al, cl
    aam                         ; ah tens, al ones
    add     ax, 0x3030
    xchg    al, ah
    mov     [ebx*4 + DZSTR1], ax
    ret
