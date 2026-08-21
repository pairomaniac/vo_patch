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
#include <stdarg.h>
#include <string.h>

#define DEFAULT_PORT     47624   /* same number DirectPlay used, for firewall parity */
#define RING             64      /* sequence window, seq is 6 bits */
#define RING_MASK        (RING - 1)
#define MSGRING          0x2000  /* byte-stream ring for SendDirectPlay traffic */
#define MSGRING_MASK     (MSGRING - 1)
#define MAXPKT           512
#define RESEND_MS        1000
#define BEAT_MS          250     /* idle heartbeat, so silence means silence  */
#define SILENCE_MS       3000    /* nothing at all from the peer -> link dead */

/* The wait for the peer's frame used to be a bare spin on recvfrom, which
   holds a core flat out for most of every frame. select() sleeps until the
   packet lands, so it wakes just as fast but leaves the scheduler alone.
   Build with -DVO_YIELD=0 to get the old spin back for comparison. */
#ifndef VO_YIELD
#define VO_YIELD 1
#endif
/* No handshake timeout: the wait dialog has a Cancel button, and a host
   waiting for a friend to alt-tab back has no business being disconnected. */
#define NEGOTIATE_MS     10000
#define PING_ROUNDS      9

/* Matchcode rendezvous (net/rendezvous.py). The host gets a code from the
   server, the guest types it, and the server hands each side the other's
   public endpoint so both NATs open without a forwarded port. If nothing
   gets through within PUNCH_MS, game traffic goes through the server. */
#define MATCH_SERVER_EU  "eu.segaonline.net"
#define MATCH_SERVER_US  "us.segaonline.net"
#define MATCH_PORT       47625
#define MATCH_MAGIC      "VOR1"
#define MATCH_RETRY_MS   1000
#define PUNCH_MS         4000
#define CODE_LEN         5
#define RELAY_HDR        (5 + CODE_LEN)   /* "VOR1" 'R' code */

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
#define P_PUNCH  0x83   /* [type]  opens our NAT mapping, ignored on receipt */

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
    int    msg_head, msg_tail, msg_used;
    int    msg_dropped;

    unsigned char pkt[MAXPKT + RELAY_HDR];   /* last datagram received */
    int    pktlen;

    /* handshake dialog state */
    int    hs_done, hs_failed;
    DWORD  hs_last;

    DWORD  last_rx;        /* last packet accepted from the peer */
    DWORD  last_tx;        /* last packet we put on the wire     */
    int    peer_quit;      /* peer said goodbye, cleanly         */

    char   ip[128];
    int    port;
    char   lan[192];       /* this machine's LAN addresses               */
    char   pub[64];        /* public address, only looked up on request  */
    int    pub_shown;

    /* matchcode */
    int    match;          /* go through the rendezvous server           */
    int    region;         /* 0 = EU, 1 = US                             */
    struct sockaddr_in rv; /* the server                                 */
    char   code[CODE_LEN + 1];
    int    rv_peer;        /* server has told us the peer's endpoint     */
    int    relay;          /* direct path failed, server forwards for us */
    DWORD  rv_last, punch_start;
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
    if (!g.up)
        return;
    if (g.relay) {
        unsigned char b[MAXPKT + RELAY_HDR];
        memcpy(b, MATCH_MAGIC, 4);
        b[4] = 'R';
        memcpy(b + 5, g.code, CODE_LEN);
        memcpy(b + RELAY_HDR, buf, len);
        sendto(g.sock, (const char *)b, RELAY_HDR + len, 0,
               (struct sockaddr *)&g.rv, sizeof(g.rv));
    } else {
        sendto(g.sock, (const char *)buf, len, 0,
               (struct sockaddr *)&g.peer, sizeof(g.peer));
    }
    g.last_tx = timeGetTime();
}

static int from_server(const struct sockaddr_in *a)
{
    return a->sin_addr.s_addr == g.rv.sin_addr.s_addr &&
           a->sin_port == g.rv.sin_port;
}

/* A relayed datagram is "VOR1" 'D' payload. Strips the wrapper in place
   and returns the payload length, or -1 if this was not one. */
