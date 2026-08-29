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

from pcbqa import headless                        # noqa: E402
headless.suppress_blocking_ui()
from pcbqa import placement as placement_module    # noqa: E402
from pcbqa import zone_inheritance                 # noqa: E402
from pcbqa import critical_topology                # noqa: E402

#: DRC violation types that are FABRICATION GEOMETRY: a routing
#: stage whose output carries any of these under the board's own
#: declared rules is fabrication-invalid and cannot advance the
#: checkpoint. Connectivity obtained by violating a declared minimum
#: is not success.
FABRICATION_VIOLATION_TYPES = (
    "clearance", "hole_clearance", "track_width", "via_diameter",
    "drill_out_of_range", "connection_width", "hole_near_hole",
    "courtyards_overlap", "copper_edge_clearance",
)

GENERATOR_SEMANTICS_VERSION = "5"

#: Nets with more fixed pads than this are broadcast nets (grounds,
#: supplies); they carry no useful fanout direction.
_FANOUT_NET_FIXED_PAD_LIMIT = 6


class CandidateError(Exception):
    """Candidate generation cannot proceed as asked."""


def _sha256_file(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def _place_pro_sibling(board_path):
    """The board's DECLARED design rules travel with every candidate
    artifact: the authoritative .kicad_pro is copied beside it, so
    every DRC - the router's own read, the stage check, the gates -
    judges at the declared floors and never at anything a tool wrote
    down for its own convenience."""
    source = os.path.join(REPO, "microphone_array_v2.kicad_pro")
    sibling = os.path.splitext(board_path)[0] + ".kicad_pro"
    with open(source, "rb") as handle:
        data = handle.read()
    with open(sibling, "wb") as handle:
        handle.write(data)
    return sibling


def stage_fabrication_check(board_path, workdir):
    """Judge one stage output's geometry against the DECLARED rules.

    The attempt artifact itself is never touched: a copy (with the
    authoritative .kicad_pro beside it) is refilled and DRC'd, and
    only the FABRICATION_VIOLATION_TYPES count here - unconnected
    items and parity findings are other gates' business. Returns a
    machine-readable verdict; ok is strictly zero violations.
    """
    import shutil as shutil_module
    os.makedirs(workdir, exist_ok=True)
    check_pcb = os.path.join(workdir, "fabcheck.kicad_pcb")
    shutil_module.copyfile(board_path, check_pcb)
    _place_pro_sibling(check_pcb)
    report_path = os.path.join(workdir, "fabcheck_drc.json")
    completed = subprocess.run(
        ["C:/Program Files/KiCad/10.0/bin/kicad-cli.exe", "pcb",
         "drc", "--format", "json", "-o", report_path,
         "--severity-all", "--all-track-errors", "--refill-zones",
         "--save-board", check_pcb],
        capture_output=True, text=True, timeout=600)
    if not os.path.isfile(report_path):
        return {"ok": "unknown",
                "detail": "kicad-cli drc produced no report "
                          "(rc {})".format(completed.returncode)}
    report = json.load(open(report_path, encoding="utf-8"))
    judged_sha = _sha256_file(check_pcb)
    counts = {}
    for violation in report.get("violations", []):
        if violation.get("type") in FABRICATION_VIOLATION_TYPES:
            counts[violation["type"]] =                 counts.get(violation["type"], 0) + 1
    return {"ok": not counts,
            "violations_by_type": dict(sorted(counts.items())),
            "judged_board_sha256": judged_sha,
            "detail": "kicad-cli DRC at the board's declared rules "
                      "(judged bytes: the refilled working copy, "
                      "bound above); only fabrication-geometry "
                      "violation types counted"}


def _parse_min_clearance(log_text):
    import re
    values = re.findall(r'"min_clearance_used":\s*([0-9.]+)',
                        log_text or "")
    if not values:
        return None
    return min(float(value) for value in values)


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
    """Router parameters per stage - the board's OWN values, never a
    relaxation.

    Clearances and track widths are NOT overridden anywhere: with
    them omitted the router honors the board's net classes in full,
    and --no-fix-drc-settings forbids it from rewriting the output
    project's DRC floors to legalize whatever it produced - the
    declared constraints stay authoritative and the per-stage
    geometry DRC judges the actual copper against them. The clock
    stage additionally runs on F.Cu only (the declared clock
    topology permits no other layer and no vias), with the
    microphone BGA-misdetection disabled and the fine grid the
    guard-ring corner channel needs; the escapes through that
    channel themselves come from the critical-topology planner at
    the declared values, so the router never needs a sub-floor
    allowance to reach a microphone clock pad.
    """
    base = ["--via-proximity-cost", "25", "--no-fix-drc-settings",
            "--fab-overrides",
            os.path.join(HERE, "fab_floors.txt")]
    if stage_name == "power":
        # The largest via any net class on this stage demands
        # (POWER/PLANE: 0.6/0.35); a via at the largest class
        # minimum satisfies every class minimum.
        return base + ["--via-size", "0.6", "--via-drill", "0.35"]
    if stage_name == "clock":
        return base + ["--no-bga-zones", "--grid-step", "0.05",
                       "--layers", "F.Cu"]
    if stage_name == "cleanup":
        return base + ["--no-bga-zones", "--grid-step", "0.05"]
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
        # The part's EXTENT stays clear of the board edge, with the
        # declared 0.3 mm copper-to-edge clearance plus margin - an
        # origin-only check let a test point's pad sit ON the edge.
        corner_reach = max(
            math.hypot(cx - self.center[0], cy - self.center[1])
            for cx in (box[0], box[2]) for cy in (box[1], box[3]))
        if corner_reach > self.radius - 0.5:
            return False
        self.boxes[reference] = box
        return True

    def _place_near(self, reference, anchor_xy, budget_mm,
                    prefer_angle, rotation=None):
        original_box = self.boxes.pop(reference, None)
        footprint = self.footprints[reference]
        original_position = footprint.GetPosition()
        original_rotation = footprint.GetOrientationDegrees()
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
        # Failure must leave no ghost: the part returns to exactly
        # where it was, and its collision box returns to the field.
        footprint.SetPosition(original_position)
        footprint.SetOrientationDegrees(original_rotation)
        if original_box is not None:
            self.boxes[reference] = original_box
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
        members toward the block centroid. Separation violations are
        not repaired - a quench that collapsed a required separation
        fails the candidate honestly. An individual re-place that
        finds no room is RECORDED and skipped, not fatal: the next
        round retries it after overlap decluttering has loosened
        the field.
        """
        repaired = []
        self.repair_failures = []
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
                try:
                    self._place_near(constraint["reference"],
                                     anchor_xy,
                                     constraint["max_distance_mm"],
                                     angle)
                    repaired.append(constraint["reference"])
                except CandidateError as error:
                    self.repair_failures.append(str(error))
            elif constraint["kind"] == "functional_block" and \
                    "max_spread_mm" in constraint:
                members = constraint["members"]
                positions = {m: self._position_of(m)
                             for m in members}
                # The LARGEST member anchors the block: small parts
                # come to it. Asking the big IC to fit into the
                # crowd of its own already-gathered members is how
                # repair used to wedge.
                def _area(member):
                    box = self.boxes.get(member) or _bbox_mm(
                        self.footprints[member])
                    return (box[2] - box[0]) * (box[3] - box[1])
                anchor_member = max(members, key=_area)
                cx, cy = positions[anchor_member]
                budget = constraint["max_spread_mm"] / 2.0 - 0.5
                # Every member outside the compliant radius comes
                # home this round; one-at-a-time repair oscillates.
                for member in sorted(
                        m for m in members
                        if m not in self.fixed
                        and m != anchor_member
                        and math.hypot(positions[m][0] - cx,
                                       positions[m][1] - cy)
                        > budget):
                    try:
                        self._place_near(
                            member, (cx, cy), budget,
                            math.atan2(positions[member][1] - cy,
                                       positions[member][0] - cx))
                        repaired.append(member)
                    except CandidateError as error:
                        self.repair_failures.append(str(error))
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


def strip_slivers(source_path, target_path,
                  minimum_width_mm=0.127):
    """Delete track segments narrower than the board's DRC floor.

    The general router sometimes emits sub-floor taper slivers at
    tight pads; deleting them makes the geometry honest at the cost
    of connectivity - the affected net degrades to partial, which
    the classification reports and the cleanup stage retries. No
    via is ever touched.
    """
    import pcbnew
    board = pcbnew.LoadBoard(source_path)
    removed = 0
    for track in list(board.GetTracks()):
        if track.GetClass() in ("PCB_VIA", "VIA"):
            continue
        if track.GetWidth() / 1e6 < minimum_width_mm:
            board.Delete(track)
            removed += 1
    pcbnew.SaveBoard(target_path, board)
    return removed


EDGE_CLEARANCE_MM = 0.30
EDGE_REPAIR_MARGIN_MM = 0.20


def repair_edge_clearance(board, placer, circle, fixed):
    """Cheap hard geometry, enforced where it is cheap: a pad whose
    copper reaches inside the board-edge clearance is found by the
    toolkit's pad-accurate check and its footprint (when movable)
    is walked radially inward past the requirement plus a margin.
    The fabrication DRC remains the authority; this stops full
    routing runs dying to one testpoint the quench parked on the
    rim. Findings on requirement-fixed references are returned
    unrepaired - a fixed part's geometry is a design fact."""
    import math as math_module
    import pcbnew
    from pcbqa import feedback as feedback_module
    from pcbqa import geom as geom_module
    findings = feedback_module.edge_clearance_findings(
        board,
        {"kind": "circle", "center_mm": [circle[0], circle[1]],
         "radius_mm": circle[2]},
        EDGE_CLEARANCE_MM, geom_module.pad_copper_polygon)
    moved = []
    remaining = []
    by_reference = {}
    for finding in findings:
        reference = finding["references"][0]
        if reference in fixed:
            remaining.append(finding)
            continue
        deficit = (EDGE_CLEARANCE_MM
                   - finding["observed_margin_mm"]
                   + EDGE_REPAIR_MARGIN_MM)
        previous = by_reference.get(reference, 0.0)
        by_reference[reference] = max(previous, deficit)
    for reference, deficit in sorted(by_reference.items()):
        footprint = next(
            fp for fp in board.GetFootprints()
            if fp.GetReference() == reference)
        position = footprint.GetPosition()
        dx = position.x / 1e6 - circle[0]
        dy = position.y / 1e6 - circle[1]
        norm = math_module.hypot(dx, dy) or 1.0
        footprint.SetPosition(pcbnew.VECTOR2I(
            int(round((position.x / 1e6 - dx / norm * deficit)
                      * 1e6)),
            int(round((position.y / 1e6 - dy / norm * deficit)
                      * 1e6))))
        placer.boxes[reference] = _bbox_mm(footprint)
        moved.append(reference)
    return findings, moved, remaining


def net_clearance_map(board, project_path):
    """Per-net clearance exactly as the DRC will judge it: each
    net's class clearance, resolved from the authoritative
    project's OWN netclass patterns (fnmatch), deliberately NOT
    from pcbnew's GetNetClassName - that lookup was observed
    mid-run (2026-08-28, first seed11/13 runs, since superseded)
    resolving every net to Default, pricing POWER pads at 0.20 mm
    and refusing 0.15-designed escape corridors; it could not be
    reproduced in isolation, so the defense is structural: this
    resolution never consults process state, and the derivation
    records the map's histogram. When several patterns match a
    net, the LARGEST matching clearance wins: the planner may
    over-clear, never under-clear. Fail-closed: a pattern naming a
    class without a defined clearance, or a project without a
    Default clearance, refuses the run rather than guessing."""
    import fnmatch
    project = json.load(open(project_path, encoding="utf-8"))
    settings = project["net_settings"]
    classes = {}
    default_clearance = None
    for record in settings["classes"]:
        clearance = record.get("clearance")
        if clearance is not None:
            classes[record["name"]] = float(clearance)
        if record.get("name") == "Default":
            default_clearance = clearance
    if default_clearance is None:
        raise SystemExit(
            "the authoritative project defines no Default netclass "
            "clearance; refusing to plan blind")
    patterns = settings.get("netclass_patterns") or []
    for entry in patterns:
        if entry["netclass"] not in classes:
            raise SystemExit(
                "netclass pattern {!r} names class {!r}, which "
                "defines no clearance; refusing to plan "
                "blind".format(entry["pattern"], entry["netclass"]))
    mapping = {}
    for key in board.GetNetsByName().keys():
        name = str(key)
        if not name:
            continue
        matched = [classes[entry["netclass"]]
                   for entry in patterns
                   if fnmatch.fnmatchcase(name, entry["pattern"])]
        mapping[name] = max(matched) if matched \
            else float(default_clearance)
    # Copper with NO net (netname ""): the planner's fallback for
    # an unmapped name is rules.clearance_mm, which can be BELOW
    # Default - an under-clearance. Price it at the largest class
    # clearance instead: over-clearing is the only safe direction
    # for copper whose pairing rules are unknown.
    mapping[""] = max(classes.values())
    return mapping


def clearance_map_fingerprint(mapping):
    """A machine-checkable shape of the clearance map for the
    derivation record: if a run ever plans with a degenerate map
    again (every net at one value), the artifact says so."""
    histogram = {}
    for value in mapping.values():
        key = "{:.3f}".format(value)
        histogram[key] = histogram.get(key, 0) + 1
    return {
        "nets": len(mapping),
        "histogram": dict(sorted(histogram.items())),
        "sha256": hashlib.sha256(json.dumps(
            mapping, sort_keys=True).encode("utf-8")).hexdigest(),
    }


def descend_from_parent(parent_seed, out_dir,
                        constraints_doc, record, circle,
                        apply_moves=True):
    """A targeted descendant: the parent's PLACED board plus moves
    derived from the parent's own feedback artifact. Every move
    names its cause; the ordinary repair loop, policy evaluation
    and progression then judge the result - descent is a search
    move, not a shortcut past any gate."""
    import math as math_module
    import pcbnew
    from pcbqa import freshness as freshness_module
    parent_name = "seed{:02d}".format(parent_seed)
    parent_dir = os.path.join(HERE, "candidates", parent_name)
    parent_board_path = os.path.join(parent_dir,
                                     "candidate_placed.kicad_pcb")
    feedback_path = os.path.join(parent_dir, "feedback.json")
    for needed in (parent_board_path, feedback_path):
        if not os.path.isfile(needed):
            raise CandidateError(
                "descent needs {}, which does not exist; a "
                "descendant without its parent's evidence is just "
                "a random seed wearing a label".format(needed))
    with open(feedback_path, encoding="utf-8") as handle:
        feedback_doc = json.load(handle)
    board = pcbnew.LoadBoard(parent_board_path)
    fixed = set(constraints_doc["requirement_fixed_references"])
    footprints = {fp.GetReference(): fp
                  for fp in board.GetFootprints()}
    displacements = {}
    causes = {}
    for index, rec in enumerate(
            feedback_doc["records"] if apply_moves else []):
        for reference in rec["suggested_movable_references"]:
            if reference in fixed or reference not in footprints:
                continue
            fp = footprints[reference]
            position = fp.GetPosition()
            if rec["kind"] == "board-edge-clearance":
                # Radially inward, toward the OUTLINE CENTER,
                # past the requirement.
                dx = circle[0] - position.x / 1e6
                dy = circle[1] - position.y / 1e6
                magnitude = (rec["required_margin_mm"]
                             - rec["observed_margin_mm"]
                             + EDGE_REPAIR_MARGIN_MM)
            else:
                # Away from the refused pad: open the corridor.
                # A heuristic displacement, recorded as one - the
                # validation decides if it helped.
                dx = position.x / 1e6 - rec["location_mm"][0]
                dy = position.y / 1e6 - rec["location_mm"][1]
                magnitude = 0.4
            norm = math_module.hypot(dx, dy) or 1.0
            move = displacements.setdefault(reference,
                                            [0.0, 0.0])
            move[0] += dx / norm * magnitude
            move[1] += dy / norm * magnitude
            causes.setdefault(reference, []).append(
                {"record_index": index, "kind": rec["kind"],
                 "pads": rec["pads"]})
    moves = []
    boxes = {fp.GetReference(): _bbox_mm(fp)
             for fp in board.GetFootprints()}
    for reference, (dx, dy) in sorted(displacements.items()):
        # Several refusals may pull one reference at once; the
        # aggregate is CLAMPED to one step so a popular movable is
        # nudged, not launched across the board - and a move whose
        # courtyard lands on a neighbour is REVERTED and recorded,
        # because a descent that trades a refusal for a collision
        # learned nothing.
        total = math_module.hypot(dx, dy)
        if total > 0.4:
            dx *= 0.4 / total
            dy *= 0.4 / total
        fp = footprints[reference]
        position = fp.GetPosition()
        fp.SetPosition(pcbnew.VECTOR2I(
            int(round(position.x + dx * 1e6)),
            int(round(position.y + dy * 1e6))))
        boxes[reference] = _bbox_mm(fp)
        collisions = [pair for pair in
                      placement_module.overlapping_pairs(boxes)
                      if reference in pair]
        if collisions:
            fp.SetPosition(position)
            boxes[reference] = _bbox_mm(fp)
            moves.append({"reference": reference,
                          "dx_mm": round(dx, 4),
                          "dy_mm": round(dy, 4),
                          "applied": False,
                          "reverted_reason":
                              "courtyard collision with "
                              + json.dumps(collisions),
                          "causes": causes[reference]})
            continue
        moves.append({"reference": reference,
                      "dx_mm": round(dx, 4),
                      "dy_mm": round(dy, 4),
                      "applied": True,
                      "causes": causes[reference]})
    descended_path = os.path.join(out_dir,
                                  "descended_placement.kicad_pcb")
    pcbnew.SaveBoard(descended_path, board)
    _place_pro_sibling(descended_path)
    entry = {
        "stage": "descend",
        "status": "completed",
        "parent_candidate": parent_name,
        "parent_placed_sha256": _sha256_file(parent_board_path),
        "feedback_artifact": {
            "path": os.path.relpath(feedback_path, REPO),
            "canonical_sha256":
                freshness_module.canonical_json_digest(
                    feedback_doc)},
        "moves": moves,
        "feedback_moves_applied": apply_moves,
        "output_sha256": _sha256_file(descended_path),
    }
    record(entry)
    return entry, descended_path


def strip_named_grazes(source_path, target_path, report_path):
    """Delete tracks matched to a DRC report's connection_width
    violations by reported length (within 0.01 mm) and one
    endpoint (within 0.05 mm Manhattan) - not by UUID, so the
    match is approximate and can in principle remove more than
    one candidate track per violation (hardening to the report's
    own UUIDs is a recorded follow-up). The safety does not rest
    on the match: connectivity is re-measured from the
    post-deletion board file, the affected net honestly degrades
    to partial for the cleanup stage to retry, and the check that
    found the graze re-judges the result. No via is ever touched.
    """
    import pcbnew
    import re as re_module
    with open(report_path, encoding="utf-8") as handle:
        report = json.load(handle)
    targets = []
    for violation in report.get("violations", []):
        if violation.get("type") != "connection_width":
            continue
        for item in violation.get("items", []):
            description = item.get("description", "")
            if not description.startswith("Track"):
                continue
            match = re_module.search(
                r"length ([0-9.]+) mm", description)
            position = item.get("pos") or {}
            if match and "x" in position:
                targets.append((float(match.group(1)),
                                position["x"], position["y"]))
    board = pcbnew.LoadBoard(source_path)
    removed = 0
    for track in list(board.GetTracks()):
        if track.GetClass() in ("PCB_VIA", "VIA"):
            continue
        start = track.GetStart()
        end = track.GetEnd()
        length = (((end.x - start.x) ** 2
                   + (end.y - start.y) ** 2) ** 0.5) / 1e6
        for want_length, x, y in targets:
            if abs(length - want_length) > 0.01:
                continue
            near = min(
                abs(start.x / 1e6 - x) + abs(start.y / 1e6 - y),
                abs(end.x / 1e6 - x) + abs(end.y / 1e6 - y))
            if near < 0.05:
                board.Delete(track)
                removed += 1
                break
    pcbnew.SaveBoard(target_path, board)
    return removed


def refill_zones(board_file):
    import pcbnew
    board = pcbnew.LoadBoard(board_file)
    filler = pcbnew.ZONE_FILLER(board)
    filler.Fill(board.Zones())
    pcbnew.SaveBoard(board_file, board)
    # SaveBoard writes a DEFAULT project sibling when the board has
    # no attached project - factory floors beside a candidate. The
    # declared rules must travel with every artifact, so the
    # authoritative project is re-placed here, at the one choke
    # point every publish passes through (the seed14 lesson:
    # a last-mile round that changed nothing shipped a stripped
    # sibling, and the release DRC judged 790 phantom errors at
    # KiCad defaults).
    _place_pro_sibling(board_file)
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
    parser.add_argument("--no-feedback-moves",
                        action="store_true",
                        help="descend from the parent's placement "
                             "WITHOUT applying feedback moves - "
                             "pure placement reuse, for when the "
                             "downstream machinery (not the "
                             "placement) was what changed")
    parser.add_argument("--route-anyway", action="store_true",
                        help="run general routing even when "
                             "mandatory critical escapes refused "
                             "or the critical-stage geometry "
                             "failed - diagnostics only; the "
                             "default skips provably doomed "
                             "routing")
    parser.add_argument("--descend-from", type=int, default=None,
                        help="start from this parent seed's placed "
                             "board and consume its feedback.json "
                             "instead of seeding and quenching "
                             "fresh; lineage is recorded in the "
                             "derivation")
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

    import inspect

    def _placement_semantics_sha256():
        """The digest of the code that actually decides placement:
        the seed placer, the lock translation, the policy
        evaluation, the geometry helpers, plus the optimizer script.
        Reuse binds to THIS, not to a manually bumped version
        string - when any of these change, a placed artifact was
        produced under different semantics."""
        import hashlib as hashlib_module
        pieces = [inspect.getsource(SeedPlacer),
                  inspect.getsource(locked_references),
                  inspect.getsource(evaluate_policy),
                  inspect.getsource(positions_of),
                  inspect.getsource(_bbox_mm),
                  inspect.getsource(_overlaps)]
        digest = hashlib_module.sha256()
        for piece in pieces:
            digest.update(piece.encode("utf-8"))
        with open(os.path.join(PLUGIN, "place_optimize.py"),
                  "rb") as handle:
            digest.update(handle.read())
        return digest.hexdigest()

    current_inputs = {
        "board_a_sha256": _sha256_file(BOARD_A),
        "placement_semantics_sha256":
            _placement_semantics_sha256(),
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
        _place_pro_sibling(input_path)
        try:
            completed = subprocess.run(
                command, capture_output=True, text=True,
                timeout=timeout)
            entry["seconds"] = round(time.time() - start, 1)
            entry["returncode"] = completed.returncode
            log_text = completed.stdout + completed.stderr
            entry["log_tail"] = log_text[-1500:]
            entry["min_clearance_used_mm"] = \
                _parse_min_clearance(log_text)
            produced = os.path.isfile(output_path)
            if completed.returncode == 0 and produced:
                entry["output_sha256"] = _sha256_file(output_path)
                if tool == "route.py":
                    verdict = stage_fabrication_check(
                        output_path,
                        os.path.join(out_dir, "stages",
                                     "fabcheck-" + attempt_id))
                    entry["fabrication_geometry"] = verdict
                    if verdict["ok"] is True:
                        entry["status"] = "completed"
                    else:
                        # Connectivity below the declared floor is
                        # not success: the attempt stands recorded,
                        # the checkpoint does not move.
                        entry["status"] = "fabrication-invalid"
                else:
                    entry["status"] = "completed"
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
                            "generator_semantics_version",
                            "placement_semantics_sha256")
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
        # Reuse requires the policy AND collision-free courtyards:
        # an overlapping placement is not reusable, whatever the
        # thresholded constraints say.
        placement_ok = policy["summary"]["ok"] and not overlaps
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
        if arguments.descend_from is not None:
            quench_entry, quench_path = descend_from_parent(
                arguments.descend_from, out_dir, constraints_doc,
                record, circle,
                apply_moves=not arguments.no_feedback_moves)
        else:
            quench_entry, quench_path = run_quench(seeded_path,
                                                   locked)
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
                    # Declutter first: overlapping courtyards are
                    # why constraint repair finds no room.
                    repaired = placer.resolve_overlaps(overlaps)
                    repaired += placer.repair(policy)
                except CandidateError as error:
                    repair_rounds.append({"round": round_index,
                                          "failed": str(error)})
                    break
                edge_findings, edge_moved, _edge_fixed = \
                    repair_edge_clearance(
                        board, placer, circle,
                        set(constraints_doc[
                            "requirement_fixed_references"]))
                repaired += edge_moved
                repair_rounds.append({
                    "round": round_index, "moved": repaired,
                    "edge_findings": len(edge_findings),
                    "edge_moved": edge_moved,
                    "deferred": list(getattr(
                        placer, "repair_failures", []))})
                if not repaired:
                    break
            policy = evaluate_policy(board, constraints_doc,
                                     circle)
            boxes = {fp.GetReference(): _bbox_mm(fp)
                     for fp in board.GetFootprints()}
            overlaps = placement_module.overlapping_pairs(boxes)
            final_edge, _moved, edge_on_fixed = \
                repair_edge_clearance(
                    board, placer, circle,
                    set(constraints_doc[
                        "requirement_fixed_references"]))
            movable_edge = [finding for finding in final_edge
                            if finding not in edge_on_fixed]
            # Placement is only OK when the cheap hard rules hold
            # too: a movable pad still inside the edge clearance
            # after repair is a placement failure HERE, not a
            # routing-stage surprise three stages later.
            placement_ok = (policy["summary"]["ok"]
                            and not overlaps
                            and not movable_edge)
            if placement_ok:
                pcbnew.SaveBoard(placed_path, board)
            record({"stage": "post_quench_policy",
                    "repair_rounds": repair_rounds,
                    "policy_ok": policy["summary"]["ok"],
                    "violated": policy["summary"]["violated"],
                    "overlapping_pairs": overlaps,
                    "edge_findings_remaining_movable": [
                        finding["pads"][0]
                        for finding in movable_edge],
                    "edge_findings_on_fixed": [
                        finding["pads"][0]
                        for finding in edge_on_fixed],
                    "placed_sha256": _sha256_file(placed_path)
                    if placement_ok else None})

    if not arguments.skip_route and placement_ok:
        checkpoint = placed_path
        _place_pro_sibling(checkpoint)

        # ---- critical-topology stages: verified escapes out of the
        # microphone guard ring for every mic clock pad, and GND
        # stitches from the guard bars to the internal planes - all
        # at the DECLARED values, before any general routing.
        critical_started = time.time()
        intent = json.load(open(os.path.join(
            HERE, "critical_structures.json"), encoding="utf-8"))
        import re as re_module
        from pcbqa import geom as geom_module
        board = pcbnew.LoadBoard(checkpoint)
        refill_board = pcbnew.ZONE_FILLER(board)
        refill_board.Fill(board.Zones())
        escape_regex = re_module.compile(
            intent["escapes"]["pad_regex"])
        mandatory_net_regex = re_module.compile(
            intent.get("mandatory_escape_nets", "$^"))

        def _escape_alternatives(pad_xy, direction, length,
                                 net=None):
            """Candidate endpoints on an arc sweep around the
            preferred heading, nearest headings first, longer
            reach first: the endpoint is scaffolding, and every
            candidate is planned and re-verified under the same
            rules - search freedom, never acceptance freedom."""
            endpoints = []
            for step in (15, -15, 30, -30, 45, -45, 60, -60,
                         90, -90, 120, -120, 150, -150, 180):
                heading = direction + math.radians(step)
                for reach in (length, 0.75 * length,
                              0.55 * length):
                    endpoints.append((
                        pad_xy[0] + reach * math.cos(heading),
                        pad_xy[1] + reach * math.sin(heading)))
            # The straight heading at shorter reaches, before the
            # sweep widens.
            endpoints.insert(0, (
                pad_xy[0] + 0.75 * length * math.cos(direction),
                pad_xy[1] + 0.75 * length * math.sin(direction)))
            # Counterpart-aimed ordering was TRIED and did not
            # beat the plain nearest-heading sweep on the same
            # placement (74/83 vs 76/83, seed35 vs seed34); the
            # experiment is recorded here and the simpler
            # ordering kept.
            return endpoints
        stitch_regex = re_module.compile(
            intent["stitches"]["pad_regex"])
        planner_outcome = {"escapes": {"placed": 0, "refused": []},
                           "stitches": {"placed": 0, "refused": []}}
        clearance_by_net = net_clearance_map(
            board, os.path.join(REPO,
                                "microphone_array_v2.kicad_pro"))
        for footprint in board.GetFootprints():
            center = footprint.GetPosition()
            for pad in footprint.Pads():
                label = "{}.{}".format(footprint.GetReference(),
                                       pad.GetNumber())
                position = pad.GetPosition()
                pad_xy = (position.x / 1e6, position.y / 1e6)
                if escape_regex.match(label):
                    direction = math.atan2(
                        position.y - center.y,
                        position.x - center.x)
                    end = (pad_xy[0] + intent["escapes"][
                               "length_mm"] * math.cos(direction),
                           pad_xy[1] + intent["escapes"][
                               "length_mm"] * math.sin(direction))
                    try:
                        proposal = critical_topology.local_connect(
                            board, pad.GetNetname(), pad_xy, end,
                            intent["rules"]["escape"],
                            geom_module.pad_copper_polygon,
                            outline={"center_mm": [circle[0],
                                                   circle[1]],
                                     "radius_mm": circle[2],
                                     "clearance_mm": 0.3},
                            net_clearances=clearance_by_net,
                            alternatives=_escape_alternatives(
                                pad_xy, direction,
                                intent["escapes"]["length_mm"],
                                net=pad.GetNetname()))
                        critical_topology.apply_proposal(board,
                                                         proposal)
                        planner_outcome["escapes"]["placed"] += 1
                        planner_outcome["escapes"].setdefault(
                            "fallback_endpoints", 0)
                        if proposal["endpoint_index"]:
                            planner_outcome["escapes"][
                                "fallback_endpoints"] += 1
                    except critical_topology.TopologyPlanError \
                            as error:
                        planner_outcome["escapes"][
                            "refused"].append(
                            {"pad": label,
                             "net": pad.GetNetname(),
                             "location_mm": [pad_xy[0],
                                             pad_xy[1]],
                             "reason": str(error)})
                elif stitch_regex.match(label) and \
                        pad.GetNetname() == intent["stitches"][
                            "net"]:
                    direction = math.atan2(
                        position.y - center.y,
                        position.x - center.x)
                    try:
                        proposal = \
                            critical_topology.stitch_to_plane(
                                board, intent["stitches"]["net"],
                                pad_xy,
                                intent["stitches"]["plane_net"],
                                intent["rules"]["stitch"],
                                geom_module.pad_copper_polygon,
                                prefer_angle=direction,
                                outline={"center_mm": [circle[0],
                                                       circle[1]],
                                         "radius_mm": circle[2],
                                         "clearance_mm": 0.3},
                                net_clearances=clearance_by_net)
                        critical_topology.apply_proposal(board,
                                                         proposal)
                        planner_outcome["stitches"]["placed"] += 1
                    except critical_topology.TopologyPlanError \
                            as error:
                        planner_outcome["stitches"][
                            "refused"].append(
                            {"pad": label, "reason": str(error)})
        critical_path = os.path.join(
            out_dir, "candidate_critical.kicad_pcb")
        pcbnew.SaveBoard(critical_path, board)
        _place_pro_sibling(critical_path)
        verdict = stage_fabrication_check(
            critical_path, os.path.join(out_dir, "stages",
                                        "fabcheck-critical"))
        # Downstream refusals become a structured feedback
        # artifact a descendant candidate can consume: each record
        # names the pad, the net, the location, why it refused and
        # which references the design intent permits to move.
        from pcbqa import feedback as feedback_module
        fixed_references = set(
            constraints_doc["requirement_fixed_references"])
        feedback_records = []
        for refusal in planner_outcome["escapes"]["refused"]:
            owner = refusal["pad"].split(".")[0]
            feedback_records.append(
                feedback_module.escape_refusal_record(
                    refusal["pad"], refusal.get("net", ""),
                    refusal.get("location_mm", [0.0, 0.0]),
                    refusal["reason"],
                    [] if owner in fixed_references else [owner],
                    {"kind": "planner-outcome",
                     "identity": "derivation:critical_topology:"
                                 "seed{:02d}".format(
                                     arguments.seed)},
                    intent["rules"]["escape"]["clearance_mm"]))
        feedback_path = os.path.join(out_dir, "feedback.json")
        with open(feedback_path, "w", encoding="utf-8",
                  newline="\n") as handle:
            json.dump({
                "kind": "candidate-feedback",
                "candidate": "seed{:02d}".format(arguments.seed),
                "records": feedback_records,
                "meaning": "structured failure attribution for the "
                           "NEXT iteration: a descendant applies "
                           "these within its own constraints and "
                           "the ordinary progression judges the "
                           "result",
            }, handle, indent=1)
            handle.write("\n")
        record({"stage": "critical_topology",
                "planner": "pcbqa.critical_topology",
                "seconds": round(time.time() - critical_started,
                                 1),
                "feedback_records": len(feedback_records),
                "net_clearances": clearance_map_fingerprint(
                    clearance_by_net),
                "intent_sha256": _sha256_file(os.path.join(
                    HERE, "critical_structures.json")),
                "outcome": planner_outcome,
                "fabrication_geometry": verdict,
                "output_sha256": _sha256_file(critical_path)})
        if verdict["ok"] is True:
            checkpoint = critical_path
        # A candidate that cannot produce its MANDATORY critical
        # structures, or whose critical-stage geometry already
        # violates the declared floors, is provably going to
        # reject: the general router cannot legalise a refused
        # clock escape or un-violate fabrication geometry. Routing
        # such a candidate is diagnostics, not search - opt in
        # with --route-anyway.
        refused_mandatory = [
            refusal["pad"] for refusal
            in planner_outcome["escapes"]["refused"]
            if mandatory_net_regex.match(refusal.get("net", ""))]
        if (refused_mandatory or verdict["ok"] is not True) \
                and not arguments.route_anyway:
            record({"stage": "routing_skipped",
                    "reason": "mandatory critical escapes "
                              "refused: {}".format(
                                  refused_mandatory)
                    if refused_mandatory else
                    "critical-stage fabrication geometry "
                    "failed",
                    "refused_mandatory": refused_mandatory,
                    "critical_fab_ok": verdict.get("ok"),
                    "meaning": "the reject is provable now; the "
                              "candidate publishes its placed/"
                              "critical state for validation and "
                              "no router time is spent on it"})
            all_nets = [net for _name, nets in _routing_stages()
                        for net in nets]
            final_path = os.path.join(
                out_dir, "candidate_routed.kicad_pcb")
            with open(checkpoint, "rb") as source:
                held = source.read()
            with open(final_path, "wb") as target:
                target.write(held)
            refilled_sha = refill_zones(final_path)
            classes = connectivity_by_net(final_path, all_nets)
            complete = sum(1 for c in classes.values()
                           if c == "connectivity-complete")
            record({"stage": "final_candidate",
                    "from_attempt_output": os.path.relpath(
                        checkpoint, out_dir),
                    "zones_refilled": True,
                    "routing_skipped": True,
                    "output_sha256": refilled_sha,
                    "connectivity_complete": complete,
                    "net_total": len(all_nets),
                    "not_complete": {net: c for net, c in sorted(
                        classes.items())
                        if c != "connectivity-complete"}})
            print("candidate seed {} -> {}".format(
                arguments.seed, out_dir))
            for entry in derivation["records"][-6:]:
                print("  {}: {}".format(
                    entry["stage"],
                    entry.get("status") or entry.get(
                        "reason", "")))
            return 0
        for stage_name, nets in _routing_stages():
            entry = run_attempt(
                "route_{}".format(stage_name), checkpoint,
                _stage_router_args(stage_name) + ["--nets"] + nets,
                _stage_timeout(stage_name, arguments.stage_timeout),
                "route.py")
            if entry["status"] == "fabrication-invalid" and \
                    set((entry.get("fabrication_geometry") or {})
                        .get("violations_by_type") or {}) <= {
                            "track_width", "connection_width"}:
                # Sliver-only invalidity is recoverable: delete the
                # sub-floor tapers AND the exact tracks the DRC
                # names in connection-width grazes, then re-judge.
                # The net that loses copper degrades to partial -
                # honestly - and the group cleanup retries it.
                stripped_path = os.path.join(
                    out_dir, "stages",
                    "{}-slivstrip.kicad_pcb".format(
                        entry["attempt_id"]))
                removed = strip_slivers(
                    os.path.join(out_dir, entry["output_path"]),
                    stripped_path)
                graze_report = os.path.join(
                    out_dir, "stages",
                    "fabcheck-{}".format(entry["attempt_id"]),
                    "fabcheck_drc.json")
                if os.path.isfile(graze_report):
                    removed += strip_named_grazes(
                        stripped_path, stripped_path,
                        graze_report)
                _place_pro_sibling(stripped_path)
                verdict = stage_fabrication_check(
                    stripped_path,
                    os.path.join(out_dir, "stages",
                                 "fabcheck-{}-slivstrip".format(
                                     entry["attempt_id"])))
                record({"stage": "sliver_strip",
                        "from_attempt": entry["attempt_id"],
                        "removed_sub_floor_segments": removed,
                        "fabrication_geometry": verdict,
                        "output_sha256": _sha256_file(
                            stripped_path),
                        "output_path": os.path.relpath(
                            stripped_path, out_dir)})
                if verdict["ok"] is True:
                    entry = dict(entry,
                                 status="completed",
                                 output_path=os.path.relpath(
                                     stripped_path, out_dir))
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
            # cleanup stages chasing copper that already connects.
            # Cleanup runs PER STAGE GROUP, with that group's own
            # router parameters - a power net is never rerouted
            # with clock-stage via geometry.
            refilled_sha = publish(checkpoint)
            for group_name, group_nets in _routing_stages():
                missed = [net for net, cls in connectivity_by_net(
                    final_path, group_nets).items()
                    if cls != "connectivity-complete"]
                if not missed:
                    continue
                entry = run_attempt(
                    "route_cleanup_{}".format(group_name),
                    final_path,
                    _stage_router_args(group_name) + ["--nets"]
                    + missed,
                    _stage_timeout(group_name,
                                   arguments.stage_timeout),
                    "route.py",
                    extra={"reattempted_nets": missed})
                if entry["status"] == "fabrication-invalid" and \
                        set((entry.get("fabrication_geometry")
                             or {}).get("violations_by_type")
                            or {}) <= {"track_width",
                                       "connection_width"}:
                    stripped_path = os.path.join(
                        out_dir, "stages",
                        "{}-slivstrip.kicad_pcb".format(
                            entry["attempt_id"]))
                    removed = strip_slivers(
                        os.path.join(out_dir,
                                     entry["output_path"]),
                        stripped_path)
                    _place_pro_sibling(stripped_path)
                    verdict = stage_fabrication_check(
                        stripped_path,
                        os.path.join(
                            out_dir, "stages",
                            "fabcheck-{}-slivstrip".format(
                                entry["attempt_id"])))
                    record({"stage": "sliver_strip",
                            "from_attempt": entry["attempt_id"],
                            "removed_sub_floor_segments": removed,
                            "fabrication_geometry": verdict,
                            "output_sha256": _sha256_file(
                                stripped_path),
                            "output_path": os.path.relpath(
                                stripped_path, out_dir)})
                    if verdict["ok"] is True:
                        entry = dict(entry, status="completed",
                                     output_path=os.path.relpath(
                                         stripped_path, out_dir))
                if entry["status"] == "completed":
                    refilled_sha = publish(os.path.join(
                        out_dir, entry["output_path"]))

            # ---- planner last-mile repair: still-partial nets get
            # verified local connections between their stranded pad
            # groups (plane stitches for the plane net), at the
            # declared values, judged by the same fabrication check.
            from pcbqa import geom as geom_module
            from pcbqa.connectivity import classify_net
            for repair_round in range(2):
                board = pcbnew.LoadBoard(final_path)
                lastmile_clearances = net_clearance_map(
                    board, os.path.join(
                        REPO, "microphone_array_v2.kicad_pro"))
                partial = {}
                for net in all_nets:
                    state = classify_net(
                        board, net, geom_module.pad_copper_polygon)
                    if state["class"] == "partial-copper":
                        partial[net] = state["pad_components"]
                if not partial:
                    break
                pad_lookup = {}
                for footprint in board.GetFootprints():
                    for pad in footprint.Pads():
                        pad_lookup["{}.{}".format(
                            footprint.GetReference(),
                            pad.GetNumber())] = (
                            pad.GetPosition().x / 1e6,
                            pad.GetPosition().y / 1e6)
                outcome = {}
                changed = False
                for net, groups in sorted(partial.items()):
                    groups = sorted(groups, key=len)
                    small, large = groups[0], groups[-1]
                    best = None
                    for a in small:
                        for b in large:
                            ax, ay = pad_lookup[a]
                            bx, by = pad_lookup[b]
                            distance = math.hypot(bx - ax, by - ay)
                            if best is None or distance < best[0]:
                                best = (distance, a, b)
                    try:
                        if net == intent["stitches"]["plane_net"]:
                            proposal = \
                                critical_topology.stitch_to_plane(
                                    board, net,
                                    pad_lookup[best[1]], net,
                                    intent["rules"]["stitch"],
                                    geom_module.pad_copper_polygon,
                                    net_clearances=(
                                        lastmile_clearances))
                        else:
                            proposal = \
                                critical_topology.local_connect(
                                    board, net,
                                    pad_lookup[best[1]],
                                    pad_lookup[best[2]],
                                    dict(intent["rules"]["escape"],
                                         search_radius_mm=7.0,
                                         track_width_mm=0.2),
                                    geom_module.pad_copper_polygon,
                                    net_clearances=(
                                        lastmile_clearances))
                        critical_topology.apply_proposal(board,
                                                         proposal)
                        outcome[net] = {"joined": [best[1],
                                                   best[2]],
                                        "distance_mm": round(
                                            best[0], 3)}
                        changed = True
                    except critical_topology.TopologyPlanError \
                            as error:
                        outcome[net] = {"refused": str(error)}
                if changed:
                    previous_final = final_path + ".pre-lastmile"
                    with open(final_path, "rb") as source:
                        held = source.read()
                    with open(previous_final, "wb") as target:
                        target.write(held)
                    pcbnew.SaveBoard(final_path, board)
                    refilled_sha = refill_zones(final_path)
                    _place_pro_sibling(final_path)
                verdict = stage_fabrication_check(
                    final_path,
                    os.path.join(out_dir, "stages",
                                 "fabcheck-lastmile-{}".format(
                                     repair_round)))
                if changed and verdict["ok"] is not True:
                    # A repair that violates the declared floors is
                    # REVERTED, exactly like a routing stage: the
                    # previous fabrication-clean final stands, and
                    # the attempt stays recorded.
                    with open(previous_final, "rb") as source:
                        held = source.read()
                    with open(final_path, "wb") as target:
                        target.write(held)
                    refilled_sha = _sha256_file(final_path)
                    record({"stage": "last_mile_repair",
                            "round": repair_round,
                            "outcome": outcome,
                            "fabrication_geometry": verdict,
                            "reverted": True,
                            "output_sha256": refilled_sha})
                    break
                record({"stage": "last_mile_repair",
                        "round": repair_round,
                        "outcome": outcome,
                        "fabrication_geometry": verdict,
                        "output_sha256": refilled_sha})
                if not changed:
                    break
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
