; vocd.asm - file-based CD audio for Virtual-On, entirely inside v_on.exe.
;
; Two blobs, code then data, both in a new .vocd section the patcher appends -
; there is no padding left in .text and the zero runs in .data are globals the
; game writes at runtime. apply_cdaudio fills the MAGIC_ placeholders with
; real addresses and points AddressOfEntryPoint at code_base+5.
;
;   code_base+0   jmp hook       <- what the 37 call sites are pointed at
;   code_base+5   jmp startup    <- new entry point
;
; The hook is reached by rewriting the game's own calls, not by owning the
; winmm IAT slot. Anything else that hooks mciSendCommandA by import name -
; cnc-ddraw does, and reinstalls it whenever a module loads - would otherwise
; overwrite the slot and drop this out of the chain. The slot is still read,
; at call time rather than at startup, so whoever does own it stays below us.
;
; Tracks are <gamedir>\music\trackNN.wav, 44100/16/stereo, as written by the
; ripper. Length comes from the file size, so no WAV parsing.

bits 32
%include "strings.inc"

%define MAGIC_ORIGENTRY 0xE1E1E1E1      ; VA of the previous entry point
%define MAGIC_IATMCI    0xE2E2E2E2      ; VA of the mciSendCommandA IAT slot
%define MAGIC_LOADLIB   0xE3E3E3E3      ; VA of the LoadLibraryA IAT slot
%define MAGIC_GETPROC   0xE4E4E4E4      ; VA of the GetProcAddress IAT slot
%define MAGIC_DATA      0xE5E5E5E5      ; VA of the data blob

%define VOCD_ID         0xFACE

%define MCI_OPEN        0x803
%define MCI_CLOSE       0x804
%define MCI_PLAY        0x806
%define MCI_STOP        0x808
%define MCI_PAUSE       0x809
%define MCI_GETDEVCAPS  0x80B
%define MCI_STATUS      0x814
%define MCI_RESUME      0x855

%define MCI_FROM            0x004
%define MCI_TRACK           0x010
%define MCI_OPEN_TYPE_ID    0x1000
%define MCI_OPEN_TYPE       0x2000

%define ST_LENGTH       1
%define ST_NTRACKS      3
%define ST_MODE         4
%define ST_MEDIA        5
%define ST_TIMEFMT      6
%define ST_READY        7
%define ST_CURTRACK     8
%define ST_CDATYPE      0x4001

%define MODE_STOP       525
%define MODE_PLAY       526
%define MODE_PAUSE      529
%define FORMAT_TMSF     10
%define CDA_AUDIO       1088
%define CDA_OTHER       1089

; ---------------------------------------------------------------- thunks

        jmp     hook                    ; code_base + 0
        jmp     startup                 ; code_base + 5

; ------------------------------------------------------------- utilities

; esi -> source, edi -> destination. Copies including NUL, leaves edi on it.
scat:
        lodsb
        stosb
        test    al, al
        jnz     scat
        dec     edi
        ret

; eax = value 0..99, written as two digits at edi.
two_digits:
        xor     edx, edx
        mov     ecx, 10
        div     ecx
        add     al, '0'
        stosb
        mov     al, dl
        add     al, '0'
        stosb
        ret

; ------------------------------------------------------------------ init

