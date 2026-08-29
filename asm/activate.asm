bits 32
; Coming back to the window. The game's WM_ACTIVATEAPP handler releases its
; DirectDraw surfaces and creates them again, then resumes the frame loop -
; without looking at whether the create succeeded. On a DirectDraw that
; cannot give exclusive mode back at that instant, the game resumes with
; no surfaces and the next frame dereferences nothing. Three wraps: the
; recreate remembers its arguments and its result, the resume only resumes
; on success, and the idle pass the loop makes while inactive retries the
; recreate until it takes.

extern RECREATE                 ; release and create the surfaces; (w, h,
                                ; bpp) cdecl, 1 on success
extern GRESUME                  ; the built-in dialogs' resume; clears the
                                ; inactive flag
extern IDLE                     ; what the loop does per pass while inactive
extern PENDING                  ; scratch: 1 while a recreate is owed, then
                                ; the three arguments to make it with
extern INACTIVE                 ; the flag GPAUSE sets and GRESUME clears;
                                ; the loop idles while it is set

recreate:
    mov     eax, [esp + 4]
    mov     [PENDING + 4], eax
    mov     eax, [esp + 8]
    mov     [PENDING + 8], eax
    mov     eax, [esp + 12]
    mov     [PENDING + 12], eax
    push    dword [esp + 12]
    push    dword [esp + 12]
    push    dword [esp + 12]
    call    RECREATE
    add     esp, 12
    xor     eax, 1              ; 1 on success -> 0 owed; 0 -> 1 owed
    mov     [PENDING], eax
    test    eax, eax
    jz      .done
    mov     dword [INACTIVE], 1 ; not every deactivation pauses the game -
                                ; the intro movie's does not - so make the
                                ; loop idle until the surfaces are back
.done:
    ret

resume:
    cmp     dword [PENDING], 0
    jne     .stay               ; no surfaces: stay inactive, the idle pass
    jmp     GRESUME             ; will resume once it has them
.stay:
    ret

idle:
    call    IDLE
    cmp     dword [PENDING], 0
    je      .out
    push    dword [PENDING + 12]
    push    dword [PENDING + 8]
    push    dword [PENDING + 4]
    call    RECREATE
    add     esp, 12
    test    eax, eax
    jz      .out
    mov     dword [PENDING], 0
    call    GRESUME
.out:
    mov     eax, 1              ; what IDLE returns
    ret
