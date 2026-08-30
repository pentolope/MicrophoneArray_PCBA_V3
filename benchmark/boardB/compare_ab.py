"""Board A vs one Board B candidate, through the sanctioned path.

Both boards are measured by validate_candidate's own measurement
code (same extractor, same resolved physical construction, same net
inventory - the committed Board A baseline's), governed by REAL
connectivity: only connectivity-complete nets produce comparable
metrics, a partial net's copper appears solely as an explicitly
partial inventory under its own semantic definition, and the
comparison itself runs ONLY through benchmark.compare_reports,
which refuses mismatched schemas, constructions, definitions or
units before any number meets another.

Nothing here calls a shorter total "better": comparable copper is
reported next to the count of unmeasured nets, and the reviewer -
or the search loop - weighs completeness first.

Run from the repository root:

    python3 benchmark/boardB/compare_ab.py --seed 2
"""

from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.environ.get("PCB_TOOLKIT_PATH")
                or os.path.join(REPO, "tooling",
                                "PCB_AutoDesignAndTest"))

from pcbqa import headless                        # noqa: E402
headless.suppress_blocking_ui()
from pcbqa import benchmark, freshness             # noqa: E402

import validate_candidate as vc                    # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, required=True)
    arguments = parser.parse_args()
    seed_dir = os.path.join(HERE, "candidates",
                            "seed{:02d}".format(arguments.seed))
    candidate_board = None
    for basename in ("candidate_routed.kicad_pcb",
                     "candidate_placed.kicad_pcb"):
        path = os.path.join(seed_dir, basename)
        if os.path.isfile(path):
            candidate_board = path
            break
    if candidate_board is None:
        raise SystemExit("no candidate board for seed{:02d}".format(
            arguments.seed))

    manifest_doc = json.load(open(
        os.path.join(REPO, "board", "manifest.live.json"),
        encoding="utf-8"))
    from pcbqa import geom
    geom.configure(manifest_doc["geometry_profile"]["tolerances"][
        "polygon_chord_error_mm"]["value"])
    nets = vc.baseline_nets()
    copper, thickness, evidence = vc.physical_inputs()
    toolkit_commit = subprocess.run(
        ["git", "-C", os.path.join(REPO, "tooling",
                                   "PCB_AutoDesignAndTest"),
         "rev-parse", "HEAD"], capture_output=True,
        text=True).stdout.strip() or "unknown"

    baseline = json.load(open(
        os.path.join(REPO, "benchmark", "board_a_baseline.json"),
        encoding="utf-8"))
    gates_path = os.path.join(seed_dir, "gates.json")
    if not os.path.isfile(gates_path):
        raise SystemExit(
            "no gates.json for this candidate; run "
            "validate_candidate first - complete-path truth comes "
            "from the validation gates")
    gates_doc = json.load(open(gates_path, encoding="utf-8"))
    recorded_closure = gates_doc.get("producer_closure")
    if recorded_closure is None:
        raise SystemExit("gates.json carries no producer closure; "
                         "unverifiable evidence is refused")
    verdict = freshness.verify(
        recorded_closure,
        vc.gates_closure_components(candidate_board, seed_dir))
    if not verdict["fresh"]:
        raise SystemExit(
            "gates.json is stale (moved: {}); regenerate with "
            "validate_candidate before comparing".format(
                verdict["moved"] + verdict["missing"]
                + verdict["added"]))
    delay_by_label = {
        "board_a": (baseline.get("interface_paths") or {}).get(
            "TIMING.INTERCONNECT_DELAY", {}).get("measurements"),
        "board_b": (gates_doc["statuses"].get("validation")
                    or {}).get("timing_delay_measurements"),
    }
    reports = {}
    connectivity = {}
    for label, board_file in (
            ("board_a", os.path.join(
                REPO, "microphone_array_v2.kicad_pcb")),
            ("board_b", candidate_board)):
        sha, metrics, states = vc.measure_board(
            board_file, nets, copper, thickness)
        connectivity[label] = states
        delay = delay_by_label[label]
        if delay:
            metrics.extend(vc.complete_path_metrics(delay, sha))
        else:
            metrics.append(benchmark.unmeasured(
                "complete_path_spread_mm", "board",
                vc.extract.METRIC_DEFINITIONS[
                    "complete_path_spread_mm"],
                "path-resolution",
                "no timing-gate path records exist for this "
                "board"))
        reports[label] = benchmark.report(
            {"board_file_sha256": sha,
             "toolkit_commit": toolkit_commit,
             "physical_evidence": evidence,
             "schema_version": benchmark.SCHEMA_VERSION}, metrics)

    comparison = benchmark.compare_reports(reports["board_a"],
                                           reports["board_b"])
    out_path = os.path.join(seed_dir, "ab_comparison.json")
    with open(out_path, "w", encoding="utf-8",
              newline="\n") as handle:
        json.dump({"reports": reports, "comparison": comparison,
                   # The A/B consumes the gates artifact (candidate
                   # timing paths) and the Board A baseline; both
                   # are named canonically, so regenerated timing
                   # evidence makes this comparison honestly stale.
                   "producer_closure": freshness.closure(dict(
                       vc.closure_components(candidate_board,
                                             seed_dir),
                       **{"compare_ab.py": {"text_path":
                          os.path.abspath(__file__)},
                          "gates_artifact": {"json_path":
                          gates_path},
                          "board_a_baseline": {"json_path":
                          os.path.join(REPO, "benchmark",
                                       "board_a_baseline.json")},
                          }))},
                  handle, indent=1)
        handle.write("\n")

    total_a = total_b = 0.0
    for pair in comparison["compared"]:
        if pair["name"].endswith(":copper_length_mm"):
            total_a += pair["a"]
            total_b += pair["b"]
    blocked_nets = sorted({
        entry["name"].split(":")[0]
        for entry in comparison["blocked"]
        if entry["name"].count(":")})
    b_states = connectivity["board_b"]
    incomplete = {net: state for net, state in sorted(
        b_states.items()) if state != "connectivity-complete"}
    print("candidate connectivity: {}/{} complete{}".format(
        len(b_states) - len(incomplete), len(b_states),
        "" if not incomplete else " | " + ", ".join(
            "{}={}".format(net, state)
            for net, state in incomplete.items())))
    path_spread = next(
        (pair for pair in comparison["compared"]
         if pair["name"] == "complete_path_spread_mm"), None)
    if path_spread is not None:
        print("complete PDM path spread: A {:.3f} mm | "
              "B {:.3f} mm".format(path_spread["a"],
                                   path_spread["b"]))
    else:
        print("complete PDM path spread: unmeasured on at least "
              "one side")
    path_pairs = [pair for pair in comparison["compared"]
                  if pair["name"].startswith("path:")
                  and pair["name"].endswith(":copper_length_mm")]
    if path_pairs:
        deltas = [pair["delta_b_minus_a"] for pair in path_pairs]
        print("complete PDM paths compared: {} | mean delta "
              "B-A: {:+.2f} mm".format(
                  len(path_pairs),
                  sum(deltas) / len(deltas)))
    spread = next(
        (pair for pair in comparison["compared"]
         if pair["name"] == "clock_leaf_net_length_spread_mm"),
        None)
    print("comparable copper totals: A {:.1f} mm | B {:.1f} mm "
          "(over {} mutually measured nets)".format(
              total_a, total_b,
              sum(1 for pair in comparison["compared"]
                  if pair["name"].endswith(":copper_length_mm"))))
    print("candidate nets unmeasured (routing): {} -> {}".format(
        len(blocked_nets), ", ".join(blocked_nets) or "none"))
    if spread is not None:
        print("leaf NET inventory spread (not path spread): "
              "A {:.3f} mm | B {:.3f} mm".format(
                  spread["a"], spread["b"]))
    else:
        print("leaf NET inventory spread: unmeasured on at least "
              "one side")
    print("report:", out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
