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
%include "frames.inc"      ; the caller's locals, by name; the offset
                            ; is the retail build's, and build.py finds
                            ; each use so a build can move it
                            ; DEVSEL: the F7 combo selection
                            ; DEVNUM: and the device it maps to

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
    mov     eax, [ebp + DEVSEL]            ; the combo selection
    cmp     eax, 7
    ja      .raw
    movzx   eax, byte [devof + eax]
.raw:
    mov     ecx, [ebp + DEVNUM]
    ret
