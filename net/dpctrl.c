/*
 * dpctrl.c - drop-in replacement for Virtual-On (PC, 1997) DPCTRL.DLL
 *
 * The original routes the game's netplay through DirectPlay 1 (DirectPlayCreate),
 * which has no address mechanism, so host discovery is a LAN broadcast. This
 * replacement speaks plain UDP to one peer: host binds a port, guest dials it.
 *
 * The seven exports, their stdcall signatures and their observable behaviour
 * match the original. The on-wire packet layout is kept identical too, since
 * the game can see it through SendDirectPlay/ReceiveDirectPlay.
 *
 * Build (32-bit, mingw-w64):
 *   i686-w64-mingw32-gcc -O2 -s -shared -o dpctrl.dll dpctrl.c dpctrl.def \
 *       -lws2_32 -lwinmm
 */

#include <winsock2.h>
#include <windows.h>
#include <stdio.h>
#include <string.h>

#define DEFAULT_PORT     47624   /* same number DirectPlay used, for firewall parity */
#define RING             64      /* sequence window, seq is 6 bits */
#define RING_MASK        (RING - 1)
#define MSGRING          0x2000  /* byte-stream ring for SendDirectPlay traffic */
#define MSGRING_MASK     (MSGRING - 1)
#define MAXPKT           512
#define RESEND_MS        1000
#define HANDSHAKE_MS     30000
#define NEGOTIATE_MS     10000
#define PING_ROUNDS      9

/* packet types. 1..0x18 are the original's; 0x80+ are ours. */
#define P_DATA   0x01   /* [type][seq][payload]              */
#define P_RESEND 0x02   /* [type][..][u32 seq]               */
#define P_DELAY  0x05   /* [type][..][u32 frames]            */
#define P_PONG   0x06   /* [type]                            */
#define P_POLL   0x07   /* [type]                            */
#define P_PING   0x16   /* [type]                            */
#define P_CMD    0x18   /* [type][..][u32 wparam]            */
#define P_HELLO  0x80   /* [type][16-byte session tag]       */
#define P_ACK    0x81   /* [type][16-byte session tag]       */
#define P_QUIT   0x82   /* [type]                            */

/* handle_packet return codes */
#define R_NONE     0
#define R_DATA     1
#define R_RESEND   2
#define R_DELAY    3
#define R_PONG     4
#define R_CMD      5
#define R_PEERQUIT 6

/* the struct v_on.exe passes to InitialDirectPlay */
typedef struct {
    HWND   hwnd;          /* +0x00 game window                                  */
    DWORD  unused;        /* +0x04 stored by the original, never read           */
    int    datasize;      /* +0x08 payload bytes per frame (40)                 */
    int   *out_player1;   /* +0x0c receives 1 if we are player 1 (the host)     */
    char  *session;       /* +0x10 8 bytes, first half of the session tag       */
    char  *name;          /* +0x14 player name                                  */
    int    japanese;      /* +0x18 dialog language, the game passes 0           */
    int    timeout_sec;   /* +0x1c link-dead timeout in seconds (30)            */
    void  *ddraw;         /* +0x20 LPDIRECTDRAW, for FlipToGDISurface           */
} VO_NETINIT;

static struct {
    int    up;             /* socket is open                    */
    int    linked;         /* peer handshake completed          */
    int    host;           /* 1 = we are player 1               */
    SOCKET sock;
    struct sockaddr_in peer;

    HWND   hwnd;
    void  *ddraw;
    int    framelen;       /* datasize + 2, matches the original */
    DWORD  timeout_ms;
    char   tag[16];        /* session identity, checked in the handshake */

    int    delay;          /* negotiated input delay in frames  */
    int    prime;          /* extra sends left on the first frame */
    int    txseq;          /* next sequence to send             */
    int    cursor;         /* frame we are waiting to consume   */

    unsigned char tx[RING * MAXPKT];
    unsigned char rx[RING * MAXPKT];

    unsigned char msg[MSGRING];   /* SendDirectPlay byte stream  */
    int    msg_head, msg_tail;

    unsigned char pkt[MAXPKT];    /* last datagram received      */
    int    pktlen;

    /* handshake dialog state */
    int    hs_done, hs_failed;
    DWORD  hs_start, hs_last;

    char   ip[128];
    int    port;
} g;

/* ------------------------------------------------------------------ */
/* helpers                                                             */
/* ------------------------------------------------------------------ */

