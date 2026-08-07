bits 32
org 0x0063e970          ; .rdata raw padding, past VirtualSize, after
BASE        equ 0x0063e970  ; kbpage.asm; 144 bytes to the end of the section
; Runs in place of the `call GetMessageA` at 0x5c5eac.
;
; The intro movie plays asynchronously and leaves 0x6bc598 at 1, which sends
; the message loop down a branch that blocks in GetMessageA. A pad press is
; not a message, so nothing wakes it and the pad cannot reach the window
; procedure - which is the only thing that would skip the movie, since the
; VK jump table at 0x5c6bf4 sends Return, Escape and Space to the skip at
; 0x5c6bce. The pump stub polls on the *other* branch and never runs here.
;
; So poll here instead, and wait in short sleeps rather than in the call.
; Once anything is queued the call is made for real, so the game sees exactly
; what GetMessageA would have given it, WM_QUIT included.
;
; The stack makes that free. On entry it is
;
;     [retaddr][lpMsg][hWnd][wMsgFilterMin][wMsgFilterMax]
;
; which is what GetMessageA itself expects, so the tail jump below returns
; straight to 0x5c5eb2 and lets GetMessageA do the stdcall cleanup.

LOADLIB     equ 0x0365d504      ; LoadLibraryA
GETPROC     equ 0x0365d508      ; GetProcAddress
GETMSG      equ 0x0365d58c      ; GetMessageA, the call this replaced
PEEKMSG     equ 0x0365d590      ; PeekMessageA

POLLPADS    equ 0x006080a4      ; padxinput.asm, pinned there for this

; This blob lands in .rdata, which the patch marks executable but not
; writable, so the resolved pointer cannot be cached here. It goes in the
; four bytes between PSTATE and PREV in the scratch the routine already owns.
SLEEPFN     equ 0x0365cb80      ; resolved Sleep: 0 not yet, 1 failed

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

; This lives in the raw padding past .rdata's VirtualSize, not in a run of
; zeros inside it. The long zero runs at 0x5f80e0 and 0x623d98 look free and
; are not: both are read as data - 0x623d08 is a table of twenty-byte entries
; the code indexes into, and 0x623e40 is a qword the FPU loads - so zeros
; there mean NULL and 0.0. Nothing can reference this padding, because it is
; past the size the image declares.
;
; It ends at file offset 0x23de00. Growing past that runs into .data, where
; the site's expected bytes stop being zeros, so selftest.py catches it.
