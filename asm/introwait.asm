bits 32
; Runs in place of the `call GetMessageA` at 0x5c5eac.
;
; The intro movie plays asynchronously and leaves 0x6bc598 at 1, which sends
; the message loop down a branch that blocks in GetMessageA. A pad press is
; not a message, so nothing wakes it and the pad cannot reach the window
; procedure - which is the only thing that would skip the movie, since the
; VK jump table at 0x5c6bf4 sends Return, Escape and Space to the skip at
; 0x5c6bce. The pump stub polls on the *other* branch and never runs here.
;
; So poll here, and wait in short sleeps rather than in the call. Once
; anything is queued the call is made for real, so the game sees exactly what
; GetMessageA would have given it, WM_QUIT included.
;
; On entry the stack is
;
;     [retaddr][lpMsg][hWnd][wMsgFilterMin][wMsgFilterMax]
;
; which is what GetMessageA expects, so the tail jump below returns straight
; to 0x5c5eb2 and lets GetMessageA do the stdcall cleanup.

extern LOADLIB                  ; LoadLibraryA
extern GETPROC                  ; GetProcAddress
extern GETMSG                   ; GetMessageA, the call this replaced
extern PEEKMSG                  ; PeekMessageA

extern POLLPADS                 ; padxinput.asm, pinned there for this

; .rdata is executable here but not writable, so the resolved pointer cannot
; be cached in this blob. It goes in the four bytes between PSTATE and PREV,
; in the scratch the routine already owns.
extern SLEEPFN                  ; resolved Sleep: 0 not yet, 1 failed

PM_NOREMOVE equ 0
NAP         equ 8               ; ms; the movie is not interactive, so this
                                ; only has to beat human reaction time

introwait:
.loop:
    call    POLLPADS            ; preserves everything, resolves XInput itself

    push    PM_NOREMOVE         ; PeekMessageA(lpMsg, 0, 0, 0, PM_NOREMOVE)
    push    0
    push    0
    push    0
    push    dword [esp + 20]    ; lpMsg: four pushes and the return address
    call    [PEEKMSG]
    test    eax, eax
    jnz     .have

    call    nap
    test    eax, eax
    jnz     .loop               ; no Sleep to be had: block as the game did,
.have:                          ; so the worst case is what it does today
    jmp     [GETMSG]

; ---------------------------------------------------------------------------
; Sleep, resolved once. Returns non-zero if it actually slept. Nothing here
; is fatal: without it the hook degrades to the blocking call it replaced.
nap:
    mov     eax, [SLEEPFN]
    test    eax, eax
    jz      .resolve
    cmp     eax, 1
    je      .none
.sleep:
    push    NAP
    call    eax
    mov     eax, 1
    ret
.resolve:
    push    kern32
    call    [LOADLIB]
    test    eax, eax
    jz      .fail
    push    sleepnm
    push    eax
    call    [GETPROC]
    test    eax, eax
    jz      .fail
    mov     [SLEEPFN], eax
    jmp     .sleep
.fail:
    mov     dword [SLEEPFN], 1
.none:
    xor     eax, eax
    ret

kern32:     db 'kernel32.dll', 0
sleepnm:    db 'Sleep', 0
