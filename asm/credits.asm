bits 32
org 0x0063d6d0          ; a run of zeros in .rdata; 156 bytes of it, and
                        ; nothing in the file points anywhere near it
; Makes the ending credits skippable, which they are not in the stock game.
;
; The credits are sub-state 0x20, whose handler at 0x59081f is a phase
; machine on PHASE: 0 and 1 are the ending cutscene and the mission complete
; screen, 2 is the roll itself, and anything else falls through to the tail
; at 0x5908f2, which stops the music and moves to the name entry. So a skip
; is one write - put the phase past 2 and the game ends the sequence its own
; way on the next frame. Nothing here duplicates that teardown.
;
; The input is not the one the game over and ranking screens test. Those
; read the press edges at 0x1ed5ec4, built at 0x56207a, which does not run
; in this state: the word sits at zero for the whole sequence. The key
; buffer is live throughout, so A is read from the slot the XInput tick
; writes before the keyboard handler runs - the same slot Space lands in, so
; a keyboard player gets the skip without the gamepad patch.
;
; The slot is a level and not an edge, so it is tracked here. PREV is
; updated from phase 0 onwards, before the phase gate, so A still held from
; the last round does not skip the roll it arrives on: only a press that
; starts during the credits does.
;
; Runs in place of the `mov dword [0x1ae1c1c], 0` that opens the handler,
; which is why that write is repeated below.

ACCEPT      equ 0x00bf0481      ; 1P's key buffer slot for A and Space
CAMERA      equ 0x00bf0457      ; and the one Select writes, which is what
                                ; skips the win and lose screens
PHASE       equ 0x01ad0964      ; where the credits sequence is up to
FLAG        equ 0x01ae1c1c      ; the displaced write
PREV        equ 0x006c3d48      ; last frame's slot, in .data: a byte
                                ; inside a 36 KB run of zeros that nothing
                                ; in the file points at

ROLL        equ 2               ; the phase the credits themselves are

skip:
    mov     al, [ACCEPT]        ; 0x80 while held, 0 otherwise
    or      al, [CAMERA]        ; either button, as on the screens before
    mov     dl, [PREV]
    mov     [PREV], al          ; tracked in every phase, not just the roll
    cmp     byte [PHASE], ROLL
    jne     .done
    test    al, al
    jz      .done               ; not held
    test    dl, dl
    jnz     .done               ; held last frame too, so not a fresh press
    mov     byte [PHASE], ROLL + 1
.done:
    mov     dword [FLAG], 0
    ret
