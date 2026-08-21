# net

A replacement for the game's `DPCTRL.DLL`. Same seven exports, same calling
conventions, plain UDP where the original used DirectPlay.

```
dpctrl.c    the implementation
dpctrl.def  export names, undecorated, as the game imports them
build.py    compiles it and bakes the result into vo-patch.py
```

## Why

`v_on.exe` does no networking of its own. It imports six functions from
`DPCTRL.DLL` - every export but `CloseProvider` - and that DLL uses DirectPlay 1 - `DirectPlayCreate` and
`DirectPlayEnumerate`, ordinals 1 and 2, nothing else.

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
be sent again, 5 announces the input delay, 6 and 0x16 are a ping and its
reply, 7 is a poll, 0x18 relays a `WM_COMMAND` so a menu opens on both
machines at once. Types above 0x80 are ours and did not exist in the
original: hello, hello-ack and goodbye.

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

Loopback proves the ABI and the ring handling and nothing more: no loss, no
latency, both sides the same build. The resend path, the input delay and
every question about NAT need two machines and a real link.

Touch `vo-net.log` beside the game to log the latency probe and the
resulting frame delay. Delete it to stop.

Both sides need the same DLL, and gameplay-affecting patches should match:
each machine runs its own copy of the game on both players' inputs, and a
behavioural difference can make the copies drift apart.
