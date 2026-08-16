bits 32
org 0x0063dda0          ; a second run of zeros in .rdata, 156 bytes, with
                        ; nothing in the file pointing near it
; Lets A skip the win and lose screens, which stock only takes on the camera
; key.
;
; Those screens read the camera key, not the accept key: it arrives as bit 4
; of the input word 0x56207a builds, and A is not in it. Select works there
; because the tick writes the camera slot for Back. So A writes that slot
; too - but only on those screens, since everywhere else the camera key
; swings the camera, and A is jump by default.
;
; Called from the tick with ebx still holding the parameter block, from
; inside the branch that has already established A is held.

MODE        equ 0x01ae3594      ; game state and sub-state, the pair the
SUBMODE     equ 0x01ae3690      ; tick already gates its bind slots on
WIN         equ 0x0c            ; the win and lose screens. A round is 0x0a,
REPLAY      equ 0x14            ; where the camera key must stay the camera

camskip:
    cmp     dword [MODE], 4
    jne     .out
    cmp     dword [SUBMODE], WIN
    je      .press
    cmp     dword [SUBMODE], REPLAY
    jne     .out
.press:
    mov     edx, [ebx + 0x24]   ; the camera key slot, as Back uses
    mov     byte [edx], 0x80
.out:
    ret
