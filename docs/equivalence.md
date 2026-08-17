# Equivalence report — V2 baseline vs V3 through the toolkit

Every result below was produced by running the same board through the extracted
toolkit and comparing against a baseline recorded from `PCB_MicrophoneArrayV2`
at commit `9ba07cf720ed0bbb2023d121d544f0144bba7b2f` **before** any separation
work began. V2 was not modified by this migration.

V3 corresponds to `823aef258c92fd89de916de9877ae48d4d9bc3ad`, which the
reference repository gained during the migration. It differs from the baseline
commit in exactly one file, `docs/architecture.md` — documentation, not design.
That file is scanned by `CONTRACT.CONNECTOR` and `PROV.SOURCE_AUTHORITY`, so it
was brought across and the board re-validated rather than reasoned about; it is
not in the source closure, so §2 is unaffected.

Toolchain for both: Python 3.11.5, pcbnew 10.0.5, Shapely 2.1.2,
kicad-cli 10.0.5.

## 1. Design inputs are byte-identical

Of the V2 tracked source files that belong in V3, **65 of 65 are
digest-identical**, including the board, schematic, project, symbol library,
all four footprints, both library tables and `constraints.json`.

`generated/` came across as committed content: **34 of 34 tracked files
digest-identical**, excluding only `generated/route/` (7 legacy-router
artifacts).

The only intentionally different file is `.gitattributes`, which was rewritten
with an added note; its `check-attr` behaviour was verified to resolve
identically to V2's for design sources, release output, frozen evidence and
binaries.

## 2. The source closure reproduces exactly

    V2: bd2afdef6dd0df0d
    V3: bd2afdef6dd0df0d

43 entries, **0 differing**. The closure covers the board, schematic, project,
symbol library, both library tables, `constraints.json`,
`tools/jlc_orientation.py`, all 31 frozen orientation evidence files, the
manifest's configuration identity, and the digests of the three toolkit modules
that were actually imported.

This is the strongest single piece of evidence that the migration changed no
design input. It also confirms that `configuration_identity()` correctly
excludes paths: the manifest's `project_root` edit did not move it.

## 3. Validation — every gate preserved

| | V2 baseline | V3 via toolkit |
|---|---|---|
| Verdict | ACCEPTED | ACCEPTED |
| Gates reported | 29 | 29 |
| PASS / ADVISORY / N/A | 26 / 2 / 1 | 26 / 2 / 1 |

- **Gate status mismatches: 0.**
- **Finding counts identical for all 29 gates.**
- **Measurements: 137 identical, 7 differing and explained, 0 unexplained.**

The 7 explained measurement differences are run timestamps (×4), absolute
attempt-directory paths inside one recorded command, and the digest of the
manifest *file* (×2) — which differs solely by the single documented
`project_root` edit. The manifest *content* identity that feeds the source
closure is unchanged, as §2 shows.

Both approved `DRC.AUTHORITATIVE` waivers still match. They are bound to
`approved_source_sha256` and `approved_rules_sha256`, which are the digests of
the board and project files, so this is a direct consequence of §1 rather than a
separate claim.

## 4. Release — succeeds and publishes

| | V2 baseline | V3 |
|---|---|---|
| Exit | 0 | 0 |
| Verdict | ACCEPTED | ACCEPTED |
| Published | yes | yes |
| Files in package | 23 | 23 |
| Package source closure | `bd2afdef6dd0df0d` | `bd2afdef6dd0df0d` |
| Sealed | NO | NO |

Clean-room steps ran identically: `erc` 0, `drc` 5, `gerbers` 0, `drill` 0,
`cpl` 0, `bom` 0, `fab_naming` 0, `cpl_orientation` 0, `fab_format:cpl` 0,
`fab_format:bom` 0.

A published release is a **candidate**, not an order. Sealing still requires
recorded visual-review evidence, and no order has been or may be placed without
human approval.

## 5. Fabrication outputs — equivalent in design content

All **23 of 23** files equivalent; same file set, nothing extra, nothing
missing.

**Byte-identical:** `bom.csv`, `cpl.csv`, `UNSEALED.txt`. The assembly data —
the most consequential output — matches exactly.

**Equivalent after declared normalisation:** 11 Gerbers, 2 Excellon drills, the
fabrication archive (13 members), `MANIFEST.md`, `RECEIPT.json`,
`clean_room.json`, `validation.json`, and both check reports.

