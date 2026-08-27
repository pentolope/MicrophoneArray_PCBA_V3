"""Rank the candidate pool and record WHY the winner won.

Reads each candidate's decision.json (produced by
validate_candidate.py), ranks accepted candidates by their recorded
components - routed completeness first, then the composite whose
weights the decisions themselves carry - and writes
search_decision.json. Rejected candidates stay in the pool with
their reasons: the next search step reads WHY each one lost, never
just that it did.

Run from the repository root with any python:

    python benchmark/boardB/search.py --seeds 2 3 4
"""

from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", type=int, nargs="+",
                        required=True)
    arguments = parser.parse_args()
    pool = []
    for seed in arguments.seeds:
        path = os.path.join(HERE, "candidates",
                            "seed{:02d}".format(seed),
                            "decision.json")
        if not os.path.isfile(path):
            pool.append({"candidate": "seed{:02d}".format(seed),
                         "decision": "missing",
                         "reasons": ["no decision.json; the "
                                     "candidate pipeline did not "
                                     "complete"]})
            continue
        pool.append(json.load(open(path, encoding="utf-8")))
    accepted = [entry for entry in pool
                if entry.get("decision") == "accept-for-comparison"]
    accepted.sort(key=lambda entry: (
        entry["components"]["routed_fraction"],
        entry["ranking_score"]), reverse=True)
    # Copper is a tie-break ONLY among candidates whose measured-net
    # sets are identical: totals over different net sets are not
    # comparable, and a missing route must never look like saved
    # copper.
    if accepted:
        top = accepted[0]["components"]
        peers = [entry for entry in accepted
                 if entry["components"]["routed_fraction"]
                 == top["routed_fraction"]
                 and entry["ranking_score"]
                 == accepted[0]["ranking_score"]
                 and entry["components"].get(
                     "measured_net_set_sha256")
                 == top.get("measured_net_set_sha256")
                 and entry["components"].get(
                     "measured_copper_total_mm") is not None]
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
            "components": entry.get("components"),
            "ranking_score": entry.get("ranking_score"),
            "reasons": entry.get("reasons"),
        } for entry in pool],
        "best": best["candidate"] if best else None,
        "why_best": None if best is None else {
            "routed_fraction":
                best["components"]["routed_fraction"],
            "ranking_score": best["ranking_score"],
            "measured_copper_total_mm":
                best["components"].get("measured_copper_total_mm"),
            "rule": "highest routed completeness on the A/B net "
                    "set; composite, then LOWEST measured copper "
                    "as tie-breaks - copper only among candidates "
                    "with identical measured-net sets; the "
                    "components are the evidence",
        },
        "no_winner_reason": None if best else
            "no candidate reached accept-for-comparison",
    }
    out_path = os.path.join(HERE, "search_decision.json")
    with open(out_path, "w", encoding="utf-8",
              newline="\n") as handle:
        json.dump(outcome, handle, indent=1)
        handle.write("\n")
    print(json.dumps(outcome["pool"], indent=1)[:1200])
    print("best:", outcome["best"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
