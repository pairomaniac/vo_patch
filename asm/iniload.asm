bits 32
; The load half of asm/inisave.asm. It replaces the startup call that
; refilled +0x38 with a legacy joystick set on every launch - the profiles
; that read those defaults are hidden, so the call only destroyed Simple's
; binds. Each keyboard-page block is loaded from its own line through
; asm/iniparse.asm: "NP Simple Assign" into +0x38, and "NP Keyboard
; Assign" into +0x08 as well - the stock loader only parses that line into
; the live table, and the gamepad block used to get it back through the
; bind page's live-table seed, which asm/blockcur.asm now skips when the
; page's device is not the committed one. A missing line leaves the
; shipped set from moments earlier. When the saved device is Simple, the
; live table is seeded from +0x38, overriding the seed the stock loader
; left there for the gamepad. cdecl (player, flag), flag unused.


%include "padtables.inc"    ; INIKEYS

extern BLOCKS
SIMPLE      equ 3
extern DEVICES
extern BINDS1                     ; + player * 0x18
extern PARSE12                  ; asm/iniparse.asm

loadsimple:
    push    ebx
    push    esi
    push    edi
    mov     ebx, [esp + 16]         ; player
    imul    edi, ebx, 0x70
    lea     edi, [edi + BLOCKS + 0x38]
    imul    eax, ebx, 17
    add     eax, INIKEYS
    call    PARSE12                 ; Simple's line into +0x38
    sub     edi, 0x30
    imul    eax, ebx, 19
    lea     eax, [eax + INIKEYS + 34]
    call    PARSE12                 ; the gamepad's line into +0x08
    cmp     dword [ebx*4 + DEVICES], SIMPLE
    jne     .done
    lea     esi, [edi + 0x30]       ; live table <- the Simple block
    imul    edi, ebx, 0x18
    lea     edi, [edi + BINDS1]
    mov     ecx, 6
    rep movsd
.done:
    pop     edi
    pop     esi
    pop     ebx
    ret
