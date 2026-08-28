"""Generate one Board B placement/routing candidate.

The experiment's rules, enforced here (generator semantics version
in GENERATOR_SEMANTICS_VERSION, recorded in every derivation):

  * the authoritative Board A file is read-only input; every artifact
    lands under benchmark/boardB/candidates/;
  * requirement-fixed parts keep their positions and rotations -
    those ARE the product requirement;
  * SEMANTIC constraints drive placement: the seed placement is
    constructed to satisfy every thresholded constraint, members
    with fixed-fanout nets face their targets (RC series resistors
    point at their microphone pairs - the electrical mapping is
    never changed, only geometry), the quench moves everything
    except the fixed parts and the satellites of fixed anchors, and
    a bounded REPAIR pass restores any thresholded constraint the
    quench broke; the toolkit's evaluate_placement judges the final
    positions - never this script's word;
  * the scatter is called non-overlapping only because courtyard
    boxes are collision-checked; the result is recorded;
  * zone inheritance is EXECUTED from zone_policy.json by the
    toolkit's zone_inheritance engine - changing the policy changes
    behavior or refuses; an unclassified zone refuses;
  * routing is STAGED with per-stage router parameters derived from
    the board's own declared fabrication minimums; the clock stages
    run with the microphone BGA-misdetection disabled and the
    0.15/0.15/0.05 mm parameters that the mic guard-ring escape
    geometrically requires (root-caused against Board A itself);
  * every routing invocation has an ATTEMPT IDENTITY: unique output
    path bound to attempt id + input SHA + stage, tool SHA and
    configuration recorded, and the checkpoint advances ONLY on a
    completed invocation whose output that invocation itself
    produced - a stale file can never masquerade as fresh routing;
  * after the final stage the zones are REFILLED so the stored fills
    describe THIS candidate, and every net's real connectivity class
    (toolkit classify_net; the board file is the arbiter) is
    recorded;
  * --reuse-placed refuses when the derivation that produced the
    placed artifact used different inputs (board, constraints, zone
    policy, generator semantics), and re-evaluates placement policy
    and collisions on the reused board regardless.

Run with KiCad's python:

    ".../kicad/python.exe" benchmark/boardB/generate_candidate.py \
        --seed 6 [--skip-route] [--reuse-placed]
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
from pcbqa import zone_inheritance                 # noqa: E402

GENERATOR_SEMANTICS_VERSION = "4"

#: Nets with more fixed pads than this are broadcast nets (grounds,
#: supplies); they carry no useful fanout direction.
_FANOUT_NET_FIXED_PAD_LIMIT = 6


class CandidateError(Exception):
    """Candidate generation cannot proceed as asked."""


def _sha256_file(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def _configure_geometry():
    from pcbqa import geom
    manifest = json.load(open(
        os.path.join(REPO, "board", "manifest.live.json"),
        encoding="utf-8"))
    geom.configure(manifest["geometry_profile"]["tolerances"][
        "polygon_chord_error_mm"]["value"])


def _routing_stages():
    """Net groups by board semantics, in routing priority order."""
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


def _stage_router_args(stage_name):
    """Router parameters per stage.

    The clock stages carry the parameters the microphone guard-ring
    escape REQUIRES, established by isolation experiments against
    Board A's own placement: the mics are not BGAs (the router's
    auto-detection walls its own target pad, so it is disabled), and
    the 0.15 mm track/clearance with a 0.05 mm grid are exactly the
    board's declared fabrication minimums - the 0.1 mm default grid
    cannot resolve the ~0.5 mm guard-ring corner channel that Board
    A's own committed escape threads. The via proximity cost is
    raised everywhere to discourage vias near pad mask openings; the
    VIA.* gates remain the authority on whether that sufficed.
    """
    base = ["--via-proximity-cost", "25"]
    if stage_name in ("clock", "cleanup"):
        return base + ["--no-bga-zones", "--track-width", "0.15",
                       "--clearance", "0.15",
                       "--grid-step", "0.05"]
    return base


def _stage_timeout(stage_name, default_timeout):
    if stage_name in ("clock", "cleanup", "power"):
        return max(default_timeout, 1800)
    return default_timeout


# ------------------------------------------------------------ geometry

def _bbox_mm(footprint):
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
    gr_poly on Edge.Cuts but NOT gr_circle. Board A is never
    modified."""
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


