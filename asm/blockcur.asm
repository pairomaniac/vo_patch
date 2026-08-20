bits 32
; The bind page's store hands its block fork a fused index - the player's
; struct offset plus the slot times two - so asm/bindblock.asm's check,
; which reads the device dword at the incoming offset, would consult a
; different field for every slot but the first. This fork serves that one
; call site: the device comes from the current player, which is what the
; caller built the index from two instructions earlier, and the incoming
; eax is used purely as an address.

org 0x005fd864          ; a run of zeros in .rdata

BASE        equ 0x00bf6838
SIMPLE      equ 3
CURPLAYER   equ 0x00bf6bac

blockcur:               ; in: eax = player * 0x70 + slot * 2
    push    ecx
    mov     ecx, [CURPLAYER]
    imul    ecx, ecx, 0x70
    cmp     dword [ecx + BASE], SIMPLE
    pop     ecx
    je      .simple
    lea     eax, [eax + BASE + 0x08]
    ret
.simple:
    lea     eax, [eax + BASE + 0x38]
    ret

    times   0x28 - ($ - blockcur) db 0x90

; ---------------------------------------------------------------- 0x5fd88c
; The bind page seeds its block from the live table at open - stock
; behaviour, and how the gamepad's saved set normally returns to its block
; after a restart. With two profiles sharing one live table that seed is
; only right when the page's pending device is the one actually committed;
; otherwise it would copy the active profile's binds into the other's
; block. This wraps the seed's memcpy and skips it in that case; the
; caller's stack cleanup is unaffected either way. asm/iniload.asm loads
; each block's own ini line at launch, so a skipped seed loses nothing.
; Runs in the seeder's frame: [ebp + 8] is the player.

MEMCPY      equ 0x005e6030
DEVICES     equ 0x03651540

syncshim:
    push    eax
    push    ecx
    mov     eax, [ebp + 8]
    imul    ecx, eax, 0x70
    mov     eax, [eax*4 + DEVICES]
    cmp     [ecx + BASE], eax
    pop     ecx
    pop     eax
    jne     .skip
    jmp     MEMCPY
.skip:
    ret