Each Gerber and drill differs on **exactly 2 lines out of thousands** — the X2
`TF.CreationDate` attribute and the KiCad creation-date comment. For example
`microphone_array_v2.GTL`: 3387 lines, 2 differ, both timestamps.

### Every normalisation applied, and nothing else

| Normalisation | Why |
|---|---|
| X2 `TF.CreationDate` | run timestamp |
| KiCad `Created by KiCad … date` comment | run timestamp |
| Excellon `; DRILL file … date` and `; #@! TF.CreationDate` | run timestamp |
| Timestamps: `date`, `generated_utc`, `written_utc`, `started_utc`, `finished_utc`, `seconds`, `duration_s` | run timing |
| Identifiers: `attempt_id`, `release_id`, `run_root`, `attempt` | per-run identity |
| Absolute filesystem paths, `command`, `fresh_export_command`, `project_root` | machine and attempt location |
| `constraint_manifest_sha256` and the 12-hex provenance citation suffix | digest of the manifest *file*; the one documented path edit |
| SHA-256 digests recorded inside receipts, reports and the release manifest | they are digests **of the files above**, every one of which is compared directly and independently in the same run |
| `purged`, `files`, `files_present`, `files_recorded` | counts of the clean-room's copy of the project, not of the design — see §6 |

## 6. The one count that changed, fully accounted for

`PROV.FIXTURE_INTEGRITY` reports 117 files in V2's frozen copy and 80 in V3's.
Every file in that difference is intentional:

**Removed (21):** `pcbflow.json`; the legacy-router scripts `apply_escapes.py`,
`close_gaps.py`, `merge_routing.py`, `patch_dsn.py`, `pcbflow.py`,
`kicad_specctra.py`; the 7 files under `generated/route/`; `compare_boards.py`,
`render_copper_layers.py` and `sexpr.py` (promoted to the toolkit);
`routing_plan.json` (moved to `board/`); and `cleanup_tracks.py`,
`patch_board.py`, `place_testpoints.py` (moved to `tools/quarantine/`, still
present).

`candidates/` (2 boards) was initially treated as disposable output and left
behind. That silently turned two toolkit tests into skips — the ones proving a
source enters the closure because it was *selected*, not because a glob reached
it. Both boards were restored, digest-identical. The manifest excludes
`candidates/**` from the closure, so the closure digest is unaffected — verified
after the copy.

**Added (11):** `CLAUDE.md`, `board/manifest.live.json`,
`board/routing_plan.json`, `board/toolchain.json`, `docs/migration.md`,
`tools/_toolkit.py`, `tools/test_imports.py`, and the four files under
`tools/quarantine/`.

**Unexplained differences: 0.**

## 7. Regeneration chain

All 23 V3 entry points import, with every toolkit dependency resolving through
the pinned toolkit path and nothing resolving from a sibling checkout or the V2
repository. Verified by `tools/test_imports.py`, which also resolves every
module-level path constant — a check added after four tools were found still
pointing at the old `verification/boards/live.json`.

**The generator was not run.** Regeneration equivalence is a separate exercise
with its own review, as recorded in `docs/migration.md`.

## 8. Fresh clone

A clone of this repository, validated against a clone of the toolkit:

| | Result |
|---|---|
| Verdict | **ACCEPTED**, exit 0 |
| Gates | 26 PASS, 2 ADVISORY, 1 NOT_APPLICABLE |
| Design files | byte-identical to V2, both waivers matching |
| Toolkit fixture | `PROV.FIXTURE_INTEGRITY` PASS, 70/70 files |

Getting there required fixing an inherited line-ending defect that made the
first two clone attempts fail — see "Line endings" in
[migration.md](migration.md). The clone proof earned its place: nothing else in
this report would have caught it, because every other check ran against a
working tree that had never been through a checkout.

Two things this does **not** yet prove, both blocked on publishing the toolkit:

- the clone was made from the local repository, not from GitHub;
- `--recursive` was not exercised, because the submodule is not yet added.

## 9. Reproducing this report

```bash
"C:/Program Files/KiCad/10.0/bin/python.exe" tooling/PCB_AutoDesignAndTest/run.py validate board/manifest.live.json
```
```bash
"C:/Program Files/KiCad/10.0/bin/python.exe" tooling/PCB_AutoDesignAndTest/run.py release board/manifest.live.json
```
```bash
"C:/Program Files/KiCad/10.0/bin/python.exe" tools/test_imports.py
```
