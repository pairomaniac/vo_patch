# net

A replacement for the game's `DPCTRL.DLL`. Same seven exports, same calling
conventions, plain UDP where the original used DirectPlay.

```
dpctrl.c    the implementation
dpctrl.def  export names, undecorated, as the game imports them
build.py    compiles it and bakes the result into vo-patch.py
```

## Why

`v_on.exe` does no networking itself. It imports six functions from
`DPCTRL.DLL` and that DLL, in turn, uses DirectPlay 1 - `DirectPlayCreate`
and `DirectPlayEnumerate`, ordinals 1 and 2. That API has no address
mechanism at all: compound addresses and `InitializeConnection` arrived
with `IDirectPlay3`, years later. So the service provider is created with a
GUID and nothing else, and finding an opponent falls back to whatever it
does with no address, which for TCP/IP is a subnet broadcast.

No router forwards that. Port forwarding cannot help, because the search
never leaves the building. That is the whole reason link play only ever
worked on a LAN, and why a layer-2 VPN made it work again.

What DirectPlay actually contributed was one call:
`Send(this, idFrom, DPID_ALLPLAYERS, dwFlags=0, buf, len)`. Flags zero is
non-guaranteed - unordered, unacknowledged, no retransmission. It is
`sendto` with a session header on top. Everything that makes link play hold
up over a real connection was Sega's, in this DLL, above that call.

So the transport is replaced and the rest is reimplemented as it was.

## What the game sees

Frame size is the game's 40 bytes plus 2.

```
byte 0     type
byte 1     sequence & 0x3F
byte 2..   payload
```

Type 1 is a frame. Control packets are 1 or 8 bytes: 2 asks for a frame to
be sent again, 5 announces the input delay, 6 and 0x16 are a ping and its
reply, 7 is a poll, 0x18 relays a `WM_COMMAND` so a menu opens on both
machines at once. Types above 0x80 are ours and did not exist in the
original: hello, hello-ack and goodbye.

Two 64-slot rings, indexed by `seq & 0x3F`. `SWDataSendReceive` sends the
local player's frame, blocks until the peer's frame for the current
sequence arrives, then writes both out - the peer's from the receive ring,
and *our own from the send ring*, not the buffer we were handed. That last
part is what keeps the two machines symmetric under input delay.

On connect the host pings nine times, discards the first, averages the
other eight and derives `delay = mean_rtt / 32 + 1` frames, then tells the
guest. The first `SWDataSendReceive` sends that frame `delay + 1` times to
prime the pipeline; every later call sends once.

Missing frames and a missing peer are different problems and are treated
differently. A frame that has not arrived means the peer is slow: ask again
every second, hold on for the full 30, exactly as the original did. Silence
means the peer is gone: a 250ms heartbeat runs in both directions, so three
seconds without anything at all is a dead link. The original never needed
this - DirectPlay noticed the drop and told the game.

## Two deliberate differences

**The input delay is clamped** to `RING/2 - 2` frames. The original has no
clamp, and somewhere past a second of round trip its send sequence would
overwrite ring slots still waiting to be read.

**The wait yields.** `select()` sleeps until the packet lands rather than
spinning on `recvfrom`. The original spun too, but through DirectPlay,
which blocks internally and hands time back to the scheduler; replacing
DirectPlay with non-blocking UDP inherited a busy-wait Sega never had.
Build with `-DVO_YIELD=0` to get the spin back for comparison.

## Building

```bash
python3 net/build.py            # compile and bake into vo-patch.py
python3 net/build.py --check    # is the blob current? no compiler needed
```

The patcher ships as one file and cannot read this directory at runtime,
so the compiled DLL rides along as compressed base64 between marker
comments. Never edit that by hand; the next `build.py` run discards it.

`--check` compares a hash of `dpctrl.c` against the one recorded beside the
blob, rather than recompiling and diffing bytes: two mingw versions do not
produce identical output from identical source.

## Testing

Loopback proves the ABI and the rings and nothing else - no loss, no
latency, both sides the same build. The resend path, the input delay and
everything about NAT need two machines.

Touch `vo-net.log` beside the game to log the latency probe and the
resulting frame delay. Delete it to stop.

Both players need the same DLL *and* the same patches. The two machines run
one simulation in lockstep; if they disagree about the rules they will
disagree about the match.

## Provenance

Written from observed behaviour - imports, exports, packet sizes, jump
tables, constants - not from Sega's code, and not decompiled. The parts
that came from reading the original are facts about a format and a
protocol. Nothing was copied.
