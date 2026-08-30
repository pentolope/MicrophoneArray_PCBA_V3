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

Run from the repository root:

    python3 benchmark/boardB/validate_candidate.py \
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
                                "PCBA_AutoDesignAndTest"))

from pcbqa import headless                        # noqa: E402
headless.suppress_blocking_ui()
from pcbqa import benchmark, extract, freshness    # noqa: E402
from pcbqa import netlist_contract                 # noqa: E402
from pcbqa import progression                      # noqa: E402
from pcbqa.fabricators.store import CatalogStore   # noqa: E402

TOOLKIT_ROOT = (os.environ.get("PCB_TOOLKIT_PATH")
                or os.path.join(REPO, "tooling",
                                "PCBA_AutoDesignAndTest"))


def closure_components(candidate_board_path,
                       seed_dir=None):
    """The deliberate dependency set of every artifact this script
    derives: its own code, the toolkit modules whose semantics the
    numbers depend on, the candidate board, the AUTHORITATIVE board
    (the netlist contract's source), the semantic inputs and the
    metric schema. Unrelated files are deliberately absent - a doc
    change invalidates nothing.

    Identities are canonical: source text and KiCad files by
    LF-normalized content, JSON artifacts by canonical
    serialization - checkout byte conventions never masquerade as
    change, while any content change always registers."""
    five_v = os.path.join(seed_dir or "",
                          "candidate_5v_link.result.json")
    derivation = os.path.join(seed_dir or "", "derivation.json")
    return {
        "validate_candidate.py": {
            "text_path": os.path.abspath(__file__)},
        "generate_candidate.py": {
            "text_path": os.path.join(
                HERE, "generate_candidate.py")},
        "toolkit.extract": {"text_path": os.path.join(
            TOOLKIT_ROOT, "pcbqa", "extract.py")},
        "toolkit.connectivity": {"text_path": os.path.join(
            TOOLKIT_ROOT, "pcbqa", "connectivity.py")},
        "toolkit.benchmark": {"text_path": os.path.join(
            TOOLKIT_ROOT, "pcbqa", "benchmark.py")},
        "toolkit.progression": {"text_path": os.path.join(
            TOOLKIT_ROOT, "pcbqa", "progression.py")},
        "toolkit.netlist_contract": {"text_path": os.path.join(
            TOOLKIT_ROOT, "pcbqa", "netlist_contract.py")},
        "candidate_board": {"text_path": candidate_board_path},
        "authoritative_board": {"text_path": os.path.join(
            REPO, "microphone_array_v2.kicad_pcb")},
        "constraints": {"json_path": os.path.join(
            HERE, "constraints.json")},
        "zone_policy": {"json_path": os.path.join(
            HERE, "zone_policy.json")},
        "schema": {"text": benchmark.SCHEMA_VERSION},
        # The derivation and the 5V evidence are INPUTS to the
        # decision; an absent result is part of the identity too,
        # so producing it later makes the decision honestly stale.
        "derivation": (
            {"json_path": derivation}
            if os.path.isfile(derivation) else {"text": "absent"}),
        "candidate_5v_result": (
            {"json_path": five_v} if os.path.isfile(five_v)
            else {"text": "absent"}),
    }


def gates_closure_components(candidate_board_path,
                             seed_dir=None):
    """The gate artifact's own dependency set: the toolkit
    validation's inputs. The 5V evidence and the derivation are
    deliberately absent - the gates read neither, so their later
    appearance must not stale the gate record."""
    components = closure_components(candidate_board_path, seed_dir)
    for absent in ("candidate_5v_result", "derivation"):
        components.pop(absent, None)
    return components


def decision_closure_components(candidate_board_path,
                                seed_dir=None):
    """The decision's dependency set: the base components plus the
    upstream ARTIFACTS the decision consumes - gates and metrics -
    by canonical content. A regenerated gates.json moves the
    decision; a regenerated decision moves the search, which names
    decisions the same way: transitive freshness, link by link."""
    components = closure_components(candidate_board_path, seed_dir)
    for name, basename in (
            ("gates_artifact", "gates.json"),
            ("metrics_artifact", "candidate_metrics.json")):
        path = os.path.join(seed_dir or "", basename)
        components[name] = (
            {"json_path": path} if os.path.isfile(path)
            else {"text": "absent"})
    return components


def producer_closure(candidate_board_path, seed_dir=None):
    return freshness.closure(
        closure_components(candidate_board_path, seed_dir))


