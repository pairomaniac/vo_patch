bits 32
org 0x0366865c          ; .rsrc raw padding, past VirtualSize. The cave
                        ; starts four bytes into the section's padding: the
                        ; frame rate patch's F5 labels grew the last resource
                        ; into it. 420 bytes to the end of the section.

; Places the intro movie's window.
;
; The movie is not drawn through DirectDraw. mciavi opens it as a WS_CHILD of
; the main window and the game moves that window itself, from 0x54e817, to an
; offset it reads from two globals - each a hardcoded centre for one movie
; size in a 640x480 picture. Scaled up, the main window is the whole screen
; and the picture is drawn centred inside it, which the child window knows
; nothing about, so the movie sits in the corner at its original size.
;
; 0x54e817 does call GetClientRect on the parent, but cnc-ddraw hooks it and
; answers with the game's own 640x480, so the game cannot learn the real size
; that way. cnc-ddraw exports DDGetProcAddress for exactly this, so ask it for
; the real GetClientRect and go from there. Without cnc-ddraw the import is
; already the real thing and the result is what the game did before.
;
; Called from the middle of 0x54e817 with that routine's ebp, so the values it
; is about to pass to MoveWindow can be rewritten in place:
;
;     [frame+0x08]  parent hwnd
;     [frame+0x10]  movie width, read back as a word
;     [frame+0x14]  movie height
;     [frame-0x0c]  X, written here
;     [frame-0x10]  Y, written here
;
; mciavi does not follow the window, so a destination rect goes with it. The
; game never sends MCI_PUT, so this is the only one.

GETMODULE   equ 0x0365d4a0      ; GetModuleHandleA
GETPROC     equ 0x0365d508      ; GetProcAddress
GETCLIENT   equ 0x0365d5d4      ; GetClientRect, the hooked one
MOVEWINDOW  equ 0x0365d5e0      ; MoveWindow
MCISEND     equ 0x0365d648      ; mciSendCommandA

MOVIEHWND   equ 0x01ef88c8      ; the mciavi window, from MCI_ANIM_STATUS_HWND
MOVIEDEV    equ 0x01ef88f0      ; its device id
MOVIEX      equ 0x01ae5f34      ; the offsets the replaced code read
MOVIEY      equ 0x01ae5f38

MCI_PUT     equ 0x0842
PUT_DEST    equ 0x00050000      ; MCI_ANIM_RECT | MCI_ANIM_PUT_DESTINATION

F_HWND      equ 0x08            ; caller frame
F_W         equ 0x10
F_H         equ 0x14
F_X         equ -0x0c
F_Y         equ -0x10

R_LEFT      equ -0x10           ; our frame: RECT from GetClientRect
R_TOP       equ -0x0c
R_RIGHT     equ -0x08
R_BOTTOM    equ -0x04
P_PARMS     equ -0x24           ; MCI_ANIM_RECT_PARMS, 20 bytes
C_W          equ -0x28
C_H          equ -0x2c
M_W          equ -0x30
M_H          equ -0x34
N_W          equ -0x38
N_H          equ -0x3c

movie_place:
        push    ebp
        mov     ebp, esp
        sub     esp, 0x40
        push    ebx
        push    esi
        push    edi
        mov     edi, [ebp + 8]          ; caller's frame

        ; What the replaced code did, first, so every bail below leaves the
        ; game exactly as it was.
        mov     eax, [MOVIEX]
        mov     [edi + F_X], eax
        mov     eax, [MOVIEY]
        mov     [edi + F_Y], eax

        ; ddraw.dll's DDGetProcAddress hands back the unhooked user32.
        xor     esi, esi
        push    s_ddraw
        call    [GETMODULE]
        test    eax, eax
        jz      .resolved
        push    s_ddgpa
        push    eax
        call    [GETPROC]
        test    eax, eax
        jz      .resolved
        mov     ebx, eax
        push    s_user32
        call    [GETMODULE]
        test    eax, eax
        jz      .resolved
        push    s_getclient
        push    eax
        call    ebx
        mov     esi, eax

