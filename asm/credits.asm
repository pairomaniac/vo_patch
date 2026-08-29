bits 32
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
; The slot is a level and not an edge, so it is tracked here. A press that
; starts during the roll begins a count, and the phase is written once that
; count reaches HOLD - about a second, since the sub-state runs once a tick
; whatever Motion is set to. Releasing zeroes it. A button already held when
; the roll opens never starts a count at all, which is what stops the press
; that skipped the win screens from carrying through.
;
; Runs in place of the `mov dword [0x1ae1c1c], 0` that opens the handler,
; which is why that write is repeated below.

extern ACCEPT1                  ; 1P's key buffer slot for A and Space
extern CAMERA1                  ; and the one Select writes, which is what
                                ; skips the win and lose screens
extern PHASE                    ; where the credits sequence is up to
extern FLAG                     ; the displaced write
extern PREV                     ; last frame's slot, shared with nameentry.asm
extern HELD                     ; and how long this press has lasted

ROLL        equ 2               ; the phase the credits themselves are
HOLD        equ 60              ; ticks to hold before it counts

skip:
    mov     al, [ACCEPT1]        ; 0x80 while held, 0 otherwise
    or      al, [CAMERA1]        ; either button, as on the screens before
    mov     dl, [PREV]
    mov     [PREV], al          ; tracked in every phase, not just the roll
    cmp     byte [PHASE], ROLL
    jne     .clear
    test    al, al
    jz      .clear              ; not held, so start again
    test    dl, dl
    jz      .start              ; a fresh press: begin the count
    cmp     byte [HELD], 0
    je      .done               ; held since before the roll: never counts
    inc     byte [HELD]
    cmp     byte [HELD], HOLD
    jb      .done
    mov     byte [PHASE], ROLL + 1
    jmp     .done
.start:
    mov     byte [HELD], 1
    jmp     .done
.clear:
    mov     byte [HELD], 0
.done:
    mov     dword [FLAG], 0
    ret
