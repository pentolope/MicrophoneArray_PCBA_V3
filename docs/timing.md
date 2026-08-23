# Interconnect timing

This board declares an interconnect timing policy in
[board/manifest.live.json](../board/manifest.live.json) under `timing`. It
drives the toolkit's `TIMING.*` and `STACK.PHYSICAL` gates, and it measures
copper.

Report what was measured with:

```bash
"C:/Program Files/KiCad/10.0/bin/python.exe" tools/check_timing.py
```

## What this is not

The gates measure the passive propagation of the board's own interconnect
between two pads. They are not a clock timing analysis, and the reports say so
in their own scope text rather than leaving it to be inferred.

Arrival time at a microphone also depends on the FPGA's output timing, the
`SN74LVC244APWR`'s propagation delay and its output-to-output skew, both
packages, the microphones' input threshold behaviour, and process, voltage and
temperature. None of that is on the board and none of it is derivable from
geometry. On this clock tree the buffer's own output-to-output skew is a few
nanoseconds, which is larger than the entire copper term — so PCB interconnect
skew is a *component* of clock arrival skew here, and a small one.

`TIMING.SETUP_HOLD` is the gate that would answer the whole question. It stays
`NOT_APPLICABLE` because this board declares no `timing.device_timing`, and
that is the correct answer rather than a gap.

## Why a net is not a path

`net_topology.rules`' `PDM_CLOCK_BRANCHES` rule measures net `PDM_CLK_Bn`.
That net begins at `RCn.2`, the *output* side of the branch resistor, because
that is where KiCad says it begins. Everything between the buffer pin and
`RCn.1` is on `PDM_CLK_Yn` — a different net — and is therefore not in that
measurement at all.

That rule is not wrong. It enforces the frozen requirement it was written for,
`critical_routes.pdm_clock_branches.branch_length_match_mm = 5.0`, which is
about branch matching downstream of the resistors, and it still passes. It
simply answers a narrower question than "how long does the signal take to get
there".

The `timing` block declares the whole path instead:

```
U2.<n>  --PDM_CLK_Y<n>-->  RC<n>.1 -- RC<n> --> RC<n>.2  --PDM_CLK_B<n>-->  MK<n>.3
```

Eight routes, one per branch, each fanning out to the two microphones on that
branch: sixteen complete paths. `required_component_crossings: 1` asserts that
every one of them crosses its resistor, which is what makes it impossible for a
path to be silently measured from the wrong side.

## What it measures

Complete paths, buffer pin to microphone clock pad:

| | length |
|---|---|
| shortest | 73.179 mm at `MK9.3` |
| longest | 85.264 mm at `MK10.3` |
| spread | 12.085 mm |

The same sixteen endpoints counted only from the resistors' output nets — what
a net-scoped measurement can see — run 63.195 mm to 75.280 mm. The copper
upstream of the resistors, invisible to that measurement, is 5.346 mm to
10.613 mm depending on the branch.

Two branches are not even on one layer. `PDM_CLK_Y2` and `PDM_CLK_Y3` drop to
`B.Cu` and back, so `pdm_clock_branch_3` and `pdm_clock_branch_4` each carry
two via transitions and 3.9–5.4 mm of `B.Cu`. A measurement scoped to
`PDM_CLK_B*` — which is `F.Cu` only, zero vias — cannot see any of that.

The endpoint-to-endpoint *spread* happens to be 12.085 mm either way, because
the extreme pair (`MK9.3` and `MK10.3`) sits on the same branch and its shared
upstream copper cancels. That is a coincidence of this layout, not a reason the
narrower measurement would do.

## No skew limit is declared, deliberately

`microphone_clock_endpoints` groups all sixteen endpoints and declares no
limit, so `TIMING.INTERCONNECT_SKEW` reports the spread and is
`NOT_APPLICABLE` rather than passing.

The frozen requirement this board carries — `branch_length_match_mm = 5.0` —
governs a different quantity: the spread of the longest driver-to-load path
*per net*, post-resistor. `NET.TOPOLOGY` enforces that and it passes. An
endpoint-to-endpoint limit over complete paths is a requirement this design has
never stated, and writing one into the manifest would be a new constraint
introduced as a measurement. If such a requirement is wanted, it belongs in
`constraints.json` as a reviewed design decision, and the group here can then
cite it.