/* The game owns the display. Flip to GDI or the message box is invisible. */
static void flip_to_gdi(void)
{
    if (g.ddraw) {
        void **vt = *(void ***)g.ddraw;
        ((HRESULT (__stdcall *)(void *))vt[10])(g.ddraw);   /* FlipToGDISurface */
    }
}

static void notice(const char *text)
{
    SetFocus(g.hwnd);
    ShowCursor(TRUE);
    flip_to_gdi();
    MessageBoxA(g.hwnd, text, "Message", 0);
    ShowCursor(FALSE);
}

static void sock_close(void)
{
    if (g.up) {
        closesocket(g.sock);
        WSACleanup();
        g.up = 0;
    }
    g.linked = 0;
}

static void send_raw(const void *buf, int len)
{
    if (g.up)
        sendto(g.sock, (const char *)buf, len, 0,
               (struct sockaddr *)&g.peer, sizeof(g.peer));
}

static void send_ctl(unsigned char type, DWORD arg)
{
    unsigned char b[8];
    memset(b, 0, sizeof(b));
    b[0] = type;
    switch (type) {
    case P_RESEND: case P_DELAY: case P_CMD:
        *(DWORD *)(b + 4) = arg;
        send_raw(b, 8);
        break;
    default:
        send_raw(b, 1);
        break;
    }
}

/* Send the local player's frame and keep a copy for the peer to ask for. */
static void send_frame(const unsigned char *payload)
{
    unsigned char *slot;
    int i;

    g.pkt[0] = P_DATA;
    g.pkt[1] = (unsigned char)(g.txseq & RING_MASK);
    for (i = 2; i < g.framelen; i++)
        g.pkt[i] = payload[i - 2];

    slot = g.tx + (g.txseq & RING_MASK) * MAXPKT;
    memcpy(slot, g.pkt, g.framelen);

    g.txseq = (g.txseq + 1) & RING_MASK;
    send_raw(g.pkt, g.framelen);
}

/* Pull one datagram. 1 = got one, 0 = nothing waiting, -1 = link is gone. */
static int poll_recv(void)
{
    struct sockaddr_in from;
    int fromlen = sizeof(from);
    int n;

    if (!g.up)
        return -1;

    n = recvfrom(g.sock, (char *)g.pkt, sizeof(g.pkt), 0,
                 (struct sockaddr *)&from, &fromlen);
    if (n <= 0) {
        int e = WSAGetLastError();
        /* An ICMP unreachable surfaces here as WSAECONNRESET. Not fatal. */
        if (e == WSAEWOULDBLOCK || e == WSAECONNRESET)
            return 0;
        return -1;
    }

    /* Ignore anything that is not our peer. */
    if (g.linked && from.sin_addr.s_addr != g.peer.sin_addr.s_addr)
        return 0;

    g.pktlen = n;
    return 1;
}

static int handle_packet(void)
{
    unsigned char type = g.pkt[0];
    unsigned char *slot;
    int i;

    switch (type) {
    case P_DATA: {
        int seq = g.pkt[1] & RING_MASK;
        slot = g.rx + seq * MAXPKT;
        for (i = 0; i < g.framelen && i < g.pktlen; i++)
            slot[i] = g.pkt[i];
        return R_DATA;
    }
    case P_RESEND: {
        int seq = (int)(*(DWORD *)(g.pkt + 4)) & RING_MASK;
        slot = g.tx + seq * MAXPKT;
        if (slot[0] == P_DATA)
            send_raw(slot, g.framelen);
        return R_RESEND;
    }
    case P_PING:
        send_ctl(P_PONG, 0);
        return R_PONG;
    case P_PONG:
        return R_PONG;
    case P_POLL:
        return R_NONE;
    case P_DELAY:
        return R_DELAY;
    case P_CMD:
        return R_CMD;
    case P_QUIT:
        return R_PEERQUIT;
    case P_HELLO:
        /* A late duplicate of the guest's hello. Re-ack it. */
        if (!memcmp(g.pkt + 1, g.tag, sizeof(g.tag))) {
            unsigned char b[1 + sizeof(g.tag)];
            b[0] = P_ACK;
            memcpy(b + 1, g.tag, sizeof(g.tag));
            send_raw(b, sizeof(b));
        }
        return R_NONE;
    case P_ACK:
        return R_NONE;
    default:
        /* Anything else is a game message. Queue it for ReceiveDirectPlay. */
        g.msg[g.msg_head] = (unsigned char)g.pktlen;
        g.msg_head = (g.msg_head + 1) & MSGRING_MASK;
        for (i = 0; i < g.pktlen; i++) {
            g.msg[g.msg_head] = g.pkt[i];
            g.msg_head = (g.msg_head + 1) & MSGRING_MASK;
        }
        return R_NONE;
    }
}

