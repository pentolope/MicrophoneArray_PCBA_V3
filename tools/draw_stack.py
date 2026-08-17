"""Draw the array over the Raspberry Pi, to scale, as a stacking check.

kicad-cli renders one board at a time, so the thing that actually needs looking
at - where the Pi sits under the disc and what is in the way - has no render.
This draws it: the array outline and its underside parts from the board itself,
the Pi outline and its tall connectors from constraints.json, both placed by the
one thing that fixes them together, which is P1 mating with J1.

    "C:/Program Files/KiCad/10.0/bin/python.exe" tools/draw_stack.py [OUT.svg]
"""

from __future__ import annotations

import json
import math
import os
import sys

import pcbnew

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "tools"))

import design as d                                   # noqa: E402

SCALE = 4.0          # pixels per millimetre
MARGIN = 30.0


def pi_outline(spec, j1_pin1, j1_rotation_deg):
    """The Pi's outline and tall parts, put where J1 says P1 must be.

    P1's pin 1 is at a known place on the Pi, so mating fixes the Pi's frame
    against the array's: rotate the Pi by however J1 is rotated, then slide it
    until its P1 pin 1 lands on J1's pin 1.
    """
    width, height = spec["host_board"]["outline_mm"]
    px, py = spec["host_board"]["p1_pin1_from_board_corner_mm"]
    angle = math.radians(j1_rotation_deg)
    cos_a, sin_a = math.cos(angle), math.sin(angle)

    def place(x, y):
        ox, oy = x - px, y - py            # relative to P1 pin 1
        return (j1_pin1[0] + ox * cos_a - oy * sin_a,
                j1_pin1[1] + ox * sin_a + oy * cos_a)

    corners = [place(0, 0), place(width, 0), place(width, height),
               place(0, height)]
    return corners, place


def board_facts(board_path):
    board = pcbnew.LoadBoard(board_path)
    j1 = board.FindFootprintByReference("J1")
    pin1 = next(p for p in j1.Pads() if p.GetNumber() == "1").GetPosition()
    radius = 0.0
    for shape in board.GetDrawings():
        if shape.GetLayer() == pcbnew.Edge_Cuts:
            radius = max(radius, shape.GetRadius() / 1e6)
    parts = []
    for footprint in board.Footprints():
        if not footprint.IsFlipped():
            continue
        box = footprint.GetBoundingBox(False, False)
        parts.append((footprint.GetReference(),
                      box.GetLeft() / 1e6 - d.PAGE_CX,
                      -(box.GetBottom() / 1e6 - d.PAGE_CY),
                      box.GetWidth() / 1e6, box.GetHeight() / 1e6))
    return {
        "radius": radius or d.BOARD_RADIUS,
        "pin1": (pin1.x / 1e6 - d.PAGE_CX, -(pin1.y / 1e6 - d.PAGE_CY)),
        "rotation": j1.GetOrientationDegrees(),
        "underside": parts,
    }


def draw(board_path, out_path):
    with open(os.path.join(HERE, "constraints.json"), encoding="utf-8") as fh:
        spec = json.load(fh)["mechanical_stack"]
    facts = board_facts(board_path)
    corners, _place = pi_outline(spec, facts["pin1"], facts["rotation"])

    extent = facts["radius"] + MARGIN
    size = int(2 * extent * SCALE)

    def sx(x):
        return (x + extent) * SCALE

    def sy(y):
        return (extent - y) * SCALE

    parts = ["<svg xmlns='http://www.w3.org/2000/svg' width='{0}' height='{0}' "
             "viewBox='0 0 {0} {0}'>".format(size),
             "<rect width='100%' height='100%' fill='#faf9f6'/>"]
    parts.append("<circle cx='{:.1f}' cy='{:.1f}' r='{:.1f}' fill='#1b5e2015' "
                 "stroke='#1b5e20' stroke-width='2'/>".format(
                     sx(0), sy(0), facts["radius"] * SCALE))
    points = " ".join("{:.1f},{:.1f}".format(sx(x), sy(y)) for x, y in corners)
    parts.append("<polygon points='{}' fill='#c6282815' stroke='#c62828' "
                 "stroke-width='2' stroke-dasharray='8 4'/>".format(points))
    for ref, x, y, w, h in facts["underside"]:
        parts.append("<rect x='{:.1f}' y='{:.1f}' width='{:.1f}' "
                     "height='{:.1f}' fill='#1565c025' stroke='#1565c0' "
                     "stroke-width='1.5'/>".format(
                         sx(x), sy(y + h), w * SCALE, h * SCALE))
        parts.append("<text x='{:.1f}' y='{:.1f}' font-family='sans-serif' "
                     "font-size='13' fill='#0d47a1'>{}</text>".format(
                         sx(x) + 3, sy(y + h) - 4, ref))
    parts.append("<circle cx='{:.1f}' cy='{:.1f}' r='6' fill='#c62828'/>".format(
        sx(facts["pin1"][0]), sy(facts["pin1"][1])))
    parts.append("<text x='{:.1f}' y='{:.1f}' font-family='sans-serif' "
                 "font-size='14' fill='#c62828'>J1.1 = P1.1</text>".format(
                     sx(facts["pin1"][0]) + 10, sy(facts["pin1"][1]) + 5))

    lines = [
        "array: {:.0f} mm disc, seen from above".format(2 * facts["radius"]),
        "Pi: {} x {} mm, placed by P1 mating with J1".format(
            *spec["host_board"]["outline_mm"]),
        "separation {} mm = {} mm below the array + {} mm of Pi connector".format(
            spec["required_separation_mm"]["value"],
            spec["array_underside_mm"]["total_below_array"],
            max(spec["host_tall_parts_mm"].values())),
        "Pi dimensions are published figures, not yet measured",
    ]
    for index, line in enumerate(lines):
        parts.append("<text x='14' y='{}' font-family='sans-serif' "
                     "font-size='15' fill='#333'>{}</text>".format(
                         22 + index * 20, line))
    parts.append("</svg>")

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(parts))
    return out_path, facts


def main(argv):
    board = os.path.join(HERE, "microphone_array_v2.kicad_pcb")
    out = argv[1] if len(argv) > 1 else os.path.join(
        HERE, "generated", "release", "renders", "stack_plan.svg")
    path, facts = draw(board, out)
    print("wrote " + path)
    print("  J1 pin 1 at {:.2f}, {:.2f} mm, rotated {:.0f} deg".format(
        facts["pin1"][0], facts["pin1"][1], facts["rotation"]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
