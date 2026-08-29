bits 32
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

extern MODE                     ; game state and sub-state, the pair the
extern SUBMODE                  ; tick already gates its bind slots on
WIN         equ 0x0c            ; the win and lose screens
REPLAY      equ 0x14            ; and the replay that follows a decided match
                                ; A round is 0x0a, where the camera key has
                                ; to stay the camera, so neither is listed.

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
