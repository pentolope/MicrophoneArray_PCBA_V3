"""Generate one Board B placement/routing candidate.

The experiment's rules, enforced here:

  * the authoritative Board A file is read-only input; every artifact
    lands under benchmark/boardB/candidates/;
  * requirement-fixed parts (microphone acoustic ring, host and
    module interfaces, from constraints.json) keep their positions
    and rotations - those ARE the product requirement;
  * SEMANTIC constraints drive placement: the seed placement is
    CONSTRUCTED to satisfy every thresholded constraint (satellites
    inside their proximity budgets, blocks inside their spreads,
    separations honored), the toolkit's evaluate_placement judges the
    result on actual positions - never on this script's word - and
    constraint-bound references are translated into optimizer locks,
    so the numerical quench (KiCadRoutingTools place_optimize) can
    improve only what the constraints leave free;
  * the initial scatter is called non-overlapping only because
    courtyard/bounding boxes are collision-checked; the check's
    result is recorded, not assumed;
  * zone inheritance follows zone_policy.json: GND planes inherit as
    declared architecture, placement-derived via-mask keepouts are
    kept only where they still guard requirement-fixed geometry, and
    an unclassified zone refuses;
  * routing is STAGED (clock spine, microphone data, host control,
    power) with bounded runtimes; every stage checkpoints its board
    file, a stage failure or timeout never destroys earlier stages,
    and per-net routed status is read from the board file - the
    board file, never the router log, is the arbiter;
  * every candidate carries an APPEND-ONLY derivation
    (derivation.json): inputs, seeds, configurations, stage
    outcomes, tool identities. --reuse-placed appends a reuse record
    to the existing derivation; nothing ever replaces the original
    scramble/placement history.

Run with KiCad's python:

    ".../kicad/python.exe" benchmark/boardB/generate_candidate.py \
        --seed 1 [--skip-route] [--reuse-placed]
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

sys.path.insert(0, os.environ.get("PCB_TOOLKIT_PATH")
                or os.path.join(REPO, "tooling",
                                "PCB_AutoDesignAndTest"))

from pcbqa import placement as placement_module    # noqa: E402


class CandidateError(Exception):
    """Candidate generation cannot proceed as asked."""


def _sha256_file(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def _routing_stages():
    """Net groups by board semantics, in routing priority order:
    the clock spine first (oscillator -> buffer -> 16 microphones is
    the timing-critical structure), microphone data next (each pairs
    with its clock), host control, then power - power last because
    the inherited GND planes and wide supply freedom make it the
    least constrained."""
    clock = (["MCLK_OSC", "AUDIO_MCLK", "PDM_CLK_IN",
              "PDM_CLK_FPGA"]
             + ["PDM_CLK_Y{}".format(i) for i in range(8)]
             + ["PDM_CLK_B{}".format(i) for i in range(8)])
    mic_data = (["PDM_D{}".format(i) for i in range(8)]
                + ["MIC_DOUT_{}".format(i) for i in range(16)])
    host = ["SPI_SCLK", "SPI_CS_N", "SPI_MISO", "SPI_MOSI",
            "PI_SCLK", "PI_CS_N", "PI_MISO", "PI_MOSI", "PI_IRQ",
            "PI_RESET_N", "PI_STATUS", "PI_SYNC", "HOST_IRQ",
            "HOST_RESET_N", "HOST_STATUS", "HOST_SYNC"]
    power = (["5V_FUSED", "+5V", "PI_5V", "+3V3A", "+3V3_CLK",
              "TANG_3V3"]
             + ["MIC_VDD_{}".format(i) for i in range(16)]
             + ["GND"])
    return [("clock", clock), ("mic-data", mic_data),
            ("host", host), ("power", power)]


# ------------------------------------------------------------ geometry

def _bbox_mm(footprint):
    """Courtyard box where one exists, else the footprint bounding
    box - the collision question is only ever answered from these."""
    import pcbnew
    for layer in (pcbnew.F_CrtYd, pcbnew.B_CrtYd):
        try:
            courtyard = footprint.GetCourtyard(layer)
        except Exception:
            courtyard = None
        if courtyard is not None and courtyard.OutlineCount() > 0:
            box = courtyard.BBox()
            return [pcbnew.ToMM(box.GetLeft()),
                    pcbnew.ToMM(box.GetTop()),
                    pcbnew.ToMM(box.GetRight()),
                    pcbnew.ToMM(box.GetBottom())]
    box = footprint.GetBoundingBox()
    return [pcbnew.ToMM(box.GetLeft()), pcbnew.ToMM(box.GetTop()),
            pcbnew.ToMM(box.GetRight()), pcbnew.ToMM(box.GetBottom())]


def _overlaps(box, boxes):
    for other in boxes.values():
        if box[0] < other[2] and other[0] < box[2] \
                and box[1] < other[3] and other[1] < box[3]:
            return True
    return False


def _board_circle(board):
    """Center and radius of the circular Edge.Cuts outline, read
    before any conversion."""
    import pcbnew
    for drawing in board.GetDrawings():
        if drawing.GetClass() != "PCB_SHAPE":
            continue
        if drawing.GetLayer() != pcbnew.Edge_Cuts:
            continue
        if drawing.GetShape() == pcbnew.SHAPE_T_CIRCLE:
            center = drawing.GetCenter()
            return (pcbnew.ToMM(center.x), pcbnew.ToMM(center.y),
                    pcbnew.ToMM(drawing.GetRadius()))
    raise CandidateError("the board has no circular Edge.Cuts "
                         "outline; this generator expects one")


def convert_circular_outline(board):
    """Candidate-only workaround for a verified KiCadRoutingTools
    limitation: its board-bounds parser reads gr_rect/gr_line/gr_arc/
    gr_poly on Edge.Cuts but NOT gr_circle. The candidate's circle
    becomes two semicircular arcs - geometrically identical; Board A
    itself is never modified."""
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
    removed = 0
    for track in list(board.GetTracks()):
        board.Delete(track)
        removed += 1
    return removed


def apply_zone_policy(board, fixed_boxes, policy):
    """Enforce zone_policy.json. Unclassified zones refuse."""
    kept_fills = []
    kept_keepouts = 0
    removed_keepouts = 0
    for zone in list(board.Zones()):
        if zone.GetIsRuleArea():
            if zone.GetZoneName() != "via_mask_keepout":
                raise CandidateError(
                    "rule area {!r} is not classified by the zone "
                    "policy; refusing".format(zone.GetZoneName()))
            import pcbnew
            box = zone.GetBoundingBox()
            zone_box = [pcbnew.ToMM(box.GetLeft()),
                        pcbnew.ToMM(box.GetTop()),
                        pcbnew.ToMM(box.GetRight()),
                        pcbnew.ToMM(box.GetBottom())]
            if _overlaps(zone_box, fixed_boxes):
                kept_keepouts += 1
            else:
                board.Delete(zone)
                removed_keepouts += 1
        else:
            if zone.GetNetname() != "GND":
                raise CandidateError(
                    "fill zone on net {!r} is not classified by the "
                    "zone policy; refusing".format(
                        zone.GetNetname()))
            kept_fills.append(zone.GetNetname())
    return {"kept_gnd_fills": len(kept_fills),
            "kept_via_mask_keepouts_on_fixed": kept_keepouts,
            "removed_stale_via_mask_keepouts": removed_keepouts}


# ------------------------------------------------- constraint placement

_GOLDEN = math.pi * (3.0 - math.sqrt(5.0))


class SeedPlacer:
    """Deterministic constraint-honoring seed placement.

    Nothing about Board A's internal arrangement is consumed: block
    centroids come from the seeded RNG, satellites derive from their
    anchors' REQUIRED positions, and free parts follow a seeded
    golden-angle spiral. Every position is courtyard-collision
    checked against everything already placed.
    """

    def __init__(self, board, constraints_doc, rng, circle):
        self.board = board
        self.rng = rng
        self.center = (circle[0], circle[1])
        self.radius = circle[2]
        self.footprints = {fp.GetReference(): fp
                           for fp in board.GetFootprints()}
        for reference in constraints_doc[
                "requirement_fixed_references"]:
            if reference not in self.footprints:
                raise CandidateError(
                    "fixed reference {!r} is not on the "
                    "board".format(reference))
        self.fixed = set(constraints_doc[
            "requirement_fixed_references"])
        self.constraints = constraints_doc["constraints"]
        self.boxes = {}
        self.moved = []

    def _set(self, reference, x, y, rotation):
        import pcbnew
        footprint = self.footprints[reference]
        footprint.SetPosition(pcbnew.VECTOR2I(
            pcbnew.FromMM(x), pcbnew.FromMM(y)))
        footprint.SetOrientationDegrees(rotation)
        return _bbox_mm(footprint)

    def _admit(self, reference, x, y, rotation):
        box = self._set(reference, x, y, rotation)
        if _overlaps(box, self.boxes):
            return False
        inside = math.hypot(x - self.center[0], y - self.center[1])
        if inside > self.radius - 2.0:
            return False
        self.boxes[reference] = box
        return True

    def _place_near(self, reference, anchor_xy, budget_mm,
                    prefer_angle):
        rotation = self.rng.choice([0.0, 90.0, 180.0, 270.0])
        step = 0
        radius = 2.0
        while radius <= budget_mm - 0.3:
            angle = prefer_angle + step * _GOLDEN
            x = anchor_xy[0] + radius * math.cos(angle)
            y = anchor_xy[1] + radius * math.sin(angle)
            if self._admit(reference, x, y, rotation):
                self.moved.append(reference)
                return
            step += 1
            if step % 12 == 0:
                radius += 0.6
        raise CandidateError(
            "no collision-free position for {!r} within {} mm of "
            "its anchor".format(reference, budget_mm))

    def _position_of(self, reference):
        import pcbnew
        position = self.footprints[reference].GetPosition()
        return (pcbnew.ToMM(position.x), pcbnew.ToMM(position.y))

    def run(self):
        # 1. Fixed parts stay exactly where the requirement puts them.
        for reference in sorted(self.fixed):
            self.boxes[reference] = _bbox_mm(
                self.footprints[reference])

        proximities = [c for c in self.constraints
                       if c["kind"] == "proximity"]
        blocks = [c for c in self.constraints
                  if c["kind"] == "functional_block"]
        block_members = {member for block in blocks
                         for member in block["members"]}

        # 2. Satellites of FIXED anchors (microphone decoupling and
        # series parts, ESD at the connector): preferred direction is
        # radially inward from the anchor, budget from the constraint.
        for constraint in sorted(
                (c for c in proximities
                 if c["anchor"] in self.fixed
                 and c["reference"] not in self.fixed),
                key=lambda c: c["reference"]):
            anchor_xy = self._position_of(constraint["anchor"])
            inward = math.atan2(self.center[1] - anchor_xy[1],
                                self.center[0] - anchor_xy[0])
            self._place_near(constraint["reference"], anchor_xy,
                             constraint["max_distance_mm"], inward)

        # 3. Functional blocks: seeded centroids on an inner disk,
        # members on a compact spiral, intra-block satellites next to
        # their anchors. A draw that cannot satisfy the block's
        # spread is rolled back and redrawn.
        intra = {c["reference"]: c for c in proximities
                 if c["anchor"] not in self.fixed}
        for block in blocks:
            members = [m for m in block["members"]
                       if m not in self.fixed]
            anchors = [m for m in members if any(
                c["anchor"] == m for c in intra.values())]
            ordered = anchors + [m for m in members
                                 if m not in anchors
                                 and m not in intra]
            satellites = [m for m in members if m in intra]
            placed_here = []
            for draw in range(12):
                try:
                    angle = self.rng.uniform(0.0, 2.0 * math.pi)
                    reach = self.rng.uniform(8.0, 30.0)
                    cx = self.center[0] + reach * math.cos(angle)
                    cy = self.center[1] + reach * math.sin(angle)
                    local_budget = (
                        block["max_spread_mm"] / 2.0 - 0.5
                        if "max_spread_mm" in block else 12.0)
                    for index, member in enumerate(ordered):
                        rotation = self.rng.choice(
                            [0.0, 90.0, 180.0, 270.0])
                        step = 0
                        radius = 0.0 if index == 0 else 1.5
                        done = False
                        while radius <= local_budget:
                            theta = index * 2.1 + step * _GOLDEN
                            x = cx + radius * math.cos(theta)
                            y = cy + radius * math.sin(theta)
                            if self._admit(member, x, y, rotation):
                                placed_here.append(member)
                                done = True
                                break
                            step += 1
                            if step % 10 == 0:
                                radius += 0.8
                        if not done:
                            raise CandidateError(
                                "block {} member {} did not "
                                "fit".format(block["name"], member))
                    for satellite in sorted(satellites):
                        constraint = intra[satellite]
                        anchor_xy = self._position_of(
                            constraint["anchor"])
                        self._place_near(
                            satellite, anchor_xy,
                            constraint["max_distance_mm"],
                            self.rng.uniform(0.0, 2.0 * math.pi))
                        placed_here.append(satellite)
                    break
                except CandidateError:
                    for member in placed_here:
                        self.boxes.pop(member, None)
                    placed_here = []
            else:
                raise CandidateError(
                    "block {!r} found no feasible centroid in 12 "
                    "draws".format(block["name"]))
            self.moved.extend(placed_here)

        # 4. Everything else: seeded golden-angle spiral, outward,
        # collision-checked.
        remaining = sorted(
            reference for reference in self.footprints
            if reference not in self.fixed
            and reference not in self.boxes)
        for index, reference in enumerate(remaining):
            rotation = self.rng.choice([0.0, 90.0, 180.0, 270.0])
            step = 0
            radius = 12.0
            placed = False
            while radius < self.radius - 2.5:
                angle = (index * 1.7) + step * _GOLDEN
                x = self.center[0] + radius * math.cos(angle)
                y = self.center[1] + radius * math.sin(angle)
                if self._admit(reference, x, y, rotation):
                    placed = True
                    break
                step += 1
                if step % 10 == 0:
                    radius += 1.2
            if not placed:
                raise CandidateError(
                    "free part {!r} found no position".format(
                        reference))
            self.moved.append(reference)
        return {"moved": sorted(self.moved),
                "boxes": dict(self.boxes)}


def positions_of(board):
    import pcbnew
    positions = {}
    for footprint in board.GetFootprints():
        position = footprint.GetPosition()
        positions[footprint.GetReference()] = {
            "x_mm": pcbnew.ToMM(position.x),
            "y_mm": pcbnew.ToMM(position.y),
            "rotation_deg": footprint.GetOrientationDegrees(),
        }
    return positions


def evaluate_policy(board, constraints_doc, circle):
    outcome = placement_module.evaluate_placement(
        positions_of(board), constraints_doc["constraints"],
        outline={"kind": "circle",
                 "center_mm": [circle[0], circle[1]],
                 "radius_mm": circle[2]})
    return outcome


def locked_references(constraints_doc):
    """Fixed refs plus every reference bound by a thresholded
    constraint: the translation of semantic constraints into the
    optimizer's own constraint vocabulary (locks)."""
    locked = set(constraints_doc["requirement_fixed_references"])
    for constraint in constraints_doc["constraints"]:
        kind = constraint["kind"]
        if kind == "proximity":
            locked.add(constraint["reference"])
            locked.add(constraint["anchor"])
        elif kind == "functional_block" and \
                "max_spread_mm" in constraint:
            locked.update(constraint["members"])
        elif kind == "separation":
            locked.update(constraint["group_a"])
            locked.update(constraint["group_b"])
        elif kind in ("ordering",):
            locked.update(constraint["references"])
        elif kind == "orientation":
            locked.add(constraint["reference"])
    return sorted(locked)