static int unwrap_relay(unsigned char *p, int n)
{
    if (n < 5 || memcmp(p, MATCH_MAGIC, 4) || p[4] != 'D')
        return -1;
    memmove(p, p + 5, n - 5);
    return n - 5;
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

    /* Ignore anything that is not our peer, or the server when relaying. */
    if (g.relay) {
        if (!from_server(&from) || (n = unwrap_relay(g.pkt, n)) <= 0)
            return 0;
    } else if (g.linked && from.sin_addr.s_addr != g.peer.sin_addr.s_addr) {
        return 0;
    }

    g.pktlen = n;
    g.last_rx = timeGetTime();
    return 1;
}

/* Keep something on the wire while idle, so a gap really means the peer is
   gone rather than merely quiet - during a relayed menu neither side sends
   frames, and without this that looks identical to a crash. */
static void heartbeat(void)
{
    if (g.linked && timeGetTime() - g.last_tx >= BEAT_MS)
        send_ctl(P_POLL, 0);
}

/* True once the peer has been silent long enough to call it dead. Frames
   going missing is a separate, far more patient case: the peer is still
   audible, so the resend path handles it on the original's timings. */
static int link_silent(void)
{
    return g.linked && (timeGetTime() - g.last_rx >= SILENCE_MS);
}

static void netlog(const char *fmt, ...);

/* Sleep until the socket has something or the timeout expires. Returns
   straight away if a packet is already waiting. */
static void wait_readable(int ms)
{
#if VO_YIELD
    struct timeval tv;
    fd_set rd;

    if (!g.up)
        return;
    FD_ZERO(&rd);
    FD_SET(g.sock, &rd);
    tv.tv_sec = 0;
    tv.tv_usec = ms * 1000;
    select(0, &rd, NULL, NULL, &tv);
#else
    (void)ms;
#endif
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
        g.linked = 1;          /* the original sets its link flag here */
        return R_NONE;
    case P_DELAY:
        return R_DELAY;
    case P_CMD:
        return R_CMD;
    /* Our own types are length-checked as well as value-checked. The game
       sends 21-byte messages of an unknown type through SendDirectPlay, and
       one of those must never be mistaken for ours and swallowed. */
    case P_QUIT:
        if (g.pktlen == 1) {
            g.peer_quit = 1;
            return R_PEERQUIT;
        }
        goto queue;
    case P_HELLO:
        if (g.pktlen == 1 + (int)sizeof(g.tag) &&
            !memcmp(g.pkt + 1, g.tag, sizeof(g.tag))) {
            unsigned char b[1 + sizeof(g.tag)];   /* re-ack a late hello */
            b[0] = P_ACK;
            memcpy(b + 1, g.tag, sizeof(g.tag));
            send_raw(b, sizeof(b));
            return R_NONE;
        }
        goto queue;
    case P_ACK:
        if (g.pktlen == 1 + (int)sizeof(g.tag))
            return R_NONE;
        goto queue;
    case P_PUNCH:
        if (g.pktlen == 1)
            return R_NONE;
        goto queue;
    default:
        if (g.pktlen >= 5 && !memcmp(g.pkt, MATCH_MAGIC, 4))
            return R_NONE;   /* a late rendezvous reply */
    queue:
        /* Anything else is a game message, queued for ReceiveDirectPlay.
           The length goes in one byte, and a full ring is dropped rather
           than allowed to lap the reader: a wrapped head puts a mid-message
           byte where a length belongs, and everything after that is
           garbage the game acts on. */
        if (g.pktlen > 255 || 1 + g.pktlen > MSGRING - g.msg_used) {
            g.msg_dropped++;
            netlog("message dropped: %d bytes, %d free of %d",
                   g.pktlen, MSGRING - g.msg_used, MSGRING);
            return R_NONE;
        }
        g.msg[g.msg_head] = (unsigned char)g.pktlen;
        g.msg_head = (g.msg_head + 1) & MSGRING_MASK;
        for (i = 0; i < g.pktlen; i++) {
            g.msg[g.msg_head] = g.pkt[i];
            g.msg_head = (g.msg_head + 1) & MSGRING_MASK;
        }
        g.msg_used += 1 + g.pktlen;
        return R_NONE;
    }
}

/* ------------------------------------------------------------------ */
/* connection dialogs, built in memory so the DLL needs no resources    */
/* ------------------------------------------------------------------ */

