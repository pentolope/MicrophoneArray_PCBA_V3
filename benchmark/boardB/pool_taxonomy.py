"""Failure taxonomy over the candidate pool: every seed classified
by its ACTUAL root cause, with the repetition quantified, from the
committed artifacts alone. Derivations are append-only across
generator runs, so each stage is read from its LAST occurrence -
the pass that produced the committed board.

Run with KiCad's python from the repository root:

    ".../kicad/python.exe" benchmark/boardB/pool_taxonomy.py \
        --seeds 9 10 13 14 ...
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.environ.get("PCB_TOOLKIT_PATH")
                or os.path.join(REPO, "tooling",
                                "PCB_AutoDesignAndTest"))

from pcbqa import freshness                        # noqa: E402


def load(path):
    if not os.path.isfile(path):
        return None
    with io.open(path, encoding="utf-8") as handle:
        return json.load(handle)


def analyze_seed(seed):
    name = "seed{:02d}".format(seed)
    seed_dir = os.path.join(HERE, "candidates", name)
    derivation = load(os.path.join(seed_dir, "derivation.json"))
    decision = load(os.path.join(seed_dir, "decision.json"))
    row = {"candidate": name, "exists": derivation is not None}
    if derivation is None:
        return row
    records = derivation["records"]
    stages = [r.get("stage") for r in records]
    row["descended_from"] = next(
        (r.get("parent_candidate") for r in records
         if r.get("stage") == "descend"), None)
    row["seed_placement_ok"] = any(
        r.get("stage") == "seed_placement"
        and r.get("outcome") == "ok" for r in records)
    quench = next((r for r in reversed(records)
                   if r.get("stage") == "place_optimize"), None)
    row["quench_completed"] = (quench or {}).get(
        "status") == "completed" if quench else (
        row["descended_from"] is not None)
    policy = next((r for r in reversed(records)
                   if r.get("stage") == "post_quench_policy"),
                  None)
    if policy:
        row["policy_ok"] = policy.get("policy_ok")
        row["violated_constraints"] = policy.get("violated")
        row["overlaps"] = policy.get("overlapping_pairs")
        row["edge_movable_remaining"] = policy.get(
            "edge_findings_remaining_movable")
        rounds = policy.get("repair_rounds") or []
        row["edge_repairs"] = sorted({
            ref for r in rounds
            for ref in (r.get("edge_moved") or [])})
    critical = next((r for r in reversed(records)
                     if r.get("stage") == "critical_topology"),
                    None)
    if critical:
        outcome = critical["outcome"]
        row["escapes_requested"] = (
            outcome["escapes"]["placed"]
            + len(outcome["escapes"]["refused"]))
        row["escapes_placed"] = outcome["escapes"]["placed"]
        row["escapes_refused_pads"] = [
            r["pad"] for r in outcome["escapes"]["refused"]]
        row["stitches_placed"] = outcome["stitches"]["placed"]
        fab = critical.get("fabrication_geometry") or {}
        row["critical_fab_ok"] = fab.get("ok")
        row["critical_fab_violations"] = fab.get(
            "violations_by_type") or {}
    row["routing_ran"] = any(
        str(s).startswith("route_") for s in stages)
    row["stage_seconds"] = {
        r["stage"]: r["seconds"] for r in records
        if isinstance(r.get("seconds"), (int, float))}
    row["total_routing_seconds"] = round(sum(
        v for k, v in row["stage_seconds"].items()
        if k.startswith("route")), 1)
    final = next((r for r in reversed(records)
                  if r.get("stage") == "final_candidate"), None)
    if final:
        row["final_complete"] = final.get("connectivity_complete")
        row["final_total"] = final.get("net_total")
        row["nets_not_complete"] = final.get("not_complete") or {}
    if decision:
        row["decision"] = decision.get("decision")
        row["progress_class"] = decision["assessment"].get(
            "progress_class")
        row["board_completion"] = decision["components"][
            "board_required_net_completion"]
        row["fabrication_ok"] = decision["components"][
            "fabrication_identity"]["verdict_ok"] \
            if "fabrication_identity" in decision["components"] \
            else decision["components"].get(
                "fabrication_geometry_ok")
    # Root cause classification, in pipeline order.
    if not row.get("seed_placement_ok"):
        cause = "seed-placement-failed"
    elif quench and quench.get("status") not in (None, "completed") \
            and row["descended_from"] is None:
        cause = "quench-failed"
    elif policy and not policy.get("policy_ok"):
        cause = "semantic-placement-policy: constraints {}".format(
            policy.get("violated"))
    elif policy and policy.get("overlapping_pairs"):
        cause = "courtyard-overlap-unrepaired: {}".format(
            policy["overlapping_pairs"])
    elif policy and policy.get("edge_findings_remaining_movable"):
        cause = "edge-clearance-unrepaired"
    elif critical and row.get("critical_fab_violations"):
        cause = "fabrication-invalid-at-critical: {}".format(
            sorted(row["critical_fab_violations"]))
    elif critical and row.get("escapes_refused_pads"):
        cause = "critical-escapes-refused ({} pads)".format(
            len(row["escapes_refused_pads"]))
    elif row.get("board_completion") and \
            row["board_completion"]["complete"] \
            < row["board_completion"]["total"]:
        cause = "routing-incomplete"
    else:
        cause = "none-detected"
    row["root_cause"] = cause
    return row


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", type=int, nargs="+",
                        required=True)
    arguments = parser.parse_args()
    rows = [analyze_seed(seed) for seed in arguments.seeds]
    refusal_pads = Counter()
    refusal_parts = Counter()
    for row in rows:
        for pad in row.get("escapes_refused_pads", []):
            refusal_pads[pad] += 1
            refusal_parts[pad.split(".")[0]] += 1
    causes = Counter(row.get("root_cause", "missing")
                     for row in rows)
    reached_critical = [row for row in rows
                        if "escapes_placed" in row]
    document = {
        "kind": "pool-taxonomy",
        "seeds": rows,
        "aggregate": {
            "root_causes": dict(causes.most_common()),
            "escape_refusals_by_pad": dict(
                refusal_pads.most_common()),
            "escape_refusals_by_part": dict(
                refusal_parts.most_common()),
            "seeds_reaching_critical": len(reached_critical),
            "total_routing_seconds": round(sum(
                row.get("total_routing_seconds", 0)
                for row in rows), 1),
            "routing_seconds_on_candidates_with_refused_escapes":
                round(sum(
                    row.get("total_routing_seconds", 0)
                    for row in rows
                    if row.get("escapes_refused_pads")), 1),
        },
        # The closure binds the ARTIFACTS this taxonomy read, not
        # just the script: a re-run seed moves the taxonomy.
        "producer_closure": freshness.closure(dict({
            "pool_taxonomy.py": {
                "text_path": os.path.abspath(__file__)},
        }, **{
            "derivation.seed{:02d}".format(seed): {
                "json_path": os.path.join(
                    HERE, "candidates",
                    "seed{:02d}".format(seed),
                    "derivation.json")}
            for seed in arguments.seeds
            if os.path.isfile(os.path.join(
                HERE, "candidates", "seed{:02d}".format(seed),
                "derivation.json"))})),
    }
    out_path = os.path.join(HERE, "pool_taxonomy.json")
    with io.open(out_path, "w", encoding="utf-8",
                 newline="\n") as handle:
        json.dump(document, handle, indent=1)
        handle.write("\n")
    print(json.dumps(document["aggregate"], indent=1))
    for row in rows:
        print(row["candidate"], "|", row.get("root_cause"),
              "| routing_s:", row.get("total_routing_seconds", 0),
              "| completion:", (row.get("board_completion")
                                or {}).get("complete"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
