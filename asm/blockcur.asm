bits 32
; The bind page's store hands its block fork a fused index - the player's
; struct offset plus the slot times two - so asm/bindblock.asm's check,
; which reads the device dword at the incoming offset, would consult a
; different field for every slot but the first. This fork serves that one
; call site: the device comes from the current player, which is what the
; caller built the index from two instructions earlier, and the incoming
; eax is used purely as an address.

extern BLOCKS
SIMPLE      equ 3
extern CURPLAYER

blockcur:               ; in: eax = player * 0x70 + slot * 2
    push    ecx
    mov     ecx, [CURPLAYER]
    imul    ecx, ecx, 0x70
    cmp     dword [ecx + BLOCKS], SIMPLE
    pop     ecx
    je      .simple
    lea     eax, [eax + BLOCKS + 0x08]
    ret
.simple:
    lea     eax, [eax + BLOCKS + 0x38]
    ret

; ----------------------------------------------------------------
; The bind page seeds its block from the live table at open - stock
; behaviour, and how the gamepad's saved set normally returns to its block
; after a restart. With two profiles sharing one live table that seed is
; only right when the page's pending device is the one actually committed;
; otherwise it would copy the active profile's binds into the other's
; block. This wraps the seed's memcpy and skips it in that case; the
; caller's stack cleanup is unaffected either way. asm/iniload.asm loads
; each block's own ini line at launch, so a skipped seed loses nothing.
; Runs in the seeder's frame: [ebp + 8] is the player.

extern MEMCPY
extern DEVICES

syncshim:
    push    eax
    push    ecx
    mov     eax, [ebp + 8]
    imul    ecx, eax, 0x70
    mov     eax, [eax*4 + DEVICES]
    cmp     [ecx + BLOCKS], eax
    pop     ecx
    pop     eax
    jne     .skip
    jmp     MEMCPY
.skip:
    ret
