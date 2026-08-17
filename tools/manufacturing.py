"""Manufacturing rules the generator places against, and the keep-outs it emits.

One module so that the board is generated against the same geometry the
validator later measures. When the generator and the checker disagree about
what "0.40 mm from a mask opening" means, the board passes generation and fails
verification, and somebody starts nudging vias to close the gap. That is the
failure this exists to prevent.

Everything here is derived from the board and the project files - net-class
clearances, via sizes, the mask target - so there are no numbers to keep in
step by hand.
"""

from __future__ import annotations

import fnmatch
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _toolkit                                          # noqa: E402,F401

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

import pcbnew                                            # noqa: E402
from shapely.geometry import LineString, MultiPolygon    # noqa: E402
from shapely.geometry import Point, Polygon              # noqa: E402
from shapely.ops import unary_union                      # noqa: E402
from shapely.strtree import STRtree                      # noqa: E402

from pcbqa import geom                                   # noqa: E402
from pcbqa.core import Manifest                          # noqa: E402

IU = 1e6
PROJECT = os.path.join(HERE, "microphone_array_v2.kicad_pro")
MANIFEST = os.path.join(HERE, "board", "manifest.live.json")
KEEPOUT_NAME = "via_mask_keepout"
SIMPLIFY_MM = 0.01
SIDE_OF = {pcbnew.F_Cu: "front", pcbnew.B_Cu: "back"}


def to_mm(value):
    return value / IU


def load_rules(project_root=None):
    """Clearances, via sizes and the mask target, from the real project files.

    `project_root` is the project being built, which is not always the one this
    file sits in: a clean build works in its own directory. The net classes are
    read from that project's own .kicad_pro so a build directory can never be
    measured against the repository's rules by accident.
    """
    manifest = Manifest(MANIFEST)
    profile = manifest.geometry_profile()
    project = PROJECT if project_root is None else os.path.join(
        project_root, "microphone_array_v2.kicad_pro")
    with open(project, encoding="utf-8") as fh:
        net_settings = json.load(fh)["net_settings"]
    by_class = {c["name"]: c for c in net_settings["classes"]}
    patterns = net_settings.get("netclass_patterns") or []
    default = by_class.get("Default", {})

    def class_of(net):
        for rule in patterns:
            if fnmatch.fnmatch(net, rule.get("pattern", "")):
                return by_class.get(rule.get("netclass"), default)
        return default

    return {
        "manifest": manifest,
        "profile": profile,
        "class_of": class_of,
        "clearance_of": lambda net: class_of(net).get(
            "clearance", default.get("clearance", 0.2)),
        "min_clearance": min(c.get("clearance", 0.2)
                             for c in net_settings["classes"]),
        "mask_target": manifest.get("via_mask.design_target_mm"),
        "process_limit": manifest.get("via_mask.process.limit_mm"),
        "chord_error": profile.tolerance("polygon_chord_error_mm").value,
        "contact": profile.tolerance("contact_mm").value,
    }


