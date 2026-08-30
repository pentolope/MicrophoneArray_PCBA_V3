"""The fused-5V-link scenario, path-scoped, on BOTH boards.

The net-scoped model refuses on a branched candidate net - honest,
but it leaves the candidate without a number. The path-scoped model
is the next honest abstraction: DC resistance over the ACTUAL copper
traversal F1.2 -> D1.2, stubs excluded by construction, parallel
copper refused during extraction. Endpoint semantics are identical
on both boards (the same two pads of the same fixed parts), so the
same scenario runs against Board A and the candidate and the two
results are genuinely comparable.

Ideal source and load are DECLARED assumptions, accepted for design
decisions; the result policy carries that structurally.

Run from the repository root:

    python3 \
        benchmark/scenarios/make_candidate_5v_link.py --seed 9
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
                                "PCBA_AutoDesignAndTest"))

from pcbqa import headless                        # noqa: E402
headless.suppress_blocking_ui()
from pcbqa import extract, geom                    # noqa: E402
from pcbqa.sim import fidelity, ngspice            # noqa: E402

NET = "5V_FUSED"
FROM_PAD, TO_PAD = "F1.2", "D1.2"


def run_board(board_file, copper, thickness, workdir):
    import pcbnew
    board = pcbnew.LoadBoard(board_file)
    with open(board_file, "rb") as handle:
        board_sha = hashlib.sha256(handle.read()).hexdigest()
    path_record = extract.path_resistance(board, NET, FROM_PAD,
                                          TO_PAD, copper)
    link = extract.interconnect_model_from_path(
        path_record, board_sha,
        {"copper_thickness_mm": copper,
         "board_thickness_mm": thickness})
    registry = fidelity.ModelRegistry([link])
    vout_measurement = {"name": "vout", "kind": "op_voltage",
                        "node": "vout",
                        "assertion": {"op": ">=", "value": 4.999}}
    scenario = {
        "name": "5v-fused-link-drop-path-scoped",
        "description": "DC drop over the traced F1.2 -> D1.2 copper "
                       "traversal at 100 mA; fuse and diode NOT "
                       "modeled; 20 C because the extracted copper "
                       "is fixed at the IEC 60028 resistivity "
                       "reference.",
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
        "assumptions": {
            "host": {"stands_in_for": "the host 5 V supply, whose "
                                      "model is unavailable",
                     "accepted_for_design_decision": True},
            "demand": {"stands_in_for": "downstream demand drawing "
                                        "100 mA at 5 V",
                       "accepted_for_design_decision": True},
        },
        "measurements": [vout_measurement],
        "required_coverage": {
            "interconnect_dc": ["geometry-derived"],
        },
    }
    # The bound direction is DERIVED mechanically from the
    # series-divider template and the model's own declared
    # resistance bound - never trusted from prose. Outside the
    # template (an 'uncertain' bound), no sound classification
    # exists and the assertion is dropped: the value is still
    # reported, unclassified.
    from pcbqa.sim import scenario as scenario_module
    derived = scenario_module.derive_value_bound(
        scenario, vout_measurement, registry)
    if derived is not None and derived["direction"] != "exact":
        vout_measurement["value_bound"] = derived
    elif derived is None:
        vout_measurement.pop("assertion", None)
    result = ngspice.run_scenario(registry, scenario, workdir)
    return {"board_file_sha256": board_sha,
            "path": path_record, "model": link,
            "scenario": scenario, "result": result}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, required=True)
    arguments = parser.parse_args()
    seed_dir = os.path.join(REPO, "benchmark", "boardB",
                            "candidates",
                            "seed{:02d}".format(arguments.seed))
    manifest_doc = json.load(open(
        os.path.join(REPO, "board", "manifest.live.json"),
        encoding="utf-8"))
    geom.configure(manifest_doc["geometry_profile"]["tolerances"][
        "polygon_chord_error_mm"]["value"])
    sys.path.insert(0, os.path.join(REPO, "benchmark", "boardB"))
    import validate_candidate as vc
    copper, thickness, _evidence = vc.physical_inputs()

    outcomes = {}
    for label, board_file in (
            ("board_a", os.path.join(
                REPO, "microphone_array_v2.kicad_pcb")),
            ("board_b", os.path.join(
                seed_dir, "candidate_routed.kicad_pcb"))):
        try:
            outcomes[label] = run_board(
                board_file, copper, thickness,
                os.path.join(seed_dir,
                             "workdir_5v_path_" + label))
        except extract.ExtractionError as error:
            outcomes[label] = {"refused": str(error)}
    out_path = os.path.join(seed_dir,
                            "candidate_5v_link.result.json")
    document = {"kind": "path-scoped-5v-link-ab",
                "endpoints": {"net": NET, "from_pad": FROM_PAD,
                              "to_pad": TO_PAD,
                              "equivalence": "identical fixed-part "
                                             "pads on both boards"},
                "outcomes": outcomes}
    both = all("result" in outcome
               for outcome in outcomes.values())
    if both:
        document["result"] = outcomes["board_b"]["result"]
        resistance = {
            label: outcome["path"]["resistance_ohm"]
            for label, outcome in outcomes.items()}
        vout = {label: outcome["result"]["measurements"]["vout"]
                ["value"]
                for label, outcome in outcomes.items()
                if outcome["result"].get("measurements")}
        document["comparison"] = {
            "path_resistance_ohm": resistance,
            "resistance_bound": {
                label: outcome["path"]["resistance_bound"]
                for label, outcome in outcomes.items()},
            "assertion_verdicts": {
                label: outcome["result"]["measurements"]["vout"]
                .get("verdict")
                for label, outcome in outcomes.items()
                if outcome["result"].get("measurements")},
            "vout_v": vout,
            "usable_for_design_decision": {
                label: outcome["result"]["result_policy"]
                ["usable_for_design_decision"]
                for label, outcome in outcomes.items()},
        }
    with open(out_path, "w", encoding="utf-8",
              newline="\n") as handle:
        json.dump(document, handle, indent=1)
        handle.write("\n")
    for label, outcome in outcomes.items():
        if "refused" in outcome:
            print(label, "REFUSED:", outcome["refused"][:160])
        else:
            result = outcome["result"]
            line = [label, result["status"],
                    "| R:", str(outcome["path"]["resistance_ohm"]),
                    "ohm | len:",
                    str(outcome["path"]["path_length_mm"]), "mm"]
            if result.get("measurements"):
                line += ["| vout:",
                         str(result["measurements"]["vout"]
                             ["value"]),
                         "| usable:",
                         str(result["result_policy"]
                             ["usable_for_design_decision"])]
            print(" ".join(line))
    print("report:", out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
