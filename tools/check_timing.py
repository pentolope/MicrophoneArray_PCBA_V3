"""Report this board's PDM clock interconnect, path by path.

The validator's timing gates decide pass or fail. This prints what they
measured, which is the part a person actually reads: where each of the sixteen
clock paths starts, which series resistor it crosses, how much copper it is on
each layer, and how far apart the earliest and latest arrivals are.

It exists mainly to make one thing visible. `net_topology.rules`'
PDM_CLOCK_BRANCHES rule measures net `PDM_CLK_Bn`, which begins at `RCn.2` -
the output side of the branch resistor. Everything between the buffer pin and
`RCn.1` is on net `PDM_CLK_Yn` and is not in that measurement at all, and on
two of the eight branches it is not even on the same layer. Both figures are
printed side by side below so the difference is a number rather than an
argument.

Passive PCB interconnect only. Not clock arrival time, not clock skew: the
buffer's own output-to-output skew, both packages, the microphones' threshold
behaviour and PVT are all outside anything a board file can answer.

    "C:/Program Files/KiCad/10.0/bin/python.exe" tools/check_timing.py

Honours PCB_TOOLKIT_PATH, so the board can be measured against a local toolkit
checkout before its submodule pointer moves.
"""

from __future__ import annotations

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import _toolkit                                                    # noqa: E402

from pcbqa import core                                             # noqa: E402
from pcbqa.core import Context, Manifest, Status                   # noqa: E402
from pcbqa.gates import g_timing                                   # noqa: E402,F401

MANIFEST = os.path.join(ROOT, "board", "manifest.live.json")

GATES = ("TIMING.PATH_INTEGRITY", "STACK.PHYSICAL",
         "TIMING.INTERCONNECT_DELAY", "TIMING.INTERCONNECT_SKEW",
         "TIMING.SETUP_HOLD", "PROV.TIMING_MODELS")


def evaluate(manifest_path=MANIFEST):
    manifest = Manifest(manifest_path)
    workdir = tempfile.mkdtemp(prefix="pcbqa_timing_")
    context = Context(manifest, workdir)
    results = core.run_all(context, only=set(GATES))
    return {r.gate_id: r for r in results}


def _paths(results):
    interfaces = results["TIMING.PATH_INTEGRITY"].measurements.get(
        "interfaces", {})
    rows = []
    for name, interface in sorted(interfaces.items()):
        for row in interface["paths"]:
            rows.append({**row, "interface": name})
    return rows


def main(argv):
    print("toolkit: {}{}".format(
        _toolkit.toolkit_root(),
        "   (PCB_TOOLKIT_PATH override)"
        if os.environ.get("PCB_TOOLKIT_PATH") else ""))
    print("")
    results = evaluate(argv[1] if len(argv) > 1 else MANIFEST)

    for gate_id in GATES:
        result = results.get(gate_id)
        if result is None:
            print("  [MISSING] {}".format(gate_id))
            continue
        print("  [{:<14}] {}".format(result.status, gate_id))
        print("                   {}".format(result.reason))
    print("")

    rows = sorted(_paths(results), key=lambda r: r["copper_length_mm"])
    if not rows:
        print("no electrical paths were resolved")
        return 1
    print("  {:<20} {:<7} {:<8} {:<8} {:>9}  {:>5}  {}".format(
        "path", "from", "to", "crosses", "copper", "vias", "by layer (mm)"))
    for row in rows:
        layers = ", ".join("{} {:.3f}".format(layer, value) for layer, value
                           in sorted(row["length_by_layer_mm"].items()))
        print("  {:<20} {:<7} {:<8} {:<8} {:9.3f}  {:>5}  {}".format(
            row["path"], row["source"], row["destination"],
            ",".join(row["crosses"]), row["copper_length_mm"],
            row["via_transitions"], layers))

    lengths = [r["copper_length_mm"] for r in rows]
    print("")
    print("  complete paths (buffer pin -> microphone clock pad):")
    print("    shortest {:.3f} mm at {}".format(lengths[0],
                                                rows[0]["destination"]))
    print("    longest  {:.3f} mm at {}".format(lengths[-1],
                                                rows[-1]["destination"]))
    print("    spread   {:.3f} mm".format(lengths[-1] - lengths[0]))

    _post_series(rows)
    _stackup(results)
    _delay(results)

    blocking = [r for r in results.values() if r.status in Status.BLOCKING]
    print("")
    if blocking:
        for result in blocking:
            print("  BLOCKING {}: {}".format(result.gate_id, result.reason))
        return 1
    print("  No timing gate blocks. Passive interconnect only - this says "
          "nothing about")
    print("  buffer output skew, package delay, receiver thresholds or PVT.")
    return 0


def _post_series(rows):
    """The same endpoints measured the way a net-scoped rule measures them.

    Not recomputed - subtracted. Each path's post-resistor copper is the whole
    path minus its pre-resistor step, which is exactly the copper a rule scoped
    to the downstream net can see.
    """
    print("")
    print("  the same sixteen endpoints, counted only from the resistor's "
          "output net:")
    post = []
    for row in rows:
        pre = _pre_series_mm(row)
        if pre is None:
            continue
        post.append((row["copper_length_mm"] - pre, row["destination"], pre))
    if not post:
        return
    post.sort()
    print("    shortest {:.3f} mm at {}".format(post[0][0], post[0][1]))
    print("    longest  {:.3f} mm at {}".format(post[-1][0], post[-1][1]))
    print("    spread   {:.3f} mm".format(post[-1][0] - post[0][0]))
    invisible = [p[2] for p in post]
    print("    copper upstream of the resistors, invisible to a net-scoped "
          "measurement: {:.3f} to {:.3f} mm".format(min(invisible),
                                                    max(invisible)))


def _pre_series_mm(row):
    for step in row.get("steps", []) or []:
        if step.get("kind") == "copper":
            return step.get("length_mm")
    return None


def _stackup(results):
    stack = results["STACK.PHYSICAL"].measurements
    physical = stack.get("physical_stackup")
    if not physical:
        return
    print("")
    print("  physical stackup: {}".format(physical["source"]))
    print("    overall thickness {} mm, reference planes {}".format(
        physical["declared_total_thickness_mm"],
        ", ".join(stack.get("reference_plane_layers") or ["<none>"])))
    missing = stack.get("insufficient_fields") or []
    if not missing:
        print("    complete")
        return
    blocking = [m for m in missing if m.get("needed_for") == "delay"]
    print("    {} field(s) absent, {} of them needed for a delay:".format(
        len(missing), len(blocking)))
    for entry in blocking:
        print("      {}: {}".format(entry.get("layer", "?"), entry["issue"]))


def _delay(results):
    delay = results["TIMING.INTERCONNECT_DELAY"].measurements
    print("")
    print("  propagation: model {} via {} backend {}".format(
        delay.get("propagation_model"), delay.get("via_delay_model"),
        delay.get("backend")))
    print("    fidelity {}".format(", ".join(delay.get("fidelity") or ["-"])))
    unresolved = delay.get("paths_without_derivable_delay")
    total = len(delay.get("paths") or [])
    if unresolved:
        print("    {} of {} path(s) have no derivable delay: the physical "
              "stackup".format(unresolved, total))
        print("    does not state the figures the model needs, and none are "
              "assumed.")
    else:
        print("    all {} path(s) carry an estimated delay".format(total))


if __name__ == "__main__":
    sys.exit(main(sys.argv))
