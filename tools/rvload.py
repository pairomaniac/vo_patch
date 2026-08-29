#!/usr/bin/env python3
"""A/B a rendezvous server, or two against each other.

    python3 tools/rvload.py segaonline.net
    python3 tools/rvload.py segaonline.net us.segaonline.net    # compare

Read-only where it can be: the probes use codes they create themselves and
leave them to expire. The flood probe sends unwanted traffic and is off
unless asked for with --flood; run it against your own servers only, not
while people are playing.

Two servers on the same source read the same on every probe. A box that
has drifted from `net/rendezvous.py` shows up in `relay`, `guessing` or
`percap`; `latency` and `punch` are the match path and should agree
regardless.
"""

import argparse
import socket
import statistics
import sys
import time

MAGIC = b'VOR1'
PORT = 47625


def udp():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(2.0)
    return s


def call(sock, addr, payload, tries=3):
    """Send payload, return the first reply or None. A stray ICMP from an
    earlier datagram can surface as a reset on the next send, so a reset
    is retried rather than fatal."""
    for _ in range(tries):
        try:
            sock.sendto(payload, addr)
            r, _ = sock.recvfrom(2048)
            return r
        except socket.timeout:
            return None
        except ConnectionError:
            continue
    return None


class Server:
    def __init__(self, host, port):
        self.host = host
        self.addr = (socket.gethostbyname(host), port)


def probe_reachable(sv):
    r = call(udp(), sv.addr, MAGIC + b'C')
    if r is None:
        return 'no reply'
    return ('ok, code %s' % r[5:].decode('ascii', 'replace')
            if r[4:5] == b'K' else 'reply %r' % r)


def probe_latency(sv, n=20):
    """Round trip on one held code, keepalive and reply, so the per-IP cap
    is never touched. Unchanged by the patch: a baseline the two servers
    should share once network distance is out. A host keepalive on a code
    with no guest yet is answered by nothing, so this joins its own code
    first and times the H that then gets a P back."""
    h, g = udp(), udp()
    r = call(h, sv.addr, MAGIC + b'C')
    if r is None or r[4:5] != b'K':
        return 'no create'
    code = r[5:]
    call(g, sv.addr, MAGIC + b'J' + code)      # so H gets a P, not silence

    # The join is answered to both sides, so h already holds a P. Reading it
    # here rather than in the loop below keeps each timed H matched with its
    # own reply; without this the probe stays one datagram behind and reports
    # the sleep instead of the round trip.
    h.setblocking(False)
    try:
        while True:
            h.recvfrom(2048)
    except OSError:
        pass
    h.settimeout(2.0)

    got = []
    for _ in range(n):
        t = time.perf_counter()
        rr = call(h, sv.addr, MAGIC + b'H' + code, tries=1)
        if rr and rr[4:5] == b'P':
            got.append((time.perf_counter() - t) * 1000)
        time.sleep(0.03)
    h.close()
    g.close()
    if not got:
        return 'no replies'
    got.sort()
    return 'min %.1f  median %.1f  p90 %.1f ms  (%d/%d)' % (
        got[0], statistics.median(got), got[min(int(len(got) * 0.9),
        len(got) - 1)], len(got), n)


def probe_punch(sv):
    """A full create/join/keepalive as two clients. The path that has to
    keep working; times the handshake end to end."""
    h, g = udp(), udp()
    t = time.perf_counter()
    r = call(h, sv.addr, MAGIC + b'C')
    if r is None or r[4:5] != b'K':
        return 'create FAILED'
    code = r[5:]
    gp = call(g, sv.addr, MAGIC + b'J' + code)
    hp = call(h, sv.addr, MAGIC + b'H' + code)
    ms = (time.perf_counter() - t) * 1000
    ok = gp and gp[4:5] == b'P' and hp and hp[4:5] == b'P'
    return 'handshake %s in %.0f ms' % ('ok' if ok else 'FAILED', ms)


