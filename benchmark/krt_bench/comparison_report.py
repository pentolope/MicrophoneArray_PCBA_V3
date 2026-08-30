"""The old-vs-new comparison, recomputed from artifacts on the spot.

Reads every benchmark artifact this cycle produced - replay
records, probes, the agreement analysis, the repair experiment,
the portfolio documents when present - plus the committed
historical derivations, and emits one machine-readable comparison
with a compute ledger that must add up (pcbqa.compute refuses a
ledger that does not).

Axes are reported separately, never blended into one number:
  routing outcome, compute efficiency, candidate selection,
  repair capability, diagnostic quality.
"""

from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
RESULTS = os.path.join(HERE, "results")

sys.path.insert(0, os.environ.get("PCB_TOOLKIT_PATH")
                or os.path.join(REPO, "tooling",
                                "PCBA_AutoDesignAndTest"))
from pcbqa import compute                           # noqa: E402


def load(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def replay_axis():
    rows = []
    ledger = []
    for candidate in sorted(os.listdir(RESULTS)):
        replay_path = os.path.join(RESULTS, candidate, "krt0213-a",
                                   "replay.json")
        if not os.path.isfile(replay_path):
            continue
        doc = load(replay_path)
        historical = doc["historical_evidence"]
        row = {"candidate": candidate,
               "krt_identity_sha256": doc["krt"]["identity_sha256"],
               "historical_route_seconds":
                   historical["total_route_seconds"]}
        rerun_route_seconds = 0.0
        for record in doc["records"]:
            stage = record.get("stage", "")
            if stage == "historical_endstate":
                row["historical_complete"] = record.get("complete")
                row["historical_missing"] = record.get(
                    "nets_not_complete")
                row["historical_vias"] = record.get("vias")
            elif stage == "rerun_endstate":
                row["rerun_complete"] = record.get("complete")
                row["rerun_missing"] = record.get(
                    "nets_not_complete")
                row["rerun_vias"] = record.get("vias")
            elif stage == "determinism_check":
                row["byte_deterministic"] = record.get("identical")
            elif stage == "determinism_semantic_comparison":
                row["semantically_deterministic"] = record.get(
                    "same_missing_set")
            if "seconds" in record and record.get("status") \
                    and not stage.startswith("repeat_"):
                rerun_route_seconds += record["seconds"]
                ledger.append({
                    "label": "{} {}".format(candidate, stage),
                    "category": "full-routing",
                    "seconds": record["seconds"]})
        row["rerun_route_seconds"] = round(rerun_route_seconds, 1)
        rows.append(row)
    return rows, ledger


def probe_axis():
    agreement_path = os.path.join(RESULTS, "probe_agreement.json")
    agreement = (load(agreement_path)
                 if os.path.isfile(agreement_path) else None)
    ledger = []
    probes_dir = os.path.join(RESULTS, "probes")
    if os.path.isdir(probes_dir):
        for name in sorted(os.listdir(probes_dir)):
            if not name.endswith(".json"):
                continue
            probe = load(os.path.join(probes_dir, name))
            if probe.get("seconds") is not None:
                ledger.append({
                    "label": "probe " + probe["name"],
                    "category": "probe-routing",
                    "seconds": probe["seconds"],
                    "classification": "diagnostic"})
    return agreement, ledger


def repair_axis():
    path = os.path.join(RESULTS, "repair_seed34.json")
    return load(path) if os.path.isfile(path) else None


def portfolio_axis():
    out = {}
    base = os.path.join(HERE, "portfolio", "seed34")
    for run in ("run-a", "run-b"):
        doc_path = os.path.join(base, run, "portfolio.json")
        if not os.path.isfile(doc_path):
            out[run] = None
            continue
        doc = load(doc_path)
        candidates = doc.get("candidates") or []
        out[run] = {
            "seed": doc.get("seed"),
            "candidates": len(candidates),
            "kept": doc.get("kept"),
            "ranking_static": doc.get("ranking_static"),
            "ranking_routed": doc.get("ranking_routed"),
            "rule1_violators": doc.get("rule1_violators"),
            "crossings_by_index": {
                str(c.get("index")): (c.get("metrics") or {}).get(
                    "crossings")
                for c in candidates},
            "route_by_index": {
                str(c.get("index")): c.get("route")
                for c in candidates if c.get("route")},
        }
    determinism = None
    if out.get("run-a") and out.get("run-b"):
        a, b = dict(out["run-a"]), dict(out["run-b"])
        # The determinism claim covers the generated slate and the
        # static ranking. The probe phase re-runs the router, which
        # is already shown not byte-deterministic, so probe-derived
        # fields are compared separately.
        static_equal = all(
            a.get(key) == b.get(key)
            for key in ("candidates", "kept", "ranking_static",
                        "crossings_by_index"))
        timeouts = {
            run: sorted(
                index for index, verdict in
                (out[run].get("route_by_index") or {}).items()
                if verdict and verdict.get("failures") is None)
            for run in ("run-a", "run-b")}
        agreeing = disagreeing = 0
        for index, verdict_a in (a.get("route_by_index")
                                 or {}).items():
            verdict_b = (b.get("route_by_index") or {}).get(index)
            if not verdict_a or not verdict_b:
                continue
            if verdict_a.get("failures") is None \
                    or verdict_b.get("failures") is None:
                continue
            if verdict_a.get("failures") == verdict_b.get(
                    "failures") and verdict_a.get(
                    "iterations") == verdict_b.get("iterations"):
                agreeing += 1
            else:
                disagreeing += 1
        determinism = {
            "static_slate_identical": static_equal,
            "routed_ranking_identical":
                a.get("ranking_routed") == b.get("ranking_routed"),
            "probe_timeouts_by_run": timeouts,
            "probes_with_identical_metrics": agreeing,
            "probes_with_differing_metrics": disagreeing,
            "disposition": ("probe verdicts that completed in both "
                            "runs are compared by failures and "
                            "iteration count; a probe that timed "
                            "out under concurrent machine load is "
                            "an environmental wall-clock artifact, "
                            "recorded per run above, not router "
                            "nondeterminism"),
        }
    return out, determinism


def main():
    axes = {}
    ledger = []
    replays, replay_ledger = replay_axis()
    ledger += replay_ledger
    axes["routing_outcome"] = replays
    agreement, probe_ledger = probe_axis()
    ledger += probe_ledger
    axes["candidate_selection_probe"] = agreement
    axes["repair_capability"] = repair_axis()
    portfolio, portfolio_determinism = portfolio_axis()
    axes["candidate_selection_portfolio"] = portfolio
    axes["portfolio_determinism"] = portfolio_determinism
    axes["diagnostic_quality"] = {
        "statement": ("0.21.3 emits per-run JSON_SUMMARY with "
                      "named blockers (blocking net, cell counts), "
                      "boxed-in verdicts with geometry, failed "
                      "pads by reference and coordinate, rescue "
                      "and reconciliation ledgers; 0.19.1-era "
                      "attempts recorded only a log tail and a "
                      "parsed min_clearance_used"),
        "evidence": "results/*/krt0213-a/*/log.txt and "
                    "replay.json json_summaries fields",
    }

    summary = compute.summarize(ledger)
    doc = {
        "kind": "krt-benchmark-comparison",
        "axes": axes,
        "compute_ledger_entries": ledger,
        "compute_summary": summary,
        "compute_ledger_scope": (
            "ONLY the replay stage attempts (determinism repeats "
            "excluded) and the standalone clock probes are "
            "ledgered - their seconds exist as artifact fields. "
            "Fabrication DRC checks, both portfolio runs, the "
            "repair loop's internal routes and the seed36/seed37 "
            "pipeline runs recorded no per-step wall seconds in "
            "their artifacts and are NOT in this ledger; no "
            "completeness is asserted, and the compute summary's "
            "own meaning field says so"),
        "meaning": ("axes reported separately on purpose: no "
                    "single better/worse figure exists across "
                    "them, and none is claimed. Portfolio probe "
                    "verdicts under candidate_selection_portfolio "
                    "are the router's own tallies, recorded as "
                    "such; board-measured verdicts exist only "
                    "where a clock-probe artifact names a board "
                    "sha"),
    }
    out = os.path.join(RESULTS, "comparison.json")
    with open(out, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(doc, handle, indent=1)
        handle.write("\n")
    for row in replays:
        print("{:8s} historical {} ({}s) -> rerun {} ({}s)".format(
            row["candidate"], row.get("historical_complete"),
            row["historical_route_seconds"],
            row.get("rerun_complete"),
            row["rerun_route_seconds"]))
    print("compute:", summary["by_category"])
    print("wrote", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
