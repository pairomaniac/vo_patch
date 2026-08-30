# Documentation map

The root [README](../README.md) is for playing: installing, what each patch
does, internet play, the gamepad, music, resolution. Everything here is for
working on the patcher.

| Read | For |
| --- | --- |
| [DEVELOPING.md](DEVELOPING.md) | setup, the daily loop, the checks and what each catches, adding a blob, a site or a build, netplay development, releasing, troubleshooting |
| [NOTES.md](NOTES.md) | how each patch works inside the game: the patch table with every site, the builds and how their offsets map, and a section per patch on what the game does and why the change is what it is |
| [TEXT.md](TEXT.md) | the three ways the game draws text, where each string the patcher touches lives, and how the title banner and the credit line are made |
| [HIRES.md](HIRES.md) | the resolution patch: what it rewrites, the blob, the game's scenes as read off it, the multi-build port and its record, what is queued |
| [../asm/README.md](../asm/README.md) | the assembly sources, how they become bytes in the patcher, and a section per file |
| [../net/README.md](../net/README.md) | the netplay DLL and the rendezvous server |
| [../maps/README.md](../maps/README.md) | the build maps and port tables `tools/maps.sh` generates |

Where something lives, by question:

- *What does patch X change?* NOTES.md's table, then its section.
- *Where is this address / offset from?* NOTES.md for the game's own
  routines, TEXT.md for strings and artwork, HIRES.md for the resolution
  patch's sites and the other builds' addresses.
- *How do I rebuild after editing assembly?* asm/README.md for `asm/`,
  HIRES.md's *Rebuilding* for `ui.asm`.
- *Why is the widescreen box greyed out on my build?* HIRES.md, *What
  porting actually taught*.
- *How do I cut a release?* DEVELOPING.md, *Releasing*.