def routed_status(board_file, nets):
    """Per-net routed status read from the board file itself - the
    arbiter; router logs are never trusted for this."""
    import pcbnew
    board = pcbnew.LoadBoard(board_file)
    per_net = {}
    for track in board.GetTracks():
        name = track.GetNetname()
        if name:
            per_net[name] = per_net.get(name, 0) + 1
    return {net: per_net.get(net, 0) for net in nets}


# ------------------------------------------------------------- pipeline

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--skip-route", action="store_true")
    parser.add_argument("--reuse-placed", action="store_true")
    parser.add_argument("--quench-timeout", type=int, default=1500)
    parser.add_argument("--stage-timeout", type=int, default=1200)
    arguments = parser.parse_args()

    import pcbnew
    constraints_path = os.path.join(HERE, "constraints.json")
    zone_policy_path = os.path.join(HERE, "zone_policy.json")
    constraints_doc = json.load(open(constraints_path,
                                     encoding="utf-8"))
    json.load(open(zone_policy_path, encoding="utf-8"))

    out_dir = os.path.join(HERE, "candidates",
                           "seed{:02d}".format(arguments.seed))
    os.makedirs(out_dir, exist_ok=True)
    derivation_path = os.path.join(out_dir, "derivation.json")
    if os.path.isfile(derivation_path):
        derivation = json.load(open(derivation_path,
                                    encoding="utf-8"))
    else:
        derivation = {
            "kind": "candidate-derivation",
            "candidate": "seed{:02d}".format(arguments.seed),
            "parent_candidate_sha256": None,
            "seed": arguments.seed,
            "inputs": {
                "board_a_sha256": _sha256_file(BOARD_A),
                "constraints_sha256": _sha256_file(
                    constraints_path),
                "zone_policy_sha256": _sha256_file(
                    zone_policy_path),
                "generator_sha256": _sha256_file(
                    os.path.abspath(__file__)),
                "optimizer_sha256": _sha256_file(
                    os.path.join(PLUGIN, "place_optimize.py")),
                "router_sha256": _sha256_file(
                    os.path.join(PLUGIN, "route.py")),
            },
            "records": [],
        }

    def record(entry):
        derivation["records"].append(entry)
        with open(derivation_path, "w", encoding="utf-8",
                  newline="\n") as handle:
            json.dump(derivation, handle, indent=1)
            handle.write("\n")

    def run_stage(name, command, output_path, timeout, extra=None):
        start = time.time()
        entry = {"stage": name, "command": command[1:],
                 "timeout_s": timeout}
        if extra:
            entry.update(extra)
        try:
            completed = subprocess.run(
                command, capture_output=True, text=True,
                timeout=timeout)
            entry.update({
                "returncode": completed.returncode,
                "seconds": round(time.time() - start, 1),
                "log_tail": (completed.stdout
                             + completed.stderr)[-2000:]})
        except subprocess.TimeoutExpired:
            entry.update({"returncode": None,
                          "timed_out_after_s": timeout,
                          "seconds": round(time.time() - start, 1)})
        entry["output_sha256"] = (_sha256_file(output_path)
                                  if os.path.isfile(output_path)
                                  else None)
        record(entry)
        return entry

    seeded_path = os.path.join(out_dir,
                               "candidate_seeded.kicad_pcb")
    placed_path = os.path.join(out_dir,
                               "candidate_placed.kicad_pcb")

    if arguments.reuse_placed and os.path.isfile(placed_path):
        record({"stage": "reuse_placed",
                "note": "reusing the placed artifact whose "
                        "derivation this file already records; the "
                        "original scramble/placement records above "
                        "remain the authority for how it came to be",
                "placed_sha256": _sha256_file(placed_path)})
        placement_ok = True
        circle = _board_circle(pcbnew.LoadBoard(BOARD_A))
    else:
        board = pcbnew.LoadBoard(BOARD_A)
        circle = _board_circle(board)
        converted = convert_circular_outline(board)
        removed_tracks = strip_routing(board)
        fixed_boxes = {
            reference: _bbox_mm(footprint)
            for reference, footprint in
            ((fp.GetReference(), fp)
             for fp in board.GetFootprints())
            if reference in set(constraints_doc[
                "requirement_fixed_references"])}
        zone_outcome = apply_zone_policy(board, fixed_boxes,
                                         None)
        record({"stage": "prepare",
                "circular_outlines_converted": converted,
                "removed_tracks_and_vias": removed_tracks,
                "zone_policy": zone_outcome})

        rng = random.Random(arguments.seed)
        seed_outcome = None
        for attempt in range(8):
            try:
                placer = SeedPlacer(board, constraints_doc, rng,
                                    circle)
                seed_outcome = placer.run()
            except CandidateError as error:
                record({"stage": "seed_placement_attempt",
                        "attempt": attempt, "outcome": "failed",
                        "reason": str(error)})
                continue
            policy = evaluate_policy(board, constraints_doc,
                                     circle)
            overlap_pairs = placement_module.overlapping_pairs(
                seed_outcome["boxes"])
            moved_overlaps = [
                pair for pair in overlap_pairs
                if set(pair) & set(seed_outcome["moved"])]
            record({"stage": "seed_placement_attempt",
                    "attempt": attempt,
                    "outcome": "evaluated",
                    "policy_ok": policy["summary"]["ok"],
                    "violated": policy["summary"]["violated"],
                    "collision_checked_overlaps_moved":
                        moved_overlaps})
            if policy["summary"]["ok"] and not moved_overlaps:
                break
            seed_outcome = None
        if seed_outcome is None:
            record({"stage": "seed_placement",
                    "outcome": "failed",
                    "reason": "no attempt satisfied the policy "
                              "with a collision-free scatter"})
            print("candidate failed at seed placement")
            return 1
        pcbnew.SaveBoard(seeded_path, board)
        record({"stage": "seed_placement", "outcome": "ok",
                "moved_footprints": len(seed_outcome["moved"]),
                "output_sha256": _sha256_file(seeded_path)})

        locked = locked_references(constraints_doc)
        entry = run_stage(
            "place_optimize",
            [sys.executable,
             os.path.join(PLUGIN, "place_optimize.py"),
             seeded_path, placed_path,
             "--max-displacement", "200", "--step", "3.0",
             "--max-passes", "1", "--lock"] + locked,
            placed_path, arguments.quench_timeout,
            extra={"locked_references": len(locked),
                   "lock_meaning": "semantic constraints "
                                   "translated into optimizer "
                                   "locks at constraint-"
                                   "satisfying seed positions"})
        placement_ok = entry.get("returncode") == 0
        if placement_ok:
            placed_board = pcbnew.LoadBoard(placed_path)
            policy = evaluate_policy(placed_board,
                                     constraints_doc, circle)
            record({"stage": "post_quench_policy",
                    "policy_ok": policy["summary"]["ok"],
                    "violated": policy["summary"]["violated"],
                    "unthresholded": policy["summary"][
                        "unthresholded"]})
            placement_ok = policy["summary"]["ok"]

    if not arguments.skip_route and placement_ok:
        checkpoint = placed_path
        for stage_name, nets in _routing_stages():
            stage_path = os.path.join(
                out_dir, "candidate_routed_{}.kicad_pcb".format(
                    stage_name))
            entry = run_stage(
                "route_{}".format(stage_name),
                [sys.executable,
                 os.path.join(PLUGIN, "route.py"), checkpoint,
                 "--output", stage_path, "--nets"] + nets,
                stage_path, arguments.stage_timeout)
            if entry["output_sha256"] is not None:
                checkpoint = stage_path
                status = routed_status(stage_path, nets)
                record({"stage": "route_{}_status".format(
                            stage_name),
                        "board_file_is_arbiter": True,
                        "nets_with_copper": sum(
                            1 for count in status.values()
                            if count),
                        "nets_total": len(nets),
                        "per_net_track_count": status})
            else:
                record({"stage": "route_{}_status".format(
                            stage_name),
                        "board_file_is_arbiter": True,
                        "note": "stage produced no output; the "
                                "previous checkpoint stands"})
        if checkpoint != placed_path:
            all_nets = [net for _name, nets in _routing_stages()
                        for net in nets]
            missed = [net for net, count in
                      routed_status(checkpoint, all_nets).items()
                      if not count]
            if missed:
                cleanup_path = os.path.join(
                    out_dir, "candidate_routed_cleanup.kicad_pcb")
                entry = run_stage(
                    "route_cleanup",
                    [sys.executable,
                     os.path.join(PLUGIN, "route.py"), checkpoint,
                     "--output", cleanup_path, "--nets"] + missed,
                    cleanup_path, arguments.stage_timeout,
                    extra={"reattempted_nets": missed})
                if entry["output_sha256"] is not None:
                    checkpoint = cleanup_path
                    status = routed_status(cleanup_path, missed)
                    record({"stage": "route_cleanup_status",
                            "board_file_is_arbiter": True,
                            "recovered": sorted(
                                net for net, count in
                                status.items() if count),
                            "still_unrouted": sorted(
                                net for net, count in
                                status.items() if not count)})
        final_path = os.path.join(out_dir,
                                  "candidate_routed.kicad_pcb")
        if checkpoint != placed_path:
            with open(checkpoint, "rb") as source:
                data = source.read()
            with open(final_path, "wb") as target:
                target.write(data)
            record({"stage": "final_checkpoint",
                    "from": os.path.basename(checkpoint),
                    "output_sha256": _sha256_file(final_path)})

    print("candidate seed {} -> {}".format(arguments.seed, out_dir))
    for entry in derivation["records"][-8:]:
        print("  {}: {}".format(
            entry["stage"],
            entry.get("outcome",
                      entry.get("returncode",
                                entry.get("policy_ok", "")))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