def complete_path_metrics(delay_measurements, board_sha):
    """Typed metrics for the COMPLETE PDM clock paths, from the
    timing gate's own path records (U2 output -> Y copper -> RCn
    traversal -> B copper -> microphone clock pad). Both boards use
    the identical manifest path definitions; each leaf keeps its own
    path identity. The spread is measured only over a complete set
    of sixteen resolved paths."""
    definitions = extract.METRIC_DEFINITIONS
    metrics = []
    lengths = {}
    for path in delay_measurements.get("paths", []):
        if path.get("interface") != "pdm_clock":
            continue
        name = "path:{}->{}".format(path["path"],
                                    path["destination"])
        length = path.get("copper_length_mm")
        if length is None:
            metrics.append(benchmark.unmeasured(
                name + ":copper_length_mm", "electrical-path",
                definitions["complete_path_copper_length_mm"],
                "path-resolution",
                "the timing gate did not resolve this path's "
                "copper"))
            continue
        lengths[name] = length
        metrics.append(benchmark.measured(
            name + ":copper_length_mm", "electrical-path",
            definitions["complete_path_copper_length_mm"],
            round(length, 6), "mm", "geometry-derived",
            {"source": "TIMING.INTERCONNECT_DELAY path records",
             "board_file_sha256": board_sha},
            "complete driver-to-microphone clock path copper, "
            "series-resistor traversal included by the path "
            "definition"))
    if len(lengths) == 16:
        values = sorted(lengths.values())
        metrics.append(benchmark.measured(
            "complete_path_spread_mm", "board",
            definitions["complete_path_spread_mm"],
            round(values[-1] - values[0], 6), "mm",
            "geometry-derived",
            {"definition": "max minus min complete PDM clock path "
                           "copper over all sixteen paths",
             "board_file_sha256": board_sha},
            "a copper-length spread over COMPLETE electrical "
            "paths; not a delay or skew claim"))
    else:
        metrics.append(benchmark.unmeasured(
            "complete_path_spread_mm", "board",
            definitions["complete_path_spread_mm"],
            "path-resolution",
            "only {} of 16 PDM paths resolved; a spread over a "
            "partial set would be fiction".format(len(lengths))))
    return metrics

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
                               "PCBA_AutoDesignAndTest"))
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
            "clock_leaf_net_length_spread_mm", "board",
            definitions["clock_leaf_net_length_spread_mm"],
            round(max(leaves) - min(leaves), 6), "mm",
            "geometry-derived",
            {"definition": "max minus min PER-NET copper inventory "
                           "over the 16 PDM clock leaf nets",
             "board_file_sha256": sha},
            "a per-NET copper inventory spread; NOT the spread of "
            "the complete clock paths across the series resistors "
            "- that is complete_path_spread_mm"))
    else:
        metrics.append(benchmark.unmeasured(
            "clock_leaf_net_length_spread_mm", "board",
            definitions["clock_leaf_net_length_spread_mm"],
            "routing",
            "not every PDM clock leaf is connectivity-complete; a "
            "spread over a partial set would be fiction"))
    return sha, metrics, connectivity


def make_candidate_gerbers(seed_dir, seed, board_basename):
    """Export the candidate's own gerbers and drills with the
    manifest's exact declared flags. The gerber-reading gates then
    measure THIS candidate's geometry."""
    live = json.load(open(os.path.join(REPO, "board",
                                       "manifest.live.json"),
                          encoding="utf-8"))
    board_path = os.path.join(seed_dir, board_basename)
    gerber_dir = os.path.join(seed_dir, "generated", "release",
                              "gerbers")
    os.makedirs(gerber_dir, exist_ok=True)
    for stale in os.listdir(gerber_dir):
        os.remove(os.path.join(gerber_dir, stale))
    # Same resolution as everywhere else; this was an inline absolute
    # Windows path that the Linux migration did not reach.
    from pcbqa import preflight
    kicad_cli = preflight.resolve_tool(json.load(open(os.path.join(
        REPO, "board", "toolchain.json")))["kicad"]["cli"])
    outcomes = {}
    completed = subprocess.run(
        [kicad_cli, "pcb", "export", "gerbers", "-o",
         gerber_dir + os.sep]
        + live["artifacts"]["gerber_export_flags"]
        + [board_path],
        capture_output=True, text=True, timeout=600)
    outcomes["gerbers"] = completed.returncode
    completed = subprocess.run(
        [kicad_cli, "pcb", "export", "drill", "-o",
         gerber_dir + os.sep]
        + live["release_generation"]["drill"]["flags"]
        + [board_path],
        capture_output=True, text=True, timeout=600)
    outcomes["drill"] = completed.returncode
    outcomes["files"] = sorted(os.listdir(gerber_dir))
    return outcomes


