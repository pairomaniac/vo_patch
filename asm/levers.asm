bits 32
; Runs in place of the tick's epilogue, ebx still the parameter block and ebp
; still the frame. The game's gestures are exclusive lever positions, but the
; tick ORs every active input together, so a held direction contaminates jump
; and guard. Strip the contamination back off.
    cmp     dword [ebp - 4], 0      ; did XInputGetState succeed this tick?
    je      .out                    ; no pad: leave the keyboard alone
    mov     edx, [ebx + 8]          ; lever word A, left lever
    mov     ecx, [ebx + 0x0c]       ; lever word B, right lever

    test    byte [edx], 0x80        ; A left?   bit clear means pushed
    jnz     .not_jump
    test    byte [ecx], 0x40        ; B right?
    jnz     .not_jump
    or      byte [edx], 0x70        ; drop right, up and down from A
    or      byte [ecx], 0xb0        ; drop left, up and down from B
    jmp     .out

.not_jump:
    test    byte [edx], 0x40        ; A right?
    jnz     .out
    test    byte [ecx], 0x80        ; B left?
    jnz     .out
    or      byte [edx], 0xb0
    or      byte [ecx], 0x70

.out:
    pop     edi
    pop     esi
    pop     ebx
    leave
    ret
