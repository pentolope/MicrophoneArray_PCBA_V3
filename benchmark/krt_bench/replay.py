"""Replay a frozen candidate's routing under the CURRENT resolved
KiCadRoutingTools, and record a comparable, honestly labelled result.

The benchmark question: does the new router complete more of the
same work, from the same bytes, under the same stage arguments?

- The INPUT is a frozen board: the candidate's committed critical
  checkpoint (`candidate_critical.kicad_pcb`), byte-identified by
  SHA-256 - exactly what the historical router received.
- The STAGES are the generator's own: same net groups, same router
  arguments, same fabrication judgement, same generic recovery
  (sliver strip and DRC-named graze removal). They are imported
  from generate_candidate.py, never re-stated.
- The planner's last-mile repair is deliberately NOT replayed: it
  is our machinery, not the router's. The comparison point is
  "router + generic recovery, after per-group cleanup". The
  historical number at the same point is recomputed from the
  committed bytes, and the historical FINAL number (which includes
  last-mile repair) is reported separately, labelled historical.
- Every rerun record carries the resolved KRT provenance. The
  historical records predate KRT provenance and say so explicitly.

Nothing here touches Board A or any committed candidate artifact:
all outputs land under benchmark/krt_bench/results/.

Run with KiCad's python:

    ".../kicad/python.exe" benchmark/krt_bench/replay.py \
        --candidate seed34 [--repeat-first] [--label NAME]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
BOARD_B = os.path.join(REPO, "benchmark", "boardB")

sys.path.insert(0, BOARD_B)
import generate_candidate as gc                     # noqa: E402

sys.path.insert(0, os.environ.get("PCB_TOOLKIT_PATH")
                or os.path.join(REPO, "tooling",
                                "PCB_AutoDesignAndTest"))
from pcbqa import krt                               # noqa: E402


class ReplayError(Exception):
    """The replay cannot proceed as asked."""


def _parse_json_summaries(log_text):
    """Every JSON_SUMMARY the router printed, in order. 0.21.x
    marks reconciliation-subset summaries with scope; keep them
    all - the reader needs the run-scoped one AND the evidence of
    sub-passes."""
    summaries = []
    for line in (log_text or "").splitlines():
        line = line.strip()
        for prefix in ("JSON_SUMMARY: ", "JSON_SUMMARY_MIN: "):
            if line.startswith(prefix):
                try:
                    summaries.append(
                        {"prefix": prefix.rstrip(": "),
                         "summary": json.loads(
                             line[len(prefix):])})
                except ValueError:
                    summaries.append({"prefix": prefix.rstrip(": "),
                                      "unparsed": line[:500]})
    return summaries


def _count_vias(board_path):
    import pcbnew
    board = pcbnew.LoadBoard(board_path)
    return sum(1 for track in board.GetTracks()
               if track.GetClass() in ("PCB_VIA", "VIA"))


def historical_block(candidate):
    """The 0.19.1-era evidence, extracted from the committed
    derivation and labelled historical. Nothing is rerun here."""
    derivation_path = os.path.join(BOARD_B, "candidates",
                                   candidate, "derivation.json")
    doc = json.load(open(derivation_path, encoding="utf-8"))
    attempts = []
    connectivity = []
    for record in doc["records"]:
        if record.get("tool") == "route.py":
            attempts.append({
                "attempt_id": record.get("attempt_id"),
                "stage": record.get("stage"),
                "status": record.get("status"),
                "seconds": record.get("seconds"),
                "input_board_sha256":
                    record.get("input_board_sha256"),
                "output_sha256": record.get("output_sha256"),
                "min_clearance_used_mm":
                    record.get("min_clearance_used_mm"),
            })
        if record.get("stage", "").endswith("_connectivity") \
                and "complete" in record:
            connectivity.append({
                "stage": record["stage"],
                "complete": record["complete"],
                "total": record["total"],
                "not_complete": sorted(
                    record.get("not_complete", {})),
            })
    return {
        "historical": True,
        "source": os.path.relpath(derivation_path, REPO),
        "krt_identity": ("unrecorded: these attempts predate KRT "
                         "provenance; the installation of record "
                         "was the PCM package at 0.19.1"),
        "route_attempts": attempts,
        "connectivity_records": connectivity,
        "total_route_seconds": round(sum(
            a["seconds"] or 0 for a in attempts), 1),
    }


def measure_endstate(board_path, label, workdir_base):
    """One comparable end-state measurement, computed the same way
    for historical bytes and rerun bytes: per-group and overall
    connectivity on a refilled working copy, via count, and the
    identities of incomplete nets. All scratch lands under the run
    directory - never beside a committed artifact."""
    import shutil
    workdir = os.path.join(workdir_base, "measure-" + label)
    os.makedirs(workdir, exist_ok=True)
    working = os.path.join(workdir, "measured.kicad_pcb")
    shutil.copyfile(board_path, working)
    gc.refill_zones(working)
    groups = {}
    incomplete = []
    for group_name, nets in gc._routing_stages():
        status = gc.connectivity_by_net(working, nets)
        complete = sum(1 for c in status.values()
                       if c == "connectivity-complete")
        groups[group_name] = {"complete": complete,
                              "total": len(nets)}
        incomplete.extend(sorted(
            net for net, c in status.items()
            if c != "connectivity-complete"))
    return {
        "board_sha256": gc._sha256_file(board_path),
        "measured_refilled_sha256": gc._sha256_file(working),
        "groups": groups,
        "complete": sum(g["complete"] for g in groups.values()),
        "total": sum(g["total"] for g in groups.values()),
        "nets_not_complete": incomplete,
        "vias": _count_vias(board_path),
    }


def historical_endstate(candidate, run_dir):
    """The SAME comparison-point measurement, applied to the
    historical router's committed bytes: walk the derivation the
    way the generator advanced its checkpoint (completed route
    attempts, fabrication-clean sliver-strip recoveries, completed
    cleanups) and measure the last artifact BEFORE last-mile
    repair. The bytes are historical; the measurement is performed
    now, identically to the rerun's."""
    import shutil
    candidate_dir = os.path.join(BOARD_B, "candidates", candidate)
    doc = json.load(open(os.path.join(candidate_dir,
                                      "derivation.json"),
                         encoding="utf-8"))
    final_rel = None
    for record_entry in doc["records"]:
        if record_entry.get("tool") == "route.py" \
                and record_entry.get("status") == "completed":
            final_rel = record_entry.get("output_path")
        if record_entry.get("stage") == "sliver_strip" and \
                (record_entry.get("fabrication_geometry") or {}
                 ).get("ok") is True:
            final_rel = record_entry.get("output_path")
    if final_rel is None:
        return {"historical_bytes": True,
                "detail": "no completed router artifact in the "
                          "historical derivation"}
    source = os.path.join(candidate_dir, final_rel)
    copied = os.path.join(run_dir, "historical_bytes.kicad_pcb")
    shutil.copyfile(source, copied)
    measured = measure_endstate(copied, "historical", run_dir)
    measured.update({
        "historical_bytes": True,
        "measured_now": True,
        "source": os.path.relpath(source, REPO),
        "note": "last pre-last-mile router artifact of the "
                "historical run; connectivity and via count "
                "computed today with the same function as the "
                "rerun's"})
    return measured