#define ID_IP      0x3E8
#define ID_PORT    0x3E9
#define ID_HOST    0x3EA    /* radio: host a game */
#define ID_JOIN    0x3EB    /* radio: join a game */
#define ID_STATUS  0x3EC
#define ID_LOCAL   0x3ED    /* shows this machine's address when hosting */
#define ID_IPLABEL 0x3EE
#define ID_PUBBTN  0x3EF    /* toggles the public address on and off */
#define ID_PUBTEXT 0x3F0
#define ID_COPYBTN 0x3F1
#define ID_MATCH   0x3F2    /* radio: matchcode            */
#define ID_DIRECT  0x3F3    /* radio: direct IP            */
#define ID_EU      0x3F4    /* radio: region               */
#define ID_US      0x3F5
#define ID_REGLBL  0x3F6
#define ID_PORTLBL 0x3F7

#define STUN_HOST1 "stun.l.google.com"
#define STUN_HOST2 "stun1.l.google.com"
#define STUN_PORT  19302
#define STUN_WAIT  1500     /* total ms to wait before giving up */

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

/* Writes to vo-net.log beside the game, but only if that file already
   exists - create it to switch logging on, delete it to switch it off. */
static void netlog(const char *fmt, ...)
{
    static int checked, on;
    FILE *f;
    va_list ap;

    if (!checked) {
        checked = 1;
        f = fopen("vo-net.log", "r");
        if (f) { on = 1; fclose(f); }
    }
    if (!on)
        return;
    f = fopen("vo-net.log", "a");
    if (!f)
        return;
    va_start(ap, fmt);
    vfprintf(f, fmt, ap);
    va_end(ap);
    fputc('\n', f);
    fclose(f);
}

/* Ask a STUN server what address the outside world sees us as. The mapped
   port is deliberately ignored: it describes the NAT's outbound mapping,
   not whether an inbound forward exists, so showing it would mislead.
   Best effort - any failure just means the address is not shown. */
static int stun_query(const char *server, char *out, int outsz)
{
    unsigned char req[20], resp[512];
    struct sockaddr_in sa, from;
    struct hostent *he;
    SOCKET s2;
    unsigned long nb = 1;
    DWORD start;
    int fromlen, n, i;

    he = gethostbyname(server);
    if (!he || he->h_addrtype != AF_INET)
        return 0;

    s2 = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    if (s2 == INVALID_SOCKET)
        return 0;
    ioctlsocket(s2, FIONBIO, &nb);

    /* Binding request: type 0x0001, no attributes, magic cookie, then a
       transaction id we do not bother to randomise. */
    memset(req, 0, sizeof(req));
    req[1] = 0x01;
    req[4] = 0x21; req[5] = 0x12; req[6] = 0xA4; req[7] = 0x42;
    for (i = 8; i < 20; i++)
        req[i] = (unsigned char)(i * 7 + 3);

    memset(&sa, 0, sizeof(sa));
    sa.sin_family = AF_INET;
    sa.sin_port = htons((u_short)STUN_PORT);
    memcpy(&sa.sin_addr, he->h_addr, sizeof(sa.sin_addr));

    start = timeGetTime();
    sendto(s2, (const char *)req, sizeof(req), 0,
           (struct sockaddr *)&sa, sizeof(sa));

    for (;;) {
        if (timeGetTime() - start >= STUN_WAIT)
            break;

        fromlen = sizeof(from);
        n = recvfrom(s2, (char *)resp, sizeof(resp), 0,
                     (struct sockaddr *)&from, &fromlen);
        if (n < 20) {
            Sleep(20);
            if ((timeGetTime() - start) % 500 < 25)
                sendto(s2, (const char *)req, sizeof(req), 0,
                       (struct sockaddr *)&sa, sizeof(sa));
            continue;
        }
        if (resp[0] != 0x01 || resp[1] != 0x01)     /* binding response */
            continue;

        /* Walk the attributes for XOR-MAPPED-ADDRESS (0x0020), falling back
           to the older MAPPED-ADDRESS (0x0001). */
        i = 20;
        while (i + 4 <= n) {
            int atype = (resp[i] << 8) | resp[i + 1];
            int alen  = (resp[i + 2] << 8) | resp[i + 3];
            unsigned char *v = resp + i + 4;

            if (i + 4 + alen > n)
                break;
            if ((atype == 0x0020 || atype == 0x0001) && alen >= 8 &&
                v[1] == 0x01) {
                unsigned char ip[4];
                memcpy(ip, v + 4, 4);
                if (atype == 0x0020) {
                    ip[0] ^= 0x21; ip[1] ^= 0x12;
                    ip[2] ^= 0xA4; ip[3] ^= 0x42;
                }
                wsprintfA(out, "%d.%d.%d.%d", ip[0], ip[1], ip[2], ip[3]);
                closesocket(s2);
                return 1;
            }
            i += 4 + ((alen + 3) & ~3);
        }
    }

    closesocket(s2);
    return 0;
}

