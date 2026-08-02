bits 32
; Two fixes to the keyboard bind page. The slots are a fixed size so the
; second cannot move when the first is edited: its site names an address, and
; nothing downstream would catch a drift.

org 0x0063e938          ; .rdata past VirtualSize, after the F11 dialog

CURPLAYER   equ 0x00bf6bac      ; the side being configured, 0 or 1
DEVICE1P    equ 0x03651540      ; 1P's profile, 0 being the keyboard
CROSSCHECK  equ 0x0049776e      ; look at 1P's key map
ACCEPT      equ 0x004977c6      ; take the key
DEFAULTS    equ 0x0049790f      ; fill a player's binds from the shipped set
RESUME      equ 0x0049789a      ; what the Default button does next

; ---------------------------------------------------------------- 0x63e938
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
    cmp     dword [DEVICE1P], 0
    jne     .accept             ; 1P is on a pad, so its keys are dormant
    jmp     CROSSCHECK
.accept:
    jmp     ACCEPT

    times   32 - ($ - dupkey) db 0x90

; ---------------------------------------------------------------- 0x63e958
; Default passed a hardcoded player 0, so pressing it on the 2P side reset
; 1P's binds and left 2P's alone. The gamepad and joystick pages both pass
; ds:0xbf6bac here; this one is the odd one out.
default_button:
    push    1                   ; also refresh the live table, as before
    push    dword [CURPLAYER]
    call    DEFAULTS
    add     esp, 8
    jmp     RESUME