class ClearanceModel:
    """Where a via or a track may legally go, on this board, right now.

    Rebuild it after adding copper: it is a snapshot, not a live view.
    """

    def __init__(self, board, rules, ignore_positions=()):
        self.rules = rules
        self.clearance_of = rules["clearance_of"]
        self.mask_target = rules["mask_target"]
        settings = board.GetDesignSettings()
        self.hole_to_hole = settings.m_HoleToHoleMin / IU
        self.hole_clearance = settings.m_HoleClearance / IU
        self.widest = max(
            self.clearance_of(net) for net in ("GND", "+5V", "PDM_D0"))

        geom.configure(rules["chord_error"])
        survey = geom.BoardGeometry(board, contact_tolerance_mm=rules["contact"])
        self.survey = survey
        self.apertures = [shape for side in ("front", "back")
                          for _entry, shape in survey.mask_openings(side)]
        self.aperture_tree = STRtree(self.apertures) if self.apertures else None

        skip = {(round(x, 4), round(y, 4)) for x, y in ignore_positions}
        # Each shape carries the side it lives on, or None for copper that is
        # on every layer at once: through-hole pads and vias. A track only has
        # to clear what shares its side. Measuring against all of it instead
        # said a B.Cu data spoke sat on top of U3's F.Cu ground pad and left
        # that pad with nowhere to stitch.
        copper, holes = [], []
        for entry in survey.pads:
            sides = entry["copper"]
            through = len(sides) > 1 or entry.get("drill_mm")
            for side, shape in sides.items():
                copper.append((entry["net"], None if through else side, shape))
            if entry.get("drill_mm"):
                pos = entry["pad_obj"].GetPosition()
                holes.append((to_mm(pos.x), to_mm(pos.y),
                              entry["drill_mm"] / 2.0))
        for track in board.Tracks():
            net = track.GetNetname()
            if isinstance(track, pcbnew.PCB_VIA):
                pos = track.GetPosition()
                if (round(to_mm(pos.x), 4), round(to_mm(pos.y), 4)) in skip:
                    continue
                radius = track.GetWidth(pcbnew.F_Cu) / IU / 2.0
                copper.append((net, None, Point(
                    to_mm(pos.x), to_mm(pos.y)).buffer(radius, quad_segs=16)))
                holes.append((to_mm(pos.x), to_mm(pos.y),
                              track.GetDrill() / IU / 2.0))
                continue
            start, end = track.GetStart(), track.GetEnd()
            copper.append((net, SIDE_OF.get(track.GetLayer()), LineString(
                [(to_mm(start.x), to_mm(start.y)),
                 (to_mm(end.x), to_mm(end.y))]
            ).buffer(track.GetWidth() / IU / 2.0, cap_style=1, quad_segs=8)))
        self.nets = [n for n, _side, _s in copper]
        self.sides = [side for _n, side, _s in copper]
        self.shapes = [s for _n, _side, s in copper]
        self.tree = STRtree(self.shapes) if self.shapes else None
        self.holes = holes
        # Copper added after the snapshot. Small enough to scan linearly, and
        # keeping it separate avoids rebuilding the index per placement.
        self.extra = []

    def add_via(self, x, y, diameter, drill, net):
        """Record a via just placed, so later placements see it."""
        self.extra.append(
            (net, None, Point(x, y).buffer(diameter / 2.0, quad_segs=16)))
        self.holes.append((x, y, drill / 2.0))

    def add_track(self, ax, ay, bx, by, width, net, side=None):
        self.extra.append((net, side, LineString([(ax, ay), (bx, by)]).buffer(
            width / 2.0, cap_style=1, quad_segs=8)))

    def _required(self, net, other):
        return max(self.clearance_of(net), self.clearance_of(other))

    def via_ok(self, x, y, diameter, drill, net):
        """A via here would clear the mask target and every other net."""
        annulus = Point(x, y).buffer(diameter / 2.0, quad_segs=20)
        hole = Point(x, y).buffer(drill / 2.0, quad_segs=12)
        probe = annulus.buffer(max(self.mask_target, self.widest) + 0.01)
        if self.aperture_tree is not None:
            for i in self.aperture_tree.query(probe):
                if annulus.distance(self.apertures[int(i)]) < self.mask_target:
                    return False
        # A through via meets every layer, so nothing is filtered out here.
        if self.tree is not None:
            for i in self.tree.query(probe):
                i = int(i)
                other = self.nets[i]
                if other == net:
                    continue
                if annulus.distance(self.shapes[i]) < self._required(net, other):
                    return False
                if hole.distance(self.shapes[i]) < self.hole_clearance:
                    return False
        for other, _side, shape in self.extra:
            if other == net:
                continue
            if annulus.distance(shape) < self._required(net, other):
                return False
            if hole.distance(shape) < self.hole_clearance:
                return False
        for hx, hy, radius in self.holes:
            gap = math.hypot(hx - x, hy - y)
            if gap < 1e-9:
                continue
            if gap - radius - drill / 2.0 < self.hole_to_hole:
                return False
        return True

    def track_ok(self, ax, ay, bx, by, width, net, side=None):
        """A track of this width, on this side, would clear every other net.

        `side` is "front", "back", or None for "check against everything",
        which is the conservative answer for a caller that does not know.
        """
        shape = LineString([(ax, ay), (bx, by)]).buffer(
            width / 2.0, cap_style=1, quad_segs=8)
        if self.tree is None:
            return True
        probe = shape.buffer(self.widest + 0.01)
        for i in self.tree.query(probe):
            i = int(i)
            other = self.nets[i]
            if other == net or not self._shares_side(side, self.sides[i]):
                continue
            if shape.distance(self.shapes[i]) < self._required(net, other):
                return False
        for other, other_side, existing in self.extra:
            if other == net or not self._shares_side(side, other_side):
                continue
            if shape.distance(existing) < self._required(net, other):
                return False
        return True

    @staticmethod
    def _shares_side(side, other):
        return side is None or other is None or side == other


