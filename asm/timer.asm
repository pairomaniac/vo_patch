bits 32
; The entry point the frame rate patch redirects to. It asks Windows for a
; 1 ms scheduler tick and then goes where the entry point used to go.
;
; The game's frame pacing sleeps in millisecond units against a 15.6 ms
; default tick, so without this the wait rounds up and the game runs at about
; 70 per cent speed.
;
; AddressOfEntryPoint at 0xa8 names this address, and nodisc chains it in
; turn, which is why that patch is applied last.

STRLEN      equ 62              ; code and both strings, up to debugbox.asm

extern ORIGENTRY                ; the entry point this replaces
extern LOADLIB                  ; LoadLibraryA
extern GETPROC                  ; GetProcAddress

start:
    push    winmm
    call    [LOADLIB]
    push    procname
    push    eax
    call    [GETPROC]
    test    eax, eax
    je      .done               ; no winmm: nothing to raise, carry on
    push    1                   ; timeBeginPeriod(1)
    call    eax
.done:
    jmp     ORIGENTRY

winmm:      db 'winmm.dll', 0
procname:   db 'timeBeginPeriod', 0

    times   STRLEN - ($ - $$) db 0
