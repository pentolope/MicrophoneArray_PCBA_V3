# 16-channel circular PDM microphone array

A 120 mm circular, four-layer KiCad 10 carrier for sixteen `MSM261DHP006` PDM
MEMS microphones running at 3.3 V. A socketed Sipeed Tang Nano 9K performs
acquisition and decimation; a 2012 Raspberry Pi Model B collects the audio over
SPI0 over a 26-way cable to the Pi's P1 header.

The sixteen acoustic ports sit on a 54.00 mm radius at 22.5 degree intervals,
channel 0 on +X, numbered counter-clockwise viewed from the component side.

![top side](generated/render/top.png)

## Design summary

| | |
|---|---|
| Board | 120 mm circle, 1.6 mm, 4 layers, ENIG, green / white |
| Stackup | signal / GND / GND / signal - both inner layers solid ground |
| Microphones | 16 x `MSM261DHP006`, top-ported, 3.3 V, ports up at the rim |
| Channel pairing | adjacent channels share a data line by clock edge, 8 data nets |
| Reference clock | 24.576 MHz oscillator to a global-clock FPGA pin |
| PDM clock | 3.072 MHz, buffered to 8 series-terminated branches |
| Sample rate | 48 kHz after decimate-by-64 |
| Level translation | none - microphones, FPGA banks and Pi GPIO are all 3.3 V |
| Power | Raspberry Pi 5 V only, via PTC and series Schottky |
| Microphone supply | `LP5907MFX-3.3`, 10 uVrms, 82 dB PSRR at 1 kHz |
| Host link | SPI0, 25 MHz design target, ESD-protected and series-damped |
| Assembly | all SMT on top; module sockets and Pi header hand-soldered |

The `MSM261DHP006` is a top-ported part, so "outward-facing" is realised as
ports facing up at the rim rather than radially. The capsules are
omnidirectional, so the array response is set by the port geometry, not by
package orientation; the alternative - sixteen perpendicular daughter-cards -
cannot be assembled by JLCPCB. This is discussed in
[docs/architecture.md](docs/architecture.md).

## Documentation

- [docs/architecture.md](docs/architecture.md) - signal, clock, power and
  stackup decisions, Tang Nano 9K pin assignments, and known limitations.
- [docs/host-interface.md](docs/host-interface.md) - Raspberry Pi pinout,
  bandwidth analysis and the wire protocol.
- [docs/routing.md](docs/routing.md) - what is pre-routed and why, the
  microphone escape geometry, and how the router is driven.
- [docs/manufacturing.md](docs/manufacturing.md) - order options, order remark,
  microphone handling limits and bring-up sequence.
- [docs/timing.md](docs/timing.md) - what the interconnect timing gates
  measure on the PDM clock tree, why a net is not an electrical path, and
  which physical-stackup figures are still missing
- [docs/sources.md](docs/sources.md) - every datasheet and manufacturer rule the
  design relies on, with the facts taken from each.
- [docs/environment.md](docs/environment.md) - the Ubuntu development
  environment: which package supplies each tool, the exact versions this board
  is verified against, and why KiCad is pinned to 10.0.5.

## Repository layout

| Path | Contents |
|---|---|
| `microphone_array_v2.kicad_sch` | generated schematic - do not hand-edit |
| `microphone_array_v2.kicad_pcb` | generated board - do not hand-edit, see below |
| `microphone_array_v2.kicad_pro` | project settings, net classes, design rules |
| `MicArrayV2.kicad_sym` | generated project symbols |
| `MicArrayV2.pretty/` | project footprints: microphone, oscillator, silk-free probe pads |
| `constraints.json` | frozen requirements and manufacturing profile |
| `tools/` | design source of truth, generators and checkers |
| `docs/` | engineering documentation |
| `generated/` | reports, renders and release artefacts |

## Source of truth