# ---------------------------------------------------------------------------
# keep-outs the autorouter obeys
# ---------------------------------------------------------------------------

def keepout_polygons(board, rules):
    """Mask apertures grown so a via placed outside them meets the target.

    KiCad Routing Tools rejects a via whose centre lies inside a rule area or
    within (clearance + via_size/2) of its edge, so the polygon only supplies
    the remainder of the target. Buffering by the whole target as well would
    demand roughly twice the real requirement on an already dense board.
    """
    geom.configure(rules["chord_error"])
    survey = geom.BoardGeometry(board, contact_tolerance_mm=rules["contact"])
    halo = max(0.0, rules["mask_target"] - rules["min_clearance"])
    per_side = {}
    for side in ("front", "back"):
        shapes = [shape.buffer(halo, quad_segs=8)
                  for _entry, shape in survey.mask_openings(side)]
        if not shapes:
            continue
        merged = unary_union(shapes).simplify(SIMPLIFY_MM)
        if isinstance(merged, Polygon):
            merged = MultiPolygon([merged])
        per_side[side] = merged
    return per_side


def add_keepouts(board, rules):
    """Write the via keep-outs as KiCad rule areas. Returns how many."""
    for zone in list(board.Zones()):
        if zone.GetIsRuleArea() and zone.GetZoneName() == KEEPOUT_NAME:
            board.Remove(zone)
    layer_for = {"front": pcbnew.F_Cu, "back": pcbnew.B_Cu}
    added = 0
    for side, polygons in keepout_polygons(board, rules).items():
        layers = pcbnew.LSET()
        layers.addLayer(layer_for[side])
        for polygon in polygons.geoms:
            zone = pcbnew.ZONE(board)
            zone.SetIsRuleArea(True)
            zone.SetDoNotAllowTracks(False)     # pads still have to be reached
            zone.SetDoNotAllowVias(True)
            zone.SetDoNotAllowPads(False)
            zone.SetDoNotAllowFootprints(False)
            zone.SetDoNotAllowZoneFills(False)  # planes are on inner layers
            zone.SetLayerSet(layers)
            zone.SetZoneName(KEEPOUT_NAME)
            outline = zone.Outline()
            outline.NewOutline()
            for x, y in list(polygon.exterior.coords)[:-1]:
                outline.Append(pcbnew.FromMM(float(x)), pcbnew.FromMM(float(y)))
            board.Add(zone)
            added += 1
    return added


def silk_obstacles(board, rules, side="front"):
    """Everything a silkscreen legend has to keep away from.

    Two things, because KiCad checks two rules: exposed copper, which is what
    the solder-mask openings are, and other silkscreen - the footprint outlines
    and pin-1 marks the libraries bring with them.
    """
    geom.configure(rules["chord_error"])
    survey = geom.BoardGeometry(board, contact_tolerance_mm=rules["contact"])
    shapes = [shape for _entry, shape in survey.mask_openings(side)]

    silk = pcbnew.F_SilkS if side == "front" else pcbnew.B_SilkS
    for footprint in board.Footprints():
        items = list(footprint.GraphicalItems())
        items += [field for field in footprint.GetFields() if field.IsVisible()]
        for item in items:
            if item.GetLayer() != silk:
                continue
            if isinstance(item, pcbnew.PCB_SHAPE) and \
                    item.GetShape() == pcbnew.SHAPE_T_SEGMENT:
                start, end = item.GetStart(), item.GetEnd()
                shapes.append(LineString(
                    [(to_mm(start.x), to_mm(start.y)),
                     (to_mm(end.x), to_mm(end.y))]
                ).buffer(max(item.GetWidth(), 1) / IU / 2.0, cap_style=2))
                continue
            shapes.append(bounding_box(item))
    return shapes


def bounding_box(item):
    """An item's bounding box as a polygon, in millimetres."""
    box = item.GetBoundingBox()
    return Polygon([(to_mm(box.GetLeft()), to_mm(box.GetTop())),
                    (to_mm(box.GetRight()), to_mm(box.GetTop())),
                    (to_mm(box.GetRight()), to_mm(box.GetBottom())),
                    (to_mm(box.GetLeft()), to_mm(box.GetBottom()))])


def lock_generated_copper(board):
    """Lock every track and via the generator produced.

    The autorouter may add copper; it may not re-open decisions made here.
    """
    locked = 0
    for track in board.Tracks():
        track.SetLocked(True)
        locked += 1
    return locked
