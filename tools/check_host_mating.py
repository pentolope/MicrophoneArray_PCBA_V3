"""Prove the host socket mates with a Raspberry Pi P1 header, pin for pin.

The array plugs straight down onto the Pi: no cable, so nothing can re-map a
pin on the way. Two things have to be true, and arguing them from footprint
library conventions is how boards get built inside out. This measures them.

**Handedness.** Lower the array onto the Pi and look down at both. Array pad n
has to land on Pi pin n, so in that one view the two pin fields must differ by
a rigid motion - a translation and a rotation - and by nothing else. If they
differ by a reflection the connector is the wrong half: pin 1 still meets pin
1, and every other pin meets its mirror image. The test fits the best rigid
transform between the two labelled point sets and refuses a determinant of -1.

**Pinout.** Every pad still carries the net the Pi's own pinout gives that pin.
A mirrored connector can be made to "work" by moving nets to different pins,
which produces a board that mates mechanically and is wired to the wrong GPIOs;
this compares the board's nets against design.PI_HEADER pin by pin.

    "C:/Program Files/KiCad/10.0/bin/python.exe" tools/check_host_mating.py [BOARD]
"""

from __future__ import annotations

import math
import os
import sys

import pcbnew

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _toolkit                                      # noqa: E402
import design as d                                   # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Where KiCad keeps its stock footprints is a property of the installation, not
# of this board, so it is named once in board/toolchain.json rather than baked
# in here. A fresh clone on a machine with KiCad elsewhere only edits that file.
REFERENCE_LIB = os.path.join(
    _toolkit.toolchain()["kicad"]["stock_footprint_libraries"],
    "Connector_PinHeader_2.54mm.pretty")
REFERENCE_FP = "PinHeader_2x13_P2.54mm_Vertical"
FIT_TOLERANCE_MM = 0.01


def board_pins(board):
    """J1's pads in the top view, keyed by pin number, in millimetres."""
    footprint = board.FindFootprintByReference("J1")
    if footprint is None:
        raise SystemExit("J1 is not on this board")
    pins = {}
    for pad in footprint.Pads():
        position = pad.GetPosition()
        pins[pad.GetNumber()] = (position.x / 1e6, position.y / 1e6,
                                 pad.GetNetname())
    return footprint, pins


def reference_pins():
    """The Pi's P1 as its own board carries it: a header, component side up."""
    footprint = pcbnew.FootprintLoad(REFERENCE_LIB, REFERENCE_FP)
    if footprint is None:
        raise SystemExit("reference header footprint not found: " + REFERENCE_FP)
    return {pad.GetNumber(): (pad.GetPosition().x / 1e6,
                              pad.GetPosition().y / 1e6)
            for pad in footprint.Pads()}


def best_rigid_fit(source, target):
    """Kabsch fit: the rotation and translation taking source onto target.

    Returns (determinant, worst residual mm, rotation degrees). A determinant
    of -1 means the only way to superimpose the two is to turn one over, which
    for two connectors that have to mate face to face is the whole question.
    """
    keys = sorted(set(source) & set(target), key=int)
    if len(keys) < 3:
        raise SystemExit("not enough common pins to fit")
    sx = sum(source[k][0] for k in keys) / len(keys)
    sy = sum(source[k][1] for k in keys) / len(keys)
    tx = sum(target[k][0] for k in keys) / len(keys)
    ty = sum(target[k][1] for k in keys) / len(keys)

    # 2x2 covariance of the centred clouds.
    sxx = sxy = syx = syy = 0.0
    for k in keys:
        ax, ay = source[k][0] - sx, source[k][1] - sy
        bx, by = target[k][0] - tx, target[k][1] - ty
        sxx += ax * bx
        sxy += ax * by
        syx += ay * bx
        syy += ay * by
    # For a 2x2 problem the optimal rotation is available in closed form.
    angle = math.atan2(sxy - syx, sxx + syy)
    cos_a, sin_a = math.cos(angle), math.sin(angle)

    worst = 0.0
    for k in keys:
        ax, ay = source[k][0] - sx, source[k][1] - sy
        rx = ax * cos_a - ay * sin_a
        ry = ax * sin_a + ay * cos_a
        bx, by = target[k][0] - tx, target[k][1] - ty
        worst = max(worst, math.hypot(rx - bx, ry - by))

    # Whether a reflection is needed: compare the fit above with the fit after
    # flipping the source in x. The better one wins, and its sign is reported.
    flipped = {k: (-source[k][0], source[k][1]) for k in keys}
    fsx = -sx
    fangle = 0.0
    fxx = fxy = fyx = fyy = 0.0
    for k in keys:
        ax, ay = flipped[k][0] - fsx, flipped[k][1] - sy
        bx, by = target[k][0] - tx, target[k][1] - ty
        fxx += ax * bx
        fxy += ax * by
        fyx += ay * bx
        fyy += ay * by
    fangle = math.atan2(fxy - fyx, fxx + fyy)
    fcos, fsin = math.cos(fangle), math.sin(fangle)
    fworst = 0.0
    for k in keys:
        ax, ay = flipped[k][0] - fsx, flipped[k][1] - sy
        rx = ax * fcos - ay * fsin
        ry = ax * fsin + ay * fcos
        bx, by = target[k][0] - tx, target[k][1] - ty
        fworst = max(fworst, math.hypot(rx - bx, ry - by))

    determinant = 1.0 if worst <= fworst else -1.0
    return determinant, min(worst, fworst), math.degrees(
        angle if worst <= fworst else fangle) % 360.0


def check(board_path):
    board = pcbnew.LoadBoard(board_path)
    footprint, pins = board_pins(board)
    reference = reference_pins()
    results = []

    side = "back" if footprint.IsFlipped() else "front"
    results.append(("mounted on the board's underside", side == "back", side))

    library = str(footprint.GetFPID().GetUniStringLibId())
    female = "PinSocket" in library or "socket" in library.lower()
    results.append(("footprint is the female mating half", female, library))

    determinant, residual, rotation = best_rigid_fit(
        {k: (v[0], v[1]) for k, v in pins.items()}, reference)
    results.append((
        "pin field is a rigid match for a P1 header, not a mirror image",
        determinant > 0 and residual <= FIT_TOLERANCE_MM,
        "determinant {:+.0f}, worst residual {:.4f} mm, rotated {:.1f} deg"
        .format(determinant, residual, rotation)))

    wrong = []
    for pin, expected in d.PI_HEADER.items():
        number = str(pin)
        actual = pins.get(number, (0, 0, "<absent>"))[2]
        if actual != expected:
            wrong.append("{}: board {} vs Pi pinout {}".format(
                number, actual, expected))
    results.append(("every pin carries the net the Pi's pinout gives it",
                    not wrong, wrong or "26 of 26 agree"))

    pin1 = pins.get("1")
    results.append(("pin 1 is on the board", pin1 is not None,
                    "at {:.3f}, {:.3f} mm".format(
                        pin1[0] - d.PAGE_CX, -(pin1[1] - d.PAGE_CY))
                    if pin1 else "missing"))
    return results


def main(argv):
    board = argv[1] if len(argv) > 1 else os.path.join(
        HERE, "microphone_array_v2.kicad_pcb")
    results = check(board)
    for label, ok, detail in results:
        print("  [{}] {:<58} {}".format("PASS" if ok else "FAIL", label, detail))
    return 0 if all(ok for _l, ok, _d in results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
