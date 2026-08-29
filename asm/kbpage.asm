bits 32
; Two fixes to the keyboard bind page.

extern CURPLAYER                ; the side being configured, 0 or 1
extern DEVICES                  ; 1P's profile, 0 being the keyboard
extern CROSSCHECK               ; look at 1P's key map
extern KBACCEPT                 ; take the key
extern DEFAULTS                 ; fill a player's binds from the shipped set
extern RESUME                   ; what the Default button does next

; ----------------------------------------------------------------
; The page refuses a key for 2P if 1P already has it. Sensible when both are
; on the keyboard, needlessly strict when 1P is on a pad: 1P's binds are still
; in the map but nothing reads them. Replaces the "am I 2P" test.
;
; This only decides what may be *entered*. It does not stop 1P returning to
; the keyboard afterwards and both sides ending up on the same keys, which
; the game then drives both mechs from. Checking that would mean validating
; on the device switch too, which is a separate job.
dupkey:
    cmp     dword [CURPLAYER], 1
    jne     .accept             ; configuring 1P: never cross checked
    mov     eax, [DEVICES]
    dec     eax
    cmp     eax, 1
    jbe     .accept             ; 1P is on a pad, so its keys are dormant
    jmp     CROSSCHECK          ; devices 0 and 3 are both keyboards
.accept:
    jmp     KBACCEPT

; ----------------------------------------------------------------
; Default passed a hardcoded player 0, so pressing it on the 2P side reset
; 1P's binds and left 2P's alone. The gamepad and joystick pages both pass
; ds:0xbf6bac here; this one is the odd one out.
default_button:
    push    1                   ; also refresh the live table, as before
    push    dword [CURPLAYER]
    call    DEFAULTS
    add     esp, 8
    jmp     RESUME
