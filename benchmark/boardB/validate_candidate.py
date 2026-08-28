"""Make one Board B candidate a real toolkit validation subject.

A candidate stops being "router output plus extractor metrics" here:
this script derives a candidate manifest from the product's live
manifest (same requirements, same gates - only the board under test,
its identity and its output tree change), runs the toolkit's normal
validation against it, extracts typed metrics under the same
physical evidence as Board A's baseline, and reduces everything to a
machine-readable score and decision. The authoritative live manifest
is never mutated; the candidate manifest is a generated artifact in
the candidate's own directory.

Router return codes appear nowhere in the acceptance logic: routed
completeness is read from the board file, correctness from the
gates, semantics from the recorded placement policy.

Run with KiCad's python:

    ".../kicad/python.exe" benchmark/boardB/validate_candidate.py \
        --seed 2 [--skip-validate]
"""

from __future__ import annotations

import argparse
import copy
import glob
import hashlib
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
KICAD_PYTHON = sys.executable

sys.path.insert(0, os.environ.get("PCB_TOOLKIT_PATH")
                or os.path.join(REPO, "tooling",
                                "PCB_AutoDesignAndTest"))

from pcbqa import benchmark, extract               # noqa: E402
from pcbqa.fabricators.store import CatalogStore   # noqa: E402

#: The A/B net inventory: exactly the nets Board A's committed
#: baseline reports, so every candidate is measured on the same set.
def baseline_nets():
    baseline = json.load(open(
        os.path.join(REPO, "benchmark", "board_a_baseline.json"),
        encoding="utf-8"))
    return sorted(baseline["nets"])


CLOCK_LEAVES = (["PDM_CLK_Y{}".format(i) for i in range(8)]
                + ["PDM_CLK_B{}".format(i) for i in range(8)])

#: The 20 clock nets: the board's critical structure. A candidate
#: whose clock tree is not connectivity-complete cannot win, whatever
#: else it saves.
CLOCK_NETS = (["MCLK_OSC", "AUDIO_MCLK", "PDM_CLK_IN",
               "PDM_CLK_FPGA"]
              + ["PDM_CLK_Y{}".format(i) for i in range(8)]
              + ["PDM_CLK_B{}".format(i) for i in range(8)])

#: Composite ranking weights - recorded, not implied. Connectivity
#: completion dominates BY CONSTRUCTION: the search sorts on the
#: completeness hierarchy first and touches this scalar only among
#: candidates that tie there, so a candidate with partial routes can
#: never outrank a complete one on any downstream metric.
RANKING_WEIGHTS = {
    "connectivity_complete_fraction": 0.5,
    "critical_clock_complete": 0.2,
    "placement_policy_ok": 0.15,
    "gate_acceptance": 0.15,
}

#: Why a failing gate fails, for a candidate: a real candidate-design
#: problem, or a comparison against Board A's release artifacts that
#: the candidate never generated. The second class is missing
#: candidate-derived artifacts, not board correctness - kept visible,
#: never waived.
GATE_FAILURE_CLASSES = {
    "DRC.AUTHORITATIVE": "candidate-design",
    "NET.TOPOLOGY": "candidate-design",
    "TIMING.PATH_INTEGRITY": "candidate-design",
    "ROUTE.GEOMETRY_HYGIENE": "candidate-design",
    "VIA.ANNULUS_MASK_OVERLAP": "candidate-design",
    "VIA.IN_PAD_CONTACT": "candidate-design",
    "VIA.MASK_CLEARANCE_PROCESS": "candidate-design",
    "VIA.MASK_CLEARANCE_TARGET": "candidate-design",
    "VIA.NATIVE_GERBER_AGREEMENT":
        "board-a-release-parity (missing candidate-derived "
        "artifacts)",
    "CPL.NATIVE_PARITY":
        "board-a-release-parity (missing candidate-derived "
        "artifacts)",
    "CPL.ORIENTATION":
        "board-a-release-parity (missing candidate-derived "
        "artifacts)",
    "FAB.LAYER_IDENTITY":
        "board-a-release-parity (missing candidate-derived "
        "artifacts)",
    "STACK.GERBER_PARITY":
        "board-a-release-parity (missing candidate-derived "
        "artifacts)",
    "PROV.REPORT_FRESHNESS":
        "board-a-release-parity (missing candidate-derived "
        "artifacts)",
}


