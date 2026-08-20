bits 32
; Keyboard (Simple)'s binds get a v_on.ini line of their own. The game's
; apply-and-serialize switch at 0x496e4f writes one Assign line per player
; for the committed device, and both keyboard-page devices land here: the
; cave writes "NP Simple Assign" from the +0x38 block - twelve binds as the
; same lowbyte-highbyte hex pairs "Keyboard Assign" uses - and then falls
; into the stock device 1 case, which refreshes the live table (through the
; block fork) and writes "Keyboard Assign" from +0x08. Both lines are
; always written, so neither profile's set is lost while the other is
; selected. Runs inside the F7 dialog's frame: [ebp - 0xc8] is the player,
; and the hex text is built in the frame's own line buffer through the
; cursor at [ebp - 0xcc] - the caves sit in .rdata, which the loader maps
; executable but never writable, so no static buffer can be scribbled on.
; The stock case rebuilds that buffer from the start afterwards.

org 0x00601c38          ; a run of zeros in .rdata

%include "padtables.inc"    ; INIKEYS

BLOCKS      equ 0x00bf6838
SIMPLE_OFF  equ 0x38
WRITELINE   equ 0x005b1833      ; (key, value): one v_on.ini line
CASEB       equ 0x00496b23      ; the stock device 1 apply-and-serialize
HEXCHAR     equ 0x005ff29c      ; in asm/bindblock.asm's tail

savesimple:
    push    ebx
    push    esi
    push    edi
    mov     ebx, [ebp - 0xc8]
    imul    esi, ebx, 0x70
    lea     esi, [esi + BLOCKS + SIMPLE_OFF]
    mov     edi, [ebp - 0xcc]       ; the frame's line buffer
    xor     ecx, ecx
.byte:                              ; 24 bytes -> 48 hex chars, low first
    mov     al, [esi + ecx]
    mov     dl, al
    shr     al, 4
    call    HEXCHAR
    mov     al, dl                  ; hexchar masks the top half itself
    call    HEXCHAR
    inc     ecx
    cmp     ecx, 0x18
    jl      .byte
    mov     byte [edi], 0           ; stack scratch, so terminate it
    imul    eax, ebx, 17            ; the two key strings, 17 bytes apiece
    add     eax, INIKEYS
    push    dword [ebp - 0xcc]
    push    eax
    call    WRITELINE
    add     esp, 8
    pop     edi
    pop     esi
    pop     ebx
    jmp     CASEB
