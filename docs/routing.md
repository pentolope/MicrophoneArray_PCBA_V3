# Routing methodology

The router is KiCad Routing Tools, driven by
[board/routing_plan.json](../board/routing_plan.json) from
[tools/build.py](../tools/build.py). The superseded external autorouter this
board was first routed with, its Specctra DSN/SES exchange and the scripts that
worked around it have been removed; `PCB_MicrophoneArrayV2` at commit
`9ba07cf` remains the historical record.

## Authority

KiCad owns the board. The router may add tracks and vias and nothing else.
Every candidate is routed into a *copy* of the pre-route board, and the build
compares the two: footprints, outline, netlist, stackup and origin must be
untouched, or the candidate is rejected.

Copper is never edited after routing. A failure is fixed in whichever input
caused it — the generator for placement and fanout, the rules or keep-outs for
a manufacturing violation, the critical-route generator for a clock, the
routing plan for anything else — and the board is built again from the
beginning. The scripts in [tools/quarantine/](../tools/quarantine) do edit
copper in place; they are kept for reference and must not be run against the
authoritative board.

**Existing vias are not the router's to move.** No automated step may move,
remove, resize, redrill, re-layer, retype or reassign a via that is already on
the board.

## What is pre-routed, and why

Three classes of copper are placed deterministically by `tools/gen_pcb.py`
rather than by the autorouter:

**Ground stitching.** Ground is a plane net that the autorouter is told to
ignore, and an inner plane cannot reach a surface-mount pad by itself. Every
ground pad therefore gets its own via plus a short connecting track. Via
direction is chosen along the pad's own axes, never diagonally: a diagonal
crosses the neighbouring pin on a fine-pitch package, and on the microphone it
lands in the corner channel the signal pads need.

**Microphone L/R straps.** Pad 2 sits inside the ground ring with a 0.40 mm gap
around it, so it cannot reach a via of its own, and it must not consume one of
the four diagonal corners. It is always on the same net as a neighbour it can
be joined to directly: ground (pad 6) on even channels, the supply pad on odd
channels.

**Microphone escapes.** The `MSM261DHP006` land pattern encloses its four
signal pads in a ring of ground pads. The straight gaps are 0.40 mm, which fits
no track at all once clearance is counted. The only way out is the diagonal
corner between a side bar and an end bar, which measures 0.566 mm — enough for
a 0.15 mm track with 0.15 mm clearance and nothing more. A corner that tight is
not something to hand to a general-purpose autorouter, so the escapes are
generated rather than searched for.

The geometry is identical for all sixteen channels and is generated in
footprint-local coordinates:

| Pad | Path |
|---|---|
| 1 (VDD) | inward through the pad 5 / pad 6 corner |
| 4 (DATA) | inward through the pad 5 / pad 8 corner |
| 3 (CLK) | outward through the pad 7 / pad 8 corner, then up the outside of the left ground bar |

Every corner waypoint is the exact midpoint of the 0.566 mm gap, giving
0.208 mm to each ground pad, and every segment is at 45 degrees or orthogonal.

The per-channel 100 nF capacitor is rotated 180 degrees so that its ground pad —
and therefore its stitching via — faces radially inward instead of sitting in
the clock escape corridor.

## The recorded plan

[board/routing_plan.json](../board/routing_plan.json) is the whole invocation.
Widths, clearances, via sizes and layers are deliberately **not** restated
there: `route.py` reads them from the sibling `.kicad_pro` net classes, which is
the same source the generator and the validator use, so there is one place to
change them and no way for the three to disagree.

Two parts of the plan are load-bearing and fail quietly if dropped:

- **`--no-fix-drc-settings`.** Without it, `route.py` rewrites the project's DRC
  floor to whatever clearance it actually managed to route at. Earlier runs
  silently dropped the Default class to 0.175 mm and then to 0.1 mm, below every
  net class on this board. The flag keeps the project's real limits
  authoritative; a stage that cannot route inside them must fail loudly.
- **Net scope.** A net is in scope only if the generator leaves it unfinished.
  `route.py`'s post-route cleanup prunes dead ends across every net it was asked
  to route, and it counts the input file's own copper as a removal candidate.
  Asked to route `+5V`, which the generator had already finished, it deleted a
  1.6 mm leg of the bus and left the rest dangling. Nets the generator completes
  are therefore excluded outright, and the pre-route DRC report is what proves
  that exclusion list still matches what the generator actually finishes.

Zones are refilled after routing: vias added by the router pierce the ground
planes, and without a refill every one of them reads as a zone clearance
violation.

## Reproducibility, stated honestly

The generated pre-route board **is** byte-identical run to run.

The routed candidate is not. `route.py` walks sets while deciding what to route
first, so its order of work varies between processes. `PYTHONHASHSEED=0` removes
the part of that caused by string hashing, but sets of objects still iterate in
address order, and two runs of this plan differ on about seven of eighty-three
nets — same via count, total copper within 0.1 percent, every gate passing
either way.

So the routing is *equivalent* rather than *repeated*, candidates are compared
semantically, and no claim of bitwise determinism is made. The gates are what
establish that equivalence.

## Post-route gates

`tools/check_routes.py` enforces the constraints KiCad's DRC cannot express:
allowed layers per net, via budgets on the clock nets, branch length matching
across the eight microphone pairs, stub length on the shared PDM data nets,
different-net crossings, and acid traps — corners left with an interior angle
under 90 degrees.

KiCad's own DRC is then the authority for clearance, shorts, holes, edge
clearance and schematic parity, run with `--all-track-errors`,
`--schematic-parity`, `--severity-all` and `--severity-exclusions`.
