"""How well does the cheap clock probe predict the full pipeline?

For every candidate with BOTH a probe record and a committed final
routed board, this recomputes the full pipeline's clock outcome
from the committed bytes (never from memory or prose) and compares:

- probe clock completion (bare placement, no planner escapes,
  clock-stage router arguments, one route.py call)
- full-pipeline clock completion (planner escapes + staged routing
  + recovery + cleanup + last-mile, as committed)

and reports rank agreement, per-net overlap, and the compute each
probe spent versus what the historical full pipeline spent on the
same candidate (recomputed from its derivation records).

The output is a measurement of the probe as a FILTER, including
its blind spots: a probe that routes a doomed placement's clocks
is recorded as exactly that - the legality/fabrication gates stay
in front of it.
"""

from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
BOARD_B = os.path.join(REPO, "benchmark", "boardB")

sys.path.insert(0, BOARD_B)
import generate_candidate as gc                     # noqa: E402


def historical_route_seconds(candidate):
    path = os.path.join(BOARD_B, "candidates", candidate,
                        "derivation.json")
    if not os.path.isfile(path):
        # A probe of a non-pool board (a repaired variant, a
        # portfolio candidate) has no historical pipeline spend;
        # that is a fact, not an error.
        return None
    doc = json.load(open(path, encoding="utf-8"))
    return round(sum(
        r.get("seconds") or 0 for r in doc["records"]
        if r.get("tool") == "route.py"), 1)


def main():
    gc._configure_geometry()
    clock_nets = gc._routing_stages()[0][1]
    probes_dir = os.path.join(HERE, "results", "probes")
    rows = []
    for name in sorted(os.listdir(probes_dir)):
        if not name.endswith(".json"):
            continue
        probe = json.load(open(os.path.join(probes_dir, name),
                               encoding="utf-8"))
        candidate = probe["name"]
        final = os.path.join(BOARD_B, "candidates", candidate,
                             "candidate_routed.kicad_pcb")
        row = {
            "candidate": candidate,
            "probe_class": probe.get("class"),
            "probe_clock_complete": probe.get("clock_complete"),
            "probe_clock_missing": probe.get("clock_missing"),
            "probe_seconds": probe.get("seconds"),
            "probe_input_sha256": probe.get("input_board_sha256"),
            "historical_route_seconds":
                historical_route_seconds(candidate),
        }
        if os.path.isfile(final):
            status = gc.connectivity_by_net(final, clock_nets)
            missing = sorted(net for net, cls in status.items()
                             if cls != "connectivity-complete")
            row["full_clock_complete"] = \
                len(clock_nets) - len(missing)
            row["full_clock_missing"] = missing
            row["full_board_sha256"] = gc._sha256_file(final)
            if probe.get("clock_missing") is not None:
                probe_set = set(probe["clock_missing"])
                full_set = set(missing)
                union = probe_set | full_set
                row["missing_set_jaccard"] = (
                    round(len(probe_set & full_set) / len(union), 3)
                    if union else 1.0)
        else:
            row["full_clock_complete"] = None
            row["note"] = ("no committed final routed board - the "
                           "candidate never reached routing")
        rows.append(row)

    comparable = [r for r in rows
                  if r.get("full_clock_complete") is not None
                  and r.get("probe_clock_complete") is not None]
    concordant = discordant = ties = 0
    for i in range(len(comparable)):
        for j in range(i + 1, len(comparable)):
            a, b = comparable[i], comparable[j]
            dp = a["probe_clock_complete"] - b["probe_clock_complete"]
            df = a["full_clock_complete"] - b["full_clock_complete"]
            if dp == 0 or df == 0:
                ties += 1
            elif (dp > 0) == (df > 0):
                concordant += 1
            else:
                discordant += 1
    import hashlib

    def _sha(path):
        digest = hashlib.sha256()
        digest.update(open(path, "rb").read())
        return digest.hexdigest()

    doc = {
        "kind": "probe-agreement",
        "clock_nets": clock_nets,
        "inputs": [
            {"path": os.path.relpath(
                os.path.join(probes_dir, name), REPO).replace(
                    os.sep, "/"),
             "sha256": _sha(os.path.join(probes_dir, name))}
            for name in sorted(os.listdir(probes_dir))
            if name.endswith(".json")],
        "comparable_candidates": len(comparable),
        "rows": rows,
        "pairwise_rank": {"concordant": concordant,
                          "discordant": discordant, "ties": ties},
        "meaning": ("probe completion versus the committed full-"
                    "pipeline completion, both recomputed from "
                    "bytes; pairwise rank agreement over the "
                    "comparable candidates (rows without a "
                    "committed final board are listed but not "
                    "ranked). The probe never replaces the "
                    "legality and fabrication gates in front of "
                    "it."),
    }
    out = os.path.join(HERE, "results", "probe_agreement.json")
    with open(out, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(doc, handle, indent=1)
        handle.write("\n")
    for row in rows:
        print("{:8s} probe {}/{} ({}s)  full {}  jaccard {}".format(
            row["candidate"],
            row.get("probe_clock_complete"),
            len(clock_nets),
            row.get("probe_seconds"),
            row.get("full_clock_complete"),
            row.get("missing_set_jaccard")))
    print("pairwise rank:", doc["pairwise_rank"])
    print("wrote", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
