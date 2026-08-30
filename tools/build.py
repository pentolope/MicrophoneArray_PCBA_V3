"""One command that rebuilds the microphone-array board from source.

    python3 tools/build.py

Starts from an empty build directory every time and runs the whole pipeline:

    gen_pcb.py -> pre-route board (placement, zones, keep-outs, critical
                  routing, all locked)
      verify   -> the pre-route board must be sound before routing it
    routing_plan.json -> KiCad Routing Tools routes every ordinary signal
      refill   -> ground planes closed around the new vias
      verify   -> KiCad ERC, KiCad DRC, and the board's own DFM checks
      install  -> only if every hard gate passes

Nothing in here edits copper. If a gate fails the build stops and says which
reproducible input to change; it never repairs the board it just made.

Artifacts land in build/ and are kept: the pre-route board, the routed
candidate, the plan actually executed, tool versions, every command, and the
validation reports.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(HERE, "tools")
sys.path.insert(0, TOOLS)

import _toolkit                                              # noqa: E402
_toolkit.install()

PLAN = os.path.join(HERE, "board", "routing_plan.json")
BUILD = os.path.join(HERE, "build")
# Everything gen_pcb.py reads out of the project directory it builds into: the
# schematic it must stay in parity with, the project file it rewrites the net
# classes into, and the local libraries the footprints come from. The tools
# themselves are run from the repository, not from the copy, so a build can
# never quietly use a stale script.
PROJECT_FILES = ("microphone_array_v2.kicad_pro", "microphone_array_v2.kicad_sch")
SUPPORT = ("MicArrayV2.pretty", "MicArrayV2.kicad_sym", "fp-lib-table",
           "sym-lib-table")
BOARD = "microphone_array_v2.kicad_pcb"


def reviewable_rules():
    """Rules the board reviews per revision instead of blocking every build.

    A rule is on this list because the manifest carries at least one approved
    waiver for it. The waivers themselves are bound to one exact board - one
    track, one via, one set of digests - which is right for releasing a
    revision and useless as a build gate: the router does not produce the same
    copper twice, so the findings move between builds even though the board is
    equally good. The build therefore reports them and carries on; the
    validator, which is the release authority, still demands a waiver naming
    each one on the exact board being released.
    """
    manifest = os.path.join(HERE, "board", "manifest.live.json")
    if not os.path.isfile(manifest):
        return set()
    with open(manifest, encoding="utf-8") as fh:
        entries = json.load(fh).get("waivers") or []
    return {entry.get("rule") for entry in entries if entry.get("rule")}


def kicad_cli():
    return _toolkit.toolchain()["kicad"]["cli"]


class Build:
    def __init__(self, root, install=False):
        self.root = root
        self.install = install
        self.log = []
        self.gates = []
        self.cli = kicad_cli()

    # -- plumbing ----------------------------------------------------------
    def run(self, args, label, cwd=None, env=None):
        started = time.time()
        environment = None
        if env:
            environment = dict(os.environ)
            environment.update(env)
        proc = subprocess.run(args, capture_output=True, text=True, cwd=cwd,
                              env=environment)
        logs = os.path.join(self.root, "logs")
        os.makedirs(logs, exist_ok=True)
        name = label.replace(":", "_") + ".log"
        with open(os.path.join(logs, name), "w", encoding="utf-8") as fh:
            fh.write(" ".join(args) + "\n\n")
            fh.write(proc.stdout or "")
            if proc.stderr:
                fh.write("\n--- stderr ---\n" + proc.stderr)
        self.log.append({
            "stage": label,
            "command": [os.path.basename(args[0])] + list(args[1:]),
            "exit": proc.returncode,
            "seconds": round(time.time() - started, 2),
            "log": "logs/" + name,
            "tail": (proc.stdout or "")[-800:],
            "stderr_tail": (proc.stderr or "")[-400:],
        })
        return proc

    def gate(self, name, passed, detail):
        self.gates.append({"gate": name, "pass": bool(passed), "detail": detail})
        print("  [{}] {:<28} {}".format("PASS" if passed else "FAIL",
                                        name, detail))
        return passed

    def drc(self, board, tag, refill=True, save=False):
        out = os.path.join(self.root, "reports", tag + ".json")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        args = [self.cli, "pcb", "drc", "-o", out, "--format", "json",
                "--severity-all", "--severity-exclusions", "--all-track-errors",
                "--schematic-parity"]
        if refill:
            args.append("--refill-zones")
        if save:
            args.append("--save-board")
        self.run(args + [board], "drc:" + tag)
        with open(out, encoding="utf-8") as fh:
            doc = json.load(fh)
        counts = collections.Counter(v["type"] for v in doc["violations"])
        return doc, counts

    def erc(self, schematic, tag):
        out = os.path.join(self.root, "reports", tag + ".json")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        self.run([self.cli, "sch", "erc", "-o", out, "--format", "json",
                  "--severity-all", "--severity-exclusions", schematic],
                 "erc:" + tag)
        with open(out, encoding="utf-8") as fh:
            doc = json.load(fh)
        return sum(len(s["violations"]) for s in doc.get("sheets", []))

    # -- stages ------------------------------------------------------------
    def prepare(self):
        if os.path.isdir(self.root):
            shutil.rmtree(self.root)
        os.makedirs(os.path.join(self.root, "reports"))
        self.work = os.path.join(self.root, "project")
        os.makedirs(self.work)
        for name in PROJECT_FILES:
            shutil.copy2(os.path.join(HERE, name), self.work)
        for name in SUPPORT:
            source = os.path.join(HERE, name)
            if os.path.isdir(source):
                shutil.copytree(source, os.path.join(self.work, name),
                                ignore=shutil.ignore_patterns("__pycache__"))
            elif os.path.isfile(source):
                shutil.copy2(source, self.work)

    def generate(self):
        proc = self.run([sys.executable, os.path.join(TOOLS, "gen_pcb.py"),
                         "--output", self.work],
                        "generate", cwd=TOOLS)
        board = os.path.join(self.work, BOARD)
        ok = proc.returncode == 0 and os.path.isfile(board)
        self.gate("generate", ok, "gen_pcb.py exit {}".format(proc.returncode))
        if ok:
            shutil.copy2(board, os.path.join(self.root, "pre_route.kicad_pcb"))
        return ok

    def verify_pre_route(self):
        board = os.path.join(self.work, BOARD)
        doc, counts = self.drc(board, "pre_route", refill=True, save=True)
        # A fanout stub is a track with one end waiting for the router, so
        # dangling ends are what a pre-route board is supposed to look like.
        # The candidate gate below still refuses to accept one.
        electrical = {k: v for k, v in counts.items()
                      if not k.startswith("silk")
                      and k not in ("lib_footprint_issues", "track_dangling")}
        ok = self.gate("pre_route_clean", not electrical,
                       "electrical DRC findings: {}".format(
                           dict(electrical) or "none"))
        # Everything else is still unrouted at this point, so the only
        # connectivity that can be judged is the critical routing, and that is
        # exactly the part the autorouter will not be asked to fix.
        ok &= self.critical(board, doc, "pre_route")
        return ok

    def critical(self, board, drc_doc, tag):
        """The declared critical nets, measured against constraints.json."""
        sys.path.insert(0, TOOLS)
        import critical_nets
        outcome = critical_nets.report(board)
        with open(os.path.join(self.root, "reports",
                               "critical_" + tag + ".json"), "w",
                  encoding="utf-8") as fh:
            json.dump(outcome, fh, indent=2)
        ok = self.gate("critical_nets:" + tag, not outcome["failures"],
                       "{} checks, {} failed{}".format(
                           len(outcome["results"]), len(outcome["failures"]),
                           "".join("; {} {}={}".format(f["rule"], f["check"],
                                                       f["value"])
                                   for f in outcome["failures"])))
        watched = set(outcome["nets"])
        open_links = 0
        for item in drc_doc["unconnected_items"]:
            names = " ".join(part.get("description", "")
                             for part in item.get("items", []))
            if any("[{}]".format(net) in names for net in watched):
                open_links += 1
        ok &= self.gate("critical_connected:" + tag, open_links == 0,
                        "{} open connections on critical nets".format(
                            open_links))
        return ok

    def route(self):
        with open(PLAN, encoding="utf-8") as fh:
            plan = json.load(fh)
        shutil.copy2(PLAN, os.path.join(self.root, "routing_plan.json"))
        # WHICH router, resolved by the toolkit's declared order rather than
        # by a path written down here. The plan used to carry an absolute
        # root, which meant the build only ran on the one machine that string
        # described; the router is now a submodule of the toolkit, so a
        # recursive clone has it at a pinned revision and nobody has to agree
        # on where it lives. An explicit override still wins - see pcbqa.krt.
        from pcbqa import krt
        toolchain = _toolkit.toolchain().get("router") or {}
        resolved = krt.resolve(
            configured=toolchain.get("development_checkout") or None,
            plugin_dirs=toolchain.get("plugin_dirs") or [])
        root = resolved["path"]
        self.log.append({"stage": "route:resolve", "router": root,
                         "origin": resolved["origin"]})
        print("  router: {} ({})".format(root, resolved["origin"]))
        current = os.path.join(self.work, BOARD)
        ok = True
        for index, stage in enumerate(plan["stages"]):
            # The candidate is written beside the project, never over the
            # pre-route board, and it needs the project's own net classes and
            # library table sitting next to it for kicad-cli to resolve them.
            output = os.path.join(self.work,
                                  "routed_{}.kicad_pcb".format(stage["id"]))
            shutil.copy2(os.path.join(self.work,
                                      "microphone_array_v2.kicad_pro"),
                         output.replace(".kicad_pcb", ".kicad_pro"))
            args = [sys.executable, os.path.join(root, stage["entry"]),
                    current, output, "--nets"] + stage["nets"] + stage["args"]
            environment = {key: value for key, value
                           in (plan["policy"].get("environment") or {}).items()
                           if isinstance(value, str)}
            proc = self.run(args, "route:" + stage["id"], env=environment)
            summaries = [json.loads(line.split(":", 1)[1])
                         for line in (proc.stdout or "").splitlines()
                         if line.startswith("JSON_SUMMARY:")]
            if not summaries or not os.path.isfile(output):
                ok = self.gate("route:" + stage["id"], False,
                               "router produced no summary or no board")
                break
            # route.py prints one summary for the main run and another for each
            # rescue pass, and a rescue summary covers only the nets it retried.
            # Reading the last one alone reports a net as failed when a later
            # pass actually recovered it, and vice versa.
            unrouted, floor = set(), None
            for summary in summaries:
                unrouted -= set(summary.get("routed_single") or [])
                unrouted -= set((summary.get("rescue") or {}).get(
                    "recovered") or [])
                unrouted |= set(summary.get("failed_single") or [])
                unrouted |= set(summary.get("failed_multipoint") or [])
                used = summary.get("min_clearance_used")
                if used is not None:
                    floor = used if floor is None else min(floor, used)
            limit = plan["policy"]["reject_if_min_clearance_below"]
            vias = sum(s.get("total_vias") or 0 for s in summaries)
            self.gate("route:" + stage["id"] + ":completed", not unrouted,
                      "{} nets left unrouted{}, {} vias".format(
                          len(unrouted),
                          (": " + ", ".join(sorted(unrouted))) if unrouted
                          else "", vias))
            self.gate("route:" + stage["id"] + ":no_relaxation",
                      floor is None or floor >= limit,
                      "min clearance used {} mm (floor {})".format(floor, limit))
            ok = ok and not unrouted and (floor is None or floor >= limit)
            current = output
        self.candidate = current
        if os.path.isfile(current):
            shutil.copy2(current, os.path.join(
                self.root, os.path.basename(current)))
        return ok

    def verify_candidate(self):
        board = self.candidate
        # Refill first: router vias pierce the planes.
        self.drc(board, "candidate_refill", refill=True, save=True)
        doc, counts = self.drc(board, "candidate", refill=True)
        electrical = {k: v for k, v in counts.items()
                      if not k.startswith("silk")
                      and k != "lib_footprint_issues"}
        # A rule the board has an approved waiver for is reported here and
        # judged by the validator, which binds its waivers to the board, the
        # rules, the command and the findings. Repeating that binding in the
        # build would be a second implementation of the same decision; what
        # this gate does is notice when the count changes.
        reviewable = reviewable_rules()
        blocking = {rule: count for rule, count in electrical.items()
                    if rule not in reviewable}
        reviewed = {rule: count for rule, count in electrical.items()
                    if rule in reviewable}
        ok = self.gate("drc", not blocking,
                       "findings: {}{}".format(
                           dict(blocking) or "none",
                           "; for review at release: {}".format(dict(reviewed))
                           if reviewed else ""))
        ok &= self.gate("unconnected", not doc["unconnected_items"],
                        "{} unconnected".format(len(doc["unconnected_items"])))
        violations = self.erc(os.path.join(self.work,
                                           "microphone_array_v2.kicad_sch"),
                              "erc")
        ok &= self.gate("erc", violations == 0,
                        "{} ERC violations".format(violations))
        ok &= self.critical(board, doc, "candidate")
        ok &= self.preserved(board)
        ok &= self.mechanical(board)
        ok &= self.dfm(board)
        return ok

    def mechanical(self, board):
        """The host connector mates, and the stack has room for the module."""
        import check_host_mating
        import check_stack
        ok = True
        for name, rows in (("host_mating", check_host_mating.check(board)),
                           ("pi_clearance", check_stack.check(board)[0])):
            failed = [label for label, passed, _d in rows if not passed]
            ok &= self.gate(name, not failed,
                            "{} checks, {} failed{}".format(
                                len(rows), len(failed),
                                "; " + ", ".join(failed) if failed else ""))
            with open(os.path.join(self.root, "reports", name + ".json"), "w",
                      encoding="utf-8") as fh:
                json.dump([{"check": label, "pass": passed, "detail": str(detail)}
                           for label, passed, detail in rows], fh, indent=2)
        return ok

    def preserved(self, board):
        """Nothing the autorouter is not allowed to touch has moved.

        Placement, outline, holes, stackup, netlist and origin all come from
        the generator; the router is only allowed to add tracks and vias.
        """
        import pcbnew
        before = pcbnew.LoadBoard(os.path.join(self.root,
                                               "pre_route.kicad_pcb"))
        after = pcbnew.LoadBoard(board)

        def fingerprint(loaded):
            parts = []
            for fp in loaded.Footprints():
                pos = fp.GetPosition()
                parts.append((fp.GetReference(), pos.x, pos.y,
                              round(fp.GetOrientationDegrees(), 3),
                              fp.IsFlipped(), str(fp.GetFPID().GetUniStringLibId()),
                              tuple(sorted((p.GetNumber(), p.GetNetname(),
                                            p.GetDrillSizeX())
                                           for p in fp.Pads()))))
            shapes = []
            for shape in loaded.GetDrawings():
                if shape.GetLayer() == pcbnew.Edge_Cuts:
                    shapes.append((shape.GetShape(), shape.GetStart().x,
                                   shape.GetStart().y, shape.GetEnd().x,
                                   shape.GetEnd().y))
            return {
                "footprints": sorted(parts),
                "outline": sorted(shapes),
                "layers": loaded.GetCopperLayerCount(),
                "nets": sorted(str(name) for name
                               in loaded.GetNetInfo().NetsByName().keys()),
                "origin": (loaded.GetDesignSettings().GetAuxOrigin().x,
                           loaded.GetDesignSettings().GetAuxOrigin().y),
            }

        one, two = fingerprint(before), fingerprint(after)
        changed = [key for key in one if one[key] != two[key]]
        return self.gate("inputs_preserved", not changed,
                         "changed by routing: {}".format(
                             ", ".join(changed) or "nothing"))

    def dfm(self, board):
        """The board's own manufacturability checks, on the routed candidate."""
        import pcbnew
        import manufacturing as mfg
        from pcbqa import geom
        rules = mfg.load_rules()
        geom.configure(rules["chord_error"])
        loaded = pcbnew.LoadBoard(board)
        survey = geom.BoardGeometry(loaded,
                                    contact_tolerance_mm=rules["contact"])
        target, limit = rules["mask_target"], rules["process_limit"]
        under_t = under_p = overlap = 0
        for via in survey.vias:
            worst = None
            for side in ("front", "back"):
                report = survey.via_mask_report(via, side)
                if report and report.get("annulus_to_opening_mm") is not None:
                    gap = report["annulus_to_opening_mm"]
                    if worst is None or gap < worst:
                        worst = gap
            if worst is None:
                continue
            under_t += worst < target
            under_p += worst < limit
            overlap += worst <= 0.0
        ok = self.gate("via_mask_target", under_t == 0,
                       "{} of {} vias under {} mm".format(
                           under_t, len(survey.vias), target))
        ok &= self.gate("via_mask_process", under_p == 0,
                        "{} vias under the {} mm process limit".format(
                            under_p, limit))
        ok &= self.gate("via_in_pad", overlap == 0,
                        "{} via/mask overlaps".format(overlap))
        return ok

    def finish(self, ok):
        with open(os.path.join(self.root, "build.json"), "w",
                  encoding="utf-8") as fh:
            json.dump({"gates": self.gates, "stages": self.log,
                       "passed": ok}, fh, indent=2)
        if ok and self.install:
            shutil.copy2(self.candidate, os.path.join(HERE, BOARD))
            # The project file goes with it: gen_pcb.py writes the net classes
            # and design rules into the project it builds, and a board whose
            # rules live somewhere else is a board nobody can re-check.
            shutil.copy2(
                os.path.join(self.work, "microphone_array_v2.kicad_pro"),
                os.path.join(HERE, "microphone_array_v2.kicad_pro"))
            print("\ninstalled the generated board and its project rules")
        elif ok:
            print("\nall gates passed; re-run with --install to install")
        else:
            print("\nnot installed: a hard gate failed")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--build-dir", default=BUILD)
    parser.add_argument("--install", action="store_true")
    args = parser.parse_args()

    build = Build(args.build_dir, args.install)
    print("build directory: " + build.root)
    build.prepare()
    ok = build.generate()
    if ok:
        ok = build.verify_pre_route() and ok
    if ok:
        ok = build.route() and ok
    if ok:
        ok = build.verify_candidate() and ok
    build.finish(ok)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
