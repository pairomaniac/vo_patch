bits 32
org 0x00623d98          ; a run of zeros in .rdata; 0x623e40 is a qword 480.0
                        ; that 18 sites load, so 168 bytes are free from here
; Draws the patcher's version in the bottom right of the title screen.
;
; The game's own tile font, the one the menu items on this screen are set
; in: 0x4cd8c3 puts the cursor at a cell and 0x4ceeeb prints through it,
; which is exactly what 0x44b757 does with the table at 0x6537c0.
;
; 0x4ceeeb picks the glyph set itself, out of the four at 0x600ec8: a string
; with lower case in it gets the set that has lower case, which is why this
; one can be mixed case. That set is half width, so a character is one cell
; rather than the two the menu items take.
;
; GDI was the obvious way and the wrong one here. It is what the pause text
; and HOLD TO SKIP use, but the game builds exactly two fonts at 0x5c8cd7 -
; century and modern bold, 24px - and 0x5c991c takes an index into that pair
; rather than a handle. Neither belongs on this screen and neither is small.
; The tile font costs less code than either, since none of the surface
; juggling GDI needs applies: no back buffer, no halving for low resolution.
;
; The hook is the load at 0x5c6500, four instructions past the call the
; overlay took and still ahead of the flip at 0x5c650d, so the two patches
; share no bytes and either can be applied without the other. Nothing here
; needs to run at that point in the frame - the tiles are read on the next
; one - but it is a five-byte site on a path that runs every frame, which is
; what this needs. The displaced load is repeated at the end.
;
; The string is not in here. The patcher writes it in after the blob, since
; the version comes from the git tag and the blobs are built from source.

CURSOR      equ 0x004cd8c3      ; (column, row), cdecl
PRINT       equ 0x004ceeeb      ; (text), cdecl, from the cursor
PRIMARY     equ 0x01ae5f40      ; what the displaced load reads

; 0x1ae3594 picks the machine through the table at 0x5fe5e0 and 0x1ae3690 is
; its state. All three of these are machine 1, the attract one, whose
; dispatcher is 0x44b38c and whose table is 0x5fb238. 6 and 0x17 are the logo
; with the blinking banner - 0x17 is 0x44b89d, which calls 6's own handler at
; 0x545dfa - and 0x11 is the logo with the menu. 7 is the demo match and is
; the reason this is three tests and not a range.
MODE        equ 0x01ae3594
SUBMODE     equ 0x01ae3690
ATTRACT     equ 1
PROMPT      equ 0x06            ; 0x545dfa, the logo and Press A Button
PROMPT2     equ 0x17            ; 0x44b89d, the same screen later in the loop
MENU        equ 0x11            ; 0x44b5bc, 1 PLAYER and the two below it

; Cells, of the 81-wide map, one per character in this glyph set. The
; copyright line is 59 of them from column 1 at row 44, which is what these
; were measured against.
COL         equ 53
ROW         equ 50

TEXTLEN     equ 24              ; what the patcher has to write the version in

titlever:
    mov     eax, [MODE]
    cmp     eax, ATTRACT
    jne     .out
    mov     eax, [SUBMODE]
    cmp     eax, PROMPT
    je      .draw
    cmp     eax, PROMPT2
    je      .draw
    cmp     eax, MENU
    jne     .out
.draw:
    push    ROW
    push    COL
    call    CURSOR
    add     esp, 8

    push    text
    call    PRINT
    add     esp, 4
.out:
    mov     eax, [PRIMARY]      ; the displaced load
    ret

text:
    times   TEXTLEN db 0