`tools/netlist.py` holds the complete component list, netlist and placement.
The schematic and the board are both generated from it. Nothing about the
board is edited by hand; see [the build](#building-the-board) below. Three
independent gates keep schematic and board in step:

- `tools/check_netlist_parity.py` extracts the netlist from the generated
  schematic with `kicad-cli` and compares it against `netlist.py`, including an
  exact match on which pins are deliberately left unconnected.
- KiCad's `--schematic-parity` DRC compares the board against the schematic.
- `tools/check_placement.py` re-derives the array geometry from the board:
  every microphone's acoustic port radius and azimuth, every Tang Nano 9K
  socket pin coordinate, courtyard overlaps by exact polygon distance, JLCPCB
  package-pair body spacing, and edge clearance.

`tools/check_routes.py` adds the post-route constraints KiCad cannot express:
allowed layers per net, via budgets on the clock nets, branch length matching
across the eight microphone pairs, different-net crossings, and acid traps -
corners left with an interior angle under 90 degrees.

Regeneration commands are in [docs/manufacturing.md](docs/manufacturing.md).

## Building the board

One command, from an empty directory, start to finish:

```bash
python3 tools/build.py --install
```

It runs [tools/gen_pcb.py](tools/gen_pcb.py) to produce the pre-route board -
outline, stackup, footprints, placement, zones, net classes, rules, the
solder-mask via keep-outs and the declared critical routing, all locked - then
hands it to KiCad Routing Tools under the recorded plan in
[tools/routing_plan.json](tools/routing_plan.json), refills the zones and puts
the result through KiCad ERC, KiCad DRC, the critical-net contract and the
board's own manufacturability checks. The board is installed only if every gate
passes. Drop `--install` to build and check without touching the project.

Everything the run produced is kept in `build/`: the pre-route board, the routed
candidate, the plan that was executed, every command with its output, and the
validation reports.

Copper is never edited after routing. A failure is fixed in whichever input
caused it - the generator for placement and fanout, the rules or keep-outs for a
manufacturing violation, the critical-route generator for a clock, the routing
plan for anything else - and the board is built again from the beginning.

**Critical routing, and why each is generated rather than routed.** The
requirements come from `critical_routes` in
[constraints.json](constraints.json) and are checked after every build by
[tools/critical_nets.py](tools/critical_nets.py), which runs the validator's own
topology rule:

| Nets | Requirement | Why it is generated |
| --- | --- | --- |
| `MCLK_OSC`, `AUDIO_MCLK` | F.Cu, 0 vias | 24.576 MHz, the fastest edge on the board; a via would add a stub and a layer change in the middle of it |
| `PDM_CLK_FPGA`, `PDM_CLK_IN` | F.Cu, 0 vias | a TSSOP-20 pad row has 0.25 mm between pads, so the fan-in shape is decided by the package |
| `PDM_CLK_B0..7` | matched within 5 mm, F.Cu, 0 vias | inter-channel timing across the array; all eight are routed to one measured target |
| microphone escapes, supply ring, data spokes, ground stitching | mask keep-outs, 0.40 mm to any opening | the 0.566 mm ring corner and the plugged-via rule leave no room for a search |

## Status

**Generated, routed and DRC clean.** KiCad reports 0 DRC violations, 0
unconnected items and 0 schematic parity issues; ERC, netlist parity and the
placement audit all pass. Every via clears its nearest solder-mask opening by at
least 0.40 mm, so the board needs no filled-and-capped or resin-plugged process.
The eight PDM clock branches keep their intended topology - F.Cu only, zero
vias, and now matched to 1.1 mm rather than 24 mm - and both inner layers are
still solid ground.

Fourteen of the twenty-four test points are placed, each on top of a piece of
its own net so it needs no track of its own; the other ten have nowhere on the
top layer to go, and are listed in [docs/status.md](docs/status.md).

The fabrication outputs are committed at `generated/release/` — Gerbers, drills,
BOM, CPL, the archive, the check reports and `fabrication.json`, all built from
one board revision in a single run. They are ordinary files, so a tagged commit
stays fabricable from them years later without regenerating anything. A release
is a Git tag over such a commit, and it is not an order: JLCPCB's own part
orientations cannot be checked locally, so step through their previews before
ordering. See [docs/manufacturing.md](docs/manufacturing.md).
