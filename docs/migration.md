# Migration record — V2 → V3 + toolkit submodule

## Source

| | |
|---|---|
| Repository | `https://github.com/pentolope/PCB_MicrophoneArrayV2` |
| Commit | `823aef258c92fd89de916de9877ae48d4d9bc3ad` |
| Baseline recorded at | `9ba07cf720ed0bbb2023d121d544f0144bba7b2f` |
| Working tree at migration | clean |

Two commits are named because the reference repository moved during the
migration. The phase-0 baseline — every gate result, measurement and artifact
digest this migration is checked against — was recorded at `9ba07cf`. `823aef2`
landed later and changes **exactly one file**, `docs/architecture.md`, which
names `J1`/`J2`/`J3` explicitly and describes the direct-mate host socket.

That file is documentation. It is scanned by `CONTRACT.CONNECTOR` and
`PROV.SOURCE_AUTHORITY`, so it can affect gate results and was re-validated
after being brought across. It is **not** in `reports.source_closure`, so the
closure digest is unaffected. Every design file is byte-identical at both
commits.

The V2 repository was treated as read-only throughout and its tracked files
were not modified.

## Native design files — byte-identical

Copied byte for byte, filenames unchanged. Verified by SHA-256 before and after.

| File | SHA-256 |
|---|---|
| `microphone_array_v2.kicad_pcb` | `9DDB4323…8C26F4F` |
| `microphone_array_v2.kicad_pro` | `C9B52FAE…89828B027` |
| `microphone_array_v2.kicad_sch` | `37135A84…3B27C4997` |
| `MicArrayV2.kicad_sym` | `05792779…EE78D500F` |
| `constraints.json` | `091F2C9A…10BEE75855` |

Also byte-identical: `MicArrayV2.pretty/` (4 footprints), `fp-lib-table`,
`sym-lib-table`, `docs/` (except the routing/sources edits below),
`fabrication/jlc_orientation/` (31 files), and every generator source except the
four files listed under *Generator edits*.

`candidates/` came across too — 2 boards, both digest-identical. They are
excluded from the source closure by the manifest, but the toolkit's tests need
a second selectable board in the project to prove that a source is covered
because it was *selected* rather than because a glob happened to reach it.
Omitting them turned two tests into skips.

`generated/` came across too — 34 tracked files, all digest-identical, with only
the legacy-router scratch (`generated/route/`, 7 files) left behind. This is
committed content, not disposable output: the installed release under
`generated/release/` is what `ARCH.CONTENTS`, `ARCH.PROVENANCE`,
`BOM.NATIVE_PARITY`, `CPL.NATIVE_PARITY`, `CPL.ORIENTATION`,
`STACK.GERBER_PARITY`, `VIA.NATIVE_GERBER_AGREEMENT`, `FAB.LAYER_IDENTITY` and
`PROV.RELEASE_COHERENCE` validate against, and `generated/release/reports/*.json`
are the committed reports `PROV.REPORT_FRESHNESS` ties to the source closure.
Omitting it turned nine gates into `ERROR`.

Of 136 tracked V2 source files, 65 are present in V3 and **65 of 65 are
digest-identical**. The remaining 71 either stay in V2's history (the
`verification/` tree, now the toolkit), were removed as legacy-router assets, or
moved to a different path in V3.

**Why this matters beyond tidiness:** the two approved DRC waivers in
`board/manifest.live.json` record `approved_source_sha256` and
`approved_rules_sha256`, which are the digests of the board and project files.
A single changed byte unbinds them and the waived findings block again.

## Generator edits — not byte-identical, and why

The regeneration chain is present and complete, and **was not executed** during
the migration. Four files carry the minimum edits the repository split
requires. No logic changed in any of them.

| File | Edit |
|---|---|
| `tools/build.py` | reads `board/toolchain.json` for `kicad-cli` instead of the removed workflow config; `PLAN` points at `board/routing_plan.json`; waiver rules read from `board/manifest.live.json`; imports `_toolkit` instead of adding a sibling `verification/` to `sys.path` |
| `tools/check_host_mating.py` | the stock KiCad footprint library path now comes from `board/toolchain.json` instead of being hard-coded, so a fresh clone works where KiCad is installed elsewhere |
| `tools/gen_pcb.py` | three comments renamed the superseded autorouter to "an autorouter" and dropped references to deleted scripts; the technical reasoning is unchanged. **No code changed.** |
| `tools/gen_schematic.py` | imports `_toolkit`, then `from pcbqa import sexpr` instead of the local `sexpr` |
| `tools/check_routes.py`, `tools/critical_nets.py`, `tools/manufacturing.py` | manifest path moved from `verification/boards/live.json` to `board/manifest.live.json`; `_toolkit` replaces the sibling `verification/` on `sys.path`. `check_routes.py` would otherwise have silently fallen back to its stale default thresholds. |
| `tools/check_netlist_parity.py` | same `sexpr` change; `KICAD_CLI` now comes from `board/toolchain.json` instead of a hard-coded absolute path |

`tools/_toolkit.py` is new: it resolves the pinned submodule and refuses to run
without it.

## Files removed