def replay(candidate, run_dir, stage_timeout, repeat_first,
           record):
    frozen = os.path.join(BOARD_B, "candidates", candidate,
                          "candidate_critical.kicad_pcb")
    if not os.path.isfile(frozen):
        raise ReplayError("no frozen critical checkpoint for "
                          + candidate)
    frozen_sha = gc._sha256_file(frozen)
    record({"stage": "frozen_input",
            "candidate": candidate,
            "path": os.path.relpath(frozen, REPO),
            "board_sha256": frozen_sha})

    def run_stage(stage_name, input_path, nets, tag):
        attempt_dir = os.path.join(run_dir, tag)
        os.makedirs(attempt_dir, exist_ok=True)
        output_path = os.path.join(attempt_dir,
                                   "routed.kicad_pcb")
        command = [gc.KRT_PYTHON, gc.krt_tool("route.py"),
                   input_path, "--output", output_path] \
            + gc._stage_router_args(stage_name) \
            + ["--nets"] + nets
        timeout = gc._stage_timeout(stage_name, stage_timeout)
        entry = {"stage": tag, "router_stage": stage_name,
                 "input_board_sha256": gc._sha256_file(input_path),
                 "configuration": command[4:],
                 "timeout_s": timeout, "status": "started",
                 "output_path": os.path.relpath(output_path,
                                                run_dir)}
        start = time.time()
        gc._place_pro_sibling(input_path)
        try:
            completed = subprocess.run(
                command, capture_output=True, text=True,
                timeout=timeout)
            entry["seconds"] = round(time.time() - start, 1)
            entry["returncode"] = completed.returncode
            log_text = completed.stdout + completed.stderr
            with open(os.path.join(attempt_dir, "log.txt"), "w",
                      encoding="utf-8", errors="replace") as handle:
                handle.write(log_text)
            entry["json_summaries"] = _parse_json_summaries(
                log_text)
            entry["min_clearance_used_mm"] = \
                gc._parse_min_clearance(log_text)
            if completed.returncode == 0 \
                    and os.path.isfile(output_path):
                entry["output_sha256"] = gc._sha256_file(
                    output_path)
                verdict = gc.stage_fabrication_check(
                    output_path,
                    os.path.join(attempt_dir, "fabcheck"))
                entry["fabrication_geometry"] = verdict
                entry["status"] = ("completed"
                                   if verdict["ok"] is True
                                   else "fabrication-invalid")
            else:
                entry["status"] = "failed"
                entry["output_sha256"] = None
        except subprocess.TimeoutExpired:
            entry["seconds"] = round(time.time() - start, 1)
            entry["status"] = "timed-out"
            entry["output_sha256"] = None
        # Generic recovery, exactly as the generator applies it: a
        # stage invalid ONLY for sub-floor sliver widths or a
        # DRC-named graze gets those removed and is re-judged.
        if entry["status"] == "fabrication-invalid" and \
                set((entry.get("fabrication_geometry") or {}).get(
                    "violations_by_type") or {}) <= {
                        "track_width", "connection_width"}:
            stripped = os.path.join(attempt_dir,
                                    "recovered.kicad_pcb")
            removed = gc.strip_slivers(output_path, stripped)
            graze_report = os.path.join(attempt_dir, "fabcheck",
                                        "fabcheck_drc.json")
            if os.path.isfile(graze_report):
                removed += gc.strip_named_grazes(
                    stripped, stripped, graze_report)
            gc._place_pro_sibling(stripped)
            verdict = gc.stage_fabrication_check(
                stripped, os.path.join(attempt_dir,
                                       "fabcheck-recovered"))
            entry["recovery"] = {
                "removed_segments": removed,
                "fabrication_geometry": verdict,
                "output_sha256": gc._sha256_file(stripped)}
            if verdict["ok"] is True:
                entry["status"] = "completed"
                entry["output_path"] = os.path.relpath(
                    stripped, run_dir)
        record(entry)
        return entry

    checkpoint = frozen
    stages = gc._routing_stages()
    for stage_name, nets in stages:
        entry = run_stage(stage_name, checkpoint, nets,
                          "route_" + stage_name)
        if entry["status"] == "completed":
            checkpoint = os.path.join(run_dir,
                                      entry["output_path"])
        status = gc.connectivity_by_net(checkpoint, nets)
        record({"stage": "route_{}_connectivity".format(
                    stage_name),
                "checkpoint_advanced":
                    entry["status"] == "completed",
                "complete": sum(1 for c in status.values()
                                if c == "connectivity-complete"),
                "total": len(nets),
                "not_complete": sorted(
                    net for net, c in status.items()
                    if c != "connectivity-complete")})

    # Per-group cleanup on a refilled copy, as the generator does.
    routed = os.path.join(run_dir, "routed.kicad_pcb")
    with open(checkpoint, "rb") as source:
        data = source.read()
    with open(routed, "wb") as target:
        target.write(data)
    gc.refill_zones(routed)
    for group_name, group_nets in stages:
        missed = [net for net, cls in gc.connectivity_by_net(
            routed, group_nets).items()
            if cls != "connectivity-complete"]
        if not missed:
            continue
        entry = run_stage(group_name, routed, missed,
                          "cleanup_" + group_name)
        if entry["status"] == "completed":
            with open(os.path.join(run_dir, entry["output_path"]),
                      "rb") as source:
                data = source.read()
            with open(routed, "wb") as target:
                target.write(data)
            gc.refill_zones(routed)

    endstate = measure_endstate(routed, "rerun", run_dir)
    record({"stage": "rerun_endstate", **endstate})

    if repeat_first:
        stage_name, nets = stages[0]
        first = next(r for r in run_records
                     if r.get("stage") == "route_" + stage_name)
        again = run_stage(stage_name, frozen, nets,
                          "repeat_" + stage_name)
        record({"stage": "determinism_check",
                "stage_repeated": stage_name,
                "first_output_sha256": first.get("output_sha256"),
                "repeat_output_sha256": again.get("output_sha256"),
                "identical":
                    first.get("output_sha256") is not None
                    and first.get("output_sha256")
                    == again.get("output_sha256"),
                "meaning": "byte-identical output on identical "
                           "input proves determinism for THIS "
                           "stage only; inequality disproves it"})
    return endstate


