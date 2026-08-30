"""Clock-spine probe: can the PDM clock structure route on this
placement at all, asked cheaply, before any full routing is spent.

The probe routes ONLY the clock net group (the board's own stage
definition) on a scratch copy of a placed board, with the same
stage arguments full routing would use, and classifies the result:

    clock-complete   every clock net routed
    clock-partial    some failed; their identities and the
                     router's blocker analysis are recorded
    probe-failed     the router did not produce a judgeable output

This is a FILTER measurement, not a verdict: the full pipeline
(planner escapes + staged routing) remains the authority. The
probe's value is measured, not assumed - probe_agreement.py
compares probe classes against known full-pipeline outcomes.

Outputs one JSON per probe under results/probes/, carrying the
input board's sha256 and the resolved KRT identity.

    python3 benchmark/krt_bench/clock_probe.py \
        --board <placed.kicad_pcb> --name seed34 [--timeout 600]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))

sys.path.insert(0, os.path.join(REPO, "benchmark", "boardB"))
import generate_candidate as gc                     # noqa: E402

sys.path.insert(0, os.environ.get("PCB_TOOLKIT_PATH")
                or os.path.join(REPO, "tooling",
                                "PCB_AutoDesignAndTest"))
from pcbqa import krt                               # noqa: E402


def parse_summaries(log_text):
    out = []
    for line in (log_text or "").splitlines():
        line = line.strip()
        for prefix in ("JSON_SUMMARY: ", "JSON_SUMMARY_MIN: "):
            if line.startswith(prefix):
                try:
                    out.append({"prefix": prefix.rstrip(": "),
                                "summary": json.loads(
                                    line[len(prefix):])})
                except ValueError:
                    pass
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--timeout", type=int, default=600)
    arguments = parser.parse_args()

    gc._configure_geometry()
    out_dir = os.path.join(HERE, "results", "probes")
    os.makedirs(out_dir, exist_ok=True)
    work = os.path.join(out_dir, arguments.name + "-work")
    os.makedirs(work, exist_ok=True)
    scratch = os.path.join(work, "probe_input.kicad_pcb")
    shutil.copyfile(arguments.board, scratch)
    gc._place_pro_sibling(scratch)
    output = os.path.join(work, "probe_routed.kicad_pcb")
    if os.path.exists(output):
        os.remove(output)

    clock_nets = gc._routing_stages()[0][1]
    command = [gc.KRT_PYTHON, gc.krt_tool("route.py"), scratch,
               "--output", output] \
        + gc._stage_router_args("clock") + ["--nets"] + clock_nets

    provenance = krt.provenance(gc.KRT_ROOT, gc.KRT_PYTHON)
    record = {
        "kind": "clock-probe",
        "name": arguments.name,
        "input_board": os.path.relpath(arguments.board, REPO),
        "input_board_sha256": gc._sha256_file(arguments.board),
        "krt_identity_sha256": krt.identity_digest(provenance),
        "clock_nets": clock_nets,
        "configuration": command[4:],
        "timeout_s": arguments.timeout,
    }
    start = time.time()
    try:
        completed = subprocess.run(command, capture_output=True,
                                   text=True,
                                   timeout=arguments.timeout)
        record["seconds"] = round(time.time() - start, 1)
        record["returncode"] = completed.returncode
        log_text = completed.stdout + completed.stderr
        record["json_summaries"] = parse_summaries(log_text)
        with open(os.path.join(work, "log.txt"), "w",
                  encoding="utf-8", errors="replace") as handle:
            handle.write(log_text)
        if completed.returncode == 0 and os.path.isfile(output):
            status = gc.connectivity_by_net(output, clock_nets)
            missing = sorted(net for net, cls in status.items()
                             if cls != "connectivity-complete")
            record["clock_complete"] = len(clock_nets) - len(missing)
            record["clock_total"] = len(clock_nets)
            record["clock_missing"] = missing
            record["class"] = ("clock-complete" if not missing
                               else "clock-partial")
        else:
            record["class"] = "probe-failed"
    except subprocess.TimeoutExpired:
        record["seconds"] = round(time.time() - start, 1)
        record["class"] = "probe-timeout"

    result_path = os.path.join(out_dir, arguments.name + ".json")
    with open(result_path, "w", encoding="utf-8",
              newline="\n") as handle:
        json.dump(record, handle, indent=1)
        handle.write("\n")
    print("probe {}: {} ({}s) -> {}".format(
        arguments.name, record["class"], record.get("seconds"),
        result_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