/* ------------------------------------------------------------------ */
/* connection dialogs, built in memory so the DLL needs no resources    */
/* ------------------------------------------------------------------ */

#define ID_IP     0x3E8
#define ID_PORT   0x3E9
#define ID_HOST   0x3EA
#define ID_JOIN   0x3EB
#define ID_STATUS 0x3EC

static WORD *tpl_str(WORD *p, const char *s)
{
    while (*s)
        *p++ = (WORD)(unsigned char)*s++;
    *p++ = 0;
    return p;
}

static WORD *tpl_align(WORD *base, WORD *p)
{
    while (((char *)p - (char *)base) & 3)
        *p++ = 0;
    return p;
}

static WORD *tpl_ctl(WORD *base, WORD *p, DWORD style, short x, short y,
                     short cx, short cy, WORD id, WORD cls, const char *text)
{
    p = tpl_align(base, p);
    *(DWORD *)p = style;      p += 2;
    *(DWORD *)p = 0;          p += 2;   /* exstyle */
    *p++ = x; *p++ = y; *p++ = cx; *p++ = cy;
    *p++ = id;
    *p++ = 0xFFFF; *p++ = cls;
    p = tpl_str(p, text);
    *p++ = 0;                            /* no creation data */
    return p;
}

static WORD *tpl_head(WORD *buf, DWORD style, short cx, short cy,
                      WORD nctl, const char *title)
{
    WORD *p = buf;
    *(DWORD *)p = style;      p += 2;
    *(DWORD *)p = 0;          p += 2;
    *p++ = nctl;
    *p++ = 40; *p++ = 40; *p++ = cx; *p++ = cy;
    *p++ = 0;                            /* no menu */
    *p++ = 0;                            /* default class */
    p = tpl_str(p, title);
    *p++ = 8;                            /* font size, DS_SETFONT */
    p = tpl_str(p, "MS Sans Serif");
    return p;
}

#define DLG_STYLE (DS_MODALFRAME | DS_SETFONT | WS_POPUP | WS_CAPTION | WS_SYSMENU)
#define CLS_BUTTON 0x0080
#define CLS_EDIT   0x0081
#define CLS_STATIC 0x0082

static INT_PTR CALLBACK connect_proc(HWND dlg, UINT msg, WPARAM wp, LPARAM lp)
{
    char buf[128];

    switch (msg) {
    case WM_INITDIALOG:
        SetDlgItemTextA(dlg, ID_IP, g.ip);
        wsprintfA(buf, "%d", g.port);
        SetDlgItemTextA(dlg, ID_PORT, buf);
        SetFocus(GetDlgItem(dlg, ID_IP));
        return FALSE;

    case WM_COMMAND:
        switch (LOWORD(wp)) {
        case ID_HOST:
        case ID_JOIN:
            GetDlgItemTextA(dlg, ID_IP, g.ip, sizeof(g.ip));
            GetDlgItemTextA(dlg, ID_PORT, buf, sizeof(buf));
            g.port = atoi(buf);
            if (g.port <= 0 || g.port > 65535)
                g.port = DEFAULT_PORT;
            if (LOWORD(wp) == ID_JOIN && g.ip[0] == 0) {
                MessageBoxA(dlg, "Enter the host's address.", "Message", 0);
                return TRUE;
            }
            EndDialog(dlg, LOWORD(wp) == ID_HOST ? 1 : 2);
            return TRUE;
        case IDCANCEL:
            EndDialog(dlg, 0);
            return TRUE;
        }
        break;
    }
    return FALSE;
}

