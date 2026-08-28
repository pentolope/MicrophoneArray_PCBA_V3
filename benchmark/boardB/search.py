"""Rank the candidate pool by correctness classes, and refuse stale
evidence.

Each candidate's decision.json carries the toolkit progression
assessment (rank_key: a lexicographic tuple over the correctness
classes - placement, critical structures by POLICY-owned truth,
board-required connectivity, hard fabrication geometry, blocking
gates, quality gates, applicable electrical evidence) and a producer
closure naming exactly what produced it. Before any ranking, every
decision is freshness-verified against the CURRENT producers and the
CURRENT candidate board; a stale artifact is excluded with the moved
components named - search never consumes evidence its producers have
outgrown.

Copper remains the last tie-break, only between candidates with
EQUAL rank keys and identical complete-net sets: a missing route
never reads as saved copper.

Run with KiCad's python from the repository root:

    ".../kicad/python.exe" benchmark/boardB/search.py --seeds 9 10
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.environ.get("PCB_TOOLKIT_PATH")
                or os.path.join(REPO, "tooling",
                                "PCB_AutoDesignAndTest"))

from pcbqa import freshness                        # noqa: E402

import validate_candidate as vc                    # noqa: E402


def _board_sha(seed_dir):
    for basename in ("candidate_routed.kicad_pcb",
                     "candidate_placed.kicad_pcb"):
        path = os.path.join(seed_dir, basename)
        if os.path.isfile(path):
            with open(path, "rb") as handle:
                return hashlib.sha256(handle.read()).hexdigest()
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", type=int, nargs="+",
                        required=True)
    arguments = parser.parse_args()
    pool = []
    for seed in arguments.seeds:
        name = "seed{:02d}".format(seed)
        seed_dir = os.path.join(HERE, "candidates", name)
        path = os.path.join(seed_dir, "decision.json")
        if not os.path.isfile(path):
            pool.append({"candidate": name, "decision": "missing",
                         "reasons": ["no decision.json; the "
                                     "candidate pipeline did not "
                                     "complete"]})
            continue
        decision = json.load(open(path, encoding="utf-8"))
        decision["candidate"] = name
        recorded = decision.get("producer_closure")
        board_sha = _board_sha(seed_dir)
        if recorded is None or board_sha is None:
            pool.append({"candidate": name,
                         "decision": "stale-artifacts",
                         "reasons": ["the decision carries no "
                                     "producer closure; "
                                     "unverifiable evidence is "
                                     "refused"]})
            continue
        verdict = freshness.verify(
            recorded, vc.closure_components(board_sha, seed_dir))
        if not verdict["fresh"]:
            pool.append({
                "candidate": name,
                "decision": "stale-artifacts",
                "reasons": ["producer closure is stale; moved: {}"
                            .format(verdict["moved"]
                                    + verdict["missing"]
                                    + verdict["added"])],
                "freshness": verdict})
            continue
        decision["freshness"] = {"fresh": True}
        pool.append(decision)

    accepted = [entry for entry in pool
                if entry.get("decision") == "accept-for-comparison"]
    accepted.sort(key=lambda entry: tuple(
        entry["assessment"]["rank_key"]), reverse=True)
    if len(accepted) > 1:
        top_key = tuple(accepted[0]["assessment"]["rank_key"])
        top_set = accepted[0]["components"][
            "measured_net_set_sha256"]
        peers = [entry for entry in accepted
                 if tuple(entry["assessment"]["rank_key"])
                 == top_key
                 and entry["components"][
                     "measured_net_set_sha256"] == top_set]
        if len(peers) > 1:
            peers.sort(key=lambda entry: entry["components"][
                "measured_copper_total_mm"])
            accepted.remove(peers[0])
            accepted.insert(0, peers[0])
    best = accepted[0] if accepted else None
    outcome = {
        "kind": "search-decision",
        "pool": [{
            "candidate": entry.get("candidate"),
            "decision": entry.get("decision"),
            "progress_class": (entry.get("assessment") or {}).get(
                "progress_class"),
            "board_required_net_completion":
                (entry.get("components") or {}).get(
                    "board_required_net_completion"),
            "benchmark_net_completion":
                (entry.get("components") or {}).get(
                    "benchmark_net_completion"),
            "critical": [
                (entry.get("components") or {}).get(
                    "critical_clock_nets_connected"),
                (entry.get("components") or {}).get(
                    "critical_paths_resolved"),
                (entry.get("components") or {}).get(
                    "critical_topology_valid")],
            "fabrication_geometry_ok":
                (entry.get("components") or {}).get(
                    "fabrication_geometry_ok"),
            "candidate_ready_for_next_stage":
                entry.get("candidate_ready_for_next_stage"),
            "reasons": entry.get("reasons"),
        } for entry in pool],
        "best": best["candidate"] if best else None,
        "why_best": None if best is None else {
            "rank_key": best["assessment"]["rank_key"],
            "progress_class": best["assessment"]["progress_class"],
            "rule": "lexicographic over the correctness classes "
                    "(placement, critical structures by policy "
                    "truth, board-required connectivity, hard "
                    "fabrication geometry, blocking gates, quality "
                    "gates, usable evidence); copper only between "
                    "equal keys with identical complete-net sets",
        },
        "no_winner_reason": None if best else
            "no fresh candidate reached accept-for-comparison",
        "producer_closure": freshness.closure({
            "search.py": os.path.abspath(__file__),
            "toolkit.freshness": os.path.join(
                vc.TOOLKIT_ROOT, "pcbqa", "freshness.py"),
            "toolkit.progression": os.path.join(
                vc.TOOLKIT_ROOT, "pcbqa", "progression.py"),
        }),
    }
    out_path = os.path.join(HERE, "search_decision.json")
    with open(out_path, "w", encoding="utf-8",
              newline="\n") as handle:
        json.dump(outcome, handle, indent=1)
        handle.write("\n")
    for entry in outcome["pool"]:
        print(entry["candidate"], entry["decision"],
              "| progress:", entry.get("progress_class"),
              "| board:", entry.get(
                  "board_required_net_completion"))
    print("best:", outcome["best"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
