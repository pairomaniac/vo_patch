bits 32
; The preselect half of asm/pagesec.asm: on the gamepad page the letter
; and digit matching is skipped and a list hit is the combo index itself.
; The tail is unrelated lodging: the deadzone digits formatter, which
; asm/iniall.asm had no room for.

extern DEVCUR                   ; ZF set when the pending device is Simple
extern SELDIGITS, SELLIST       ; the preselect's digit and list loops
extern SELSET                   ; and where it sets the selection
%include "frames.inc"      ; the caller's locals, by name; the offset
                            ; is the retail build's, and build.py finds
                            ; each use so a build can move it
                            ; SELIDX: the preselect loop counter

; The preselect's letter loop entry.
selsec:
    call    DEVCUR
    jne     .skip
    cmp     dword [ebp + SELIDX], 0x1a
    jge     .letters_done
    ret
.letters_done:
    add     esp, 4
    jmp     SELDIGITS          ; the digit loop
.skip:
    add     esp, 4
    jmp     SELLIST          ; the list loop

; A list hit's combo index: past letters and digits on Simple's page,
; bare on the gamepad's.
selidx:
    call    DEVCUR
    jne     .flat
    add     dword [ebp + SELIDX], 0x24
.flat:
    add     esp, 4
    jmp     SELSET          ; set the selection

; The deadzone seed: percent in cl, player in ebx, into that player's
; threshold, digit pair (tens first) and the percent itself, kept in the
; digit pair's spare fourth byte so a rejected F11 entry can be re-seeded
; to what is actually in force. asm/iniall.asm and the dialog call this
; at the address the times pins; the digits' third byte stays the
; loader's zero, so the text is terminated. Clobbers eax only.
extern DZTHR1                   ; see asm/padxinput.asm
extern DZSTR1

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
