"""Backfill feedback.json for a candidate generated before feedback
emission existed: the escape refusals come from the candidate's own
committed derivation record, their locations from the candidate's
own placed board. The source identity says 'backfilled' - this is a
reconstruction from committed evidence, not a planner run.

Run with KiCad's python from the repository root:

    ".../kicad/python.exe" benchmark/boardB/backfill_feedback.py \
        --seed 14
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.environ.get("PCB_TOOLKIT_PATH")
                or os.path.join(REPO, "tooling",
                                "PCB_AutoDesignAndTest"))

from pcbqa import headless                        # noqa: E402
headless.suppress_blocking_ui()
from pcbqa import feedback as feedback_module      # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, required=True)
    arguments = parser.parse_args()
    seed_name = "seed{:02d}".format(arguments.seed)
    seed_dir = os.path.join(HERE, "candidates", seed_name)
    derivation = json.load(io.open(
        os.path.join(seed_dir, "derivation.json"),
        encoding="utf-8"))
    constraints = json.load(io.open(
        os.path.join(HERE, "constraints.json"), encoding="utf-8"))
    intent = json.load(io.open(
        os.path.join(HERE, "critical_structures.json"),
        encoding="utf-8"))
    critical = next(r for r in derivation["records"]
                    if r.get("stage") == "critical_topology")
    refusals = critical["outcome"]["escapes"]["refused"]

    import pcbnew
    board = pcbnew.LoadBoard(os.path.join(
        seed_dir, "candidate_placed.kicad_pcb"))
    pad_positions = {}
    pad_nets = {}
    for footprint in board.GetFootprints():
        for pad in footprint.Pads():
            label = "{}.{}".format(footprint.GetReference(),
                                   pad.GetNumber())
            position = pad.GetPosition()
            pad_positions[label] = [position.x / 1e6,
                                    position.y / 1e6]
            pad_nets[label] = pad.GetNetname()

    fixed = set(constraints["requirement_fixed_references"])
    records = []
    for refusal in refusals:
        label = refusal["pad"]
        owner = label.split(".")[0]
        location = refusal.get("location_mm") \
            or pad_positions.get(label)
        if location is None:
            continue
        records.append(feedback_module.escape_refusal_record(
            label, refusal.get("net") or pad_nets.get(label, ""),
            location, refusal["reason"],
            [] if owner in fixed else [owner],
            {"kind": "planner-outcome",
             "identity": "derivation:critical_topology:{}:"
                         "backfilled".format(seed_name)},
            intent["rules"]["escape"]["clearance_mm"]))
    out_path = os.path.join(seed_dir, "feedback.json")
    with io.open(out_path, "w", encoding="utf-8",
                 newline="\n") as handle:
        json.dump({
            "kind": "candidate-feedback",
            "candidate": seed_name,
            "records": records,
            "meaning": "BACKFILLED from the committed derivation "
                       "and the candidate's own placed board: the "
                       "refusals are the planner's recorded ones, "
                       "the locations the board's own pads; a "
                       "descendant applies these within its own "
                       "constraints and the ordinary progression "
                       "judges the result",
        }, handle, indent=1)
        handle.write("\n")
    print(seed_name, "feedback records:", len(records),
          "->", out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
