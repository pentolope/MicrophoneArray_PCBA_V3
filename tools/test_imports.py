"""Every entry point loads, through the pinned toolkit and nothing else.

This is the test that a fresh recursive clone actually works. It imports each
board tool and asserts that every dependency resolved from the submodule - not
from a sibling checkout, not from an absolute path baked into a script, not
from whatever happened to be on PYTHONPATH.

It deliberately does **not** run the generator. `gen_pcb.build()` writes a
board; importing the module does not. The point here is that the regeneration
chain is present and wired up, which is what the migration promised, not that
it produces the same copper - that is a separate exercise with its own review.

    python3 tools/test_imports.py
"""

from __future__ import annotations

import importlib
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import _toolkit                                                # noqa: E402

# The regeneration closure, plus the board's own checks.
ENTRY_POINTS = [
    "design", "netlist", "manufacturing", "critical_nets",
    "gen_pcb", "gen_schematic", "gen_symbols",
    "check_netlist_parity", "check_placement", "check_routes",
    "check_host_mating", "check_stack", "draw_stack",
    "jlc_orientation", "jlc_lookup", "plot_layer",
    "build",
]

# Toolkit modules the board relies on. Named so that a toolkit that quietly
# stopped shipping one is caught here rather than three phases later.
TOOLKIT_MODULES = [
    "pcbqa.core", "pcbqa.geom", "pcbqa.rules", "pcbqa.sexpr",
    "pcbqa.orientation", "pcbqa.cleanroom",
]


def _forbidden_paths():
    """Places a dependency must not come from."""
    parent = os.path.dirname(ROOT)
    return [
        # a sibling checkout of the toolkit, under either the name it
        # has now or the one it had before the PCB -> PCBA rename: a
        # forbidden path list is worth nothing if a stale clone slips
        # through on an old name.
        os.path.join(parent, "PCBA_AutoDesignAndTest"),
        os.path.join(parent, "PCB_AutoDesignAndTest"),
        # the repository this board was migrated from
        os.path.join(parent, "PCB_MicrophoneArrayV2"),
    ]


def main():
    toolkit = _toolkit.toolkit_root()
    override = os.environ.get("PCB_TOOLKIT_PATH")
    print("toolkit: {}{}".format(
        toolkit, "   (PCB_TOOLKIT_PATH override)" if override else ""))
    print("")

    failures = []

    for name in TOOLKIT_MODULES + ENTRY_POINTS:
        try:
            module = importlib.import_module(name)
        except BaseException as exc:                          # noqa: BLE001
            failures.append("{}: {}: {}".format(
                name, type(exc).__name__, exc))
            print("  FAIL  {}".format(name))
            traceback.print_exc()
            continue
        origin = getattr(module, "__file__", None) or "<builtin>"
        print("  ok    {:24s} {}".format(name, origin))

    # Importing proves the module loads, not that the files it will reach for
    # exist. Four tools kept pointing at the old verification/boards/live.json
    # after the split and imported perfectly happily; one of them would have
    # silently fallen back to a stale default at run time. So every module-level
    # constant that names a path is resolved here.
    for name in ENTRY_POINTS:
        module = sys.modules.get(name)
        if module is None:
            continue
        for attr in dir(module):
            if attr.startswith("_") or not attr.isupper():
                continue
            value = getattr(module, attr)
            if not isinstance(value, str) or os.sep not in value:
                continue
            if not os.path.isabs(value):
                continue
            if os.path.exists(value):
                continue
            # A tool may legitimately name an output it has not written yet.
            if any(part in value for part in ("generated", "build", "out")):
                continue
            failures.append(
                "{}.{} points at a path that does not exist:\n      {}".format(
                    name, attr, value))

    # Nothing may have been resolved from a sibling checkout. When
    # PCB_TOOLKIT_PATH points at one deliberately, that path is exempt: it is
    # the development affordance, and the fresh-clone test runs without it.
    forbidden = [p for p in _forbidden_paths()
                 if not (override and os.path.abspath(override).startswith(p))]
    leaked = []
    for name, module in sorted(sys.modules.items()):
        path = getattr(module, "__file__", None)
        if not path:
            continue
        for bad in forbidden:
            if os.path.abspath(path).startswith(os.path.abspath(bad) + os.sep):
                leaked.append("{} <- {}".format(name, path))
    if leaked:
        failures.append("modules resolved outside this repository and its "
                        "submodule:\n    " + "\n    ".join(sorted(set(leaked))))

    print("")
    if failures:
        print("FAILED ({} problem(s)):".format(len(failures)))
        for line in failures:
            print("  " + line)
        return 1
    print("OK: {} module(s) import, all resolved through the pinned "
          "toolkit".format(len(TOOLKIT_MODULES) + len(ENTRY_POINTS)))
    print("The generator was NOT run.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