static int public_address(char *out, int outsz)
{
    if (stun_query(STUN_HOST1, out, outsz))
        return 1;
    return stun_query(STUN_HOST2, out, outsz);
}

static void copy_to_clipboard(HWND owner, const char *text)
{
    HGLOBAL h;
    char *p;
    int n;

    if (!text || !text[0] || !OpenClipboard(owner))
        return;
    n = lstrlenA(text) + 1;
    h = GlobalAlloc(GMEM_MOVEABLE, n);
    if (h) {
        p = (char *)GlobalLock(h);
        if (p) {
            memcpy(p, text, n);
            GlobalUnlock(h);
            EmptyClipboard();
            SetClipboardData(CF_TEXT, h);   /* clipboard owns it now */
        } else {
            GlobalFree(h);
        }
    }
    CloseClipboard();
}

/* This machine's IPv4 addresses, for reading out to the other player. */
static void local_addresses(char *out, int outsz)
{
    char host[128];
    struct hostent *he;
    int n = 0;

    out[0] = 0;
    if (gethostname(host, sizeof(host)) != 0)
        return;
    he = gethostbyname(host);
    if (!he || he->h_addrtype != AF_INET)
        return;

    while (he->h_addr_list[n]) {
        struct in_addr a;
        char *t;
        memcpy(&a, he->h_addr_list[n], sizeof(a));
        t = inet_ntoa(a);
        if (t && strcmp(t, "127.0.0.1") != 0) {
            if (out[0] && (int)(strlen(out) + strlen(t) + 3) < outsz)
                strcat(out, ", ");
            if ((int)(strlen(out) + strlen(t) + 1) < outsz)
                strcat(out, t);
        }
        n++;
    }
}

/* Show and enable only what the current mode needs. */
static void set_mode(HWND dlg)
{
    int hosting = IsDlgButtonChecked(dlg, ID_HOST) == BST_CHECKED;
    int match   = IsDlgButtonChecked(dlg, ID_MATCH) == BST_CHECKED;
    int addr_sw = (hosting && !match) ? SW_SHOW : SW_HIDE;

    /* Region is the host's choice; the guest's code carries it. */
    EnableWindow(GetDlgItem(dlg, ID_REGLBL), match && hosting);
    EnableWindow(GetDlgItem(dlg, ID_EU), match && hosting);
    EnableWindow(GetDlgItem(dlg, ID_US), match && hosting);
    EnableWindow(GetDlgItem(dlg, ID_PORTLBL), !match);
    EnableWindow(GetDlgItem(dlg, ID_PORT), !match);

    ShowWindow(GetDlgItem(dlg, ID_LOCAL), addr_sw);
    ShowWindow(GetDlgItem(dlg, ID_PUBBTN), addr_sw);
    ShowWindow(GetDlgItem(dlg, ID_PUBTEXT), addr_sw);
    ShowWindow(GetDlgItem(dlg, ID_COPYBTN), addr_sw);

    SetDlgItemTextA(dlg, ID_IPLABEL, match ? "Code:" : "Host address:");
    EnableWindow(GetDlgItem(dlg, ID_IPLABEL), !hosting);
    EnableWindow(GetDlgItem(dlg, ID_IP), !hosting);
    if (!hosting)
        SetFocus(GetDlgItem(dlg, ID_IP));
}

static int resolve_server(int region, struct sockaddr_in *out)
{
    const char *host = region ? MATCH_SERVER_US : MATCH_SERVER_EU;
    struct hostent *he = gethostbyname(host);

    if (!he)
        return 0;
    memset(out, 0, sizeof(*out));
    out->sin_family = AF_INET;
    memcpy(&out->sin_addr, he->h_addr, sizeof(out->sin_addr));
    out->sin_port = htons((u_short)MATCH_PORT);
    return 1;
}

/* "E-ABCDE", "eabcde" -> region and bare code. 0 if malformed. */
static int parse_code(const char *text, int *region, char *code)
{
    char t[32];
    int i, n = 0;

    for (i = 0; text[i] && n < (int)sizeof(t) - 1; i++) {
        char c = text[i];
        if (c == ' ' || c == '-')
            continue;
        if (c >= 'a' && c <= 'z')
            c -= 'a' - 'A';
        t[n++] = c;
    }
    t[n] = 0;
    if (n != CODE_LEN + 1 || (t[0] != 'E' && t[0] != 'U'))
        return 0;
    *region = (t[0] == 'U');
    memcpy(code, t + 1, CODE_LEN);
    code[CODE_LEN] = 0;
    return 1;
}

