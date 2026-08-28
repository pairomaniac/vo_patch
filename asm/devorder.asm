bits 32
; The F7 device list shows the profiles as gamepad, twin-stick, Keyboard
; (Simple), Keyboard (Real), while the device numbers stay what the
; executable and v_on.ini have always used (0 Real, 1 gamepad, 2 twin-
; stick, 3 Simple). Two mappings keep the list honest: the page's
; preselect turns the pending device into its list position, and the OK
; translate turns the chosen position back into a device before it is
; stored as pending. Positions past the four profiles pass through
; unchanged, as do out-of-range values.

extern BLOCKS                   ; pending devices, + player * 0x70

posof:  db 3, 0, 1, 2, 4, 5, 6, 7       ; device -> list position
devof:  db 1, 2, 3, 0, 4, 5, 6, 7       ; list position -> device

; The preselect read: in eax = player * 0x70, out eax = list position.
posshim:
    mov     eax, [eax + BLOCKS]
    cmp     eax, 7
    ja      .raw
    movzx   eax, byte [posof + eax]
.raw:
    ret

; The translate: out eax = the chosen device, ecx = the player, both as
; the two replaced loads produced them.
devshim:
    mov     eax, [ebp - 0xc]            ; the combo selection
    cmp     eax, 7
    ja      .raw
    movzx   eax, byte [devof + eax]
.raw:
    mov     ecx, [ebp - 0x14]
    ret
