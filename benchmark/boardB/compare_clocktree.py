"""First A/B side-by-side: Board A vs candidate seed01, clock tree.

Scope is exactly what the candidate has routed: the PDM clock-tree
nets. Metrics come from the same extractor with the same approved
finished-copper parameters on both boards, expressed as typed
benchmark metrics; a net the candidate failed to route appears as an
UNMEASURED metric (blocked_on: routing), never as zero. The report
binds both board SHAs, the toolkit identity and the metric schema
version.

Run with KiCad's python from the repository root; honors
PCB_TOOLKIT_PATH like the other benchmark scripts.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
TOOLKIT = (os.environ.get("PCB_TOOLKIT_PATH")
           or os.path.join(REPO, "tooling",
                           "PCB_AutoDesignAndTest"))
sys.path.insert(0, TOOLKIT)

from pcbqa import benchmark, extract               # noqa: E402
from pcbqa.fabricators import jlcpcb               # noqa: E402
from pcbqa.fabricators.store import CatalogStore   # noqa: E402

CLOCK_NETS = (["PDM_CLK_IN", "PDM_CLK_FPGA"]
              + ["PDM_CLK_Y{}".format(i) for i in range(8)]
              + ["PDM_CLK_B{}".format(i) for i in range(8)])


def _toolkit_commit():
    result = subprocess.run(["git", "-C", TOOLKIT, "rev-parse",
                             "HEAD"], capture_output=True, text=True)
    return result.stdout.strip() or "unknown"


def measure_board(board_file, copper, thickness):
    import pcbnew
    board = pcbnew.LoadBoard(board_file)
    with open(board_file, "rb") as handle:
        import hashlib
        sha = hashlib.sha256(handle.read()).hexdigest()
    metrics = []
    for net in CLOCK_NETS:
        try:
            record = extract.extract_net(board, net, copper, thickness)
        except extract.ExtractionError:
            for name in ("copper_length_mm", "via_count",
                         "segment_resistance_sum_ohm"):
                metrics.append(benchmark.unmeasured(
                    "{}:{}".format(net, name), "net", "routing",
                    "the candidate has not routed this net; absence "
                    "is reported, never zero"))
            continue
        provenance = {"extractor": "pcbqa.extract",
                      "board_file_sha256": sha}
        metrics.append(benchmark.measured(
            "{}:copper_length_mm".format(net), "net",
            record["totals"]["copper_length_mm"], "mm",
            "geometry-derived", provenance, "track segments of one "
                                            "net"))
        metrics.append(benchmark.measured(
            "{}:via_count".format(net), "net",
            record["totals"]["via_count"], "count",
            "geometry-derived", provenance, "vias on one net"))
        metrics.append(benchmark.measured(
            "{}:segment_resistance_sum_ohm".format(net), "net",
            record["dc"]["segment_resistance_sum_ohm"], "ohm",
            "geometry-derived", provenance,
            record["dc"]["meaning"]))
    return sha, metrics


def main():
    store = CatalogStore(os.path.join(TOOLKIT, "profiles", "jlcpcb"),
                         "jlcpcb")
    approved = store.approved()
    if approved is None:
        print("no approved catalog; refusing")
        return 1
    copper = extract.approved_finished_copper(approved, {
        "F.Cu": ("external", 1.0), "B.Cu": ("external", 1.0),
        "In1.Cu": ("internal", 0.5), "In2.Cu": ("internal", 0.5)})
    thickness = extract.physical_parameter(
        1.6, "mm", "caller-declared", "board thickness for via "
                                      "barrel estimates")
    binding_common = {
        "toolkit_commit": _toolkit_commit(),
        "physical_evidence": "approved catalog {} finished-copper "
                             "records".format(
                                 approved["normalized_sha256"]),
        "schema_version": benchmark.SCHEMA_VERSION,
    }
    reports = {}
    for label, board_file in (
            ("board_a", os.path.join(
                REPO, "microphone_array_v2.kicad_pcb")),
            ("board_b_seed01_clocktree", os.path.join(
                HERE, "candidates", "seed01",
                "candidate_clocktree_routed.kicad_pcb"))):
        sha, metrics = measure_board(board_file, copper, thickness)
        reports[label] = benchmark.report(
            dict(binding_common, board_file_sha256=sha), metrics)
    out_path = os.path.join(HERE, "candidates", "seed01",
                            "clocktree_ab_report.json")
    with open(out_path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(reports, handle, indent=1)
        handle.write("\n")

    a_metrics = {m["name"]: m for m in reports["board_a"]["metrics"]}
    b_metrics = {m["name"]: m
                 for m in reports["board_b_seed01_clocktree"][
                     "metrics"]}
    total_a = total_b = 0.0
    unmeasured = 0
    for name, metric_a in sorted(a_metrics.items()):
        metric_b = b_metrics[name]
        if not name.endswith("copper_length_mm"):
            continue
        if metric_b["status"] == "unmeasured":
            unmeasured += 1
            print("{:28s} A {:9.3f} mm | B unmeasured ({})".format(
                name.split(":")[0], metric_a["value"],
                metric_b["blocked_on"]))
            continue
        total_a += metric_a["value"]
        total_b += metric_b["value"]
        print("{:28s} A {:9.3f} mm | B {:9.3f} mm".format(
            name.split(":")[0], metric_a["value"],
            metric_b["value"]))
    print("comparable copper totals: A {:.1f} mm | B {:.1f} mm | "
          "B unrouted nets: {}".format(total_a, total_b, unmeasured))
    print("report:", out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