def _sha256_file(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def physical_inputs():
    """The SAME physical evidence path as the Board A baseline:
    approved finished copper by the board's declared requirements."""
    toolkit = (os.environ.get("PCB_TOOLKIT_PATH")
               or os.path.join(REPO, "tooling",
                               "PCB_AutoDesignAndTest"))
    store = CatalogStore(os.path.join(toolkit, "profiles",
                                      "jlcpcb"), "jlcpcb")
    approved = store.approved()
    if approved is None:
        raise SystemExit("no approved catalog; refusing")
    requirements_path = os.path.join(REPO, "board",
                                     "jlcpcb_requirements.json")
    with open(requirements_path, "rb") as handle:
        requirements_bytes = handle.read()
    requirements = json.loads(requirements_bytes.decode("utf-8"))
    import pcbnew
    board = pcbnew.LoadBoard(os.path.join(
        REPO, "microphone_array_v2.kicad_pcb"))
    stack = [board.GetLayerName(layer) for layer in
             board.GetEnabledLayers().CuStack()]
    assignments = extract.copper_assignments_from_requirements(
        requirements, stack)
    copper = extract.approved_finished_copper(approved, assignments)
    thickness = extract.requirements_board_thickness(
        requirements,
        hashlib.sha256(requirements_bytes).hexdigest())
    evidence = {
        "kind": "resolved-physical-construction",
        "digest": extract.construction_digest(copper, thickness),
        "detail": "canonical digest of the exact resolved physical "
                  "inputs (per-layer finished copper, board "
                  "thickness, IEC 60028 resistivity); resolved from "
                  "approved catalog {}... via requirements "
                  "{}...".format(
                      approved["normalized_sha256"][:12],
                      hashlib.sha256(
                          requirements_bytes).hexdigest()[:12]),
    }
    return copper, thickness, evidence


def measure_board(board_file, nets, copper, thickness):
    """Typed metrics for one board over the A/B net set - governed
    by REAL connectivity, never by the presence of tracks.

    Only a connectivity-complete net produces the comparable
    metrics. A partial net's copper appears solely as an explicitly
    partial inventory metric under its own semantic definition, so
    it can never pair with a complete route; its comparable metrics
    stay unmeasured. Classification comes from the toolkit's
    classify_net on this very board file."""
    import pcbnew
    from pcbqa import geom
    from pcbqa.connectivity import classify_net
    board = pcbnew.LoadBoard(board_file)
    sha = _sha256_file(board_file)
    definitions = extract.METRIC_DEFINITIONS
    metrics = []
    measured_lengths = {}
    connectivity = {}
    for net in nets:
        state = classify_net(board, net, geom.pad_copper_polygon)
        connectivity[net] = state["class"]
        if state["class"] != "connectivity-complete":
            for name in ("copper_length_mm", "via_count",
                         "segment_resistance_sum_ohm"):
                metrics.append(benchmark.unmeasured(
                    "{}:{}".format(net, name), "net",
                    definitions[name], "routing",
                    "net connectivity is {!r}; only a "
                    "connectivity-complete net yields comparable "
                    "metrics".format(state["class"])))
            if state["class"] == "partial-copper":
                record = extract.extract_net(board, net, copper,
                                             thickness)
                metrics.append(benchmark.measured(
                    "{}:partial_copper_length_mm".format(net),
                    "net", definitions["partial_copper_length_mm"],
                    record["totals"]["copper_length_mm"], "mm",
                    "geometry-derived",
                    {"extractor": "pcbqa.extract",
                     "extract_version": extract.EXTRACT_VERSION,
                     "board_file_sha256": sha,
                     "connectivity_class": state["class"]},
                    "PARTIAL copper inventory of an incompletely "
                    "connected net; never comparable to a complete "
                    "route"))
            continue
        record = extract.extract_net(board, net, copper, thickness)
        provenance = {"extractor": "pcbqa.extract",
                      "extract_version": extract.EXTRACT_VERSION,
                      "board_file_sha256": sha,
                      "connectivity_class": state["class"]}
        measured_lengths[net] = record["totals"]["copper_length_mm"]
        metrics.append(benchmark.measured(
            "{}:copper_length_mm".format(net), "net",
            definitions["copper_length_mm"],
            record["totals"]["copper_length_mm"], "mm",
            "geometry-derived", provenance,
            "track segments of one connectivity-complete net"))
        metrics.append(benchmark.measured(
            "{}:via_count".format(net), "net",
            definitions["via_count"],
            record["totals"]["via_count"], "count",
            "geometry-derived", provenance,
            "vias on one connectivity-complete net"))
        metrics.append(benchmark.measured(
            "{}:segment_resistance_sum_ohm".format(net), "net",
            definitions["segment_resistance_sum_ohm"],
            record["dc"]["segment_resistance_sum_ohm"], "ohm",
            "geometry-derived", provenance,
            record["dc"]["meaning"]))
    leaves = [measured_lengths.get(net) for net in CLOCK_LEAVES]
    if all(length is not None for length in leaves):
        metrics.append(benchmark.measured(
            "clock_leaf_length_spread_mm", "board",
            definitions["clock_leaf_length_spread_mm"],
            round(max(leaves) - min(leaves), 6), "mm",
            "geometry-derived",
            {"definition": "max minus min copper length over the "
                           "16 PDM clock leaf nets, all "
                           "connectivity-complete",
             "board_file_sha256": sha},
            "a routed-length spread, not an electrical-delay "
            "measurement"))
    else:
        metrics.append(benchmark.unmeasured(
            "clock_leaf_length_spread_mm", "board",
            definitions["clock_leaf_length_spread_mm"], "routing",
            "not every PDM clock leaf is connectivity-complete; a "
            "spread over a partial set would be fiction"))
    return sha, metrics, connectivity


def derive_manifest(seed_dir, seed, board_basename):
    live = json.load(open(os.path.join(REPO, "board",
                                       "manifest.live.json"),
                          encoding="utf-8"))
    manifest = copy.deepcopy(live)
    manifest["board_id"] = "boardB-seed{:02d}-candidate".format(seed)
    manifest["description"] = (
        "DISPOSABLE Board B candidate seed{:02d}: same product "
        "requirements and gates as the live board, pointed at the "
        "candidate PCB. Never a release input.".format(seed))
    manifest["project_root"] = "../../../.."
    manifest["sources"] = dict(manifest["sources"])
    manifest["sources"]["pcb"] = (
        "benchmark/boardB/candidates/seed{:02d}/{}".format(
            seed, board_basename))
    out_path = os.path.join(seed_dir, "manifest.candidate.json")
    with open(out_path, "w", encoding="utf-8",
              newline="\n") as handle:
        json.dump(manifest, handle, indent=1)
        handle.write("\n")
    return out_path, manifest["board_id"]


def run_validation(manifest_path, board_id, timeout):
    toolkit_run = os.path.join(REPO, "tooling",
                               "PCB_AutoDesignAndTest", "run.py")
    environment = dict(os.environ)
    environment.update({"HTTP_PROXY": "http://127.0.0.1:1",
                        "HTTPS_PROXY": "http://127.0.0.1:1"})
    start = time.time()
    try:
        completed = subprocess.run(
            [KICAD_PYTHON, toolkit_run, "validate", manifest_path],
            capture_output=True, text=True, timeout=timeout,
            cwd=REPO, env=environment)
        outcome = {"returncode": completed.returncode,
                   "seconds": round(time.time() - start, 1),
                   "log_tail": (completed.stdout
                                + completed.stderr)[-1500:]}
    except subprocess.TimeoutExpired:
        return {"timed_out_after_s": timeout}, None
    attempts = sorted(glob.glob(os.path.join(
        REPO, "out", board_id, "attempts", "*",
        "validation.json")))
    if not attempts:
        return outcome, None
    return outcome, json.load(open(attempts[-1], encoding="utf-8"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--skip-validate", action="store_true")
    parser.add_argument("--validate-timeout", type=int,
                        default=1800)
    arguments = parser.parse_args()

    seed_dir = os.path.join(HERE, "candidates",
                            "seed{:02d}".format(arguments.seed))
    derivation = json.load(open(
        os.path.join(seed_dir, "derivation.json"),
        encoding="utf-8"))
    board_file = None
    for basename in ("candidate_routed.kicad_pcb",
                     "candidate_placed.kicad_pcb"):
        candidate = os.path.join(seed_dir, basename)
        if os.path.isfile(candidate):
            board_file = candidate
            break
    if board_file is None:
        raise SystemExit("seed{:02d} has no candidate board".format(
            arguments.seed))

    policy_records = [record for record in derivation["records"]
                      if record["stage"] == "post_quench_policy"]
    policy_ok = bool(policy_records) and \
        policy_records[-1]["policy_ok"]

    from pcbqa import geom as geom_module
    manifest_doc = json.load(open(
        os.path.join(REPO, "board", "manifest.live.json"),
        encoding="utf-8"))
    geom_module.configure(
        manifest_doc["geometry_profile"]["tolerances"][
            "polygon_chord_error_mm"]["value"])
    nets = baseline_nets()
    copper, thickness, evidence = physical_inputs()
    sha, metrics, connectivity = measure_board(board_file, nets,
                                               copper, thickness)
    toolkit_commit = subprocess.run(
        ["git", "-C", os.path.join(REPO, "tooling",
                                   "PCB_AutoDesignAndTest"),
         "rev-parse", "HEAD"], capture_output=True,
        text=True).stdout.strip() or "unknown"
    report = benchmark.report(
        {"board_file_sha256": sha, "toolkit_commit": toolkit_commit,
         "physical_evidence": evidence,
         "schema_version": benchmark.SCHEMA_VERSION}, metrics)
    with open(os.path.join(seed_dir, "candidate_metrics.json"),
              "w", encoding="utf-8", newline="\n") as handle:
        json.dump(report, handle, indent=1)
        handle.write("\n")

    complete_nets = sorted(
        net for net, state in connectivity.items()
        if state == "connectivity-complete")
    complete_fraction = len(complete_nets) / float(len(nets))
    critical_clock_complete = all(
        connectivity.get(net) == "connectivity-complete"
        for net in CLOCK_NETS)
    measured_nets = complete_nets
    copper_total = round(sum(
        metric["value"] for metric in metrics
        if metric["name"].endswith(":copper_length_mm")
        and metric["status"] == "measured"), 3)

    validation_summary = None
    if arguments.skip_validate:
        gates_path = os.path.join(seed_dir, "gates.json")
        if os.path.isfile(gates_path):
            validation_summary = json.load(open(
                gates_path, encoding="utf-8"))[
                    "statuses"]["validation"]
    if not arguments.skip_validate:
        manifest_path, board_id = derive_manifest(
            seed_dir, arguments.seed,
            os.path.basename(board_file))
        outcome, validation = run_validation(
            manifest_path, board_id, arguments.validate_timeout)
        if validation is not None:
            gates = validation.get("gates", [])
            failing = sorted(
                gate["gate"] for gate in gates
                if gate.get("status") not in
                ("PASS", "NOT_APPLICABLE", "ADVISORY"))
            validation_summary = {
                "verdict": validation["summary"]["verdict"],
                "counts": validation["summary"]["counts"],
                "blocking": validation["summary"].get("blocking",
                                                      []),
                "failing_gates": failing,
                "runner": outcome,
            }
        else:
            validation_summary = {"verdict": "NO-ATTEMPT",
                                  "runner": outcome}
        failure_classes = {}
        if validation_summary and \
                validation_summary.get("failing_gates"):
            for gate in validation_summary["failing_gates"]:
                failure_classes[gate] = GATE_FAILURE_CLASSES.get(
                    gate, "unclassified")
        with open(os.path.join(seed_dir, "gates.json"), "w",
                  encoding="utf-8", newline="\n") as handle:
            json.dump({
                "kind": "candidate-gate-status",
                "candidate_board_sha256": sha,
                "manifest": os.path.basename(manifest_path),
                "statuses": {
                    "placement_policy_satisfied": policy_ok,
                    "connectivity": connectivity,
                    "connectivity_complete_nets":
                        len(complete_nets),
                    "connectivity_net_total": len(nets),
                    "critical_clock_complete":
                        critical_clock_complete,
                    "failing_gate_classes": failure_classes,
                    "validation": validation_summary,
                    "extraction_available": True,
                    "simulation_coverage": {
                        "available": ["interconnect_dc "
                                      "(geometry-derived)"],
                        "unresolved_blockers": [
                            "interconnect_si: no field-solver "
                            "backend",
                            "power_integrity: no PDN model",
                        ],
                    },
                },
                "acceptance_note": "router return codes play no "
                                   "role here; the board file, the "
                                   "gates and the recorded policy "
                                   "do",
            }, handle, indent=1)
            handle.write("\n")

    accepted = validation_summary is not None and \
        validation_summary.get("verdict") == "ACCEPTED"
    ranking = (RANKING_WEIGHTS["connectivity_complete_fraction"]
               * complete_fraction
               + RANKING_WEIGHTS["critical_clock_complete"]
               * (1.0 if critical_clock_complete else 0.0)
               + RANKING_WEIGHTS["placement_policy_ok"]
               * (1.0 if policy_ok else 0.0)
               + RANKING_WEIGHTS["gate_acceptance"]
               * (1.0 if accepted else 0.0))
    reasons = []
    if not policy_ok:
        reasons.append("placement policy not satisfied")
    if not critical_clock_complete:
        reasons.append(
            "the PDM clock tree is not connectivity-complete: "
            + ", ".join(sorted(
                net for net in CLOCK_NETS
                if connectivity.get(net)
                != "connectivity-complete")))
    decision = {
        "kind": "candidate-decision",
        "candidate": "seed{:02d}".format(arguments.seed),
        "board_file_sha256": sha,
        "components": {
            "connectivity_complete_fraction":
                round(complete_fraction, 4),
            "connectivity_complete_nets": len(complete_nets),
            "net_total": len(nets),
            "critical_clock_complete": critical_clock_complete,
            "not_complete": {
                net: state for net, state in sorted(
                    connectivity.items())
                if state != "connectivity-complete"},
            "placement_policy_ok": policy_ok,
            "validation_verdict":
                (validation_summary or {}).get("verdict"),
            "measured_copper_total_mm": copper_total,
            "measured_net_set_sha256": hashlib.sha256(
                json.dumps(measured_nets).encode(
                    "utf-8")).hexdigest(),
        },
        "ranking_score": round(ranking, 4),
        "ranking_weights": RANKING_WEIGHTS,
        "ranking_note": "the composite ranks candidates for the "
                        "next search step only; the components are "
                        "the evidence, and WHY a candidate lost is "
                        "read from them, never from the scalar",
        "decision": "reject" if reasons else
                    "accept-for-comparison",
        "reasons": reasons or ["thresholded placement policy "
                               "holds and routing cleared the "
                               "completeness bar"],
        "next_step_if_rejected": "perturb: try another seed; the "
                                 "derivation records which stage "
                                 "lost the nets",
    }
    with open(os.path.join(seed_dir, "decision.json"), "w",
              encoding="utf-8", newline="\n") as handle:
        json.dump(decision, handle, indent=1)
        handle.write("\n")
    print(json.dumps(decision["components"]))
    print("decision:", decision["decision"],
          "| ranking:", decision["ranking_score"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
