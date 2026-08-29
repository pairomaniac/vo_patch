bits 32
; Keyboard (Simple)'s binds get a v_on.ini line of their own. The game's
; apply-and-serialize switch at 0x496e4f writes one Assign line per player
; for the committed device, and both keyboard-page devices land here: the
; blob writes "NP Simple Assign" from the +0x38 block - twelve binds as the
; same lowbyte-highbyte hex pairs "Keyboard Assign" uses - and then falls
; into the stock device 1 case, which refreshes the live table (through the
; block fork) and writes "Keyboard Assign" from +0x08. Both lines are
; always written, so neither profile's set is lost while the other is
; selected. Runs inside the F7 dialog's frame: [ebp + SAVEPLAYER] is the
; player, and the hex text is built in the frame's own line buffer through
; the cursor at [ebp + SAVELINE], not a static buffer of its own.
; The stock case rebuilds that buffer from the start afterwards.


%include "padtables.inc"    ; INIKEYS

extern BLOCKS
SIMPLE_OFF  equ 0x38
extern WRITELINE                ; (key, value): one v_on.ini line
extern CASEB                    ; the stock device 1 apply-and-serialize
extern HEXCHAR                  ; in asm/bindblock.asm's tail
%include "frames.inc"      ; the caller's locals, by name; the offset
                            ; is the retail build's, and build.py finds
                            ; each use so a build can move it
                            ; SAVEPLAYER: the OK handler's player
                            ; SAVELINE: and its line buffer

savesimple:
    push    ebx
    push    esi
    push    edi
    mov     ebx, [ebp + SAVEPLAYER]
    imul    esi, ebx, 0x70
    lea     esi, [esi + BLOCKS + SIMPLE_OFF]
    mov     edi, [ebp + SAVELINE]       ; the frame's line buffer
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
    push    dword [ebp + SAVELINE]
    push    eax
    call    WRITELINE
    add     esp, 8
    pop     edi
    pop     esi
    pop     ebx
    jmp     CASEB
