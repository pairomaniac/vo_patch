# net

A replacement for the game's `DPCTRL.DLL`. Same seven exports, same calling
conventions, plain UDP where the original used DirectPlay. For installing and
using it see [README.md](../README.md); for testing it see
[DEVELOPING.md](../docs/DEVELOPING.md).

```
dpctrl.c            the implementation
dpctrl.def          export names, undecorated, as the game imports them
build.py            compiles it and bakes the result into vo_patch.py
rendezvous.py       the matchcode server, runs anywhere with a public address
rendezvous.service  a systemd unit for it
```

`tools/rendezvous-install.sh` puts the last two in place on a machine that
should keep the server up; `tools/vo-dll.sh` builds the DLL and drops it into
a game folder for testing.

## Why

`v_on.exe` does no networking of its own. It imports six functions from
`DPCTRL.DLL`, every export but `CloseProvider`, and that DLL in turn uses
two from DirectPlay 1: `DirectPlayCreate` and `DirectPlayEnumerate`,
ordinals 1 and 2, nothing else.

That API has nowhere to put an address. Compound addresses and
`InitializeConnection` arrived with `IDirectPlay3`, years later. The
service provider is handed a GUID and no destination, so finding an
opponent falls back to whatever it does unaddressed, which for TCP/IP means
broadcasting to the local subnet. No router forwards that, which is why port
forwarding never helped: the search never leaves the building.

Against that, what DirectPlay contributed to a running match was one call -
`Send(this, idFrom, DPID_ALLPLAYERS, dwFlags=0, buf, len)`. Flags zero is
non-guaranteed: unordered, unacknowledged, never retransmitted. It is
`sendto` wearing a session header. Everything that makes link play hold up
on a real connection sits above that call, in this DLL, and was Sega's.

So the transport goes and the rest is rebuilt as it was.

## What the game sees

Frame size is the game's 40 bytes plus 2.

```
byte 0     type
byte 1     sequence & 0x3F
byte 2..   payload
```

Type 1 is a frame. Control packets are 1 or 8 bytes: 2 asks for a frame to
be sent again, 5 announces the input delay, 0x16 is a ping and 6 its
reply, 7 is a poll, 0x18 relays a `WM_COMMAND` so a menu opens on both
machines at once. Types from 0x80 up are ours and did not exist in the
original: hello, hello-ack, goodbye, and the one-byte punch that opens a
NAT during a matchcode connect.

Two 64-slot rings, indexed by `seq & 0x3F`. `SWDataSendReceive` sends the
local player's frame, blocks until the peer's frame for the current
sequence arrives, then writes both out: the peer's from the receive ring,
and *our own from the send ring* rather than the buffer we were handed.
That last detail is what keeps the two machines symmetric once input delay
is in play, and it is easy to get wrong.

On connect the host pings nine times, throws the first away, averages the
other eight and works out `delay = mean_rtt / 32 + 1` frames, then tells
the guest. The first `SWDataSendReceive` sends its frame `delay + 1` times
to fill the pipeline; every call after that sends once.

Game messages that are not frames - whatever the menus exchange between
rounds - go into an 8 KB ring for `ReceiveDirectPlay` to hand back. The ring
is bounded: a message that will not fit is dropped and counted rather than
allowed to lap the reader, because a wrapped write puts a mid-message byte
where a length belongs and everything read after that is garbage the game
acts on. If `vo-net.log` starts reporting dropped messages, the game is
queueing faster than it drains and that is worth investigating rather than
tuning away.

A missing frame and a missing player are different problems and get
different answers. A frame that has not arrived means the peer is slow: ask
again every second and hold on for the full thirty, as the original did.
Silence means the peer is gone, so a 250ms heartbeat runs both ways and
three seconds of nothing at all is a dead link. The original had no need
for this - DirectPlay watched the connection and told the game.

## Matchcodes

Direct play needs the host to forward a UDP port. The matchcode path does
not. Both sides send to a rendezvous server from the socket they will play
on, the server sees the public address and port each NAT assigned, and
hands each side the other's. Both then send to each other - the host a
`P_PUNCH`, the guest its normal hello - and each outgoing packet opens its
own NAT for the reply. From the ack onward nothing is different and the
server is out of the loop.

If nothing has got through after `PUNCH_MS` (four seconds), both sides
switch to sending through the server, which forwards each datagram to the
other side. Whichever side gives up first drags the other along: a relayed
packet arriving is the signal to follow. This covers symmetric NAT and
carrier-grade NAT, where the port the server saw is not one the peer can
reach. It costs the detour through the server in latency, so direct is
always tried first.

### Codes

Codes are shown as `EU-ABCDE`, `US-ABCDE` or `JP-ABCDE`. A code only exists
on the server that issued it, so the guest has to reach that same one, and
the tag is how it knows which. Hyphens, spaces and case are ignored on the
way in; the tag itself is required.

| Tag | Constant | Server | Where |
| --- | --- | --- | --- |
| `EU` | `MATCH_SERVER_EU` | `segaonline.net` | Helsinki |
| `US` | `MATCH_SERVER_US` | `us.segaonline.net` | New York |
| `JP` | `MATCH_SERVER_JP` | `jp.segaonline.net` | Tokyo |

The constants are in `dpctrl.c`; the port is `MATCH_PORT`, 47625. The
locations are also in `MATCH_WHERE_*`, which is what the dialog shows.

*Custom* points both sides at a server of their own, `host` or `host:port`.
Those codes are `XX-ABCDE`: the tag says only that it is not one of ours,
so the guest fills in the same address. The choice and the address are kept
in `vo-net.ini` next to the game, so it is typed once:

```ini
[net]
server=vo.example.org:47625
region=2
```

### The server