# ------------------------------------------------- constraint placement

_GOLDEN = math.pi * (3.0 - math.sqrt(5.0))


class SeedPlacer:
    """Deterministic constraint-honoring placement engine.

    Also serves as the post-quench repair field via from_current():
    boxes reflect the board as it stands, and individual references
    can be re-placed within their constraint budgets.
    """

    def __init__(self, board, constraints_doc, rng, circle,
                 load_current=False):
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
        if load_current:
            for reference, footprint in self.footprints.items():
                self.boxes[reference] = _bbox_mm(footprint)

    # -- primitives ----------------------------------------------------
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
                    prefer_angle, rotation=None):
        self.boxes.pop(reference, None)
        rotations = ([rotation] if rotation is not None
                     else [self.rng.choice([0.0, 90.0, 180.0,
                                            270.0])])
        step = 0
        radius = 2.0
        while radius <= budget_mm - 0.3:
            angle = prefer_angle + step * _GOLDEN
            x = anchor_xy[0] + radius * math.cos(angle)
            y = anchor_xy[1] + radius * math.sin(angle)
            if self._admit(reference, x, y, rotations[0]):
                if reference not in self.moved:
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

    # -- fanout awareness ----------------------------------------------
    def _fixed_fanout_angle(self, reference, from_xy):
        """Direction toward the FIXED pads this part's nets land on,
        or None. Broadcast nets (many fixed pads) are ignored: a
        ground tells no direction."""
        import pcbnew
        footprint = self.footprints[reference]
        nets = {pad.GetNetname() for pad in footprint.Pads()
                if pad.GetNetname()}
        targets = []
        for net in sorted(nets):
            pads = []
            for other_ref in self.fixed:
                other = self.footprints[other_ref]
                for pad in other.Pads():
                    if pad.GetNetname() == net:
                        position = pad.GetPosition()
                        pads.append((pcbnew.ToMM(position.x),
                                     pcbnew.ToMM(position.y)))
            if pads and len(pads) <= _FANOUT_NET_FIXED_PAD_LIMIT:
                targets.extend(pads)
        if not targets:
            return None
        cx = sum(t[0] for t in targets) / len(targets)
        cy = sum(t[1] for t in targets) / len(targets)
        return math.atan2(cy - from_xy[1], cx - from_xy[0])

    def _facing_rotation(self, reference, fanout_angle):
        """The 90-degree rotation whose LAST pad best faces the
        fanout direction - so a series part's output pad points at
        its targets and the router escapes straight."""
        import pcbnew
        footprint = self.footprints[reference]
        pads = sorted(footprint.Pads(),
                      key=lambda pad: pad.GetNumber())
        if len(pads) < 2:
            return self.rng.choice([0.0, 90.0, 180.0, 270.0])
        best, best_score = 0.0, None
        for rotation in (0.0, 90.0, 180.0, 270.0):
            footprint.SetOrientationDegrees(rotation)
            center = footprint.GetPosition()
            out_pad = pads[-1].GetPosition()
            dx = pcbnew.ToMM(out_pad.x) - pcbnew.ToMM(center.x)
            dy = pcbnew.ToMM(out_pad.y) - pcbnew.ToMM(center.y)
            norm = math.hypot(dx, dy) or 1.0
            score = (dx / norm) * math.cos(fanout_angle) \
                + (dy / norm) * math.sin(fanout_angle)
            if best_score is None or score > best_score:
                best, best_score = rotation, score
        return best

    # -- full seed placement -------------------------------------------
    def run(self):
        for reference in sorted(self.fixed):
            self.boxes[reference] = _bbox_mm(
                self.footprints[reference])

        proximities = [c for c in self.constraints
                       if c["kind"] == "proximity"]
        blocks = [c for c in self.constraints
                  if c["kind"] == "functional_block"]

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
            for _draw in range(12):
                try:
                    angle = self.rng.uniform(0.0, 2.0 * math.pi)
                    reach = self.rng.uniform(8.0, 30.0)
                    cx = self.center[0] + reach * math.cos(angle)
                    cy = self.center[1] + reach * math.sin(angle)
                    local_budget = (
                        block["max_spread_mm"] / 2.0 - 0.5
                        if "max_spread_mm" in block else 12.0)
                    for index, member in enumerate(ordered):
                        fanout = self._fixed_fanout_angle(
                            member, (cx, cy))
                        if fanout is not None and index > 0:
                            rotation = self._facing_rotation(
                                member, fanout)
                            theta0 = fanout
                        else:
                            rotation = self.rng.choice(
                                [0.0, 90.0, 180.0, 270.0])
                            theta0 = index * 2.1
                        step = 0
                        radius = 0.0 if index == 0 else 1.5
                        done = False
                        while radius <= local_budget:
                            theta = theta0 + step * _GOLDEN
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

    # -- post-quench repair --------------------------------------------
    def repair(self, evaluation):
        """Restore thresholded constraints the quench broke.

        Proximity: re-place the reference within its budget of the
        anchor's CURRENT position. Block spread: pull the farthest
        member toward the block centroid. Separation violations are
        not repaired - a quench that collapsed a required separation
        fails the candidate honestly.
        """
        repaired = []
        for entry in evaluation["results"]:
            if entry["status"] != "violated":
                continue
            constraint = entry["constraint"]
            if constraint["kind"] == "proximity":
                anchor_xy = self._position_of(constraint["anchor"])
                current = self._position_of(
                    constraint["reference"])
                angle = math.atan2(current[1] - anchor_xy[1],
                                   current[0] - anchor_xy[0])
                self._place_near(constraint["reference"],
                                 anchor_xy,
                                 constraint["max_distance_mm"],
                                 angle)
                repaired.append(constraint["reference"])
            elif constraint["kind"] == "functional_block" and \
                    "max_spread_mm" in constraint:
                members = constraint["members"]
                positions = {m: self._position_of(m)
                             for m in members}
                cx = sum(p[0] for p in positions.values()) \
                    / len(positions)
                cy = sum(p[1] for p in positions.values()) \
                    / len(positions)
                budget = constraint["max_spread_mm"] / 2.0 - 0.5
                # Every member outside the compliant radius comes
                # home this round; one-at-a-time repair oscillates.
                for member in sorted(
                        m for m in members
                        if m not in self.fixed
                        and math.hypot(positions[m][0] - cx,
                                       positions[m][1] - cy)
                        > budget):
                    self._place_near(
                        member, (cx, cy), budget,
                        math.atan2(positions[member][1] - cy,
                                   positions[member][0] - cx))
                    repaired.append(member)
        return repaired

    def resolve_overlaps(self, pairs):
        """Nudge the movable half of each overlapping pair to the
        nearest collision-free spot around its own position. The
        quench's clearance model does not know courtyards; this
        does."""
        moved = []
        for one, other in pairs:
            movable = None
            for candidate in (other, one):
                if candidate not in self.fixed:
                    movable = candidate
                    break
            if movable is None:
                raise CandidateError(
                    "fixed parts {} and {} overlap; the requirement "
                    "geometry itself collides".format(one, other))
            still = one if movable == other else other
            still_xy = self._position_of(still)
            own_xy = self._position_of(movable)
            away = math.atan2(own_xy[1] - still_xy[1],
                              own_xy[0] - still_xy[0])
            self._place_near(movable, own_xy, 9.0, away)
            moved.append(movable)
        return moved


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
    return placement_module.evaluate_placement(
        positions_of(board), constraints_doc["constraints"],
        outline={"kind": "circle",
                 "center_mm": [circle[0], circle[1]],
                 "radius_mm": circle[2]})