static int ask_connection(HINSTANCE inst)
{
    WORD buf[512], *p;

    p = tpl_head(buf, DLG_STYLE, 200, 96, 7, "Virtual-On Netplay");
    p = tpl_ctl(buf, p, WS_CHILD | WS_VISIBLE, 8, 10, 70, 10,
                0xFFFF, CLS_STATIC, "Host address:");
    p = tpl_ctl(buf, p, WS_CHILD | WS_VISIBLE | WS_BORDER | WS_TABSTOP,
                80, 8, 110, 12, ID_IP, CLS_EDIT, "");
    p = tpl_ctl(buf, p, WS_CHILD | WS_VISIBLE, 8, 30, 70, 10,
                0xFFFF, CLS_STATIC, "Port:");
    p = tpl_ctl(buf, p, WS_CHILD | WS_VISIBLE | WS_BORDER | WS_TABSTOP,
                80, 28, 46, 12, ID_PORT, CLS_EDIT, "");
    p = tpl_ctl(buf, p, WS_CHILD | WS_VISIBLE | WS_TABSTOP | BS_DEFPUSHBUTTON,
                8, 60, 58, 14, ID_HOST, CLS_BUTTON, "Host game");
    p = tpl_ctl(buf, p, WS_CHILD | WS_VISIBLE | WS_TABSTOP,
                72, 60, 58, 14, ID_JOIN, CLS_BUTTON, "Connect");
    p = tpl_ctl(buf, p, WS_CHILD | WS_VISIBLE | WS_TABSTOP,
                136, 60, 54, 14, IDCANCEL, CLS_BUTTON, "Cancel");

    return (int)DialogBoxIndirectParamA(inst, (LPCDLGTEMPLATEA)buf,
                                        g.hwnd, connect_proc, 0);
}

/* One tick of the handshake, driven by the wait dialog's timer. */
static void handshake_tick(void)
{
    unsigned char b[1 + sizeof(g.tag)];
    DWORD now = timeGetTime();

    if (!g.host && now - g.hs_last >= 500) {
        b[0] = P_HELLO;
        memcpy(b + 1, g.tag, sizeof(g.tag));
        send_raw(b, sizeof(b));
        g.hs_last = now;
    }

    for (;;) {
        struct sockaddr_in from;
        int fromlen = sizeof(from);
        int n = recvfrom(g.sock, (char *)g.pkt, sizeof(g.pkt), 0,
                         (struct sockaddr *)&from, &fromlen);
        if (n <= 0)
            break;

        if (g.host && n == 1 + sizeof(g.tag) && g.pkt[0] == P_HELLO &&
            !memcmp(g.pkt + 1, g.tag, sizeof(g.tag))) {
            g.peer = from;                       /* learn the guest's address */
            b[0] = P_ACK;
            memcpy(b + 1, g.tag, sizeof(g.tag));
            send_raw(b, sizeof(b));
            g.hs_done = 1;
            return;
        }
        if (!g.host && n == 1 + sizeof(g.tag) && g.pkt[0] == P_ACK &&
            !memcmp(g.pkt + 1, g.tag, sizeof(g.tag))) {
            g.hs_done = 1;
            return;
        }
    }

    if (now - g.hs_start >= HANDSHAKE_MS)
        g.hs_failed = 1;
}

static INT_PTR CALLBACK wait_proc(HWND dlg, UINT msg, WPARAM wp, LPARAM lp)
{
    switch (msg) {
    case WM_INITDIALOG:
        SetDlgItemTextA(dlg, ID_STATUS,
                        g.host ? "Waiting for the other player..."
                               : "Connecting...");
        g.hs_start = g.hs_last = timeGetTime();
        g.hs_done = g.hs_failed = 0;
        SetTimer(dlg, 1, 50, NULL);
        return TRUE;

    case WM_TIMER:
        handshake_tick();
        if (g.hs_done || g.hs_failed) {
            KillTimer(dlg, 1);
            EndDialog(dlg, g.hs_done ? 1 : 0);
        }
        return TRUE;

    case WM_COMMAND:
        if (LOWORD(wp) == IDCANCEL) {
            KillTimer(dlg, 1);
            EndDialog(dlg, 0);
            return TRUE;
        }
        break;
    }
    return FALSE;
}

static int wait_for_peer(HINSTANCE inst)
{
    WORD buf[256], *p;

    p = tpl_head(buf, DLG_STYLE, 180, 60, 2, "Virtual-On Netplay");
    p = tpl_ctl(buf, p, WS_CHILD | WS_VISIBLE, 10, 14, 160, 10,
                ID_STATUS, CLS_STATIC, "");
    p = tpl_ctl(buf, p, WS_CHILD | WS_VISIBLE | WS_TABSTOP,
                64, 36, 52, 14, IDCANCEL, CLS_BUTTON, "Cancel");

    return (int)DialogBoxIndirectParamA(inst, (LPCDLGTEMPLATEA)buf,
                                        g.hwnd, wait_proc, 0);
}

/* ------------------------------------------------------------------ */
/* input delay negotiation                                             */
/* ------------------------------------------------------------------ */

