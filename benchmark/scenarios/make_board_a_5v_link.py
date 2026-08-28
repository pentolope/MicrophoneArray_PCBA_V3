"""Build the first Board A simulation scenario: the fused 5 V link.

The scenario is deliberately narrow so every model in it is
defensible: an ideal 5 V source (LABELED as a substitution for the
host supply, whose model is unavailable), the board's own extracted
5V_FUSED copper link (verified point-to-point F1.2 -> D1.2 on this
board, so the two-terminal assertion is factual), and a 100 mA
constant-current load standing in for downstream demand - labeled as
an assumed-behavioral substitution... except a current source is not
in the element vocabulary yet, so the load is a resistor whose value
draws 100 mA at 5 V, and THAT substitution is stated here and in the
scenario description. The fuse and diode themselves are NOT modeled:
their absence is the point - the scenario's required coverage names
only the interconnect-DC phenomenon this run can honestly claim.

Artifacts written next to this script:
  board_a_5v_link.scenario.json  - the declared scenario
  board_a_5v_link.deck.cir       - the deterministic ngspice deck
  board_a_5v_link.result.json    - the run result (with ngspice
                                   absent this records
                                   backend-unavailable, honestly)

Run with KiCad's python from the repository root.
"""

from __future__ import annotations

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

from pcbqa import headless                        # noqa: E402
headless.suppress_blocking_ui()
from pcbqa import extract                          # noqa: E402
from pcbqa.sim import fidelity, ngspice            # noqa: E402


def main():
    baseline = json.load(open(
        os.path.join(REPO, "benchmark", "board_a_baseline.json"),
        encoding="utf-8"))
    net_record = baseline["nets"]["5V_FUSED"]
    board_sha = baseline["board_file_sha256"]
    link = extract.interconnect_model_from_net(
        net_record, board_sha, baseline["physical_inputs"],
        two_terminal_asserted_by="scenario author; verified on this "
                                 "board: 5V_FUSED carries exactly "
                                 "pads F1.2 and D1.2")
    registry = fidelity.ModelRegistry([link])
    scenario = {
        "name": "board-a-5v-fused-link-drop",
        "description": "DC drop across the fused 5 V entry link's "
                       "copper at 100 mA. SUBSTITUTIONS, stated: the "
                       "host supply is an ideal 5 V source; the "
                       "downstream demand is a 50 ohm resistor "
                       "drawing 100 mA at 5 V. The fuse and diode "
                       "are NOT modeled and nothing here claims "
                       "them. 20 C is requested because the "
                       "extracted copper model is fixed at the IEC "
                       "60028 resistivity reference; any other "
                       "temperature would be flagged as not fully "
                       "covered by condition coverage.",
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
        "measurements": [
            {"name": "vout", "kind": "op_voltage", "node": "vout",
             "assertion": {"op": ">=", "value": 4.999}},
        ],
        "required_coverage": {
            "interconnect_dc": ["geometry-derived"],
        },
    }
    deck = ngspice.generate_deck(registry, scenario)
    with open(os.path.join(HERE, "board_a_5v_link.deck.cir"), "w",
              encoding="utf-8", newline="\n") as handle:
        handle.write(deck)
    with open(os.path.join(HERE, "board_a_5v_link.scenario.json"),
              "w", encoding="utf-8", newline="\n") as handle:
        json.dump({"scenario": scenario, "models": [link]}, handle,
                  indent=1)
        handle.write("\n")
    result = ngspice.run_scenario(
        registry, scenario,
        os.path.join(HERE, "workdir_5v_link"))
    with open(os.path.join(HERE, "board_a_5v_link.result.json"), "w",
              encoding="utf-8", newline="\n") as handle:
        json.dump(result, handle, indent=1)
        handle.write("\n")
    line = ["status:", result["status"],
            "| coverage satisfied:",
            str(result["model_coverage"]["satisfied"]),
            "| conditions covered:",
            str(result["condition_coverage"]["fully_covered"]),
            "| link R:",
            str(net_record["dc"]["segment_resistance_sum_ohm"]),
            "ohm"]
    if result.get("measurements"):
        vout = result["measurements"]["vout"]
        line += ["| vout:", str(vout["value"]),
                 "passed:", str(vout["passed"]),
                 "| backend:", str(result["backend"]["version"])]
    print(" ".join(str(part) for part in line))
    return 0


if __name__ == "__main__":
    sys.exit(main())