startup:
        pushad
        mov     ebx, MAGIC_DATA
        cmp     dword [ebx + D_INIT], 0
        jne     .done
        mov     dword [ebx + D_INIT], 1

        ; kernel32
        lea     eax, [ebx + S_KERNEL32]
        push    eax
        call    [MAGIC_LOADLIB]
        mov     esi, eax

        lea     eax, [ebx + S_GETMODFN]
        push    eax
        push    esi
        call    [MAGIC_GETPROC]
        mov     [ebx + D_GETMODFN], eax

        lea     eax, [ebx + S_CREATEF]
        push    eax
        push    esi
        call    [MAGIC_GETPROC]
        mov     [ebx + D_CREATEF], eax

        lea     eax, [ebx + S_GETFSIZE]
        push    eax
        push    esi
        call    [MAGIC_GETPROC]
        mov     [ebx + D_GETFSIZE], eax

        lea     eax, [ebx + S_CLOSEH]
        push    eax
        push    esi
        call    [MAGIC_GETPROC]
        mov     [ebx + D_CLOSEH], eax

        lea     eax, [ebx + S_LSTRCMPI]
        push    eax
        push    esi
        call    [MAGIC_GETPROC]
        mov     [ebx + D_LSTRCMPI], eax

        ; winmm
        lea     eax, [ebx + S_WINMM]
        push    eax
        call    [MAGIC_LOADLIB]
        test    eax, eax
        jz      .done
        mov     esi, eax
        lea     eax, [ebx + S_MCISTR]
        push    eax
        push    esi
        call    [MAGIC_GETPROC]
        mov     [ebx + D_MCISTR], eax
        test    eax, eax
        jz      .done

        ; gamedir = directory of the running executable, trailing backslash
        lea     eax, [ebx + D_GAMEDIR]
        push    264
        push    eax
        push    0
        call    [ebx + D_GETMODFN]
        test    eax, eax
        jz      .done
        lea     edi, [ebx + D_GAMEDIR]
        add     edi, eax                ; one past the last character
.strip:
        dec     edi
        cmp     byte [edi], '\'
        je      .stripped
        lea     eax, [ebx + D_GAMEDIR]
        cmp     edi, eax
        ja      .strip
.stripped:
        mov     byte [edi + 1], 0

        ; walk tracks 2..99
        mov     esi, 2
.track:
        call    build_path
        push    0                       ; hTemplateFile
        push    0x80                    ; FILE_ATTRIBUTE_NORMAL
        push    3                       ; OPEN_EXISTING
        push    0                       ; security
        push    1                       ; FILE_SHARE_READ
        push    0x80000000              ; GENERIC_READ
        lea     eax, [ebx + D_PATH]
        push    eax
        call    [ebx + D_CREATEF]
        cmp     eax, -1
        je      .nexttrack
        mov     edi, eax
        push    0
        push    edi
        call    [ebx + D_GETFSIZE]
        mov     ebp, eax
        push    edi
        call    [ebx + D_CLOSEH]

        ; frames = (size - 44) / 2352
        sub     ebp, 44
        jbe     .nexttrack
        mov     eax, ebp
        xor     edx, edx
        mov     ecx, 2352
        div     ecx
        ; eax = frames -> m:s:f packed low to high
        xor     edx, edx
        mov     ecx, 4500               ; frames per minute
        div     ecx                     ; eax = minutes, edx = remainder
        mov     ebp, eax                ; minutes
        mov     eax, edx
        xor     edx, edx
        mov     ecx, 75
        div     ecx                     ; eax = seconds, edx = frames
        shl     eax, 8
        or      ebp, eax
        shl     edx, 16
        or      ebp, edx
        mov     [ebx + esi*4 + D_TOC], ebp
        mov     [ebx + D_NTRACKS], esi
.nexttrack:
        inc     esi
        cmp     esi, 100
        jb      .track

        ; Nothing to install: the call sites already point here. With
        ; D_NTRACKS still zero the hook forwards everything.
.done:
        popad
        push    MAGIC_ORIGENTRY
        ret

; Builds <gamedir>music\trackNN.wav in D_PATH for track number esi.
build_path:
        push    esi
        lea     edi, [ebx + D_PATH]
        lea     esi, [ebx + D_GAMEDIR]
        call    scat
        lea     esi, [ebx + S_MUSICTRACK]
        call    scat
        mov     eax, [esp]
        call    two_digits
        lea     esi, [ebx + S_DOTWAV]
        call    scat
        pop     esi
        ret

; ------------------------------------------------------------ MCI helper
;
; D_PATH and D_CMD are single shared buffers with no locking. The game drives
; music from one thread, so this is deliberate rather than overlooked.

; Sends the string constant whose data-cave offset is in eax.
send_const:
        push    0
        push    0
        push    0
        lea     eax, [ebx + eax]
        push    eax
        call    [ebx + D_MCISTR]
        ret