static int negotiate_delay(void)
{
    DWORD start, sent;
    int i;

    if (g.host) {
        DWORD total = 0;
        int samples = 0;

        for (i = 0; i < PING_ROUNDS; i++) {
            sent = timeGetTime();
            send_ctl(P_PING, 0);
            for (;;) {
                int r = poll_recv();
                if (r < 0)
                    return 0;
                if (r > 0) {
                    if (handle_packet() == R_PONG) {
                        if (i > 0) {          /* first round is discarded */
                            total += timeGetTime() - sent;
                            samples++;
                        }
                        break;
                    }
                    continue;
                }
                if (timeGetTime() - sent >= RESEND_MS) {
                    if (timeGetTime() - sent >= NEGOTIATE_MS)
                        return 0;
                    send_ctl(P_PING, 0);
                    sent = timeGetTime();
                }
            }
        }

        /* The original averages eight round trips, divides by 32 and adds one. */
        g.delay = samples ? (int)(total / samples) / 32 + 1 : 1;
        if (g.delay < 1)
            g.delay = 1;
        if (g.delay > 16)
            g.delay = 16;
        send_ctl(P_DELAY, (DWORD)g.delay);
    } else {
        start = sent = timeGetTime();
        for (;;) {
            int r = poll_recv();
            if (r < 0)
                return 0;
            if (r > 0) {
                if (handle_packet() == R_DELAY) {
                    g.delay = (int)(*(DWORD *)(g.pkt + 4));
                    if (g.delay < 1)
                        g.delay = 1;
                    if (g.delay > 16)
                        g.delay = 16;
                    break;
                }
                continue;
            }
            if (timeGetTime() - sent >= RESEND_MS) {
                send_ctl(P_POLL, 0);
                sent = timeGetTime();
            }
            if (timeGetTime() - start >= NEGOTIATE_MS)
                return 0;
        }
    }

    g.prime = g.delay;
    return 1;
}

/* ------------------------------------------------------------------ */
/* exports                                                             */
/* ------------------------------------------------------------------ */

int __stdcall InitialDirectPlay(VO_NETINIT *init)
{
    WSADATA wsa;
    struct sockaddr_in local;
    unsigned long nb = 1;
    int choice;
    HINSTANCE inst = GetModuleHandleA("DPCTRL.DLL");

    if (g.up)
        return 0;

    memset(&g, 0, sizeof(g));
    g.hwnd       = init->hwnd;
    g.ddraw      = init->ddraw;
    g.framelen   = init->datasize + 2;
    g.timeout_ms = (DWORD)init->timeout_sec * 1000;
    g.port       = DEFAULT_PORT;

    if (g.framelen < 3 || g.framelen > MAXPKT)
        return 0;

    /* Session identity: the game's eight bytes plus the original's marker. */
    memcpy(g.tag, init->session, 8);
    memcpy(g.tag + 8, "SEGA PC ", 8);

    choice = ask_connection(inst);
    if (choice == 0)
        return 0;
    g.host = (choice == 1);

    if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0)
        return 0;

    g.sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    if (g.sock == INVALID_SOCKET) {
        WSACleanup();
        return 0;
    }
    ioctlsocket(g.sock, FIONBIO, &nb);
    g.up = 1;

    memset(&local, 0, sizeof(local));
    local.sin_family = AF_INET;
    local.sin_addr.s_addr = INADDR_ANY;
    local.sin_port = htons((u_short)(g.host ? g.port : 0));
    if (bind(g.sock, (struct sockaddr *)&local, sizeof(local)) != 0) {
        notice("Could not open the network port.");
        sock_close();
        return 0;
    }

    memset(&g.peer, 0, sizeof(g.peer));
    g.peer.sin_family = AF_INET;
    g.peer.sin_port = htons((u_short)g.port);

    if (!g.host) {
        unsigned long addr = inet_addr(g.ip);
        if (addr == INADDR_NONE) {
            struct hostent *he = gethostbyname(g.ip);
            if (!he) {
                notice("Could not resolve that address.");
                sock_close();
                return 0;
            }
            memcpy(&addr, he->h_addr, sizeof(addr));
        }
        g.peer.sin_addr.s_addr = addr;
    }

    if (!wait_for_peer(inst)) {
        sock_close();
        return 0;
    }
    g.linked = 1;

    if (!negotiate_delay()) {
        notice("Now Network interrupted.");
        sock_close();
        return 0;
    }

    *init->out_player1 = g.host ? 1 : 0;
    return 1;
}

