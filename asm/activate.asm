bits 32
; Coming back to the window. The game's WM_ACTIVATEAPP handler releases its
; DirectDraw surfaces and creates them again, then resumes the frame loop -
; without looking at whether the create succeeded. On a DirectDraw that
; cannot give exclusive mode back at that instant the game resumes with no
; surfaces and the next frame dereferences nothing. Three pieces: the
; recreate idles the loop when it fails, no resume goes through while the
; back buffer is missing - GRESUME is hooked at its entry, so every caller
; is covered - and the idle pass the loop makes while inactive retries the
; recreate until it takes.

extern RECREATE                 ; release and create the surfaces; (w, h,
                                ; bpp) cdecl, 1 on success
extern GRESUME                  ; the resume: sound, cursor, setactive(0)
extern SETACTIVE                ; (pause) cdecl: 1 stops the loop, 0 lets it
                                ; run; its first five bytes jump here
extern IDLE                     ; what the loop does per pass while inactive
extern BACK                     ; the back buffer, null while there is none
extern INACTIVE                 ; the flag GPAUSE sets and GRESUME clears;
                                ; the loop idles while it is set
extern FSFLAGS                  ; bit 2: the low-resolution modes
extern FSMODE                   ; nonzero with it: 320x240 rather than
                                ; 640x480 - the handler's own choice
extern PENDING                  ; scratch: 1 while a recreate is owed

; The two recreate calls in the handler come through here.
recreate:
    push    dword [esp + 12]
    push    dword [esp + 12]
    push    dword [esp + 12]
    call    RECREATE
    add     esp, 12
    test    eax, eax
    jnz     .done
    mov     dword [PENDING], 1
    mov     dword [INACTIVE], 1 ; not every deactivation pauses the game -
                                ; the intro movie's does not - so idle the
                                ; loop until the surfaces are back
.done:
    ret

; setactive's entry. Letting the loop run with no back buffer is what
; crashes, so that one call is refused; pausing always goes through.
resume:
    cmp     dword [esp + 4], 0
    jne     .go                 ; setactive(1): pause, always
    cmp     dword [BACK], 0
    jne     .go
    mov     dword [PENDING], 1
    mov     dword [INACTIVE], 1
    ret
.go:
    push    ebp                 ; the five bytes the jump displaced
    mov     ebp, esp
    push    ebx
    push    esi
    jmp     SETACTIVE + 5

; The loop's pass while inactive.
idle:
    call    IDLE
    cmp     dword [PENDING], 0
    je      .out
    push    0x10
    test    byte [FSFLAGS], 4
    jz      .full
    cmp     dword [FSMODE], 0
    je      .full
    push    0xf0
    push    0x140
    jmp     .make
.full:
    push    0x1e0
    push    0x280
.make:
    call    RECREATE
    add     esp, 12
    test    eax, eax
    jz      .out
    mov     dword [PENDING], 0
    call    GRESUME
.out:
    mov     eax, 1              ; what IDLE returns
    ret