unload:
        cmp     dword [ebx + D_TRACK], 0
        je      .out
        mov     eax, S_CLOSE
        call    send_const
        mov     dword [ebx + D_TRACK], 0
        mov     dword [ebx + D_PAUSED], 0
.out:
        ret

; ------------------------------------------------------------------ hook

hook:
        cld                             ; scat uses lodsb/stosb. The ABI says
                                        ; DF is clear here; one byte to not
                                        ; have to trust that.
        push    ebp
        mov     ebp, esp
        push    ebx
        push    esi
        push    edi
        mov     ebx, MAGIC_DATA

        cmp     dword [ebx + D_NTRACKS], 0
        je      forward

        mov     eax, [ebp + 12]                 ; message
        cmp     eax, MCI_OPEN
        jne     .not_open

        mov     edx, [ebp + 16]                 ; flags
        test    edx, MCI_OPEN_TYPE
        jz      forward
        test    edx, MCI_OPEN_TYPE_ID
        jnz     forward
        mov     esi, [ebp + 20]                 ; parms
        test    esi, esi
        jz      forward
        mov     eax, [esi + 8]                  ; lpstrDeviceType
        test    eax, eax
        jz      forward
        lea     edx, [ebx + S_CDAUDIO]
        push    edx
        push    eax
        call    [ebx + D_LSTRCMPI]
        test    eax, eax
        jnz     forward
        call    unload
        mov     dword [esi + 4], VOCD_ID        ; wDeviceID
        xor     eax, eax
        jmp     done

.not_open:
        cmp     dword [ebp + 8], VOCD_ID
        jne     forward

        cmp     eax, MCI_STATUS
        je      do_status
        cmp     eax, MCI_PLAY
        je      do_play
        cmp     eax, MCI_STOP
        je      do_stop
        cmp     eax, MCI_PAUSE
        je      do_pause
        cmp     eax, MCI_RESUME
        je      do_resume
        cmp     eax, MCI_CLOSE
        je      do_close
        cmp     eax, MCI_GETDEVCAPS
        je      do_devcaps
        xor     eax, eax                        ; SET, SEEK, anything else
        jmp     done

forward:
        push    dword [ebp + 20]
        push    dword [ebp + 16]
        push    dword [ebp + 12]
        push    dword [ebp + 8]
        call    [MAGIC_IATMCI]          ; read now, so a wrapper that owns the
                                        ; slot keeps working underneath us
done:
        pop     edi
        pop     esi
        pop     ebx
        pop     ebp
        ret     16

; --------------------------------------------------------------- handlers

do_devcaps:
        mov     esi, [ebp + 20]
        test    esi, esi
        jz      .out
        mov     dword [esi + 4], 1
.out:
        xor     eax, eax
        jmp     done

do_close:
        call    unload
        xor     eax, eax
        jmp     done

do_stop:
        cmp     dword [ebx + D_TRACK], 0
        je      .out
        mov     eax, S_STOP
        call    send_const
        call    unload
.out:
        xor     eax, eax
        jmp     done

do_pause:
        cmp     dword [ebx + D_TRACK], 0
        je      .out
        cmp     dword [ebx + D_PAUSED], 0
        jne     .out
        mov     eax, S_PAUSE
        call    send_const
        mov     dword [ebx + D_PAUSED], 1
.out:
        xor     eax, eax
        jmp     done

do_resume:
        cmp     dword [ebx + D_PAUSED], 0
        je      .out
        mov     eax, S_RESUME
        call    send_const
        mov     dword [ebx + D_PAUSED], 0
.out:
        xor     eax, eax
        jmp     done

