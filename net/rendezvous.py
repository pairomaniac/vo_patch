#!/usr/bin/env python3
"""Rendezvous server for Virtual-On netplay matchcodes.

Hosts register and get a short code. A guest sends the code; the server
tells each side the other's public address and port, and both start
sending to each other so their NATs open. If that fails, both sides send
their game traffic through the server instead and it forwards each
datagram to the other side. No state beyond the open codes.

    python3 rendezvous.py [port]        default 47625

Wire format, one UDP datagram each, all starting with the magic "VOR1":

    client -> server
        C                 create a code
        H <code>          host keepalive, refreshes the entry
        J <code>          guest wants this host
        R <code> <data>   relay: forward <data> to the other side
    server -> client
        K <code>          your code
        P <ip4> <port>    the other player's endpoint, 4 + 2 bytes big-endian
        N                 no such code
        D <data>          relayed from the other side

Codes are 5 characters, no 0/O/1/I. Entries expire after 30 s without
traffic. Both sides keep asking until they have a P, so a lost datagram
costs a second, not the match. Relayed matches keep the entry alive by
their own heartbeat.
"""

import random
import socket
import sys
import time

MAGIC = b'VOR1'
ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
CODE_LEN = 5
EXPIRE_S = 30
MAX_CODES = 1000

codes = {}   # code -> {'host': (ip, port), 'guest': (ip, port) or None, 'seen': t}


def endpoint_bytes(addr):
    return socket.inet_aton(addr[0]) + addr[1].to_bytes(2, 'big')


def new_code():
    while True:
        c = ''.join(random.choice(ALPHABET) for _ in range(CODE_LEN))
        if c not in codes:
            return c


def expire(now):
    for c in [c for c, e in codes.items() if now - e['seen'] > EXPIRE_S]:
        e = codes.pop(c)
        print('%s expired, %s' % (c, 'relayed' if e.get('relayed')
              else 'punched' if e['guest'] else 'never joined'), flush=True)


def handle(sock, data, addr, now):
    if len(data) < 5 or data[:4] != MAGIC:
        return
    op = data[4:5]
    code = data[5:5 + CODE_LEN].decode('ascii', 'replace').upper()

    if op == b'C':
        if len(codes) >= MAX_CODES:
            return
        c = new_code()
        codes[c] = {'host': addr, 'guest': None, 'seen': now}
        sock.sendto(MAGIC + b'K' + c.encode(), addr)
        print('%s created by %s:%d (%d open)' % (c, *addr, len(codes)),
              flush=True)

    elif op == b'H':
        e = codes.get(code)
        if not e or e['host'] != addr:
            sock.sendto(MAGIC + b'N', addr)
            return
        e['seen'] = now
        if e['guest']:
            sock.sendto(MAGIC + b'P' + endpoint_bytes(e['guest']), addr)

    elif op == b'J':
        e = codes.get(code)
        if not e:
            sock.sendto(MAGIC + b'N', addr)
            return
        if e['guest'] is None:
            e['guest'] = addr
            print('%s joined by %s:%d' % (code, *addr), flush=True)
        if e['guest'] != addr:
            sock.sendto(MAGIC + b'N', addr)   # someone else got there first
            return
        e['seen'] = now
        sock.sendto(MAGIC + b'P' + endpoint_bytes(e['host']), addr)
        sock.sendto(MAGIC + b'P' + endpoint_bytes(addr), e['host'])

    elif op == b'R':
        e = codes.get(code)
        if not e or not e['guest']:
            return
        if addr == e['host']:
            other = e['guest']
        elif addr == e['guest']:
            other = e['host']
        else:
            return
        if not e.get('relayed'):
            e['relayed'] = True
            print('%s relaying' % code, flush=True)
        e['seen'] = now
        sock.sendto(MAGIC + b'D' + data[5 + CODE_LEN:], other)


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 47625
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(('', port))
    sock.settimeout(1.0)
    print('listening on udp/%d' % port, flush=True)
    while True:
        now = time.monotonic()
        try:
            data, addr = sock.recvfrom(600)
        except socket.timeout:
            expire(now)
            continue
        except ConnectionResetError:
            continue
        handle(sock, data, addr, now)
        expire(now)


if __name__ == '__main__':
    main()