run_records = []


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True,
                        help="frozen candidate, e.g. seed34")
    parser.add_argument("--label", default=None,
                        help="run directory name (default: UTC "
                             "timestamp)")
    parser.add_argument("--stage-timeout", type=int, default=1200)
    parser.add_argument("--repeat-first", action="store_true",
                        help="re-run the first stage on the frozen "
                             "input and compare output bytes")
    arguments = parser.parse_args()

    label = arguments.label or time.strftime("%Y%m%dT%H%M%SZ",
                                             time.gmtime())
    run_dir = os.path.join(HERE, "results", arguments.candidate,
                           label)
    if os.path.exists(run_dir):
        raise ReplayError("run directory {} already exists; a "
                          "replay identity is never reused".format(
                              run_dir))
    os.makedirs(run_dir)
    replay_path = os.path.join(run_dir, "replay.json")

    gc._configure_geometry()
    provenance = krt.provenance(gc.KRT_ROOT, gc.KRT_PYTHON)
    document = {
        "kind": "krt-replay",
        "candidate": arguments.candidate,
        "label": label,
        "historical_evidence": None,
        "krt": {"identity_sha256": krt.identity_digest(provenance),
                "provenance": provenance},
        "comparison_point": "router + generic recovery after "
                            "per-group cleanup; the planner's "
                            "last-mile repair is not replayed",
        "records": [],
    }

    def record(entry):
        run_records.append(entry)
        document["records"] = run_records
        with open(replay_path, "w", encoding="utf-8",
                  newline="\n") as handle:
            json.dump(document, handle, indent=1)
            handle.write("\n")

    document["historical_evidence"] = historical_block(
        arguments.candidate)
    record({"stage": "begin",
            "stage_timeout_s": arguments.stage_timeout})
    record({"stage": "historical_endstate",
            **historical_endstate(arguments.candidate, run_dir)})
    start = time.time()
    endstate = replay(arguments.candidate, run_dir,
                      arguments.stage_timeout,
                      arguments.repeat_first, record)
    record({"stage": "done",
            "total_seconds": round(time.time() - start, 1)})
    print("replay {}: {}/{} complete, {} vias, {}s".format(
        arguments.candidate, endstate["complete"],
        endstate["total"], endstate["vias"],
        round(time.time() - start, 1)))
    print("results:", run_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