; The game only ever plays whole tracks from their start, so the minute,
; second and frame fields of dwFrom are read but not acted on.
do_play:
        mov     edx, [ebp + 16]
        test    edx, MCI_FROM
        jz      .out
        mov     esi, [ebp + 20]
        test    esi, esi
        jz      .out
        mov     eax, [esi + 4]                  ; dwFrom, TMSF
        movzx   esi, al                         ; track in the low byte
        cmp     esi, 2
        jb      .out
        cmp     esi, 100
        jae     .out
        mov     eax, [ebx + esi*4 + D_TOC]
        test    eax, eax
        jz      .out                            ; no file: stay silent

        call    unload
        call    build_path

        ; open "<path>" type waveaudio alias vocdbgm
        lea     edi, [ebx + D_CMD]
        lea     esi, [ebx + S_OPENQ]
        call    scat
        lea     esi, [ebx + D_PATH]
        call    scat
        lea     esi, [ebx + S_OPENTAIL]
        call    scat

        push    0
        push    0
        push    0
        lea     eax, [ebx + D_CMD]
        push    eax
        call    [ebx + D_MCISTR]
        test    eax, eax
        jnz     .out

        mov     eax, [ebp + 20]
        mov     eax, [eax + 4]
        movzx   eax, al
        mov     [ebx + D_TRACK], eax

        mov     eax, S_SETFMT
        call    send_const
        mov     eax, S_PLAY
        call    send_const
.out:
        xor     eax, eax
        jmp     done

do_status:
        mov     esi, [ebp + 20]
        test    esi, esi
        jnz     .have_parms
        xor     eax, eax
        jmp     done
.have_parms:
        mov     eax, [esi + 8]                  ; dwItem

        cmp     eax, ST_NTRACKS
        jne     .not_ntracks
        mov     edx, [ebx + D_NTRACKS]
        jmp     .ret_edx
.not_ntracks:
        cmp     eax, ST_LENGTH
        jne     .not_length
        mov     edx, [ebp + 16]
        test    edx, MCI_TRACK
        jz      .zero
        mov     ecx, [esi + 12]
        cmp     ecx, 1
        jb      .zero
        cmp     ecx, 100
        jae     .zero
        mov     edx, [ebx + ecx*4 + D_TOC]
        jmp     .ret_edx
.not_length:
        cmp     eax, ST_MODE
        jne     .not_mode
        mov     edx, MODE_STOP
        cmp     dword [ebx + D_PAUSED], 0
        jne     .mode_paused
        cmp     dword [ebx + D_TRACK], 0
        je      .ret_edx
        ; ask waveaudio whether it is still going
        push    0
        push    64
        lea     eax, [ebx + D_CMD]
        push    eax
        lea     eax, [ebx + S_STATUSMODE]
        push    eax
        call    [ebx + D_MCISTR]
        test    eax, eax
        jnz     .ret_edx
        lea     eax, [ebx + S_PLAYING]
        push    eax
        lea     eax, [ebx + D_CMD]
        push    eax
        call    [ebx + D_LSTRCMPI]
        mov     edx, MODE_STOP
        test    eax, eax
        jnz     .ret_edx
        mov     edx, MODE_PLAY
        jmp     .ret_edx
.mode_paused:
        mov     edx, MODE_PAUSE
        jmp     .ret_edx
.not_mode:
        cmp     eax, ST_CURTRACK
        jne     .not_curtrack
        mov     edx, [ebx + D_TRACK]
        test    edx, edx
        jnz     .ret_edx
        mov     edx, 1
        jmp     .ret_edx
.not_curtrack:
        cmp     eax, ST_MEDIA
        je      .one
        cmp     eax, ST_READY
        je      .one
        cmp     eax, ST_TIMEFMT
        jne     .not_timefmt
        mov     edx, FORMAT_TMSF
        jmp     .ret_edx
.not_timefmt:
        cmp     eax, ST_CDATYPE
        jne     .zero
        mov     edx, CDA_AUDIO
        mov     ecx, [ebp + 16]
        test    ecx, MCI_TRACK
        jz      .ret_edx
        cmp     dword [esi + 12], 1
        jne     .ret_edx
        mov     edx, CDA_OTHER
        jmp     .ret_edx
.one:
        mov     edx, 1
        jmp     .ret_edx
.zero:
        xor     edx, edx
.ret_edx:
        mov     [esi + 4], edx                  ; dwReturn
        xor     eax, eax
        jmp     done
