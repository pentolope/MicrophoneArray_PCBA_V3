# JLCPCB selection requirements — where each number comes from

`jlcpcb_requirements.json` feeds the toolkit's fabricator selection
(`run.py fab select`). Every value is derived from committed board sources;
nothing is a preference invented for the selector.

| Requirement | Value | Source |
|---|---|---|
| `copper_layers` | 4 | `constraints.json` `board.layers` (F.Cu, In1.Cu, In2.Cu, B.Cu) |
| `board_thickness_mm` | 1.6 | `constraints.json` `board.thickness_mm`; `docs/manufacturing.md` order options |
| `min_track_mm` | 0.15 | measured minimum segment/arc width actually present in `microphone_array_v2.kicad_pcb` (414 items at 0.15 mm; the 0.127 mm rule floor is unused) |
| `min_space_mm` | 0.15 | tightest net-class clearance in `microphone_array_v2.kicad_pro` (PLANE, MIC_SUPPLY, MIC_DATA, MANUAL_CRITICAL, PDM_DATA at 0.15 mm); DRC passes at these classes |
| `min_drill_mm` | 0.3 | `constraints.json` `rules_mm.via_drill`; board rule `min_through_hole_diameter` |
| `min_via_diameter_mm` | 0.45 | `constraints.json` `rules_mm.via_diameter`; board rule `min_via_diameter` |
| `outer_copper_oz` | 1.0 | `constraints.json` `board.outer_copper_oz`; `docs/manufacturing.md` |
| `inner_copper_oz` | 0.5 | `constraints.json` `board.inner_copper_oz`; `docs/manufacturing.md` |
| `impedance_control` | false | `docs/manufacturing.md` order options: "Impedance control: Not required" |

Result against the approved JLCPCB catalog (normalized
`f7cd05e75e1f9d6d`, parser v6, retrieved 2026-08-25): feasible, and the
stackup candidate set is exactly one - `JLC-4L-no-requirement`, JLCPCB's
own published default 4-layer construction when no impedance requirement
is stated. Under the v2 catalog the answer is coupling-checked end to end:
the 0.15/0.15 mm geometry is judged against the limits JLCPCB publishes
for exactly 1 oz outer and 0.5 oz inner copper at 4 layers (strictest
0.09/0.09 mm), and the selected construction is the one whose own table
states this copper build (0.035 mm outer at the stated 35 um/oz
equivalence; "H/HOZ" half-oz cores, corroborated by the impedance
calculator's stated 15.2 um finished half-oz inner thickness) at 1.6 mm
nominal, with 4 layers confirmed against the fabricator's discrete
offered counts rather than a numeric range. Under the v4 catalog every
remaining requirement is class-scoped too: 1.6 mm carries no stated
layer restriction (the thickness resource page forbids 0.6 mm for
4-layer boards, not 1.6), the copper weights come from records whose
stated scope covers 4-layer boards (0.5 oz inner being the stated
default, the one inner weight the fabricator's own availability caveat
does not reach), and the 0.3 mm drill / 0.45 mm via clear the
multilayer rows of the per-class drilling table. The selection
output carries the catalog digest and freshness, so the claim is
reproducible offline against the same approved snapshot.

This file records an inspection. It does not change the board, and no
exported stackup supplement has been wired into the board's timing inputs.