.resolved:
        test    esi, esi
        jnz     .measure
        mov     esi, [GETCLIENT]        ; no cnc-ddraw, or it is an old build

.measure:
        lea     eax, [ebp + R_LEFT]
        push    eax
        push    dword [edi + F_HWND]
        call    esi
        test    eax, eax
        jz      .done

        mov     eax, [ebp + R_RIGHT]
        sub     eax, [ebp + R_LEFT]
        mov     [ebp + C_W], eax
        test    eax, eax
        jle     .done
        mov     eax, [ebp + R_BOTTOM]
        sub     eax, [ebp + R_TOP]
        mov     [ebp + C_H], eax
        test    eax, eax
        jle     .done

        movsx   eax, word [edi + F_W]
        mov     [ebp + M_W], eax
        test    eax, eax
        jle     .done
        movsx   eax, word [edi + F_H]
        mov     [ebp + M_H], eax
        test    eax, eax
        jle     .done

        ; Biggest rectangle of the movie's own shape that fits. Filling the
        ; client area outright would stretch 16:10 across an ultrawide.
        mov     eax, [ebp + C_W]
        imul    dword [ebp + M_H]
        mov     ebx, eax
        mov     eax, [ebp + C_H]
        imul    dword [ebp + M_W]
        cmp     ebx, eax
        jg      .by_height

        mov     eax, [ebp + C_W]         ; width bound: nw = cw, nh = cw*mh/mw
        mov     [ebp + N_W], eax
        imul    dword [ebp + M_H]
        idiv    dword [ebp + M_W]
        mov     [ebp + N_H], eax
        jmp     .centre

.by_height:                             ; nh = ch, nw = ch*mw/mh
        mov     eax, [ebp + C_H]
        mov     [ebp + N_H], eax
        imul    dword [ebp + M_W]
        idiv    dword [ebp + M_H]
        mov     [ebp + N_W], eax

.centre:
        mov     eax, [ebp + C_W]
        sub     eax, [ebp + N_W]
        sar     eax, 1
        mov     [edi + F_X], eax
        mov     eax, [ebp + C_H]
        sub     eax, [ebp + N_H]
        sar     eax, 1
        mov     [edi + F_Y], eax

        ; The caller reads these back as words for its own MoveWindow, which
        ; runs after this and repeats the call below with the same values.
        mov     eax, [ebp + N_W]
        mov     [edi + F_W], eax
        mov     eax, [ebp + N_H]
        mov     [edi + F_H], eax

        ; Size the window here rather than leaving it to the caller, whose
        ; own MoveWindow runs after this and repeats the call with the same
        ; values. The destination rect below has to be set against a window
        ; that is already the right size, and mciavi resets it from WM_SIZE.
        push    1
        push    dword [ebp + N_H]
        push    dword [ebp + N_W]
        push    dword [edi + F_Y]
        push    dword [edi + F_X]
        push    dword [MOVIEHWND]
        call    [MOVEWINDOW]

        ; MCI rects are x, y, width, height rather than two corners.
        xor     eax, eax
        mov     [ebp + P_PARMS], eax
        mov     [ebp + P_PARMS + 4], eax
        mov     [ebp + P_PARMS + 8], eax
        mov     eax, [ebp + N_W]
        mov     [ebp + P_PARMS + 12], eax
        mov     eax, [ebp + N_H]
        mov     [ebp + P_PARMS + 16], eax
        lea     eax, [ebp + P_PARMS]
        push    eax
        push    PUT_DEST
        push    MCI_PUT
        push    dword [MOVIEDEV]
        ; Through a register: the CD audio patch counts six-byte indirect
        ; calls to this import and aborts on anything but 37.
        mov     eax, [MCISEND]
        call    eax

.done:
        pop     edi
        pop     esi
        pop     ebx
        leave
        ret

s_ddraw:        db 'ddraw.dll', 0
s_ddgpa:        db 'DDGetProcAddress', 0
s_user32:       db 'user32.dll', 0
s_getclient:    db 'GetClientRect', 0
