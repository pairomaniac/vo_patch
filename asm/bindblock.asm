bits 32
; Keyboard (Simple) keeps its binds in the block at +0x38 - the hidden
; 2 Joysticks profile's. It is saved and loaded through its own v_on.ini
; line (asm/inisave.asm, asm/iniload.asm). Every route into the shared
; page's block picks it here or in asm/blockcur.asm: four sites hand
; blockaddr a plain player offset, the store hands blockcur a fused
; player-and-slot index, the preselect reads a byte straight out of the
; block, and the Default copier's source follows the same pick. The device
; is the structure's own +0x00 dword - the one the F7 screen edits before
; OK commits it to 0x3651540 - because the page and the live-table apply
; both run against the pending pick, and it stays honest when the Default
; copier runs at startup for both sides in turn.


SIMPLE      equ 3
extern GAMEPADDEF               ; the gamepad's shipped binds, 1P then 2P
extern BLOCKS                   ; per player: this + player * 0x70; the
                                ; pending device sits at +0x00
GAMEPAD_OFF equ 0x08
SIMPLE_OFF  equ 0x38

%include "padtables.inc"    ; SIMPLEDEF

; ---------------------------------------------------------------- 0x5ff24c
; Replaces `add eax, BASE / add eax, 8`. In: eax = player * 0x70.
blockaddr:
    cmp     dword [eax + BLOCKS], SIMPLE
    je      .simple
    add     eax, BLOCKS + GAMEPAD_OFF
    ret
.simple:
    add     eax, BLOCKS + SIMPLE_OFF
    ret

    times   0x18 - ($ - blockaddr) db 0x90

; ---------------------------------------------------------------- 0x5ff264
; The Default button's copier takes its shipped set from a table picked
; here: the gamepad's at 0x66d600, or SIMPLEDEF. In: eax = player.
defsource:                      ; edx is free at the site
    imul    edx, eax, 0x70
    cmp     dword [edx + BLOCKS], SIMPLE
    imul    eax, eax, 0x18
    je      .simple
    add     eax, GAMEPADDEF
    ret
.simple:
    add     eax, SIMPLEDEF
    ret

    times   0x34 - ($ - blockaddr) db 0x90

; ---------------------------------------------------------------- 0x5ff280
; The preselect's `mov al, [ecx + eax*2 + BASE+8]`, the same read with the
; registers the other way around. In: ecx = player * 0x70, eax = slot.
preselbind:
    cmp     dword [ecx + BLOCKS], SIMPLE
    je      .simple
    mov     al, [ecx + eax*2 + BLOCKS + GAMEPAD_OFF]
    ret
.simple:
    mov     al, [ecx + eax*2 + BLOCKS + SIMPLE_OFF]
    ret

    times   0x50 - ($ - blockaddr) db 0x90

; ---------------------------------------------------------------- 0x5ff29c
; Low four bits of al as an ascii hex digit at [edi], advancing it.
; asm/inisave.asm calls this; it lives here because its own cave is full.
hexchar:
    and     al, 0x0f
    cmp     al, 10
    jb      .digit
    add     al, 'a' - 10 - '0'
.digit:
    add     al, '0'
    mov     [edi], al
    inc     edi
    ret
