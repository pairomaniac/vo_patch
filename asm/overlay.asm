bits 32
                        ; a live address, so 168 bytes are free from here
; Draws HOLD TO SKIP over the credits while the button is down.
;
; The credits roll scrolls the tilemap, so anything printed through the tile
; font climbs the screen with it. This draws through GDI instead, which
; takes screen pixels and stays put. 0x5c991c paints onto the back buffer
; when it is called, though, so it has to run after the frame is drawn: the
; hook is the call at 0x5c64e7, five bytes before the surface is flipped at
; 0x5c650d. That call is made here first, with its argument untouched.
;
; The only gate is HELD, the byte credits.asm counts the hold in. It is
; zeroed whenever the roll is not running or the button is not down, so a
; press anywhere else in the game cannot put text on the screen.

extern ORIG                     ; the call this one is made in place of
extern DRAW                     ; (text, x, y, colour, flag), cdecl
extern MODE                     ; 4 while a match is running
extern SUBMODE                  ; 0x20 is the ending sequence
extern PHASE                    ; 2 is the roll itself
extern HELD                     ; credits.asm's hold count

extern PRIMARY                  ; the surface DRAW paints on, and the one
extern BACK                     ; that is about to be flipped over it

extern WIDE                     ; the two the pause text halves its own
extern HALF                     ; coordinates on, at 0x5c9a98

X           equ 320             ; of 640 by 480, halved in low resolution
Y           equ 440
COLOUR      equ 0x0000ff00

overlay:
    push    dword [esp + 4]     ; the displaced call, same argument
    call    ORIG
    add     esp, 4

    ; The state is checked before the hold count, not instead of it. HELD
    ; lives in a run of zeros in .data, and something else in the game
    ; writes through it: on the title screen and in a match it reads
    ; nonzero on its own, which put the text on screen everywhere. Inside
    ; the roll credits.asm owns it and it counts properly.
    cmp     dword [MODE], 4
    jne     .out
    cmp     dword [SUBMODE], 0x20
    jne     .out
    cmp     byte [PHASE], 2
    jne     .out
    cmp     byte [HELD], 0
    je      .out
    mov     eax, X
    mov     edx, Y
    test    byte [WIDE], 4
    jz      .full
    cmp     dword [HALF], 0
    je      .full
    sar     eax, 1
    sar     edx, 1
.full:
    ; DRAW paints on the primary surface, which is what the pause screen
    ; wants: it is not flipping, so the text stays up. Here the frame is
    ; about to flip the back buffer over it, so point it at that instead
    ; and put it back after. A null back buffer costs the text and nothing
    ; else, since DRAW returns early on a null surface.
    mov     ecx, [PRIMARY]
    push    ecx
    mov     ecx, [BACK]
    mov     [PRIMARY], ecx

    push    1
    push    COLOUR
    push    edx
    push    eax
    push    prompt
    call    DRAW
    add     esp, 0x14

    pop     ecx
    mov     [PRIMARY], ecx
.out:
    ret

prompt:
    db      'HOLD TO SKIP', 0
