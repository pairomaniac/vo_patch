bits 32
org 0x00608060          ; the .rdata cave the XInput patch drops this into
BASE        equ 0x00608060      ; the org again, for the pins below
; The XInput routine: two profile entry stubs, the message-pump stub, the
; per-player input tick and its parameter blocks.
;
; Six addresses in here are named by something outside this file and cannot
; move. `times` pins each one, so nasm fails rather than shifting them:
;
;   0x608060, 0x608072  the profile dispatch sites point at the entry stubs
;   0x608098            the PeekMessageA call site points at the pump stub
;   0x6080a4            introwait.asm calls the pad poll
;   0x608159            twinstick.asm calls the tick
;   0x608302            the levers site replaces the epilogue, and expects
;                       the five bytes of it to be exactly where they are
;
; The blob is also padded to a fixed 830 bytes, because levers.asm is written
; at the site immediately after it.

TICKLEN     equ 830

XIFN        equ 0x0365cb40      ; resolved XInputGetState: 0 not yet, 1 failed
STATE       equ 0x0365cb44      ; the tick's XINPUT_STATE
BTN         equ 0x0365cb48      ; wButtons in it; the condition table's
                                ; offsets are relative to this
PSTATE      equ 0x0365cb70      ; the pump's own XINPUT_STATE, so the two
PBTN        equ 0x0365cb74      ; pollers cannot tread on each other
PREV        equ 0x0365cb84      ; last polled buttons, one word per pad,
                                ; stride 4

%include "padtables.inc"    ; COND, the condition table asm/padtables.py
                            ; builds and the bind bytes index into
EXIT1P      equ 0x00442ec4      ; where the 1P profile switch resumes
EXIT2P      equ 0x005bcd57      ; and the 2P one

HWND        equ 0x01ae5f58      ; the game's window
LOADLIB     equ 0x0365d504      ; LoadLibraryA
GETPROC     equ 0x0365d508      ; GetProcAddress
POSTMSG     equ 0x0365d56c      ; PostMessageA
PEEKMSG     equ 0x0365d590      ; PeekMessageA, the call this stub replaced

CAMSKIP     equ 0x0063dda0      ; camskip.asm, called from the tick

MODE        equ 0x01ae3594      ; game state and sub-state. The pair the
SUBMODE     equ 0x01ae3690      ; stock keyboard handler gates its bind
                                ; slots on; see the tick.

WM_KEYDOWN  equ 0x0100
WM_KEYUP    equ 0x0101
VK_SPACE    equ 0x20
VK_F3       equ 0x72

; ---------------------------------------------------------------- 0x608060
; One stub per player, installed in profile slot 1 of the F7 device list.
entry1p:
    push    block1
    call    tick
    add     esp, 4
    jmp     EXIT1P

    times   (0x00608072 - BASE) - ($ - $$) db 0

entry2p:
    push    block2
    call    tick
    add     esp, 4
    jmp     EXIT2P

    times   (0x00608098 - BASE) - ($ - $$) db 0

; ---------------------------------------------------------------- 0x608098
; Runs in place of one `call PeekMessageA` in the game's message pump. The
; input tick does not run while the game is paused, so pause and resume are
; posted from here instead.
;
; The intro movie takes the loop's other branch: it is played asynchronously
; - 0x5b13aa issues MCI_PLAY without MCI_WAIT - and leaves 0x6bc598 at 1, so
; the test at 0x5c5e95 picks a blocking GetMessageA instead of this call.
; introwait.asm hooks that one and shares the poll below.
pump:
    call    pollpads
    jmp     [PEEKMSG]

    times   (0x006080a4 - BASE) - ($ - $$) db 0

; ---------------------------------------------------------------- 0x6080a4
; Poll both pads and post the edges for the keys the window procedure handles
; itself. Called from the pump each frame, and from introwait.asm while the
; intro movie holds the loop.
pollpads:
    pushad
    pushfd
    call    resolve             ; during the intro the tick has never run, so
    cmp     eax, 1              ; nothing else has resolved the import yet
    jbe     .out
    xor     esi, esi
.pad:
    cmp     esi, 2
    jae     .out
    push    PSTATE
    push    esi
    call    [XIFN]
    test    eax, eax
    jnz     .nextpad            ; pad not connected
    movzx   ebx, word [PBTN]
    lea     edx, [esi*4 + PREV]
    movzx   ebp, word [edx]
    mov     [edx], bx
    xor     edi, edi
