"""Check the declared critical nets, using the validator's own topology rule.

The generator lays these nets down deliberately - clock spine on F.Cu with no
vias, branch trees matched end to end - and a requirement that is only enforced
by the comment above the code that writes it is not enforced at all.

Nothing is measured here. The measurement is pcbqa.rules.NetTopologyRule, the
same driver-to-load path walk the validator's NET.TOPOLOGY gate runs, driven by
the same rules in verification/boards/live.json. This module adds two things
that gate cannot: it runs during the build, on the pre-route board and again on
the routed candidate, and it checks that the manifest's limits still agree with
constraints.json, so the design's statement of the requirement and the number
being enforced cannot drift apart.

    "C:/Program Files/KiCad/10.0/bin/python.exe" tools/critical_nets.py BOARD
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _toolkit                                          # noqa: E402,F401

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

import pcbnew                                            # noqa: E402

from pcbqa import geom                                   # noqa: E402
from pcbqa.core import Manifest                          # noqa: E402
from pcbqa.rules import NetTopologyRule                  # noqa: E402

CONSTRAINTS = os.path.join(HERE, "constraints.json")
MANIFEST = os.path.join(HERE, "board", "manifest.live.json")

# Why each family is generated rather than autorouted, and which entry in
# constraints.json states its numbers. The reason is part of the record: a
# pre-routed net that nobody can justify is just copper the router was not
# allowed to improve.
DECLARED = {
    "AUDIO_MCLK": {
        "constraint": "audio_mclk",
        "nets": ["MCLK_OSC", "AUDIO_MCLK"],
        "reason": "24.576 MHz master clock, the fastest edge on the board. A "
                  "via would add a 1.6 mm through-hole stub and a layer change "
                  "in the middle of it, and no autorouter can be asked for "
                  "'no vias on this net'. R1 is placed directly under its "
                  "socket pin so the run is one straight track.",
        "limits": {"max_vias_per_net": "max_vias", "permitted_layers": "layer"},
    },
    "PDM_CLOCK_ROOT": {
        "constraint": "pdm_clock_root",
        "nets": ["PDM_CLK_FPGA", "PDM_CLK_IN"],
        "reason": "The clock root and the fan-in to eight buffer inputs. The "
                  "TSSOP-20 pad row leaves 0.25 mm between neighbours, which "
                  "no track plus clearance fits through, so the rail has to "
                  "enter over the end of the package and run down the centre "
                  "line under the body: shape dictated by the package, not "
                  "found by a search. F.Cu, zero vias.",
        "limits": {"max_vias_per_net": "max_vias", "permitted_layers": "layer"},
    },
    "PDM_CLOCK_BRANCHES": {
        "constraint": "pdm_clock_branches",
        "nets": ["PDM_CLK_B{}".format(n) for n in range(8)],
        "reason": "Eight branches, each feeding a pair of microphones through "
                  "its own series termination. Each branch splits on the "
                  "bisector of its two landing angles so the two arms are "
                  "mirror images and match by construction; the array's "
                  "inter-channel timing depends on it.",
        "limits": {"max_spread_mm": "branch_length_match_mm",
                   "max_vias_per_net": "max_vias",
                   "permitted_layers": "layer"},
    },
}


def _load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def rule_specs():
    """The topology rules from the board manifest, keyed by id."""
    manifest = Manifest(MANIFEST)
    return manifest, {spec["id"]: spec
                      for spec in manifest.get("net_topology.rules")}


def limits_agree():
    """The manifest's numbers still say what constraints.json says.

    A drift here is how a requirement quietly stops being enforced: the design
    document keeps promising zero vias while the rule that checks it has been
    relaxed, or a family loses its rule altogether.
    """
    declared = _load(CONSTRAINTS)["critical_routes"]
    _manifest, specs = rule_specs()
    results = []
    for rule_id, entry in DECLARED.items():
        source = declared.get(entry["constraint"], {})
        spec = specs.get(rule_id)
        if spec is None:
            results.append({"rule": rule_id, "check": "rule_present",
                            "pass": False, "value": "missing",
                            "limit": "declared in constraints.json"})
            continue
        for spec_key, constraint_key in entry["limits"].items():
            wanted = source.get(constraint_key)
            if wanted is None:
                continue
            if spec_key == "permitted_layers":
                wanted = [wanted]
            results.append({"rule": rule_id, "check": "limit:" + spec_key,
                            "pass": spec.get(spec_key) == wanted,
                            "value": spec.get(spec_key), "limit": wanted})
    return results


def verify(board):
    """Run the validator's topology rules over this board."""
    manifest, specs = rule_specs()
    geom.configure(manifest.geometry_profile()
                   .tolerance("polygon_chord_error_mm").value)
    results = list(limits_agree())
    for rule_id, entry in DECLARED.items():
        spec = specs.get(rule_id)
        if spec is None:
            continue
        rule = NetTopologyRule(spec)
        measured, issues = rule.evaluate(board, geom.pad_copper_polygon)
        issues += rule.check_limits(measured)
        found = sorted(m["net"] for m in measured)
        results.append({"rule": rule_id, "check": "nets_routed",
                        "pass": found == sorted(entry["nets"]),
                        "value": found, "limit": sorted(entry["nets"])})
        results.append({"rule": rule_id, "check": "topology",
                        "pass": not issues, "value": issues or "clean",
                        "limit": "no violations",
                        "measured": [{"net": m["net"],
                                      "max_path_mm": m["max_path_mm"],
                                      "min_path_mm": m["min_path_mm"],
                                      "vias": m["vias"],
                                      "layers": m["layers"]}
                                     for m in measured]})
    return results


def critical_net_names():
    return [net for entry in DECLARED.values() for net in entry["nets"]]


def report(board_path):
    board = pcbnew.LoadBoard(board_path)
    results = verify(board)
    return {"board": board_path,
            "declared": {k: v["reason"] for k, v in DECLARED.items()},
            "results": results,
            "nets": critical_net_names(),
            "failures": [r for r in results if not r["pass"]]}


def main(argv):
    if len(argv) != 2:
        print(__doc__)
        return 2
    outcome = report(argv[1])
    for row in outcome["results"]:
        print("  [{}] {:<20} {:<22} {}".format(
            "PASS" if row["pass"] else "FAIL", row["rule"], row["check"],
            row["value"]))
    return 0 if not outcome["failures"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
