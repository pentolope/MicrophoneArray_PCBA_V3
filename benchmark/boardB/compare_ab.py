"""Board A vs one Board B candidate, through the sanctioned path.

Both boards are measured by validate_candidate's own measurement
code (same extractor, same approved physical evidence, same net
inventory - the committed Board A baseline's), expressed as typed
ab-metrics-3 reports, and compared ONLY through
benchmark.compare_reports, which refuses mismatched schemas,
evidence or units before any number meets another. A net the
candidate has not routed stays unmeasured (blocked_on: routing) and
appears in the blocked list, never as a zero in a total.

Nothing here calls a shorter total "better": comparable copper is
reported next to the count of unmeasured nets, and the reviewer -
or the search loop - weighs completeness first.

Run with KiCad's python from the repository root:

    ".../kicad/python.exe" benchmark/boardB/compare_ab.py --seed 2
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

from pcbqa import benchmark                        # noqa: E402

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

    nets = vc.baseline_nets()
    copper, thickness, evidence = vc.physical_inputs()
    toolkit_commit = subprocess.run(
        ["git", "-C", os.path.join(REPO, "tooling",
                                   "PCB_AutoDesignAndTest"),
         "rev-parse", "HEAD"], capture_output=True,
        text=True).stdout.strip() or "unknown"

    reports = {}
    for label, board_file in (
            ("board_a", os.path.join(
                REPO, "microphone_array_v2.kicad_pcb")),
            ("board_b", candidate_board)):
        sha, metrics = vc.measure_board(board_file, nets, copper,
                                        thickness)
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
        json.dump({"reports": reports, "comparison": comparison},
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
    spread = next(
        (pair for pair in comparison["compared"]
         if pair["name"] == "clock_leaf_length_spread_mm"), None)
    print("comparable copper totals: A {:.1f} mm | B {:.1f} mm "
          "(over {} mutually measured nets)".format(
              total_a, total_b,
              sum(1 for pair in comparison["compared"]
                  if pair["name"].endswith(":copper_length_mm"))))
    print("candidate nets unmeasured (routing): {} -> {}".format(
        len(blocked_nets), ", ".join(blocked_nets) or "none"))
    if spread is not None:
        print("clock leaf spread: A {:.3f} mm | B {:.3f} mm".format(
            spread["a"], spread["b"]))
    else:
        print("clock leaf spread: unmeasured on at least one side")
    print("report:", out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