int __stdcall DestroyDirectPlay(HWND hwnd)
{
    if (g.up) {
        if (g.linked) {
            send_ctl(P_QUIT, 0);
            send_ctl(P_QUIT, 0);   /* unreliable, so say it twice */
        }
        sock_close();
    }
    return 1;
}

int __stdcall CloseProvider(void)
{
    if (!g.up)
        return 0;
    sock_close();
    return 1;
}

int __stdcall SendDirectPlay(void *buf, int len)
{
    if (!g.up || len <= 0 || len > MAXPKT)
        return 0;
    send_raw(buf, len);
    return 1;
}

int __stdcall SendDirectPlayWaitMessage(DWORD cmd)
{
    send_ctl(P_CMD, cmd);
    return 1;
}

int __stdcall ReceiveDirectPlay(HWND hwnd, void *buf)
{
    unsigned char *out = (unsigned char *)buf;
    int len, i;

    for (;;) {
        int r = poll_recv();
        if (r < 0)
            return 0;
        if (r == 0)
            break;
        if (handle_packet() == R_PEERQUIT) {
            notice("Your challenger has stopped playing Netplay.");
            return 0;
        }
    }

    if (g.msg_tail == g.msg_head)
        return 0;

    len = g.msg[g.msg_tail];
    g.msg_tail = (g.msg_tail + 1) & MSGRING_MASK;
    for (i = 0; i < len; i++) {
        out[i] = g.msg[g.msg_tail];
        g.msg_tail = (g.msg_tail + 1) & MSGRING_MASK;
    }
    return len;
}

int __stdcall SWDataSendReceive(unsigned char *p1, unsigned char *p2)
{
    unsigned char *local  = g.host ? p1 : p2;
    unsigned char *remote = g.host ? p2 : p1;
    unsigned char *slot;
    DWORD idle_since = 0, last_resend = 0;
    int relaying = 0;
    int i;

    if (!g.up)
        return 0;

    /* First frame primes the pipeline so the send sequence runs `delay`
       ahead of the read cursor. Every later frame sends exactly once. */
    while (g.prime > 0) {
        send_frame(local);
        g.prime--;
    }
    send_frame(local);

    for (;;) {
        int r = poll_recv();

        if (r < 0)
            return 0;

        if (r > 0) {
            int what = handle_packet();
            if (what == R_PEERQUIT) {
                notice("Your challenger has stopped playing Netplay.");
                return 1;
            }
            if (what == R_CMD) {
                /* Peer opened a menu. Pump our loop so ours opens too. */
                relaying = 1;
                PostMessageA(g.hwnd, WM_COMMAND, *(DWORD *)(g.pkt + 4), 0);
            }
        } else if (relaying) {
            MSG m;
            if (PeekMessageA(&m, NULL, 0, 0, PM_REMOVE)) {
                if (m.message == WM_QUIT)
                    return 1;
                TranslateMessage(&m);
                DispatchMessageA(&m);
            }
        } else {
            DWORD now = timeGetTime();
            if (idle_since == 0)
                idle_since = last_resend = now;
            if (now - last_resend >= RESEND_MS) {
                send_ctl(P_RESEND, (DWORD)(g.cursor & RING_MASK));
                last_resend = now;
            }
            if (now - idle_since >= g.timeout_ms) {
                DestroyDirectPlay(g.hwnd);
                notice("Now Network interrupted.");
                return 0;
            }
        }

        slot = g.rx + (g.cursor & RING_MASK) * MAXPKT;
        if (slot[0] == P_DATA)
            break;
    }

    /* Both sides are handed the same delayed pair: the peer's frame from the
       receive ring, and our own frame as it was actually sent. */
    slot = g.rx + (g.cursor & RING_MASK) * MAXPKT;
    for (i = 2; i < g.framelen; i++)
        remote[i - 2] = slot[i];

    slot = g.tx + (g.cursor & RING_MASK) * MAXPKT;
    for (i = 2; i < g.framelen; i++)
        local[i - 2] = slot[i];

    /* Retire the slot half a window behind, as the original does. */
    {
        int old = (g.cursor - RING / 2) & RING_MASK;
        g.rx[old * MAXPKT] = 0;
        g.tx[old * MAXPKT] = 0;
    }
    g.cursor = (g.cursor + 1) & 0xFFFF;

    return 1;
}

BOOL WINAPI DllMain(HINSTANCE inst, DWORD reason, LPVOID reserved)
{
    if (reason == DLL_PROCESS_DETACH)
        sock_close();
    return TRUE;
}
