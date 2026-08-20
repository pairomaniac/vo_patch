bits 32
; The letter and digit sections of the shared bind page belong to the
; keyboard profile only: the gamepad page lists just its sixteen pad
; inputs. Combo indices are positional, so the sections are skipped
; consistently in the fill and the store here, and in the preselect in
; asm/pagesel.asm. All four forks lean on asm/bindlist.asm's device check.

org 0x00601b70          ; a run of zeros in .rdata

DEVCUR      equ 0x005fd7e4      ; ZF set when the pending device is Simple

; The fill's letter loop entry. Simple: the stock compare, letters then
; digits. Gamepad: straight to the list section, so its entries start at
; combo index zero.
fillsec:
    call    DEVCUR
    jne     .skip
    cmp     dword [ebp - 8], 0x1a
    jge     .letters_done
    ret
.letters_done:
    add     esp, 4
    jmp     0x00497c70          ; the digit loop
.skip:
    add     esp, 4
    jmp     0x00497cb0          ; the list loop

; The store's letter threshold. Gamepad: every selection is a list entry,
; index unshifted.
storesec:
    call    DEVCUR
    jne     .skip
    cmp     dword [ebp - 0x14], 0x1a
    jge     .not_letter
    ret
.not_letter:
    add     esp, 4
    jmp     0x00497e74          ; shift down, try digits
.skip:
    add     esp, 4
    jmp     0x00497e99          ; the list id store