def derive_manifest(seed_dir, seed, board_basename):
    live = json.load(open(os.path.join(REPO, "board",
                                       "manifest.live.json"),
                          encoding="utf-8"))
    manifest = copy.deepcopy(live)
    candidate_gerbers = os.path.join(seed_dir, "generated",
                                     "release", "gerbers")
    if os.path.isdir(candidate_gerbers) and \
            os.listdir(candidate_gerbers):
        manifest["artifacts"] = dict(manifest["artifacts"])
        manifest["artifacts"]["gerber_dir"] = (
            "benchmark/boardB/candidates/seed{:02d}/generated/"
            "release/gerbers".format(seed))
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
                               "PCBA_AutoDesignAndTest", "run.py")
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
        return outcome, None, None
    with open(attempts[-1], encoding="utf-8") as handle:
        return outcome, json.load(handle), os.path.relpath(
            attempts[-1], REPO)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--skip-validate", action="store_true")
    parser.add_argument("--make-artifacts", action="store_true",
                        help="export the candidate's own gerbers "
                             "and drills before validating, so the "
                             "gerber gates measure the candidate")
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
                      if record["stage"] in ("post_quench_policy",
                                             "reuse_revalidation")]
    policy_ok = bool(policy_records) and \
        policy_records[-1]["policy_ok"]

    # The fabrication verdict the DECISION consumes is computed
    # HERE, on the exact bytes being decided - never inherited from
    # a stage record that some later write may have outrun. The raw
    # candidate bytes and the canonical zone-refilled copy the DRC
    # judged are two declared identities, bound side by side.
    raw_board_sha = _sha256_file(board_file)
    # The declared rules must have travelled with the artifact: a
    # candidate whose sibling .kicad_pro is not the authoritative
    # project would be judged - by the gates and by any DRC - at
    # floors some tool wrote for its own convenience. Refuse, never
    # judge at undeclared floors.
    sibling_pro = os.path.splitext(board_file)[0] + ".kicad_pro"
    authoritative_pro = os.path.join(
        REPO, "microphone_array_v2.kicad_pro")
    if not os.path.isfile(sibling_pro) or \
            _sha256_file(sibling_pro) != _sha256_file(
                authoritative_pro):
        raise SystemExit(
            "the candidate's sibling .kicad_pro is not the "
            "authoritative project; the declared rules did not "
            "travel with this artifact and every judgment would "
            "be at undeclared floors - refusing to validate")
    import generate_candidate as generator
    direct_verdict = generator.stage_fabrication_check(
        board_file, os.path.join(seed_dir, "stages",
                                 "fabcheck-decision"))
    fabrication_geometry = {
        "ok": direct_verdict.get("ok", "unknown"),
        "detail": ("direct kicad-cli DRC at the declared rules on "
                   "this exact candidate; raw sha {}, judged "
                   "refilled-copy sha {}; violations: {}".format(
                       raw_board_sha[:12],
                       str(direct_verdict.get(
                           "judged_board_sha256"))[:12],
                       json.dumps(direct_verdict.get(
                           "violations_by_type", {}))))[:400],
    }
    fabrication_identity = {
        "raw_board_sha256": raw_board_sha,
        "judged_board_sha256": direct_verdict.get(
            "judged_board_sha256"),
        "verdict_ok": direct_verdict.get("ok", "unknown"),
        "meaning": "raw is the candidate file as decided on; "
                   "judged is the canonical zone-refilled working "
                   "copy the DRC measured - two declared "
                   "identities, never conflated",
    }

    from pcbqa import geom as geom_module
    from pcbqa.connectivity import classify_net
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
    import pcbnew as pcbnew_module
    whole_board = pcbnew_module.LoadBoard(board_file)
    # Required connectivity comes from the AUTHORITATIVE product
    # intent: the candidate never defines its own denominator, so a
    # dropped footprint or a lost net shrinks nothing - it shows up
    # as parity findings and incomplete required nets instead.
    authoritative_contract = netlist_contract.contract_from_board(
        pcbnew_module.LoadBoard(os.path.join(
            REPO, "microphone_array_v2.kicad_pcb")))
    candidate_contract = netlist_contract.contract_from_board(
        whole_board)
    parity = netlist_contract.compare(authoritative_contract,
                                      candidate_contract)
    required_nets = netlist_contract.required_nets(
        authoritative_contract)
    board_connectivity = {}
    for net in required_nets:
        board_connectivity[net] = classify_net(
            whole_board, net,
            geom_module.pad_copper_polygon)["class"]
    board_complete = sum(
        1 for state in board_connectivity.values()
        if state == "connectivity-complete")
    closure_record = producer_closure(board_file, seed_dir)
    toolkit_commit = subprocess.run(
        ["git", "-C", os.path.join(REPO, "tooling",
                                   "PCBA_AutoDesignAndTest"),
         "rev-parse", "HEAD"], capture_output=True,
        text=True).stdout.strip() or "unknown"
    report = benchmark.report(
        {"board_file_sha256": sha, "toolkit_commit": toolkit_commit,
         "physical_evidence": evidence,
         "schema_version": benchmark.SCHEMA_VERSION}, metrics)
    metrics_inputs = freshness.closure(dict(
        closure_components(board_file, seed_dir), **{
            "baseline_net_inventory": {"json_path": os.path.join(
                REPO, "benchmark", "board_a_baseline.json")},
            "physical_construction": {
                "digest": evidence["digest"]},
            "extraction_semantics": {
                "text": extract.EXTRACT_VERSION},
            "geometry_profile": {"json_path": os.path.join(
                REPO, "board", "manifest.live.json")},
        }))
    with open(os.path.join(seed_dir, "candidate_metrics.json"),
              "w", encoding="utf-8", newline="\n") as handle:
        json.dump({"report": report,
                   "producer_closure": metrics_inputs}, handle,
                  indent=1)
        handle.write("\n")

    complete_nets = sorted(
        net for net, state in connectivity.items()
        if state == "connectivity-complete")
    critical_clock_connected = all(
        board_connectivity.get(net) == "connectivity-complete"
        for net in CLOCK_NETS)
    measured_nets = complete_nets
    copper_total = round(sum(
        metric["value"] for metric in metrics
        if metric["name"].endswith(":copper_length_mm")
        and metric["status"] == "measured"), 3)

    artifact_outcomes = None
    if arguments.make_artifacts:
        artifact_outcomes = make_candidate_gerbers(
            seed_dir, arguments.seed,
            os.path.basename(board_file))
    validation_summary = None
    if arguments.skip_validate:
        gates_path = os.path.join(seed_dir, "gates.json")
        if os.path.isfile(gates_path):
            with open(gates_path, encoding="utf-8") as handle:
                gates_doc = json.load(handle)
            recorded_gates_closure = gates_doc.get(
                "producer_closure")
            if recorded_gates_closure is None:
                raise SystemExit(
                    "gates.json carries no producer closure; "
                    "unverifiable gate evidence is refused")
            gates_verdict = freshness.verify(
                recorded_gates_closure,
                gates_closure_components(board_file, seed_dir))
            if not gates_verdict["fresh"]:
                raise SystemExit(
                    "gates.json is stale (moved: {}); a "
                    "skip-validate decision must not inherit gate "
                    "verdicts its inputs have outgrown - rerun "
                    "the full validation".format(
                        gates_verdict["moved"]
                        + gates_verdict["missing"]
                        + gates_verdict["added"]))
            if gates_doc.get("candidate_board_sha256") \
                    != raw_board_sha:
                raise SystemExit(
                    "gates.json was measured on different board "
                    "bytes ({}... vs {}...); refusing to mix "
                    "verdicts across candidates".format(
                        str(gates_doc.get(
                            "candidate_board_sha256"))[:12],
                        raw_board_sha[:12]))
            validation_summary = gates_doc["statuses"][
                "validation"]
    if not arguments.skip_validate:
        manifest_path, board_id = derive_manifest(
            seed_dir, arguments.seed,
            os.path.basename(board_file))
        outcome, validation, validation_attempt_path = \
            run_validation(manifest_path, board_id,
                           arguments.validate_timeout)
        if validation is not None:
            validation_identity = {
                "attempt_path": validation_attempt_path,
                "canonical_sha256":
                    freshness.canonical_json_digest(validation),
                "implementation": (validation.get("tooling", {})
                                   .get(
                    "validation_implementation")),
                "meaning": "the exact validation artifact these "
                           "gate truths were lifted from, by "
                           "canonical content identity, with the "
                           "implementation that produced it",
            }
            gates = validation.get("gates", [])
            failing = sorted(
                gate["gate"] for gate in gates
                if gate.get("status") not in
                ("PASS", "NOT_APPLICABLE", "ADVISORY"))
            validation_summary = {
                "validation_artifact": validation_identity,
                "verdict": validation["summary"]["verdict"],
                "counts": validation["summary"]["counts"],
                "blocking": validation["summary"].get("blocking",
                                                      []),
                "failing_gates": failing,
                "gate_status": {gate["gate"]: gate.get("status")
                                for gate in gates},
                "timing_delay_measurements": next(
                    (gate.get("measurements") for gate in gates
                     if gate.get("gate")
                     == "TIMING.INTERCONNECT_DELAY"), None),
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
    if not arguments.skip_validate:
        with open(os.path.join(seed_dir, "gates.json"), "w",
                  encoding="utf-8", newline="\n") as handle:
            json.dump({
                "kind": "candidate-gate-status",
                "candidate_board_sha256": sha,
                "manifest": os.path.basename(manifest_path),
                "producer_closure": freshness.closure(
                    gates_closure_components(board_file,
                                             seed_dir)),
                "statuses": {
                    "placement_policy_satisfied": policy_ok,
                    "benchmark_net_completion": {
                        "complete": len(complete_nets),
                        "total": len(nets),
                        "connectivity": connectivity},
                    "board_required_net_completion": {
                        "complete": board_complete,
                        "total": len(required_nets),
                        "not_complete": {
                            net: state for net, state in sorted(
                                board_connectivity.items())
                            if state != "connectivity-complete"}},
                    "critical_clock_nets_connected":
                        critical_clock_connected,
                    "netlist_parity": parity,
                    "fabrication_geometry": fabrication_geometry,
                    "fabrication_identity": fabrication_identity,
                    "candidate_derived_artifacts":
                        artifact_outcomes,
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

    gate_status = (validation_summary or {}).get(
        "gate_status") or {}

    def _gate_truth(name):
        status = gate_status.get(name)
        if status is None:
            return "unknown"
        return status in ("PASS", "ADVISORY")

    evaluated = bool(gate_status)
    failing = (validation_summary or {}).get("failing_gates") or []
    quality_names = {"NET.TOPOLOGY", "ROUTE.GEOMETRY_HYGIENE"}
    blocking_failing = sorted(
        gate for gate in failing
        if GATE_FAILURE_CLASSES.get(gate) == "candidate-design"
        and gate not in quality_names)
    quality_failing = sorted(gate for gate in failing
                             if gate in quality_names)
    parity_failing = sorted(
        gate for gate in failing
        if GATE_FAILURE_CLASSES.get(gate, "unclassified")
        != "candidate-design")
    # Evidence AVAILABILITY ("can I trust this run?") and
    # requirement OUTCOME ("did the design satisfy something?")
    # are separate truths. This board declares NO electrical
    # requirement in time or volts (constraints.json expresses its
    # clock intent geometrically), so the 5V assertion is
    # DESCRIPTIVE: its verdict is recorded and compared, and
    # electrical_requirements stays at zero applicable - a
    # requirement is never invented to have something to score.
    usable_results = 0
    five_v_verdict = None
    five_v_claimable = None
    five_v_path = os.path.join(seed_dir,
                               "candidate_5v_link.result.json")
    if os.path.isfile(five_v_path):
        with open(five_v_path, encoding="utf-8") as handle:
            five_v = json.load(handle)
        policy = five_v.get("result", {}).get("result_policy", {})
        if policy.get("usable_for_design_decision"):
            usable_results = 1
        five_v_claimable = policy.get("assertions_claimable")
        five_v_verdict = (five_v.get("result", {})
                          .get("measurements", {})
                          .get("vout", {}).get("verdict"))
    electrical_requirements = {"applicable": 0, "passed": 0,
                               "failed": 0, "unresolved": 0}
    parity_counts = {
        key: len(parity[key]) for key in (
            "missing_footprints", "added_footprints",
            "missing_pads", "added_pads", "missing_nets",
            "unexpected_nets")}
    parity_counts["changed_assignments"] = len(
        parity["changed_assignments"])
    assessment = progression.assess({
        "netlist_parity": {
            "ok": parity["ok"],
            "detail": "authoritative Board A netlist contract vs "
                      "candidate pad-net map; differences: "
                      + json.dumps(parity_counts,
                                   sort_keys=True)},
        "placement_policy_ok": policy_ok,
        "critical": {
            "nets_connected": critical_clock_connected,
            "paths_resolved":
                _gate_truth("TIMING.PATH_INTEGRITY"),
            "topology_valid": _gate_truth("NET.TOPOLOGY"),
        },
        "board_required_connectivity": {
            "complete": board_complete,
            "total": len(required_nets)},
        "benchmark_connectivity": {
            "complete": len(complete_nets), "total": len(nets)},
        "fabrication_geometry": fabrication_geometry,
        "blocking_gates": {"evaluated": evaluated,
                           "failing": blocking_failing},
        "quality_gates": {"evaluated": evaluated,
                          "failing": quality_failing},
        "electrical_requirements": electrical_requirements,
        "electrical_evidence": {"usable_results": usable_results},
        "optimization": {
            "measured_copper_total_mm": copper_total},
    })
    reasons = []
    if assessment["progress_class"] != "optimization":
        stopped = assessment["classes"][
            assessment["progress_class"]]
        reasons.append("progression stops at {}: {}".format(
            assessment["progress_class"],
            json.dumps({key: value for key, value in
                        stopped.items()
                        if key != "detail"})[:400]))
    decision = {
        "kind": "candidate-decision",
        "candidate": "seed{:02d}".format(arguments.seed),
        "board_file_sha256": sha,
        "assessment": {
            "classes": assessment["classes"],
            "progress_class": assessment["progress_class"],
            "fully_connected": assessment["fully_connected"],
            "rank_key": list(assessment["rank_key"]),
        },
        "components": {
            "netlist_parity_ok": parity["ok"],
            "netlist_parity_differences": parity_counts,
            "fabrication_identity": fabrication_identity,
            "benchmark_net_completion": {
                "complete": len(complete_nets),
                "total": len(nets)},
            "board_required_net_completion": {
                "complete": board_complete,
                "total": len(required_nets)},
            "critical_clock_nets_connected":
                critical_clock_connected,
            "critical_paths_resolved":
                _gate_truth("TIMING.PATH_INTEGRITY"),
            "critical_topology_valid":
                _gate_truth("NET.TOPOLOGY"),
            "fabrication_geometry_ok": fabrication_geometry["ok"],
            "board_not_complete": {
                net: state for net, state in sorted(
                    board_connectivity.items())
                if state != "connectivity-complete"},
            "placement_policy_ok": policy_ok,
            "validation_verdict":
                (validation_summary or {}).get("verdict"),
            "parity_failures_missing_candidate_artifacts":
                parity_failing,
            "measured_copper_total_mm": copper_total,
            "five_v_assertion": {
                "verdict": five_v_verdict,
                "assertions_claimable": five_v_claimable,
                "requirement_linked": False,
                "meaning": "descriptive evidence: no declared "
                           "board requirement consumes this "
                           "assertion, so its verdict ranks "
                           "nothing and blocks nothing - it is "
                           "recorded for A/B comparison and "
                           "honesty, never laundered into "
                           "usability",
            },
            "electrical_requirements": electrical_requirements,
            "measured_net_set_sha256": hashlib.sha256(
                json.dumps(measured_nets).encode(
                    "utf-8")).hexdigest(),
        },
        "decision": "accept-for-comparison"
        if assessment["accept_for_comparison"] else "reject",
        "search_winner_eligible":
            assessment["search_winner_eligible"],
        "candidate_ready_for_next_stage":
            assessment["candidate_ready_for_next_stage"],
        "reasons": reasons or ["every correctness class up to the "
                               "quality gates passes"],
        "next_design_action": "the progress class names the "
                              "failing correctness class; its "
                              "detail names the design variable",
        # The decision's OWN closure names the artifacts it
        # consumed (gates, metrics) canonically, on top of the base
        # inputs - the transitive link the search verifies.
        "producer_closure": freshness.closure(
            decision_closure_components(board_file, seed_dir)),
    }
    with open(os.path.join(seed_dir, "decision.json"), "w",
              encoding="utf-8", newline="\n") as handle:
        json.dump(decision, handle, indent=1)
        handle.write("\n")
    print(json.dumps({
        "parity": parity["ok"],
        "board": decision["components"][
            "board_required_net_completion"],
        "benchmark": decision["components"][
            "benchmark_net_completion"],
        "critical": [critical_clock_connected,
                     decision["components"][
                         "critical_paths_resolved"],
                     decision["components"][
                         "critical_topology_valid"]],
        "fabrication": fabrication_geometry["ok"]}))
    print("decision:", decision["decision"],
          "| ready:", decision["candidate_ready_for_next_stage"],
          "| progress:", assessment["progress_class"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