def probe_relay(sv):
    """Forward a 512-byte and a 513-byte datagram. Patched: 513 dropped.
    Unpatched: both arrive."""
    h, g = udp(), udp()
    r = call(h, sv.addr, MAGIC + b'C')
    if r is None:
        return 'no create'
    code = r[5:]
    call(g, sv.addr, MAGIC + b'J' + code)      # g learns host, h learns g
    call(h, sv.addr, MAGIC + b'H' + code)
    out = []
    for size in (512, 513):
        h.sendto(MAGIC + b'R' + code + b'x' * size, sv.addr)
        try:
            r, _ = g.recvfrom(2048)
            out.append('%d: forwarded %d' % (size, len(r) - 5))
        except socket.timeout:
            out.append('%d: dropped' % size)
        except ConnectionError:
            out.append('%d: dropped (icmp)' % size)
    return ' | '.join(out)


def probe_guessing(sv):
    """Join on a code with bytes outside the alphabet. Patched: silent.
    Unpatched: answered N, and the raw byte reaches the log."""
    r = call(udp(), sv.addr, MAGIC + b'J' + b'0OI1!', tries=1)
    return ('answered %r - not validated' % r[4:5] if r
            else 'ignored - validated')


def probe_percap(sv, tries=12):
    """Open codes from one address in a burst. Patched: granted stops at
    the per-IP cap, less whatever this IP already holds from the probes
    above, so a low number here is the cap working, not a failure.
    Unpatched: all twelve are granted."""
    socks = [udp() for _ in range(tries)]
    granted = 0
    for s in socks:
        r = call(s, sv.addr, MAGIC + b'C', tries=2)
        if r and r[4:5] == b'K':
            granted += 1
    for s in socks:
        s.close()
    verdict = 'capped' if granted < tries else 'all granted - no per-ip cap'
    return '%d of %d granted (%s)' % (granted, tries, verdict)


def probe_flood(sv, seconds=5, rate=2000):
    """Unknown-code joins as fast as asked, to watch the guess limiter and
    the server's headroom. Unwanted traffic - own servers only."""
    s = udp()
    s.setblocking(False)
    sent = replied = 0
    gap = 1.0 / rate
    end = time.time() + seconds
    while time.time() < end:
        try:
            s.sendto(MAGIC + b'J' + b'ZZZZZ', sv.addr)
            sent += 1
        except (BlockingIOError, ConnectionError):
            pass
        try:
            while True:
                s.recvfrom(64)
                replied += 1
        except (BlockingIOError, socket.timeout, ConnectionError):
            pass
        time.sleep(gap)
    # This IP has banned itself by now, so a create from it is refused too -
    # that is the guard, not a dead server. Check liveness from a different
    # source port won't help (same IP); the honest read is the answer count:
    # it should climb to about MISS_LIMIT and then flatline.
    return ('sent %d joins in %ds, %d answered before the guess-ban; '
            'server keeps the rest' % (sent, seconds, replied))


# percap opens a burst of codes and must run last, or it leaves this IP at
# its cap and the create-based probes after it fail for the wrong reason.
PROBES = [
    ('reachable', probe_reachable),
    ('guessing', probe_guessing),
    ('relay', probe_relay),
    ('punch', probe_punch),
    ('latency', probe_latency),
    ('percap', probe_percap),
]


def run(sv, flood):
    print('== %s (%s) ==' % (sv.host, sv.addr[0]))
    for name, fn in PROBES:
        try:
            print('  %-10s %s' % (name, fn(sv)))
        except OSError as e:
            print('  %-10s error: %s' % (name, e))
    if flood:
        try:
            print('  %-10s %s' % ('flood', probe_flood(sv)))
        except OSError as e:
            print('  %-10s error: %s' % ('flood', e))
    print()


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('servers', nargs='+', help='host or host:port')
    ap.add_argument('--flood', action='store_true',
                    help='add the flood probe (own servers only)')
    args = ap.parse_args(argv)
    for spec in args.servers:
        host, _, port = spec.partition(':')
        run(Server(host, int(port) if port else PORT), args.flood)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