Legacy autorouter and its Specctra DSN/SES exchange: `pcbflow.py`,
`pcbflow.json`, `apply_escapes.py`, `close_gaps.py`, `merge_routing.py`,
`patch_dsn.py`, `kicad_specctra.py`, and `generated/route/`.

A case-insensitive search of the V3 working tree for the superseded router's
name returns nothing, and no reference to a deleted script remains.

## Files quarantined

`tools/quarantine/` holds `patch_board.py`, `place_testpoints.py` and
`cleanup_tracks.py`. They edit routed copper in place, are outside the build
closure, and must not be run against the authoritative board. They are kept
because `docs/status.md` refers to them and `place_testpoints.py` imports
`patch_board.py`. See `tools/quarantine/README.md`.

## Moved to the toolkit

`verification/pcbqa/**`, `verification/run.py`, `verification/schemas/`,
`verification/tests/`, the generic `clean` and `portability` fixtures, and the
generic utilities `sexpr.py`, `compare_boards.py` and `render_copper_layers.py`.

The Rev A negative fixture moved to the toolkit as
`tests/fixtures/negative/microphone_array_reva/`, curated: 84 files → 70, with
every removal and edit recorded in its `HASHES.json` and README.

## Kept here despite looking generic

`tools/jlc_orientation.py` and `tools/jlc_lookup.py` are JLCPCB-shaped, but the
live manifest binds `tools/jlc_orientation.py` by **project-relative path** as a
declared reproduction input, and `PROV.SOURCE_CLOSURE` requires it inside the
project's closure. Moving it would have changed a currently-passing gate's
measurements for no benefit. Its reusable core already lives in the toolkit as
`pcbqa/orientation.py`, which is bound into the same closure by module identity.

`tools/plot_layer.py` imports `design`, so it is board-coupled.

## Board policy now in `board/`

| File | From |
|---|---|
| `manifest.live.json` | `verification/boards/live.json`, with only `project_root` changed (`../..` → `..`), proven by diff. Both waivers, both advisory-gate declarations and all 29 mandatory gates carried through unchanged. |
| `routing_plan.json` | `tools/routing_plan.json`, unchanged |
| `toolchain.json` | new; replaces the removed workflow config |

## `tools/jlc_orientation.py` is byte-identical on purpose

It was briefly edited to point its `--registry` CLI default at the new manifest
location. That broke `PROV.REPORT_FRESHNESS`: the file is a **declared
reproduction input**, so it is inside the project's source closure, and changing
one byte moved the closure digest from `bd2afdef6dd0df0d` to `e92590d545a8e64e`
— which unbound the two committed check reports from the sources they were made
from.

The edit was reverted. The closure now recomputes to `bd2afdef6dd0df0d`,
identical to V2's.

The cost is a stale default: `python tools/jlc_orientation.py check` needs an
explicit `--registry board/manifest.live.json`. Nothing automated uses that
default — the release applies orientations through `pcbqa.orientation`, which is
bound into the closure by module identity rather than by path. Fixing the
default is a change that must be made together with a fresh release, so the
regenerated reports carry the new closure digest.

This is also the check that proves the migration preserved the design: the
closure covers the board, schematic, project, symbol library, library tables,
constraints, the orientation derivation script and all 31 frozen evidence files,
and it recomputes bit-for-bit to V2's value.

## Toolkit submodule

`tooling/PCB_AutoDesignAndTest` is pinned to a commit published on the toolkit's
remote. Board tools reach it only through `tools/_toolkit.py`, which reads the
path from `board/toolchain.json`; there is no sibling checkout and no absolute
path in anything committed here.

`PCB_TOOLKIT_PATH` still exists as a development affordance for testing against
a local toolkit checkout before its commit is published. Nothing committed here
depends on it, and the validation recorded in `docs/equivalence.md` was run with
it unset.

```bash
git submodule update --init --recursive
```

## Windows path length

The deepest path inside the toolkit submodule is 146 characters
(`tooling/PCB_AutoDesignAndTest/tests/fixtures/negative/microphone_array_reva/project/generated/release/gerbers/…`).
A recursive clone therefore needs its root to be under about 110 characters, or
`git config --global core.longpaths true`.

This is not theoretical: cloning into a long temporary directory during the
migration failed with `Filename too long` on 21 files. At a normal location such
as `C:\Users\<you>\Documents\GitHub\V3_freshclone` the total is 196 characters
and it works.

## Release output lands inside the submodule

`run.py` roots its output tree at its own directory, so a release from this
board publishes to `tooling/PCB_AutoDesignAndTest/out/<board_id>/`, not
anywhere under the board. Both working trees stay clean — the toolkit ignores
`out/` — but the artifacts sit in a directory that `git submodule update` is
entitled to replace.

Nothing is lost silently: a published release is immutable once created and the
board's own committed `generated/release/` is untouched. Still, treat that
directory as scratch, and copy anything you intend to keep out of it before
moving the submodule pointer.

`PCBQA_OUTPUT_ROOT` overrides the location if you would rather it landed
elsewhere.

## Known debt

`tools/gen_pcb.py` and `tools/gen_schematic.py` still hard-code absolute paths
to KiCad's stock footprint and symbol libraries. Neither is executed by the
validation or release flow, so neither blocks a fresh clone; parameterising them
belongs with the first real regeneration run, not with an extraction that is
premised on not running the generator.
