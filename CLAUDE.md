# MicrophoneArray_PCB_V3 — 16-channel PDM microphone array carrier

## Mission

The authoritative design for a 120 mm circular, four-layer KiCad 10 carrier for
sixteen `MSM261DHP006` PDM MEMS microphones, manufactured and assembled by
JLCPCB.

Generic PCB verification, routing and release logic lives in the toolkit, which
this repository consumes as a pinned Git submodule at
`tooling/PCB_AutoDesignAndTest`. This repository owns the board: its native
KiCad files, its generator, its constraints, its manifests, its waivers and its
selected JLCPCB options.

## Provenance

This design was migrated from `PCB_MicrophoneArrayV2` at commit
`823aef258c92fd89de916de9877ae48d4d9bc3ad`, with the equivalence baseline
recorded at `9ba07cf720ed0bbb2023d121d544f0144bba7b2f` (the two differ only in
`docs/architecture.md`). The native KiCad files were copied **byte for byte**;
the generator chain was copied and **was not run**.

See [docs/migration.md](docs/migration.md) for what changed and why, and
[docs/equivalence.md](docs/equivalence.md) for the proof that it did not.

## Authority and safety

1. Native KiCad `.kicad_sch`, `.kicad_pcb`, `.kicad_pro` and `.kicad_dru` files
   are the final design authority. The committed board is authoritative as
   committed.
2. `tools/` can regenerate the board, but a regenerated board is a **candidate**
   that must pass every gate and an explicit equivalence review before it
   replaces the committed one. The generator is not a second design authority.
3. Use KiCad Routing Tools only, to propose tracks and permitted new routing
   vias. The superseded external autorouter and its Specctra DSN/SES exchange
   have been removed from this repository, and neither is to be reintroduced.
4. Never overwrite the source board while generating or importing a route.
   Route only into fresh candidate paths.
5. Do not change the electrical design, netlist, placement, board outline,
   stackup, holes, origins, keepouts, net classes, critical routing, assembly
   intent, or manufacturing requirements except as a deliberate, reviewed
   design change.
6. Do not weaken a check, add a waiver, suppress a finding, or change an
   expected result merely to make a test pass.
7. **Automated tools must not move, remove, resize, redrill, re-layer, retype
   or reassign a pre-existing via.** A needed change is made in the
   authoritative input and the candidate regenerated.
8. Do not run a cleanup, smoothing, repair, merge or optimisation pass that
   silently rewrites routed copper. The scripts in `tools/quarantine/` do
   exactly that; they are kept for reference and **must not be run against the
   authoritative board.**
9. Do not commit, push, create a pull request, change a remote, or update the
   submodule pointer without explicit user authorisation.
10. **Never submit an order.** JLCPCB Gerber and placement previews require
    human approval. A local release is a candidate, not an order.

## The waivers are bound to the board's bytes

`board/manifest.live.json` carries two approved `DRC.AUTHORITATIVE` waivers for
`track_not_centered_on_via`. Each records `approved_source_sha256` and
`approved_rules_sha256`, which are the SHA-256 of `microphone_array_v2.kicad_pcb`
and `microphone_array_v2.kicad_pro`.

If either file changes by one byte, the waivers stop matching and those DRC
findings block again. That is deliberate. Do not "fix" it by re-recording the
digests: re-approve the waiver against the new board, with a reason, or change
the board so the finding goes away.

## Repository boundary

Owned here:

- native KiCad board, schematic, project and libraries
- `tools/` — the generator chain (`design.py`, `netlist.py`, `gen_pcb.py`,
  `gen_schematic.py`, `gen_symbols.py`, `manufacturing.py`) and the checks that
  are genuinely specific to this board
- `board/` — live manifest, routing plan, selected JLCPCB options, toolchain
  paths
- `constraints.json` — frozen requirements
- `fabrication/jlc_orientation/` — frozen LCSC orientation evidence for the
  parts this board uses
- board documentation and release outputs

Owned by the toolkit, and not to be restated or relaxed here:

- gate implementations, rule types, measurement definitions
- JLCPCB-wide capability and process limits
- clean-room release lifecycle, publication and coherence

`tools/jlc_orientation.py` stays in this repository even though it is
JLCPCB-shaped: the live manifest binds it by project-relative path as a
declared reproduction input, and `PROV.SOURCE_CLOSURE` requires it inside the
project's closure. Its reusable core already lives in the toolkit as
`pcbqa/orientation.py`.

## Validation and release gates

- Run ERC and DRC with all severities, exclusions, unconnected items, all track
  errors, zone refill, exit-code checking and schematic parity.
- Treat errors, warnings, exclusions and unconnected items as blocking unless an
  exact reviewed waiver identifies the rule, objects, location and reason.
- Inspect actual Gerber geometry, drills, outline, mask, paste and silkscreen —
  not filenames or counts alone.
- Compare every BOM/CPL designator, coordinate, side, rotation, DNP state and
  explicit library-zero offset against the same final board revision.
- Generate all release artifacts in one clean-room attempt and publish only
  after every mandatory gate passes.
- Record the board commit, toolkit submodule commit, configuration hashes, tool
  versions and output hashes.

## Publishing discipline

Before any push of cycle work, run `/claim-audit` on the drafted
commit message and report (every claim-bearing word binds to an
artifact recomputed on the spot, never to the process that produced
it), then `/pre-push-review` (a fresh-context adversarial subagent
attacks the diff and drafts against the standing invariants).
Genuine findings are fixed before the push; dismissals are recorded
with evidence. The claim table and the review's disposition belong
in the cycle report.

## Toolkit consumption

The toolkit is used **only** from `tooling/PCB_AutoDesignAndTest`, pinned to a
commit that exists on its remote. Board tools reach it through
`tools/_toolkit.py`; no sibling checkout, no absolute path, no PYTHONPATH
assumption. `PCB_TOOLKIT_PATH` exists to test against a local toolkit checkout
before the submodule is committed — it is a development affordance, and nothing
committed here may depend on it.

A fresh recursive clone must validate and release the board with no manual
setup beyond checking out submodules.

## Running

Ubuntu, and the system Python 3. KiCad is the distribution package, so
`pcbnew` imports from `/usr/lib/python3/dist-packages` and `kicad-cli`
is on PATH - there is no separate bundled interpreter to find. See
[docs/environment.md](docs/environment.md) for the exact packages and
versions this board is verified against.

```bash
git submodule update --init --recursive
```

```bash
python3 tooling/PCB_AutoDesignAndTest/run.py validate board/manifest.live.json
```

```bash
python3 tooling/PCB_AutoDesignAndTest/run.py release board/manifest.live.json
```
