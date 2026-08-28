"""Run the fused-5V-link scenario against a Board B candidate, with
the two-terminal property ESTABLISHED AUTOMATICALLY.

Board A's scenario rests on a human-recorded assertion that 5V_FUSED
is an unbranched two-terminal run. A candidate's copper is machine
routing; nobody inspected it, so nobody may hand-assert it. Instead
this script establishes the property from the board itself:

  * the toolkit's connectivity classification must say
    connectivity-complete with EXACTLY two pads;
  * the net's copper graph must contain zero track branch points
    (a T-stub would make the segment sum exceed the two-terminal
    resistance).

Only when both hold is the extraction turned into a SPICE model,
with the establishing evidence recorded in the assertion text. When
either fails, the scenario refuses - a candidate with a branched or
incomplete 5 V link gets no simulated number, not a wrong one.

Run with KiCad's python from the repository root:

    ".../kicad/python.exe" \
        benchmark/scenarios/make_candidate_5v_link.py --seed 6
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.environ.get("PCB_TOOLKIT_PATH")
                or os.path.join(REPO, "tooling",
                                "PCB_AutoDesignAndTest"))

from pcbqa import extract, geom                    # noqa: E402
from pcbqa.connectivity import NetGraph, classify_net  # noqa: E402
from pcbqa.sim import fidelity, ngspice            # noqa: E402

NET = "5V_FUSED"


def established_two_terminal(board, board_sha):
    """The automatic establishment, or an explicit refusal."""
    state = classify_net(board, NET, geom.pad_copper_polygon)
    if state["class"] != "connectivity-complete":
        raise SystemExit(
            "refusing: {} is {} on this candidate; an incomplete "
            "link gets no simulated number".format(
                NET, state["class"]))
    if state["pad_count"] != 2:
        raise SystemExit(
            "refusing: {} carries {} pads; the two-terminal "
            "property does not hold".format(NET,
                                            state["pad_count"]))
    graph = NetGraph(board, NET, geom.pad_copper_polygon)
    branches = graph.branch_points()
    if branches:
        raise SystemExit(
            "refusing: {} has {} track branch point(s); the "
            "segment sum would exceed the two-terminal "
            "resistance".format(NET, branches))
    return ("established automatically: connectivity analysis on "
            "board {} found exactly two pads ({}) in one connected "
            "copper component with zero track branch "
            "points".format(board_sha[:12],
                            " and ".join(state["pads"])))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, required=True)
    arguments = parser.parse_args()
    seed_dir = os.path.join(REPO, "benchmark", "boardB",
                            "candidates",
                            "seed{:02d}".format(arguments.seed))
    board_file = os.path.join(seed_dir,
                              "candidate_routed.kicad_pcb")

    manifest_doc = json.load(open(
        os.path.join(REPO, "board", "manifest.live.json"),
        encoding="utf-8"))
    geom.configure(manifest_doc["geometry_profile"]["tolerances"][
        "polygon_chord_error_mm"]["value"])

    sys.path.insert(0, os.path.join(REPO, "benchmark", "boardB"))
    import validate_candidate as vc
    copper, thickness, _evidence = vc.physical_inputs()

    import pcbnew
    board = pcbnew.LoadBoard(board_file)
    with open(board_file, "rb") as handle:
        board_sha = hashlib.sha256(handle.read()).hexdigest()

    assertion = established_two_terminal(board, board_sha)
    net_record = extract.extract_net(board, NET, copper, thickness)
    link = extract.interconnect_model_from_net(
        net_record, board_sha,
        {"copper_thickness_mm": copper,
         "board_thickness_mm": thickness},
        two_terminal_asserted_by=assertion)
    registry = fidelity.ModelRegistry([link])
    scenario = {
        "name": "candidate-5v-fused-link-drop",
        "description": "DC drop across the candidate's fused 5 V "
                       "entry link at 100 mA, same substitutions as "
                       "the Board A scenario (ideal 5 V source, "
                       "50 ohm demand; fuse and diode NOT modeled). "
                       "20 C because the extracted copper is fixed "
                       "at the IEC 60028 resistivity reference.",
        "elements": [
            {"kind": "vsource_dc", "name": "host",
             "nodes": ["vin", "0"], "value": 5.0},
            {"kind": "model_instance", "name": "link",
             "nodes": ["vin", "vout"], "model": link["identity"]},
            {"kind": "resistor", "name": "demand",
             "nodes": ["vout", "0"], "value": 50.0},
        ],
        "analyses": [{"kind": "op"}],
        "operating_conditions": {"temperature_c": 20.0},
        "measurements": [
            {"name": "vout", "kind": "op_voltage", "node": "vout",
             "assertion": {"op": ">=", "value": 4.999}},
        ],
        "required_coverage": {
            "interconnect_dc": ["geometry-derived"],
        },
    }
    result = ngspice.run_scenario(
        registry, scenario,
        os.path.join(seed_dir, "workdir_5v_link"))
    out_path = os.path.join(seed_dir,
                            "candidate_5v_link.result.json")
    with open(out_path, "w", encoding="utf-8",
              newline="\n") as handle:
        json.dump({"scenario": scenario, "models": [link],
                   "result": result}, handle, indent=1)
        handle.write("\n")
    line = ["status:", result["status"],
            "| R:", str(net_record["dc"][
                "segment_resistance_sum_ohm"]), "ohm"]
    if result.get("measurements"):
        vout = result["measurements"]["vout"]
        line += ["| vout:", str(vout["value"]),
                 "passed:", str(vout["passed"]),
                 "| usable_for_design_decision:",
                 str(result["result_policy"][
                     "usable_for_design_decision"])]
    print(" ".join(line))
    print("assertion:", assertion)
    return 0


if __name__ == "__main__":
    sys.exit(main())
