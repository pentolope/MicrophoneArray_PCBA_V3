"""Generate one Board B placement/routing candidate.

The experiment's rules, enforced here:

  * the authoritative Board A file is read-only input; the candidate
    is always a separate file under benchmark/boardB/candidates/;
  * requirement-fixed parts (microphone acoustic ring, host and
    module interfaces, from constraints.json) keep their positions
    and rotations - those ARE the product requirement;
  * every other footprint is deterministically SCRAMBLED (seeded RNG)
    before optimization, so Board A's internal placement never seeds
    the answer;
  * placement coordinates belong to the optimizer
    (KiCadRoutingTools place_optimize, with the requirement-fixed
    references locked), and routing to its router;
  * acceptance is judged by the toolkit's gates afterwards, never by
    router success alone.

Run with KiCad's python:

    ".../kicad/python.exe" benchmark/boardB/generate_candidate.py \
        --seed 1 [--skip-route]

Outputs land in benchmark/boardB/candidates/seed<NN>/ together with a
history.json recording every stage's identity and outcome, so a later
comparison can say WHY a placement won.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
BOARD_A = os.path.join(REPO, "microphone_array_v2.kicad_pcb")
PLUGIN = os.path.expanduser(
    "~/Documents/KiCad/10.0/3rdparty/plugins/"
    "com_github_drandyhaas_kicadroutingtools")

CENTER_MM = (150.0, 150.0)
SCATTER_RADIUS_MM = 45.0


def _sha256(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def scramble(board, fixed, seed):
    """Deterministically scatter every non-fixed footprint."""
    import pcbnew
    rng = random.Random(seed)
    movable = [footprint for footprint in
               sorted(list(board.GetFootprints()),
                      key=lambda f: f.GetReference())
               if footprint.GetReference() not in fixed]
    # Deterministic seed-shuffled spiral: non-overlapping start (a
    # perturbative quench improves placements; it does not solve
    # from chaos), and nothing like Board A's arrangement - the
    # ordering around the spiral is the seed's, not the design's.
    rng.shuffle(movable)
    golden = math.pi * (3.0 - math.sqrt(5.0))
    moved = []
    for index, footprint in enumerate(movable):
        radius = min(6.0 + 3.4 * math.sqrt(index),
                     SCATTER_RADIUS_MM)
        angle = index * golden
        x = CENTER_MM[0] + radius * math.cos(angle)
        y = CENTER_MM[1] + radius * math.sin(angle)
        footprint.SetPosition(pcbnew.VECTOR2I(
            pcbnew.FromMM(x), pcbnew.FromMM(y)))
        footprint.SetOrientationDegrees(
            rng.choice([0.0, 90.0, 180.0, 270.0]))
        moved.append(footprint.GetReference())
    return moved


def convert_circular_outline(board):
    """Candidate-only workaround for a discovered KiCadRoutingTools
    limitation: its board-bounds parser reads gr_rect/gr_line/gr_arc/
    gr_poly on Edge.Cuts but NOT gr_circle, so a circular outline
    yields 'No board boundary'. The candidate's circle becomes two
    semicircular arcs - geometrically identical; Board A itself is
    never modified."""
    import pcbnew
    converted = 0
    for drawing in list(board.GetDrawings()):
        if drawing.GetClass() != "PCB_SHAPE":
            continue
        if drawing.GetLayer() != pcbnew.Edge_Cuts:
            continue
        if drawing.GetShape() != pcbnew.SHAPE_T_CIRCLE:
            continue
        center = drawing.GetCenter()
        radius = drawing.GetRadius()
        width = drawing.GetWidth()
        for direction in (1, -1):
            arc = pcbnew.PCB_SHAPE(board)
            arc.SetShape(pcbnew.SHAPE_T_ARC)
            arc.SetLayer(pcbnew.Edge_Cuts)
            arc.SetWidth(width)
            start = pcbnew.VECTOR2I(center.x + radius, center.y)
            mid = pcbnew.VECTOR2I(center.x,
                                  center.y + direction * radius)
            end = pcbnew.VECTOR2I(center.x - radius, center.y)
            if direction == 1:
                arc.SetArcGeometry(start, mid, end)
            else:
                arc.SetArcGeometry(end, mid, start)
            board.Add(arc)
        board.Delete(drawing)
        converted += 1
    return converted


def strip_routing(board):
    """Remove all tracks and vias; zones stay for later refill."""
    removed = 0
    for track in list(board.GetTracks()):
        board.Delete(track)
        removed += 1
    return removed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--skip-route", action="store_true")
    parser.add_argument("--passes", type=int, default=2)
    parser.add_argument("--grid", type=float, default=2.0)
    parser.add_argument("--stage-timeout", type=int, default=1500)
    parser.add_argument("--reuse-placed", action="store_true")
    arguments = parser.parse_args()

    import pcbnew
    constraints = json.load(open(
        os.path.join(HERE, "constraints.json"), encoding="utf-8"))
    fixed = set(constraints["requirement_fixed_references"])

    out_dir = os.path.join(HERE, "candidates",
                           "seed{:02d}".format(arguments.seed))
    os.makedirs(out_dir, exist_ok=True)
    history = {"board_a_sha256": _sha256(BOARD_A),
               "seed": arguments.seed,
               "requirement_fixed": sorted(fixed),
               "stages": []}

    def run_stage(name, command, output_path):
        start = time.time()
        try:
            completed = subprocess.run(
                command, capture_output=True, text=True,
                timeout=arguments.stage_timeout)
            record = {"stage": name,
                      "returncode": completed.returncode,
                      "seconds": round(time.time() - start, 1),
                      "log_tail": (completed.stdout
                                   + completed.stderr)[-3000:]}
        except subprocess.TimeoutExpired:
            record = {"stage": name, "returncode": None,
                      "timed_out_after_s": arguments.stage_timeout,
                      "seconds": round(time.time() - start, 1)}
        record["output_sha256"] = (_sha256(output_path)
                                   if os.path.isfile(output_path)
                                   else None)
        history["stages"].append(record)
        return record

    placed = os.path.join(out_dir, "candidate_placed.kicad_pcb")
    if arguments.reuse_placed and os.path.isfile(placed):
        history["stages"].append({
            "stage": "reuse_placed",
            "output_sha256": _sha256(placed)})
        placement_ok = True
    else:
        board = pcbnew.LoadBoard(BOARD_A)
        converted = convert_circular_outline(board)
        moved = scramble(board, fixed, arguments.seed)
        removed = strip_routing(board)
        scrambled = os.path.join(out_dir,
                                 "candidate_scrambled.kicad_pcb")
        pcbnew.SaveBoard(scrambled, board)
        history["stages"].append({
            "stage": "scramble", "removed_tracks": removed,
            "moved_footprints": len(moved),
            "circular_outlines_converted": converted,
            "output_sha256": _sha256(scrambled)})
        record = run_stage(
            "place_optimize",
            [sys.executable,
             os.path.join(PLUGIN, "place_optimize.py"),
             scrambled, placed, "--max-displacement", "200",
             "--step", str(arguments.grid),
             "--max-passes", str(arguments.passes),
             "--lock"] + sorted(fixed), placed)
        placement_ok = record.get("returncode") == 0

    if not arguments.skip_route and placement_ok:
        routed = os.path.join(out_dir, "candidate_routed.kicad_pcb")
        run_stage("route",
                  [sys.executable, os.path.join(PLUGIN, "route.py"),
                   placed, "--output", routed], routed)

    with open(os.path.join(out_dir, "history.json"), "w",
              encoding="utf-8", newline="\n") as handle:
        json.dump(history, handle, indent=1)
        handle.write("\n")
    print("candidate seed {} -> {}".format(arguments.seed, out_dir))
    for stage in history["stages"]:
        print("  {}: rc={} sha={}".format(
            stage["stage"], stage.get("returncode", "-"),
            (stage.get("output_sha256") or "none")[:12]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