def locked_references(constraints_doc):
    """Fixed parts plus the satellites of fixed anchors: the ONLY
    locks. Blocks and their satellites stay free for the quench;
    repair and the policy evaluator own the constraints there."""
    locked = set(constraints_doc["requirement_fixed_references"])
    for constraint in constraints_doc["constraints"]:
        if constraint["kind"] == "proximity" and \
                constraint["anchor"] in set(
                    constraints_doc["requirement_fixed_references"]):
            locked.add(constraint["reference"])
    return sorted(locked)


def connectivity_by_net(board_file, nets):
    """Real connectivity classes from the board file - the arbiter."""
    import pcbnew
    from pcbqa import geom
    from pcbqa.connectivity import classify_net
    board = pcbnew.LoadBoard(board_file)
    outcome = {}
    for net in nets:
        try:
            outcome[net] = classify_net(
                board, net, geom.pad_copper_polygon)["class"]
        except ValueError:
            outcome[net] = "no-pads"
    return outcome


def refill_zones(board_file):
    import pcbnew
    board = pcbnew.LoadBoard(board_file)
    filler = pcbnew.ZONE_FILLER(board)
    filler.Fill(board.Zones())
    pcbnew.SaveBoard(board_file, board)
    return _sha256_file(board_file)


# ------------------------------------------------------------- pipeline

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--skip-route", action="store_true")
    parser.add_argument("--reuse-placed", action="store_true")
    parser.add_argument("--cleanup-only", action="store_true",
                        help="append one recorded cleanup attempt "
                             "to the existing final candidate")
    parser.add_argument("--quench-timeout", type=int, default=1500)
    parser.add_argument("--stage-timeout", type=int, default=1200)
    arguments = parser.parse_args()

    import pcbnew
    _configure_geometry()
    constraints_path = os.path.join(HERE, "constraints.json")
    zone_policy_path = os.path.join(HERE, "zone_policy.json")
    constraints_doc = json.load(open(constraints_path,
                                     encoding="utf-8"))
    zone_policy = json.load(open(zone_policy_path,
                                 encoding="utf-8"))
    zone_inheritance.validate_policy(zone_policy)

    current_inputs = {
        "board_a_sha256": _sha256_file(BOARD_A),
        "constraints_sha256": _sha256_file(constraints_path),
        "zone_policy_sha256": _sha256_file(zone_policy_path),
        "generator_semantics_version": GENERATOR_SEMANTICS_VERSION,
        "generator_sha256": _sha256_file(os.path.abspath(__file__)),
        "optimizer_sha256": _sha256_file(
            os.path.join(PLUGIN, "place_optimize.py")),
        "router_sha256": _sha256_file(
            os.path.join(PLUGIN, "route.py")),
    }

    out_dir = os.path.join(HERE, "candidates",
                           "seed{:02d}".format(arguments.seed))
    os.makedirs(os.path.join(out_dir, "stages"), exist_ok=True)
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
            "inputs": current_inputs,
            "records": [],
        }

    def record(entry):
        derivation["records"].append(entry)
        with open(derivation_path, "w", encoding="utf-8",
                  newline="\n") as handle:
            json.dump(derivation, handle, indent=1)
            handle.write("\n")

    def run_attempt(stage_name, input_path, command_tail, timeout,
                    tool, extra=None):
        """One tool invocation with a full attempt identity. The
        output path is unique to this attempt and must not exist
        before; the recorded output SHA therefore belongs to THIS
        invocation or to nobody."""
        attempt_id = "a{:03d}".format(len(derivation["records"]))
        input_sha = _sha256_file(input_path)
        output_path = os.path.join(
            out_dir, "stages", "{}-{}-{}.kicad_pcb".format(
                attempt_id, stage_name, input_sha[:8]))
        if os.path.exists(output_path):
            raise CandidateError(
                "attempt output {} already exists; attempt "
                "identities never collide".format(output_path))
        command = [sys.executable, os.path.join(PLUGIN, tool),
                   input_path, "--output", output_path] \
            + command_tail
        entry = {"stage": stage_name, "attempt_id": attempt_id,
                 "input_board_sha256": input_sha,
                 "tool": tool,
                 "tool_sha256": current_inputs[
                     "router_sha256" if tool == "route.py"
                     else "optimizer_sha256"],
                 "configuration": command_tail,
                 "timeout_s": timeout, "status": "started",
                 "output_path": os.path.relpath(output_path,
                                                out_dir)}
        if extra:
            entry.update(extra)
        start = time.time()
        try:
            completed = subprocess.run(
                command, capture_output=True, text=True,
                timeout=timeout)
            entry["seconds"] = round(time.time() - start, 1)
            entry["returncode"] = completed.returncode
            entry["log_tail"] = (completed.stdout
                                 + completed.stderr)[-1500:]
            produced = os.path.isfile(output_path)
            if completed.returncode == 0 and produced:
                entry["status"] = "completed"
                entry["output_sha256"] = _sha256_file(output_path)
            else:
                entry["status"] = "failed"
                entry["output_sha256"] = None
        except subprocess.TimeoutExpired:
            entry["seconds"] = round(time.time() - start, 1)
            entry["status"] = "timed-out"
            entry["output_sha256"] = None
        record(entry)
        return entry

    # place_optimize takes (input, output) positionally, not --output
    def run_quench(input_path, locked):
        attempt_id = "a{:03d}".format(len(derivation["records"]))
        input_sha = _sha256_file(input_path)
        output_path = os.path.join(
            out_dir, "stages", "{}-quench-{}.kicad_pcb".format(
                attempt_id, input_sha[:8]))
        if os.path.exists(output_path):
            raise CandidateError("attempt output collision")
        command = [sys.executable,
                   os.path.join(PLUGIN, "place_optimize.py"),
                   input_path, output_path,
                   "--max-displacement", "200", "--step", "3.0",
                   "--max-passes", "1", "--lock"] + locked
        entry = {"stage": "place_optimize",
                 "attempt_id": attempt_id,
                 "input_board_sha256": input_sha,
                 "tool": "place_optimize.py",
                 "tool_sha256": current_inputs["optimizer_sha256"],
                 "configuration": command[4:],
                 "locked_references": len(locked),
                 "lock_meaning": "fixed parts and satellites of "
                                 "fixed anchors only; blocks stay "
                                 "free, repair and the evaluator "
                                 "own their constraints",
                 "timeout_s": arguments.quench_timeout,
                 "status": "started",
                 "output_path": os.path.relpath(output_path,
                                                out_dir)}
        start = time.time()
        try:
            completed = subprocess.run(
                command, capture_output=True, text=True,
                timeout=arguments.quench_timeout)
            entry["seconds"] = round(time.time() - start, 1)
            entry["returncode"] = completed.returncode
            entry["log_tail"] = (completed.stdout
                                 + completed.stderr)[-1200:]
            produced = os.path.isfile(output_path)
            if completed.returncode == 0 and produced:
                entry["status"] = "completed"
                entry["output_sha256"] = _sha256_file(output_path)
            else:
                entry["status"] = "failed"
                entry["output_sha256"] = None
        except subprocess.TimeoutExpired:
            entry["seconds"] = round(time.time() - start, 1)
            entry["status"] = "timed-out"
            entry["output_sha256"] = None
        record(entry)
        return entry, output_path

    if arguments.cleanup_only:
        final_path = os.path.join(out_dir,
                                  "candidate_routed.kicad_pcb")
        if not os.path.isfile(final_path):
            print("cleanup-only: no final candidate")
            return 3
        all_nets = [net for _name, nets in _routing_stages()
                    for net in nets]
        missed = [net for net, cls in connectivity_by_net(
            final_path, all_nets).items()
            if cls != "connectivity-complete"]
        if not missed:
            print("cleanup-only: nothing to do")
            return 0
        entry = run_attempt(
            "route_cleanup", final_path,
            _stage_router_args("cleanup") + ["--nets"] + missed,
            _stage_timeout("cleanup", arguments.stage_timeout),
            "route.py", extra={"reattempted_nets": missed})
        if entry["status"] == "completed":
            with open(os.path.join(out_dir,
                                   entry["output_path"]),
                      "rb") as source:
                data = source.read()
            with open(final_path, "wb") as target:
                target.write(data)
            refilled_sha = refill_zones(final_path)
            classes = connectivity_by_net(final_path, all_nets)
            complete = sum(1 for c in classes.values()
                           if c == "connectivity-complete")
            record({"stage": "final_candidate",
                    "from_attempt_output": entry["output_path"],
                    "zones_refilled": True,
                    "output_sha256": refilled_sha,
                    "connectivity_complete": complete,
                    "net_total": len(all_nets),
                    "not_complete": {
                        net: c for net, c in sorted(
                            classes.items())
                        if c != "connectivity-complete"}})
            print("cleanup-only: {}/{} complete".format(
                complete, len(all_nets)))
        else:
            print("cleanup-only: attempt {}".format(
                entry["status"]))
        return 0

    seeded_path = os.path.join(out_dir,
                               "candidate_seeded.kicad_pcb")
    placed_path = os.path.join(out_dir,
                               "candidate_placed.kicad_pcb")
    circle = _board_circle(pcbnew.LoadBoard(BOARD_A))

    if arguments.reuse_placed:
        if not os.path.isfile(placed_path):
            print("reuse refused: no placed artifact")
            return 3
        stored = derivation.get("inputs", {})
        mismatched = sorted(
            key for key in ("board_a_sha256", "constraints_sha256",
                            "zone_policy_sha256",
                            "generator_semantics_version")
            if stored.get(key) != current_inputs[key])
        if mismatched:
            record({"stage": "reuse_refused",
                    "mismatched_inputs": mismatched,
                    "note": "the placed artifact was produced "
                            "under different design intent; reuse "
                            "would smuggle stale semantics"})
            print("reuse refused: stale inputs", mismatched)
            return 3
        board = pcbnew.LoadBoard(placed_path)
        policy = evaluate_policy(board, constraints_doc, circle)
        boxes = {fp.GetReference(): _bbox_mm(fp)
                 for fp in board.GetFootprints()}
        overlaps = placement_module.overlapping_pairs(boxes)
        record({"stage": "reuse_revalidation",
                "placed_sha256": _sha256_file(placed_path),
                "policy_ok": policy["summary"]["ok"],
                "violated": policy["summary"]["violated"],
                "overlapping_pairs": overlaps})
        placement_ok = policy["summary"]["ok"]
    else:
        board = pcbnew.LoadBoard(BOARD_A)
        converted = convert_circular_outline(board)
        removed_tracks = strip_routing(board)
        fixed_boxes = [
            _bbox_mm(fp) for fp in board.GetFootprints()
            if fp.GetReference() in set(constraints_doc[
                "requirement_fixed_references"])]
        zone_outcome = zone_inheritance.apply_policy(
            board, zone_policy, fixed_boxes)
        record({"stage": "prepare",
                "circular_outlines_converted": converted,
                "removed_tracks_and_vias": removed_tracks,
                "zone_policy_outcome": zone_outcome})

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
                    "attempt": attempt, "outcome": "evaluated",
                    "policy_ok": policy["summary"]["ok"],
                    "violated": policy["summary"]["violated"],
                    "collision_checked_overlaps_moved":
                        moved_overlaps})
            if policy["summary"]["ok"] and not moved_overlaps:
                break
            seed_outcome = None
        if seed_outcome is None:
            record({"stage": "seed_placement",
                    "outcome": "failed"})
            print("candidate failed at seed placement")
            return 1
        pcbnew.SaveBoard(seeded_path, board)
        record({"stage": "seed_placement", "outcome": "ok",
                "moved_footprints": len(seed_outcome["moved"]),
                "output_sha256": _sha256_file(seeded_path)})

        locked = locked_references(constraints_doc)
        quench_entry, quench_path = run_quench(seeded_path, locked)
        placement_ok = quench_entry["status"] == "completed"
        if placement_ok:
            board = pcbnew.LoadBoard(quench_path)
            repair_rounds = []
            placer = SeedPlacer(board, constraints_doc, rng,
                                circle, load_current=True)
            for round_index in range(8):
                policy = evaluate_policy(board, constraints_doc,
                                         circle)
                overlaps = placement_module.overlapping_pairs(
                    placer.boxes)
                if policy["summary"]["ok"] and not overlaps:
                    break
                try:
                    repaired = placer.repair(policy)
                    repaired += placer.resolve_overlaps(overlaps)
                except CandidateError as error:
                    repair_rounds.append({"round": round_index,
                                          "failed": str(error)})
                    break
                repair_rounds.append({"round": round_index,
                                      "moved": repaired})
                if not repaired:
                    break
            policy = evaluate_policy(board, constraints_doc,
                                     circle)
            boxes = {fp.GetReference(): _bbox_mm(fp)
                     for fp in board.GetFootprints()}
            overlaps = placement_module.overlapping_pairs(boxes)
            placement_ok = policy["summary"]["ok"] and not overlaps
            if placement_ok:
                pcbnew.SaveBoard(placed_path, board)
            record({"stage": "post_quench_policy",
                    "repair_rounds": repair_rounds,
                    "policy_ok": policy["summary"]["ok"],
                    "violated": policy["summary"]["violated"],
                    "overlapping_pairs": overlaps,
                    "placed_sha256": _sha256_file(placed_path)
                    if placement_ok else None})

    if not arguments.skip_route and placement_ok:
        checkpoint = placed_path
        for stage_name, nets in _routing_stages():
            entry = run_attempt(
                "route_{}".format(stage_name), checkpoint,
                _stage_router_args(stage_name) + ["--nets"] + nets,
                _stage_timeout(stage_name, arguments.stage_timeout),
                "route.py")
            if entry["status"] == "completed":
                checkpoint = os.path.join(out_dir,
                                          entry["output_path"])
                status = connectivity_by_net(checkpoint, nets)
                record({"stage": "route_{}_connectivity".format(
                            stage_name),
                        "board_file_is_arbiter": True,
                        "complete": sum(
                            1 for c in status.values()
                            if c == "connectivity-complete"),
                        "total": len(nets),
                        "not_complete": {
                            net: c for net, c in sorted(
                                status.items())
                            if c != "connectivity-complete"}})
            else:
                record({"stage": "route_{}_connectivity".format(
                            stage_name),
                        "note": "attempt {} did not complete; the "
                                "previous checkpoint stands and no "
                                "stale file advances it".format(
                                    entry["attempt_id"])})
        all_nets = [net for _name, nets in _routing_stages()
                    for net in nets]
        if checkpoint != placed_path:
            final_path = os.path.join(
                out_dir, "candidate_routed.kicad_pcb")

            def publish(source_path):
                with open(source_path, "rb") as source:
                    data = source.read()
                with open(final_path, "wb") as target:
                    target.write(data)
                return refill_zones(final_path)

            # Missed nets are judged AFTER a refill: stale inherited
            # fills would misclassify plane-served nets and send the
            # cleanup stage chasing copper that already connects.
            refilled_sha = publish(checkpoint)
            missed = [net for net, cls in connectivity_by_net(
                final_path, all_nets).items()
                if cls != "connectivity-complete"]
            if missed:
                entry = run_attempt(
                    "route_cleanup", final_path,
                    _stage_router_args("cleanup") + ["--nets"]
                    + missed,
                    _stage_timeout("cleanup",
                                   arguments.stage_timeout),
                    "route.py",
                    extra={"reattempted_nets": missed})
                if entry["status"] == "completed":
                    refilled_sha = publish(os.path.join(
                        out_dir, entry["output_path"]))
            classes = connectivity_by_net(final_path, all_nets)
            complete = sum(1 for c in classes.values()
                           if c == "connectivity-complete")
            record({"stage": "final_candidate",
                    "from_attempt_output": os.path.relpath(
                        checkpoint, out_dir),
                    "zones_refilled": True,
                    "output_sha256": refilled_sha,
                    "connectivity_complete": complete,
                    "net_total": len(all_nets),
                    "not_complete": {net: c for net, c in sorted(
                        classes.items())
                        if c != "connectivity-complete"}})

    print("candidate seed {} -> {}".format(arguments.seed, out_dir))
    for entry in derivation["records"][-6:]:
        print("  {}: {}".format(
            entry["stage"],
            entry.get("status",
                      entry.get("outcome",
                                entry.get("policy_ok",
                                          entry.get("complete",
                                                    ""))))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
