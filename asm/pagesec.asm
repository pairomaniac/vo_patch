bits 32
; The letter and digit sections of the shared bind page belong to the
; keyboard profile only: the gamepad page lists just its sixteen pad
; inputs. Combo indices are positional, so the sections are skipped
; consistently in the fill and the store here, and in the preselect in
; asm/pagesel.asm. All four forks lean on asm/bindlist.asm's device check.

extern DEVCUR                   ; ZF set when the pending device is Simple
extern DIGITLOOP, LISTLOOP      ; the fill's digit and list loops
extern STORESHIFT, STORELIST    ; the store's shift-down and list id paths
%include "frames.inc"      ; the caller's locals, by name; the offset
                            ; is the retail build's, and build.py finds
                            ; each use so a build can move it
                            ; FILLIDX: the fill loop counter
                            ; STOREIDX: and the store's combo index

; The fill's letter loop entry. Simple: the stock compare, letters then
; digits. Gamepad: straight to the list section, so its entries start at
; combo index zero.
fillsec:
    call    DEVCUR
    jne     .skip
    cmp     dword [ebp + FILLIDX], 0x1a
    jge     .letters_done
    ret
.letters_done:
    add     esp, 4
    jmp     DIGITLOOP          ; the digit loop
.skip:
    add     esp, 4
    jmp     LISTLOOP          ; the list loop

; The store's letter threshold. Gamepad: every selection is a list entry,
; index unshifted.
storesec:
    call    DEVCUR
    jne     .skip
    cmp     dword [ebp + STOREIDX], 0x1a
    jge     .not_letter
    ret
.not_letter:
    add     esp, 4
    jmp     STORESHIFT          ; shift down, try digits
.skip:
    add     esp, 4
    jmp     STORELIST          ; the list id store