`rendezvous.py`, standard library only, keeps a table of open codes and
nothing else. Hosts refresh their entry every second; relayed matches
refresh it with their own traffic; thirty seconds of silence expires it, so
nothing needs cleaning up.

```
client -> server    C                create, reply K <code>
                    H <code>         host keepalive, reply P <peer> once there is one
                    J <code>         join, reply P <host>, and P <guest> to the host
                    R <code> <data>  forward to the other side as D <data>
server -> client    N                unknown code, or already taken
```

Datagrams start with `VOR1` so they can never be mistaken for a game packet.
Ten unknown codes from one address inside a minute and its joins are ignored
for ten minutes, so the code space cannot be swept. Joins only, since one
address may be a whole carrier NAT.

What else the server refuses, and why: a code with a character outside the
alphabet, since it would only ever be a guess or a log line; a ninth open
code from one address, so one machine cannot fill the table; a relayed
datagram over 512 bytes, the game's largest. Codes come from `secrets`,
because the default generator can be predicted from a few hundred codes
seen and a predicted code can be joined first.

The relay cannot be turned into a general tunnel. Each direction of a code
is a token bucket refilling at 250 packets a second - a 60 fps match spends
a third of that, so it never notices, while a pair trying to push bulk
traffic is held to that rate times the 512-byte cap, about 125 KB/s per
code, whatever they send. It is still a relay between the two endpoints a
code registered and nothing else: it forwards to the other endpoint, never
to a third party, so it cannot be aimed at a victim it does not already
reach.

### Running one

```bash
python3 net/rendezvous.py          # udp/47625, open it in the firewall
```

For a machine that should keep it up, `tools/rendezvous-install.sh install`
copies the script to `/opt/vo-netplay`, puts it in place as
`vo-rendezvous.service` and starts it. `update` reinstalls from the checkout
after a `git pull`, `remove` undoes it, and `status` counts how the last
day's matches ended. The unit runs as a `DynamicUser` on a read-only
filesystem with a syscall filter and no capabilities, since the server
needs none of them. Opening the port is left to you; the script prints the
command for whichever firewall it finds.

### What it trusts

Addresses, and it knows it. UDP carries no proof of where a datagram came
from, so a keepalive or relay packet is believed to be from whichever
side's address it carries. Someone who has a code *and* both
endpoints can inject into a relayed match; that is the same someone who
could send to the players directly, so nothing is lost that was ever held.

The client trusts its peer the same way the original did. A `P_CMD` posts a
`WM_COMMAND` into the game window and frames are played as received; a
hostile opponent can open menus or desync the match, and could always have.
The session tag in the handshake is not a secret: its bytes are the game's
session name, a marker and the patch fingerprint, none of them privileged.
What the matchcode path adds is that a host's address is never published:
only the holder of the code learns it.

### Seeing what happened

`vo-net.log` beside the game, created empty, turns on client logging. A
match that connected reads:

```
matchcode: EU server 203.0.113.9:47625
matchcode 7B6NZ
peer via rendezvous: 198.51.100.7:51234
linked directly to 198.51.100.7:51234 after 1841 ms
```

`linked through the relay` in place of the last line means the punch failed
and the fallback carried it - a symmetric NAT or CGNAT somewhere. The server
says the same from its side, one line per code when it expires: `punched`,
`relayed` or `never joined`.

If the server does not answer within `MATCH_WAIT_MS` the client gives up
and says so.

`relay=1` under `[net]` in `vo-net.ini` skips the punch and goes straight to
the relay, so the fallback can be tested between two machines that would
otherwise connect directly. Remove it afterwards; a forced relay is a slower
match.

## Two deliberate differences

**The input delay is clamped** to `RING/2 - 2` frames. The original has no
clamp, and somewhere past a second of round trip its send sequence would
start overwriting slots still waiting to be read. That is a bug worth not
reproducing.

**The wait yields.** `select()` sleeps until the packet lands instead of
spinning on `recvfrom`. The original spun as well, but through DirectPlay,
which blocks internally and hands the time back; swapping in non-blocking
UDP inherited a busy-wait Sega never had, and on a modern scheduler it
costs the render thread a frame here and there. Build with `-DVO_YIELD=0`
to get the spin back and feel the difference.

## Building

```bash
python3 net/build.py            # compile and bake into vo_patch.py
python3 net/build.py --check    # is the blob current? no compiler needed
```

The patcher ships as one file and cannot read this directory at runtime,
so the compiled DLL rides along as compressed base64 between marker
comments. Never edit that by hand; the next `build.py` run discards it.

`--check` compares a hash of `dpctrl.c` against the one recorded beside the
blob, rather than recompiling and diffing bytes: two mingw versions do not
produce identical output from identical source.

## Testing

Loopback proves the ABI and the ring handling and nothing more: no loss and
no latency. The resend path, the input delay and every question about NAT
need two machines and a real link. Point the two folders at different
builds of the game and it also answers whether two compiles stay in step -
see [DEVELOPING.md](../docs/DEVELOPING.md).

Both sides need the same DLL, and must agree on the patches that change the
simulation - each machine runs its own copy of the game on both players'
inputs, so a difference there drifts the copies apart. The handshake
enforces it: the session tag's last byte is a fingerprint of those patches
(today the frame-rate divisor and the round-loss fix), read from the
running exe, so a mismatch is refused at connect time rather than left to
desync mid-match. Visual and input-only patches are deliberately outside
the fingerprint and may differ.

The two bytes it reads are at a different address in each build of the
game, so the DLL finds the running one by its PE timestamp - `fp_builds`
in `dpctrl.c`, one row per build. The bytes themselves read the same once
patched, which is why builds can play each other at all. A DLL older than
that table reads retail's addresses whatever it is running in, and refuses
every peer but its own kind; if two installs will not connect, check that
both have the current DLL before anything else.
