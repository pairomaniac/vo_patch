bits 32
; The device page's plain OK commits the device number and writes the
; "NP Device No." lines - and stops there. The stock game needed nothing
; more: every device family kept its own live table, already loaded. The
; gamepad and Keyboard (Simple) share one, so committing a switch between
; them must also reseed it from the new device's block, or the old
; profile's binds keep driving the tick until a trip through the bind
; page happens to apply them. This wraps the committed-device store in
; the OK loop. In: eax = the device, ecx = the player; both are reloaded
; by the caller afterwards.

extern BLOCKS
SIMPLE      equ 3
extern DEVICES
extern LIVE                     ; + player * 0x18

commitdev:
    mov     [ecx*4 + DEVICES], eax
    cmp     eax, 1
    je      .keyboard_page
    cmp     eax, SIMPLE
    jne     .done
.keyboard_page:
    push    esi
    push    edi
    push    ecx
    imul    esi, ecx, 0x70
    lea     esi, [esi + BLOCKS + 0x08]
    cmp     eax, SIMPLE
    jne     .block_found
    add     esi, 0x30
.block_found:
    imul    edi, ecx, 0x18
    lea     edi, [edi + LIVE]
    mov     ecx, 6
    rep movsd
    pop     ecx
    pop     edi
    pop     esi
.done:
    ret