static INT_PTR CALLBACK connect_proc(HWND dlg, UINT msg, WPARAM wp, LPARAM lp)
{
    char buf[256];

    switch (msg) {
    case WM_INITDIALOG:
        SetDlgItemTextA(dlg, ID_IP, g.ip);
        wsprintfA(buf, "%d", g.port);
        SetDlgItemTextA(dlg, ID_PORT, buf);

        local_addresses(g.lan, sizeof(g.lan));
        if (g.lan[0])
            wsprintfA(buf, "This machine: %s", g.lan);
        else
            lstrcpyA(buf, "This machine: address not found");
        SetDlgItemTextA(dlg, ID_LOCAL, buf);

        SetDlgItemTextA(dlg, ID_PUBTEXT, "");
        SetDlgItemTextA(dlg, ID_PUBBTN, "Show public address");
        g.pub_shown = 0;

        CheckRadioButton(dlg, ID_MATCH, ID_DIRECT, ID_MATCH);
        CheckRadioButton(dlg, ID_EU, ID_US, ID_EU);
        CheckRadioButton(dlg, ID_HOST, ID_JOIN, ID_HOST);
        set_mode(dlg);
        return TRUE;

    case WM_COMMAND:
        switch (LOWORD(wp)) {
        case ID_HOST: case ID_JOIN: case ID_MATCH: case ID_DIRECT:
            set_mode(dlg);
            return TRUE;

        /* Off by default, and one click hides it again: the public address
           should not sit on screen while someone is streaming. */
        case ID_PUBBTN:
            if (g.pub_shown) {
                SetDlgItemTextA(dlg, ID_PUBTEXT, "");
                SetDlgItemTextA(dlg, ID_PUBBTN, "Show public address");
                g.pub_shown = 0;
                return TRUE;
            }
            SetDlgItemTextA(dlg, ID_PUBTEXT, "looking up...");
            EnableWindow(GetDlgItem(dlg, ID_PUBBTN), FALSE);
            UpdateWindow(dlg);
            if (!public_address(g.pub, sizeof(g.pub)))
                lstrcpyA(g.pub, "not available");
            SetDlgItemTextA(dlg, ID_PUBTEXT, g.pub);
            SetDlgItemTextA(dlg, ID_PUBBTN, "Hide");
            EnableWindow(GetDlgItem(dlg, ID_PUBBTN), TRUE);
            g.pub_shown = 1;
            return TRUE;

        /* Copies whatever is currently shown - the public address if it has
           been revealed, otherwise the LAN one. */
        case ID_COPYBTN:
            copy_to_clipboard(dlg,
                (g.pub_shown && g.pub[0] &&
                 lstrcmpA(g.pub, "not available") != 0) ? g.pub : g.lan);
            return TRUE;

        case IDOK: {
            int hosting = IsDlgButtonChecked(dlg, ID_HOST) == BST_CHECKED;
            g.match  = IsDlgButtonChecked(dlg, ID_MATCH) == BST_CHECKED;
            g.region = IsDlgButtonChecked(dlg, ID_US) == BST_CHECKED;

            GetDlgItemTextA(dlg, ID_PORT, buf, sizeof(buf));
            g.port = atoi(buf);
            if (g.port <= 0 || g.port > 65535) {
                MessageBoxA(dlg, "Port must be between 1 and 65535.",
                            "Message", 0);
                SetFocus(GetDlgItem(dlg, ID_PORT));
                return TRUE;
            }
            g.ip[0] = 0;
            if (!hosting) {
                GetDlgItemTextA(dlg, ID_IP, g.ip, sizeof(g.ip));
                if (g.ip[0] == 0) {
                    MessageBoxA(dlg, g.match ? "Enter the host's code."
                                             : "Enter the host's address.",
                                "Message", 0);
                    SetFocus(GetDlgItem(dlg, ID_IP));
                    return TRUE;
                }
                if (g.match && !parse_code(g.ip, &g.region, g.code)) {
                    MessageBoxA(dlg, "A code looks like E-ABCDE.",
                                "Message", 0);
                    SetFocus(GetDlgItem(dlg, ID_IP));
                    return TRUE;
                }
            }
            if (g.match && !resolve_server(g.region, &g.rv)) {
                MessageBoxA(dlg, "Could not reach the matchcode server.",
                            "Message", 0);
                return TRUE;
            }
            EndDialog(dlg, hosting ? 1 : 2);
            return TRUE;
        }

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
    WORD buf[1024], *p;

    /* Radio groups run from one WS_GROUP to the next, so each group is
       closed by the static that follows it. The count in the header must
       match the number of controls below: a short count silently drops
       the tail of the dialog. */
    p = tpl_head(buf, DLG_STYLE, 250, 190, 19, "Virtual-On Netplay");

    p = tpl_ctl(buf, p, WS_CHILD | WS_VISIBLE | BS_GROUPBOX,
                6, 4, 238, 46, 0xFFFF, CLS_BUTTON, "Connection");
    p = tpl_ctl(buf, p, WS_CHILD | WS_VISIBLE | WS_GROUP | WS_TABSTOP |
                BS_AUTORADIOBUTTON, 14, 16, 110, 12,
                ID_MATCH, CLS_BUTTON, "Matchcode (no forwarding)");
    p = tpl_ctl(buf, p, WS_CHILD | WS_VISIBLE | BS_AUTORADIOBUTTON,
                14, 30, 110, 12, ID_DIRECT, CLS_BUTTON, "Direct IP");
    p = tpl_ctl(buf, p, WS_CHILD | WS_VISIBLE | WS_GROUP, 126, 18, 30, 9,
                ID_REGLBL, CLS_STATIC, "Region:");
    p = tpl_ctl(buf, p, WS_CHILD | WS_VISIBLE | WS_GROUP | WS_TABSTOP |
                BS_AUTORADIOBUTTON, 158, 16, 40, 12,
                ID_EU, CLS_BUTTON, "Europe");
    p = tpl_ctl(buf, p, WS_CHILD | WS_VISIBLE | BS_AUTORADIOBUTTON,
                198, 16, 42, 12, ID_US, CLS_BUTTON, "America");
    p = tpl_ctl(buf, p, WS_CHILD | WS_VISIBLE | WS_GROUP, 126, 32, 28, 9,
                ID_PORTLBL, CLS_STATIC, "Port:");
    p = tpl_ctl(buf, p, WS_CHILD | WS_VISIBLE | WS_BORDER | WS_TABSTOP,
                158, 30, 46, 12, ID_PORT, CLS_EDIT, "");

    p = tpl_ctl(buf, p, WS_CHILD | WS_VISIBLE | BS_GROUPBOX,
                6, 56, 238, 104, 0xFFFF, CLS_BUTTON, "Match");
    p = tpl_ctl(buf, p, WS_CHILD | WS_VISIBLE | WS_GROUP | WS_TABSTOP |
                BS_AUTORADIOBUTTON, 14, 70, 100, 12,
                ID_HOST, CLS_BUTTON, "Host a game");
    p = tpl_ctl(buf, p, WS_CHILD | WS_VISIBLE, 24, 86, 210, 9,
                ID_LOCAL, CLS_STATIC, "");
    p = tpl_ctl(buf, p, WS_CHILD | WS_VISIBLE | WS_TABSTOP,
                24, 98, 86, 12, ID_PUBBTN, CLS_BUTTON, "Show public address");
    p = tpl_ctl(buf, p, WS_CHILD | WS_VISIBLE, 116, 100, 70, 9,
                ID_PUBTEXT, CLS_STATIC, "");
    p = tpl_ctl(buf, p, WS_CHILD | WS_VISIBLE | WS_TABSTOP,
                192, 98, 28, 12, ID_COPYBTN, CLS_BUTTON, "Copy");
    p = tpl_ctl(buf, p, WS_CHILD | WS_VISIBLE | BS_AUTORADIOBUTTON,
                14, 120, 100, 12, ID_JOIN, CLS_BUTTON, "Join a game");
    p = tpl_ctl(buf, p, WS_CHILD | WS_VISIBLE | WS_GROUP, 24, 136, 50, 9,
                ID_IPLABEL, CLS_STATIC, "");
    p = tpl_ctl(buf, p, WS_CHILD | WS_VISIBLE | WS_BORDER | WS_TABSTOP,
                78, 134, 156, 12, ID_IP, CLS_EDIT, "");

    p = tpl_ctl(buf, p, WS_CHILD | WS_VISIBLE | WS_GROUP | WS_TABSTOP |
                BS_DEFPUSHBUTTON, 134, 168, 52, 14, IDOK, CLS_BUTTON, "OK");
    p = tpl_ctl(buf, p, WS_CHILD | WS_VISIBLE | WS_TABSTOP,
                190, 168, 52, 14, IDCANCEL, CLS_BUTTON, "Cancel");

    return (int)DialogBoxIndirectParamA(inst, (LPCDLGTEMPLATEA)buf,
                                        g.hwnd, connect_proc, 0);
}

static void rv_send(const char *op, const char *code)
{
    char b[5 + CODE_LEN];
    int n = 5;

    memcpy(b, MATCH_MAGIC, 4);
    b[4] = op[0];
    if (code) {
        memcpy(b + 5, code, CODE_LEN);
        n += CODE_LEN;
    }
    sendto(g.sock, b, n, 0, (struct sockaddr *)&g.rv, sizeof(g.rv));
}

/* A reply from the rendezvous server. Returns 1 if it was one. */
static int rv_handle(const unsigned char *p, int n, HWND dlg)
{
    if (n < 5 || memcmp(p, MATCH_MAGIC, 4))
        return 0;

    switch (p[4]) {
    case 'K':                              /* our code */
        if (n >= 5 + CODE_LEN && g.host && !g.code[0]) {
            char buf[96];
            memcpy(g.code, p + 5, CODE_LEN);
            g.code[CODE_LEN] = 0;
            wsprintfA(buf, "Code: %c-%s    Waiting for the other player...",
                      g.region ? 'U' : 'E', g.code);
            SetDlgItemTextA(dlg, ID_STATUS, buf);
            netlog("matchcode %s", g.code);
        }
        break;
    case 'P':                              /* the peer's endpoint */
        if (n >= 11 && !g.rv_peer) {
            memset(&g.peer, 0, sizeof(g.peer));
            g.peer.sin_family = AF_INET;
            memcpy(&g.peer.sin_addr, p + 5, 4);
            g.peer.sin_port = htons((u_short)((p[9] << 8) | p[10]));
            g.rv_peer = 1;
            g.punch_start = timeGetTime();
            netlog("peer via rendezvous: %s:%d", inet_ntoa(g.peer.sin_addr),
                   ntohs(g.peer.sin_port));
            SetDlgItemTextA(dlg, ID_STATUS, "Connecting...");
        }
        break;
    case 'N':
        if (g.host)
            g.code[0] = 0;                 /* server forgot us: ask again */
        else
            g.hs_failed = 1;               /* no such code */
        break;
    }
    return 1;
}

/* One tick of the handshake, driven by the wait dialog's timer. */
static void handshake_tick(HWND dlg)
{
    unsigned char b[1 + sizeof(g.tag)];
    DWORD now = timeGetTime();

    /* Matchcode: talk to the server until it has given us a peer. Until
       then there is nowhere to send a hello. */
    if (g.match && now - g.rv_last >= MATCH_RETRY_MS) {
        if (g.host)
            rv_send(g.code[0] ? "H" : "C", g.code[0] ? g.code : NULL);
        else
            rv_send("J", g.code);
        g.rv_last = now;
    }
    if (g.match && !g.rv_peer)
        goto recv;

    /* The direct path had its chance. Send through the server from here. */
    if (g.match && !g.relay && now - g.punch_start >= PUNCH_MS) {
        g.relay = 1;
        netlog("no direct path, relaying");
        SetDlgItemTextA(dlg, ID_STATUS, "Connecting through the relay...");
    }

    if (now - g.hs_last >= 500) {
        if (!g.host) {
            b[0] = P_HELLO;
            memcpy(b + 1, g.tag, sizeof(g.tag));
            send_raw(b, sizeof(b));
        } else if (g.match) {
            b[0] = P_PUNCH;                /* opens our side of the NAT */
            send_raw(b, 1);
        }
        g.hs_last = now;
    }

recv:
    for (;;) {
        struct sockaddr_in from;
        int fromlen = sizeof(from);
        int n = recvfrom(g.sock, (char *)g.pkt, sizeof(g.pkt), 0,
                         (struct sockaddr *)&from, &fromlen);
        if (n <= 0)
            break;

        if (g.match && from_server(&from)) {
            int m = unwrap_relay(g.pkt, n);
            if (m < 0) {
                rv_handle(g.pkt, n, dlg);
                continue;
            }
            /* The peer gave up on direct; follow it. */
            if (!g.relay) {
                g.relay = 1;
                netlog("peer is relaying, following");
            }
            n = m;
            from = g.peer;
        } else if (g.relay) {
            continue;   /* late direct packet, the relay is the link now */
        }

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

}

static INT_PTR CALLBACK wait_proc(HWND dlg, UINT msg, WPARAM wp, LPARAM lp)
{
    switch (msg) {
    case WM_INITDIALOG:
        SetDlgItemTextA(dlg, ID_STATUS,
                        g.host ? "Waiting for the other player..."
                               : "Connecting...");
        g.hs_last = timeGetTime();
        g.rv_last = g.hs_last - MATCH_RETRY_MS;   /* ask the server at once */
        g.hs_done = g.hs_failed = 0;
        SetTimer(dlg, 1, 50, NULL);
        return TRUE;

    case WM_TIMER:
        handshake_tick(dlg);
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

    p = tpl_head(buf, DLG_STYLE, 220, 60, 2, "Virtual-On Netplay");
    p = tpl_ctl(buf, p, WS_CHILD | WS_VISIBLE, 10, 14, 200, 10,
                ID_STATUS, CLS_STATIC, "");
    p = tpl_ctl(buf, p, WS_CHILD | WS_VISIBLE | WS_TABSTOP,
                84, 36, 52, 14, IDCANCEL, CLS_BUTTON, "Cancel");

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
                        DWORD rtt = timeGetTime() - sent;
                        if (i > 0) {          /* first round is discarded */
                            total += rtt;
                            samples++;
                        }
                        netlog("ping %d: %lu ms%s", i, (unsigned long)rtt,
                               i ? "" : "  (discarded)");
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
        netlog("host: %d samples, mean %lu ms -> delay %d frames",
               samples, (unsigned long)(samples ? total / samples : 0),
               g.delay);
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
                    if (g.delay > RING / 2 - 2)
                        g.delay = RING / 2 - 2;
                    netlog("guest: host says delay %d frames", g.delay);
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

    /* Winsock comes up first: the dialog asks it for our own addresses. */
    if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0)
        return 0;

    choice = ask_connection(inst);
    if (choice == 0) {
        WSACleanup();
        return 0;
    }
    g.host = (choice == 1);

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

    if (!g.host && !g.match) {
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
        if (g.hs_failed)
            notice("No game with that code.");
        sock_close();
        return 0;
    }
    g.linked = 1;
    g.last_rx = g.last_tx = timeGetTime();

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
        if (g.linked && !g.peer_quit) {
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
            sock_close();
            notice("Your challenger has stopped playing Netplay.");
            return 0;
        }
    }

    heartbeat();
    if (link_silent()) {
        sock_close();
        notice("Now Network interrupted.");
        return 0;
    }

    if (g.msg_used <= 0)
        return 0;

    len = g.msg[g.msg_tail];
    if (len < 1 || 1 + len > g.msg_used) {
        /* Cannot happen if the writer is behaving. If it ever does, an
           empty ring is recoverable and a desynchronised one is not. */
        netlog("message ring out of step: len %d, used %d", len, g.msg_used);
        g.msg_head = g.msg_tail = g.msg_used = 0;
        return 0;
    }
    g.msg_tail = (g.msg_tail + 1) & MSGRING_MASK;
    for (i = 0; i < len; i++) {
        out[i] = g.msg[g.msg_tail];
        g.msg_tail = (g.msg_tail + 1) & MSGRING_MASK;
    }
    g.msg_used -= 1 + len;
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
                sock_close();
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
            heartbeat();
            if (link_silent()) {
                sock_close();
                notice("Now Network interrupted.");
                return 0;
            }
            if (PeekMessageA(&m, NULL, 0, 0, PM_REMOVE)) {
                if (m.message == WM_QUIT)
                    return 1;
                TranslateMessage(&m);
                DispatchMessageA(&m);
            }
        } else {
            DWORD now = timeGetTime();

            /* Nothing waiting, so give the core back until it is. */
            wait_readable(1);

            /* Peer has gone quiet altogether: crash, kill, cable out. */
            if (link_silent()) {
                sock_close();
                notice("Now Network interrupted.");
                return 0;
            }

            /* Peer is still audible, we are just missing a frame. This is
               the original's patient path: ask again once a second and hold
               on for the full timeout before giving up. */
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
