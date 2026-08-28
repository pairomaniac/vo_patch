bits 32
                        ; on the next four-byte boundary. 156 bytes from
                        ; 0x63dda0, so 65 are still free past the end of
                        ; this; credits.asm has 57 left in the other one.
; Adds A to the initials screen, which stock takes only on the weapon
; triggers - LT for 1P at 0x4d6cc8, RT for 2P just after it. 1P's slot is
; the one read, so 2P still enters initials on RT alone.
;
; Runs in place of both those tests and returns whether a letter should be
; taken, so the triggers keep working and A joins them.
;
; A is not in the press edges at 0x1ed5ec4: those are built from the lever
; words, and A is a key rather than a lever. It arrives in the key buffer
; slot instead, which is a level, so the edge is worked out here.
;
; PREV is the byte credits.asm keeps, on purpose. Skipping the credits with
; A lands on this screen a frame or two later with A still held, and a
; shared PREV is what stops that press being taken as the first letter as
; well. Releasing A and pressing it again is a fresh press to both.

extern ACCEPT1                  ; 1P's key buffer slot for A and Space
extern CAMERA1                  ; and the one Select writes, which is what
                                ; skips the win and lose screens
extern EDGEA                    ; press edges, lever A byte: bit 0 is LT
extern EDGEB                    ; and lever B's, where 2P's RT is
extern PREV                     ; last frame's slot, shared with credits.asm

confirm:
    mov     al, [EDGEA]
    or      al, [EDGEB]
    and     al, 1               ; either trigger, as the stock tests did
    mov     ah, al
    mov     al, [ACCEPT1]        ; 0x80 while held, 0 otherwise
    or      al, [CAMERA1]        ; either button, as on the screens before
    mov     dl, [PREV]
    mov     [PREV], al          ; every frame, so the edge is not missed
    test    ah, ah
    jnz     .yes
    test    al, al
    jz      .no
    test    dl, dl
    jnz     .no                 ; held last frame too, so not a fresh press
.yes:
    mov     al, 1
    ret
.no:
    xor     al, al
    ret
