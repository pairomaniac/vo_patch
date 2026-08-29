bits 32
; Twin-stick profile. No new logic: the XInput tick at 0x608159 is a
; bind -> condition -> lever-mask engine, and the arcade scheme is just a
; different set of binds and masks. Each of the twelve slots drives one
; lever direction or button instead of a named action, so the two thumbsticks
; land straight in the two lever words and the game derives walk, turn, jump
; and crouch from the pair, exactly as the cabinet did.

extern TICK                       ; the shared XInput tick
extern EXIT1P                     ; where the 1P profile switch resumes
extern EXIT2P                     ; and the 2P one
extern KBD1P                      ; stock keyboard handler, called by the tick
extern KBD2P
extern LEV1A                      ; 1P lever words, left then right
extern LEV1B
extern LEV2A
extern LEV2B
extern ACCEPT1                    ; key buffer slots the tick pokes for A
extern ACCEPT2
extern CAMERA1                    ; and for Back
extern CAMERA2
extern SCR1                       ; scratch the tick keeps per player
extern SCR2

stub1p:
    push    block1
    call    TICK
    add     esp, 4
    jmp     EXIT1P

stub2p:
    push    block2
    call    TICK
    add     esp, 4
    jmp     EXIT2P

; One bind per slot, stride 2, matching the mask rows below. The codes are
; the condition table's, 0xe0 + index, the same ones the bound profile uses.
binds:
    db 0xe8, 0, 0xe9, 0, 0xea, 0, 0xeb, 0      ; LS up down left right
    db 0xec, 0, 0xed, 0, 0xee, 0, 0xef, 0      ; RS up down left right
    db 0xe6, 0, 0xe7, 0                        ; LT, RT   - the triggers
    db 0xe4, 0, 0xe5, 0                        ; LB, RB   - the turbo buttons

; Lever bits, active low: 0x20 up, 0x10 down, 0x80 left, 0x40 right,
; 0x01 trigger, 0x02 turbo. Taken from the game's own tables at 0x653690.
maska:
    db 0x20, 0x10, 0x80, 0x40                  ; left stick drives lever A
    db 0x00, 0x00, 0x00, 0x00
    db 0x01, 0x00, 0x02, 0x00
maskb:
    db 0x00, 0x00, 0x00, 0x00
    db 0x20, 0x10, 0x80, 0x40                  ; right stick drives lever B
    db 0x00, 0x01, 0x00, 0x02

block1:
    dd 0, binds, LEV1A, LEV1B, maska, maskb, ACCEPT1, SCR1, KBD1P, CAMERA1
block2:
    dd 1, binds, LEV2A, LEV2B, maska, maskb, ACCEPT2, SCR2, KBD2P, CAMERA2
