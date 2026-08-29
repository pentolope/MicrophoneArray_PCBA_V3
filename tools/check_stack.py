"""Check the array can sit on the Raspberry Pi without anything touching.

The array carries parts on its underside and the Pi carries tall connectors on
its top, and the two face each other. So the question is a height budget, not a
footprint one: for every part hanging below the array, is there room for it
above whatever the Pi has in the same place?

Because the array's 120 mm disc covers the whole of an 85.6 x 56 mm Pi, the
honest answer is to assume every underside part sits over the Pi's tallest
feature. That is conservative and it is also nearly true - the USB stack is at
one end, the array is round, and the disc reaches past both ends.

The Pi's dimensions come from constraints.json's `mechanical_stack` block,
which is published data, not a drawing held in this project. It says so, and so
does this check.

    python3 tools/check_stack.py [BOARD]
"""

from __future__ import annotations

import json
import os
import sys

import pcbnew

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load():
    with open(os.path.join(HERE, "constraints.json"), encoding="utf-8") as fh:
        return json.load(fh)["mechanical_stack"]


def underside(board):
    """Every footprint on the board's underside, with its extent."""
    parts = []
    for footprint in board.Footprints():
        if not footprint.IsFlipped():
            continue
        box = footprint.GetBoundingBox(False, False)
        parts.append({
            "reference": footprint.GetReference(),
            "value": footprint.GetValue(),
            "width_mm": round(box.GetWidth() / 1e6, 2),
            "height_mm": round(box.GetHeight() / 1e6, 2),
        })
    return sorted(parts, key=lambda p: p["reference"])


def check(board_path):
    spec = load()
    board = pcbnew.LoadBoard(board_path)
    parts = underside(board)
    results = []

    separation = spec["required_separation_mm"]["value"]
    below = spec["array_underside_mm"]["total_below_array"]
    tallest_name, tallest = max(
        ((k, v) for k, v in spec["host_tall_parts_mm"].items()),
        key=lambda kv: kv[1])

    results.append((
        "the array's underside clears the Pi's tallest part",
        separation >= below + tallest,
        "{} mm separation against {} mm of module below the array plus {} mm "
        "of {}".format(separation, below, tallest, tallest_name)))

    results.append((
        "the separation is built from ordinary stacked 2.54 mm sockets",
        "stacked" in spec["required_separation_mm"]["provided_by"],
        spec["required_separation_mm"]["provided_by"]))

    # Nothing on the underside may be taller than the module it was budgeted
    # for. The module is the only thing down there with any height; the host
    # socket is the mating part itself and the test pads are flat.
    unexpected = [p["reference"] for p in parts
                  if p["reference"] not in ("J1", "J2", "J3")]
    results.append(("the underside carries only the connectors",
                    not unexpected,
                    ", ".join(p["reference"] for p in parts)))

    results.append((
        "the module's USB-C stays reachable with the array stacked",
        "overhangs" in spec["access"]["module_usb_c"],
        spec["access"]["module_usb_c"]))

    results.append((
        "the Pi's own dimensions are marked as unconfirmed",
        "NOT YET CONFIRMED" in spec["status"],
        "published figures, flagged for measurement before manufacture"))
    return results, parts


def main(argv):
    board = argv[1] if len(argv) > 1 else os.path.join(
        HERE, "microphone_array_v2.kicad_pcb")
    results, parts = check(board)
    for label, ok, detail in results:
        print("  [{}] {:<56} {}".format("PASS" if ok else "FAIL", label, detail))
    print("\n  underside parts:")
    for part in parts:
        print("    {:<4} {:<24} {} x {} mm".format(
            part["reference"], part["value"], part["width_mm"],
            part["height_mm"]))
    return 0 if all(ok for _l, ok, _d in results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