.key:
    cmp     edi, (keytab_end - keytab) / 4
    jae     .nextpad
    lea     ecx, [edi*4 + keytab]
    movzx   eax, word [ecx]     ; button mask
    mov     edx, ebx
    and     edx, eax            ; held now
    and     eax, ebp            ; held last poll
    cmp     edx, eax
    je      .nextkey            ; no edge
    mov     eax, WM_KEYDOWN
    test    edx, edx
    jnz     .post
    cmp     byte [ecx + 3], 0   ; a release is only posted for the keys that
    je      .nextkey            ; want one; see the table
    mov     eax, WM_KEYUP
.post:
    push    0
    movzx   edx, byte [ecx + 2]
    push    edx
    push    eax
    push    dword [HWND]
    call    [POSTMSG]
.nextkey:
    inc     edi
    jmp     .key
.nextpad:
    inc     esi
    jmp     .pad
.out:
    popfd
    popad
    ret

; mask, virtual key, and whether to post a release as well. Neither of these
; posts one: what F3 does on a keyup is not known and pause works as it is.
; The column stays because a key that latches would need one.
;
; Only keys the window procedure itself handles belong here. The game reads
; everything else through DirectInput, where a posted message never arrives.
keytab:
    dw  0x0010
    db  VK_F3,    0             ; Start
    dw  0x1000
    db  VK_SPACE, 0             ; A
keytab_end:

    times   (0x00608159 - BASE) - ($ - $$) db 0

; ---------------------------------------------------------------- 0x608159
; The input tick, one call per player per frame, reached through the profile
; dispatch. [ebp-4] records whether a pad was read, which the lever cleanup
; that replaces the epilogue reads.
;
; Parameter block, one dword each:
;   0x00 player index   0x04 binds      0x08 lever A    0x0c lever B
;   0x10 mask A         0x14 mask B     0x18 accept key slot
;   0x1c scratch        0x20 keyboard handler           0x24 camera key slot
tick:
    push    ebp
    mov     ebp, esp
    sub     esp, 4
    push    ebx
    push    esi
    push    edi
    mov     ebx, [ebp + 8]
    mov     dword [ebp - 4], 0

    call    resolve
    cmp     eax, 1
    je      .keyboard
    push    STATE
    push    dword [ebx]
    call    eax
    test    eax, eax
    jnz     .keyboard           ; pad not connected: keyboard only
    mov     dword [ebp - 4], 1

    ; A and Back are keys the game reads directly rather than actions that
    ; can be bound, so they are written into the player's key buffer. This
    ; has to happen before the keyboard handler runs, because that is the
    ; code that reads them.
    movzx   eax, word [BTN]
    test    eax, 0x1000         ; A -> the accept key slot
    jz      .back
    mov     edx, [ebx + 0x18]
    mov     byte [edx], 0x80
    call    CAMSKIP             ; and, on the screens that read only the
                                ; camera key, that slot too; see camskip.asm
.back:
    movzx   eax, word [BTN]
    test    eax, 0x20           ; Back -> the camera key slot
    jz      .keyboard
    mov     edx, [ebx + 0x24]
    mov     byte [edx], 0x80

.keyboard:
    mov     eax, [ebx + 0x20]
    call    eax
    cmp     dword [ebp - 4], 0
    je      epilogue

    ; Twelve bind slots. A pad input is 0xe0 + index into the condition
    ; table; anything else is a key and belongs to the handler above.
    xor     esi, esi
.slot:
    cmp     esi, 12
    jae     .dpadstart

    ; Not every slot is live in every game state. The stock keyboard handler
    ; at 0x443074 runs all twelve only in one state; everywhere else it drops
    ; turn and stops after slot 7, so jump, dash and guard do nothing. Menus
    ; are read out of the lever words, so without this a button bound to jump
    ; walks the cursor - which the keyboard never does. Same test, same
    ; slots.
    cmp     dword [MODE], 4
    jne     .limited
    cmp     dword [SUBMODE], 8
    jl      .limited
    cmp     dword [SUBMODE], 0x0c
    jle     .live
.limited:
    cmp     esi, 4
    je      .nextslot
    cmp     esi, 5
    je      .nextslot
    cmp     esi, 7
    jg      .dpadstart