## The physical stackup is incomplete, and that is recorded

`microphone_array_v2.kicad_pcb` carries no `(setup (stackup ...))` block.
KiCad only stores a physical stackup when a designer fills one in, and this
board's was never filled in. The board file therefore states:

- overall thickness, 1.6 mm;
- four copper layers, `F.Cu`, `In1.Cu`, `In2.Cu`, `B.Cu`;
- zones proving both inner layers are unbroken `GND` — so every clock trace on
  this board is a microstrip over a continuous reference plane, and none
  crosses a plane split.

It states nothing about dielectric thickness, material, relative permittivity
or loss tangent.

[board/timing/physical_stackup.json](../board/timing/physical_stackup.json)
declares the *structure* of the stack — which dielectric sits between which
coppers — and leaves every material figure explicitly `null`. It invents
nothing. `STACK.PHYSICAL` therefore names each absent field instead of
reporting the useless "no physical stackup at all", and
`TIMING.INTERCONNECT_DELAY` reports no picosecond figure for any of the sixteen
paths, with fidelity `geometry-only` and a per-conductor reason.

Six figures are missing and needed for a delay:

```
dielectric 1 (F.Cu -> In1.Cu)   thickness, relative permittivity
dielectric 2 (In1.Cu -> In2.Cu) thickness, relative permittivity
dielectric 3 (In2.Cu -> B.Cu)   thickness, relative permittivity
```

Loss tangent and copper weight are also absent but do not block a delay: loss
tangent sets attenuation rather than velocity, and copper thickness is only an
input to the thickness-corrected model, which this board does not select.

### To complete it

All six come from one place: the fabricator's stackup drawing for the ordered
service and thickness. Record them in `physical_stackup.json` with the same
discipline the toolkit's JLCPCB process profile demands of a process limit —
source, document identifier or URL, retrieval date, the service and thickness
it applies to, and the frequency the Dk is quoted at — and put that in its
`provenance` field.

Dk is frequency dependent. The PDM clock is 3.072 MHz with edges one to two
orders faster, so a figure quoted at 1 GHz is the appropriate one to use and
the one to record.

Do not fill them in from a textbook, a typical value, or another board. A delay
estimate whose Dk was guessed is not a weaker version of one whose Dk was
measured; it is a different kind of thing wearing the same units, and nothing
downstream could tell them apart.

Once they are recorded, set `timing.physical_stackup.require_complete` to
`true` if any limit is expressed in picoseconds, so that an unevaluated
requirement blocks.

## Model and fidelity

`timing.propagation` selects the analytic backend and Hammerstad's closed form
for microstrip effective permittivity. The model is named explicitly rather
than defaulted, because the choice changes the number and therefore belongs in
the configuration identity that provenance hashes.

`c/sqrt(Dk)` is deliberately *not* used for the outer layers. Part of a
microstrip's field is in air, so it sees an effective permittivity below the
laminate's; using Dk directly would over-state delay by roughly fifteen to
twenty-five per cent on ordinary FR-4 geometry, and the error varies with trace
width — this board's clock copper is both 0.15 mm and 0.25 mm — so it would not
cancel in a skew comparison either.

Via delay is `none`: vertical extent is measured and reported, and no delay is
attributed to it. The geometric model would need the dielectric thicknesses
this board does not have, and attributing an invented one to exactly the two
branches that change layer would bias the comparison the measurement exists to
make.

## Provenance

`board/timing/*.json` is in `reports.source_closure`, and
`PROV.TIMING_MODELS` proves each declared model file exists and is a closure
member. The `timing` block itself is manifest content, so it is inside the
clean room's configuration identity automatically — changing a limit, a route
or a model choice changes the identity of every result derived from it.

Adding this block changed that identity, which is the mechanism working: the
committed release reports bind the previous one, so `PROV.REPORT_FRESHNESS`
reports them stale until a clean-room release is run again and the package it
publishes is installed. That is not a defect to be worked around by excluding
`timing` from the identity — it is the guarantee that a result cannot outlive
the configuration it was derived under.
