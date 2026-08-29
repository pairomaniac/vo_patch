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
                                ; bpp) cdecl, 1 on success; its first nine
                                ; bytes jump here
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
extern HAVESURF                 ; the game's own "surfaces exist" flag: the
                                ; activation handler recreates only if set
extern ISICONIC                 ; IsIconic
extern HWND                     ; the game window
extern MOVEWINDOW               ; MoveWindow: the window follows the mode
extern DDRAW                    ; the IDirectDraw; its cooperative level is
                                ; set once at start-up, and a DirectDraw
                                ; that let it lapse on a switch away draws
                                ; the recreated surfaces into a plain window
extern PENDING                  ; scratch: 1 while a recreate is owed
extern RETADDR                  ; scratch: where a recreate returns to

; RECREATE's entry, every caller: the activation handler, the title
; state and two more window-procedure branches all release and create the
; surfaces through it. The caller's return address is swapped for `made`,
; so the result comes past here on the way back.
recreate:
    pop     eax
    mov     [RETADDR], eax
    push    made
    mov     eax, [DDRAW]
    test    eax, eax
    jz      .body
    push    0x11                ; DDSCL_EXCLUSIVE | DDSCL_FULLSCREEN, as the
    push    dword [HWND]        ; game set it
    push    eax
    mov     eax, [eax]
    call    [eax + 0x50]        ; IDirectDraw::SetCooperativeLevel, stdcall
.body:
    push    ebp                 ; the nine bytes the jump displaced
    mov     ebp, esp
    sub     esp, 0xe0
    jmp     RECREATE + 9
made:
    test    eax, eax
    jnz     .ok
    mov     dword [PENDING], 1
    mov     dword [INACTIVE], 1 ; the movie's exit does not pause the game,
                                ; so idle the loop until the surfaces are
                                ; back
    mov     dword [HAVESURF], 1 ; and let WM_ACTIVATEAPP try again: it
                                ; recreates only when this is set, and a
                                ; failed recreate leaves it clear
    jmp     [RETADDR]
.ok:
    call    dims                ; the window to the mode: a DirectDraw that
    push    1                   ; let the cooperative level lapse shrank it
    push    edx
    push    eax
    push    0
    push    0
    push    dword [HWND]
    call    [MOVEWINDOW]        ; stdcall
    mov     eax, 1
    jmp     [RETADDR]

; The mode the handler would pick: eax = width, edx = height.
dims:
    test    byte [FSFLAGS], 4
    jz      .full
    cmp     dword [FSMODE], 0
    je      .full
    mov     eax, 0x140
    mov     edx, 0xf0
    ret
.full:
    mov     eax, 0x280
    mov     edx, 0x1e0
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

; The loop's pass while inactive: retry, unless the window is minimised.
idle:
    call    IDLE
    cmp     dword [PENDING], 0
    je      .out
    push    dword [HWND]
    call    [ISICONIC]
    test    eax, eax
    jnz     .out
    call    dims
    push    0x10
    push    edx
    push    eax
    call    RECREATE
    add     esp, 12
    test    eax, eax
    jz      .out
    mov     dword [PENDING], 0
    call    GRESUME
.out:
    mov     eax, 1              ; what IDLE returns
    ret