.live:
    mov     edx, [ebx + 4]
    movzx   eax, byte [edx + esi*2]
    sub     eax, 0xe0
    jb      .nextslot
    cmp     eax, 0x10
    jae     .nextslot
    lea     edi, [eax*8 + COND]
    movzx   eax, byte [edi]     ; kind
    movzx   edx, word [edi + 2] ; offset from BTN
    mov     ecx, [edi + 4]      ; value
    cmp     eax, 0
    je      .less
    cmp     eax, 1
    je      .greater
    cmp     eax, 2
    je      .mask
    movzx   eax, byte [edx + BTN]   ; trigger, unsigned byte
    cmp     eax, ecx
    ja      .fire
    jmp     .nextslot
.less:
    movsx   eax, word [edx + BTN]   ; axis, signed
    cmp     eax, ecx
    jl      .fire
    jmp     .nextslot
.greater:
    movsx   eax, word [edx + BTN]
    cmp     eax, ecx
    jg      .fire
    jmp     .nextslot
.mask:
    movzx   eax, word [BTN]         ; button
    test    eax, ecx
    jnz     .fire
    jmp     .nextslot
.fire:
    call    apply
.nextslot:
    inc     esi
    jmp     .slot

    ; The D-pad drives the first four slots' lever bits directly. Menus and
    ; the mech list are read out of the lever words, not out of keys, so this
    ; is what navigates them, and in a round it moves. It cannot be a bind:
    ; one input per slot, and the left stick already holds those four.
.dpadstart:
    xor     esi, esi
.dpad:
    cmp     esi, 4
    jae     epilogue
    movzx   eax, word [BTN]
    bt      eax, esi            ; up, down, left, right are bits 0 to 3
    jnc     .nextdpad
    call    apply
.nextdpad:
    inc     esi
    jmp     .dpad

; Clear the bits slot esi's two masks name from the two lever words. Lever
; bits are active low, so an input clears rather than sets.
apply:
    mov     edx, [ebx + 0x10]
    movzx   ecx, byte [edx + esi]
    not     ecx
    mov     edx, [ebx + 8]
    movzx   eax, word [edx]
    and     eax, ecx
    mov     [edx], ax
    mov     edx, [ebx + 0x14]
    movzx   ecx, byte [edx + esi]
    not     ecx
    mov     edx, [ebx + 0x0c]
    movzx   eax, word [edx]
    and     eax, ecx
    mov     [edx], ax
    ret

; ---------------------------------------------------------------------------
; Resolve XInputGetState once, and remember that it failed so a machine with
; no XInput is not retried every frame. Called from the tick and from the
; poll, since either can be the first to run.
resolve:
    mov     eax, [XIFN]
    test    eax, eax
    jnz     .done
    push    esi
    xor     esi, esi
.next:
    cmp     esi, 3
    jae     .fail
    mov     eax, [esi*4 + dlltab]
    push    eax
    call    [LOADLIB]
    test    eax, eax
    jnz     .got
    inc     esi
    jmp     .next
.got:
    push    procname
    push    eax
    call    [GETPROC]
    test    eax, eax
    jnz     .store
.fail:
    mov     eax, 1
.store:
    mov     [XIFN], eax
    pop     esi
.done:
    ret

    times   (0x00608302 - BASE) - ($ - $$) db 0

; ---------------------------------------------------------------- 0x608302
; Replaced by levers.asm, which does the same five things after cleaning the
; contamination out of the lever words.
epilogue:
    pop     edi
    pop     esi
    pop     ebx
    leave
    ret

dlltab:
    dd  dll14, dll13, dll910
procname:
    db  'XInputGetState', 0

block1:
    dd  0, 0x03651470, 0x01cb14c4, 0x01cb14c6, 0x00653690, 0x0065369d
    dd  0x00bf0481, 0x0365cb60, 0x00443074, 0x00bf0457
block2:
    dd  1, 0x03651488, 0x01ee3ee4, 0x01ee3ee6, 0x006beb08, 0x006beb15
    dd  0x01ad0db1, 0x0365cb61, 0x005bceed, 0x01ad0d94

dll14:  db 'xinput1_4.dll', 0
dll13:  db 'xinput1_3.dll', 0
dll910: db 'xinput9_1_0.dll', 0

    times   TICKLEN - ($ - $$) db 0
