"""The accept gate for router-in-the-loop placement repair.

place_route_loop proposes moves; THIS repository decides whether a
proposed placement is acceptable. The router's own judgement
(failures, iterations) never overrides product intent:

  1. the board's placement policy (constraints.json, evaluated by
     the generator's own evaluator) must hold - a violated
     constraint REJECTS the round regardless of routing outcome;
  2. courtyard overlaps REJECT;
  3. pad-accurate board-edge clearance findings on movable parts
     REJECT (a hard fabrication rule is not tradeable for
     connectivity);
  4. among acceptable placements, fewer incomplete authoritative
     nets is better; PDM clock nets count double, because they are
     the structures this board exists to route.

Invoked by place_route_loop as:

    accept_policy.py <placed.kicad_pcb> <routed.kicad_pcb> <route.json>

Prints one line ``SCORE=<float>`` (lower is better) on acceptance;
exits non-zero to reject.
"""

from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO, "benchmark", "boardB"))

import generate_candidate as gc                     # noqa: E402


def main():
    placed, routed = sys.argv[1], sys.argv[2]
    import pcbnew
    gc._configure_geometry()
    constraints = json.load(open(
        os.path.join(REPO, "benchmark", "boardB",
                     "constraints.json"), encoding="utf-8"))
    board = pcbnew.LoadBoard(placed)
    circle = gc._board_circle(pcbnew.LoadBoard(gc.BOARD_A))

    policy = gc.evaluate_policy(board, constraints, circle)
    if not policy["summary"]["ok"]:
        print("REJECT: placement policy violated:",
              policy["summary"]["violated"])
        return 1

    from pcbqa import placement as placement_module
    boxes = {fp.GetReference(): gc._bbox_mm(fp)
             for fp in board.GetFootprints()}
    overlaps = placement_module.overlapping_pairs(boxes)
    if overlaps:
        print("REJECT: courtyard overlaps:", overlaps[:4])
        return 1

    from pcbqa import feedback as feedback_module
    from pcbqa import geom as geom_module
    findings = feedback_module.edge_clearance_findings(
        board,
        {"kind": "circle", "center_mm": [circle[0], circle[1]],
         "radius_mm": circle[2]},
        gc.EDGE_CLEARANCE_MM, geom_module.pad_copper_polygon)
    fixed = set(constraints["requirement_fixed_references"])
    movable_findings = [f for f in findings
                       if f["references"][0] not in fixed]
    if movable_findings:
        print("REJECT: board-edge clearance findings on movable "
              "parts:", [f["references"][0]
                         for f in movable_findings])
        return 1

    # Acceptable: score by what is still incomplete on the routed
    # artifact, PDM clock nets counted double.
    all_nets = [net for _name, nets in gc._routing_stages()
                for net in nets]
    status = gc.connectivity_by_net(routed, all_nets)
    incomplete = [net for net, cls in status.items()
                  if cls != "connectivity-complete"]
    clock_weight = sum(1 for net in incomplete
                       if net.startswith(("PDM_CLK", "AUDIO_MCLK",
                                          "MCLK_OSC")))
    score = len(incomplete) + clock_weight
    print("incomplete={} clock_incomplete={}".format(
        len(incomplete), clock_weight))
    print("SCORE={}".format(float(score)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
