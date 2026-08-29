"""Parasitic evidence for Board A and one candidate, through one
implementation, under the toolkit's parasitic result contract.

Produced today (each with its honest semantics):

  * interconnect_dc  - the 5V link path resistance from the shared
    5V evidence artifact: exact when nothing was omitted, a lower
    bound when via barrels were, an interval-floored lower bound
    when junction ambiguity applies;
  * propagation_delay - per complete PDM path, a LOWER bound (the
    copper is modelled from the evidenced stackup; the crossed
    series resistor's traversal is an unmodelled positive
    contribution, so the upper side is open), plus one APPROXIMATE
    copper-nominal spread whose declared assumption is that the
    unmodelled traversal contributions are equal across endpoints;
  * coupling         - the geometry-only parallelism inventory over
    the PDM clock and data nets, exact millimetres of coupled run,
    explicitly not a crosstalk voltage.

Blocked, and recorded as blocked: characteristic impedance (no
declared target consumes it and descriptive solving is not yet
wired), loop inductance and PDN impedance (no supported model).

No requirement linkage is emitted: this board declares no
electrical requirement in volts, ohms or picoseconds, and a
requirement is never invented to score against. Every metric here
is descriptive A/B evidence.

Run with KiCad's python from the repository root:

    ".../kicad/python.exe" benchmark/boardB/parasitics_report.py \
        --seed 14
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.environ.get("PCB_TOOLKIT_PATH")
                or os.path.join(REPO, "tooling",
                                "PCB_AutoDesignAndTest"))

from pcbqa import headless                        # noqa: E402
headless.suppress_blocking_ui()
from pcbqa import coupling_geometry, freshness     # noqa: E402
from pcbqa import parasitics                       # noqa: E402

TOOLKIT_ROOT = (os.environ.get("PCB_TOOLKIT_PATH")
                or os.path.join(REPO, "tooling",
                                "PCB_AutoDesignAndTest"))

COUPLING_THRESHOLD_MM = 0.30
COUPLING_NETS = (["MCLK_OSC", "AUDIO_MCLK", "PDM_CLK_IN"]
                 + ["PDM_CLK_Y{}".format(i) for i in range(8)]
                 + ["PDM_CLK_B{}".format(i) for i in range(8)]
                 + ["PDM_D{}".format(i) for i in range(8)])


def dc_record(board_label, outcome):
    path = outcome["path"]
    bound_kind = path["resistance_bound"]
    resistance = path["resistance_ohm"]
    uncertainty = path.get("resistance_uncertainty_ohm", 0.0)
    omissions = list(path.get("omissions", []))
    if bound_kind == "exact":
        quantity = {"semantics": "exact", "value": resistance,
                    "bound": None, "interval": None,
                    "units": "ohm"}
        omitted = []
    else:
        # 'lower': omissions only. 'uncertain': the junction
        # ambiguity lowers the floor; the via omissions keep the
        # ceiling open. Either way the honest one-sided claim is
        # the same shape: true resistance >= floor.
        floor = resistance - (uncertainty
                              if bound_kind == "uncertain" else 0.0)
        quantity = {"semantics": "bound", "value": None,
                    "bound": {"direction": "lower",
                              "value": round(floor, 9)},
                    "interval": None, "units": "ohm"}
        omitted = omissions
    return parasitics.validate_metric({
        "kind": "parasitic-metric",
        "phenomenon": "interconnect_dc",
        "scope": {"level": "path",
                  "identity": "{}:{}->{}".format(
                      board_label, path["from_pad"],
                      path["to_pad"])},
        "quantity": quantity,
        "model": {"name": "traversal-series-dc",
                  "fidelity": "geometry-derived"},
        "provenance": {
            "source": "candidate_5v_link.result.json path record",
            "board_file_sha256": outcome["board_file_sha256"],
            "path_length_mm": path["path_length_mm"],
            "via_count": path["via_count_in_path"]},
        "assumptions": [],
        "omitted_contributions": omitted,
        "applicability": {
            "applicable": True,
            "detail": "two-terminal traversal with alternate "
                      "copper refused by bridge analysis"},
        "requirement_linkage": None,
        "decision_significance": "descriptive A/B evidence; no "
                                 "declared requirement consumes "
                                 "it",
    })


def delay_records(board_label, paths):
    records = []
    blocked = []
    nominals = {}
    for path in paths:
        identity = "{}:{}->{}".format(board_label, path["source"],
                                      path["destination"])
        if path.get("delay_lower_ps") is None:
            blocked.append(parasitics.blocked(
                "propagation_delay", "path", identity,
                "no derivable delay: " + json.dumps(
                    path.get("insufficient") or
                    "unresolved path")[:300],
                "an established stackup figure or a resolved "
                "path"))
            continue
        omitted = [
            "{}: {}".format(t["reference"], t["reason"])
            for t in path.get("component_traversals", [])
            if t.get("model_status") == "unmodelled"]
        records.append(parasitics.validate_metric({
            "kind": "parasitic-metric",
            "phenomenon": "propagation_delay",
            "scope": {"level": "path", "identity": identity},
            "quantity": {"semantics": "bound", "value": None,
                         "bound": {"direction": "lower",
                                   "value": path[
                                       "delay_lower_ps"]},
                         "interval": None, "units": "ps"},
            "model": {"name": "hammerstad-analytic",
                      "fidelity": path.get("fidelity",
                                           "unrecorded")},
            "provenance": {
                "source": "TIMING.INTERCONNECT_DELAY path record",
                "copper_length_mm": path["copper_length_mm"],
                "nominal_delay_ps": path.get("delay_ps"),
                "length_uncertainty_mm": path.get(
                    "length_uncertainty_mm")},
            "assumptions": list(path.get("assumptions", [])),
            "omitted_contributions": omitted or [
                "none declared"],
            "applicability": {
                "applicable": True,
                "detail": "copper modelled from the evidenced "
                          "stackup; the upper side is open while "
                          "any traversal is unmodelled"},
            "requirement_linkage": None,
            "decision_significance": "descriptive A/B evidence; "
                                     "no requirement is expressed "
                                     "in picoseconds",
        }))
        if path.get("delay_ps") is not None:
            nominals[identity] = path["delay_ps"]
    spread = None
    if len(nominals) == 16 and not blocked:
        values = sorted(nominals.values())
        spread = parasitics.validate_metric({
            "kind": "parasitic-metric",
            "phenomenon": "propagation_delay",
            "scope": {"level": "group",
                      "identity": board_label
                      + ":pdm-16-path-copper-nominal-spread"},
            "quantity": {"semantics": "approximate",
                         "value": round(values[-1] - values[0],
                                        4),
                         "bound": None, "interval": None,
                         "units": "ps"},
            "model": {"name": "hammerstad-analytic",
                      "fidelity": "copper-nominal"},
            "provenance": {
                "source": "TIMING.INTERCONNECT_DELAY path "
                          "records, nominal copper delays"},
            "assumptions": [
                "the unmodelled series-resistor traversal "
                "contributions are equal across all sixteen "
                "endpoints, so they cancel in the spread"],
            "omitted_contributions": [],
            "applicability": {
                "applicable": True,
                "detail": "all sixteen paths carry nominal "
                          "copper delays"},
            "requirement_linkage": None,
            "decision_significance": "descriptive A/B evidence "
                                     "under a declared "
                                     "cancellation assumption; "
                                     "never a skew claim",
        })
    return records, blocked, spread


def board_records(board_label, board_file, five_v_outcome,
                  delay_paths):
    import pcbnew
    from pcbqa import geom
    records = []
    blocked = []
    if five_v_outcome and "path" in five_v_outcome:
        records.append(dc_record(board_label, five_v_outcome))
    delay, delay_blocked, spread = delay_records(board_label,
                                                 delay_paths)
    records.extend(delay)
    blocked.extend(delay_blocked)
    if spread is not None:
        records.append(spread)
    board = pcbnew.LoadBoard(board_file)
    for metric in coupling_geometry.parallelism_inventory(
            board, COUPLING_NETS, COUPLING_THRESHOLD_MM,
            layers=["F.Cu", "B.Cu"]):
        metric["scope"]["identity"] = (
            board_label + ":" + metric["scope"]["identity"])
        records.append(parasitics.validate_metric(metric))
    for phenomenon, reason, needed in (
            ("characteristic_impedance",
             "no declared impedance target consumes it, and "
             "descriptive solving of routed geometry is not yet "
             "wired to candidates",
             "a declared target with tolerance, or descriptive "
             "solve wiring"),
            ("loop_inductance",
             "no source-supported loop inductance model exists in "
             "the toolkit",
             "a quasi-static extractor with a stated domain"),
            ("power_integrity",
             "no PDN impedance model exists; DC path resistance "
             "is covered under interconnect_dc",
             "a source-supported PDN model")):
        blocked.append(parasitics.blocked(
            phenomenon, "board", board_label, reason, needed))
    return records, blocked


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, required=True)
    arguments = parser.parse_args()
    seed_name = "seed{:02d}".format(arguments.seed)
    seed_dir = os.path.join(HERE, "candidates", seed_name)
    from pcbqa import geom
    manifest_doc = json.load(io.open(
        os.path.join(REPO, "board", "manifest.live.json"),
        encoding="utf-8"))
    geom.configure(manifest_doc["geometry_profile"]["tolerances"][
        "polygon_chord_error_mm"]["value"])

    five_v_path = os.path.join(seed_dir,
                               "candidate_5v_link.result.json")
    five_v = json.load(io.open(five_v_path, encoding="utf-8")) \
        if os.path.isfile(five_v_path) else {"outcomes": {}}

    release_validation_path = os.path.join(
        REPO, "generated", "release", "validation.json")
    release_validation = json.load(io.open(
        release_validation_path, encoding="utf-8"))
    board_a_paths = next(
        (g["measurements"]["paths"]
         for g in release_validation["gates"]
         if g["gate"] == "TIMING.INTERCONNECT_DELAY"), [])

    gates_path = os.path.join(seed_dir, "gates.json")
    gates_doc = json.load(io.open(gates_path, encoding="utf-8"))
    candidate_delay = ((gates_doc["statuses"].get("validation")
                        or {}).get("timing_delay_measurements")
                       or {})
    candidate_paths = candidate_delay.get("paths") or []

    candidate_board = None
    for basename in ("candidate_routed.kicad_pcb",
                     "candidate_placed.kicad_pcb"):
        path = os.path.join(seed_dir, basename)
        if os.path.isfile(path):
            candidate_board = path
            break

    a_records, a_blocked = board_records(
        "board_a",
        os.path.join(REPO, "microphone_array_v2.kicad_pcb"),
        five_v["outcomes"].get("board_a"), board_a_paths)
    b_records, b_blocked = board_records(
        "board_b", candidate_board,
        five_v["outcomes"].get("board_b"), candidate_paths)

    def keyed(records, label):
        prefix = label + ":"
        table = {}
        for record in records:
            identity = record["scope"]["identity"]
            assert identity.startswith(prefix)
            table[identity[len(prefix):]] = record
        return table

    a_table = keyed(a_records, "board_a")
    b_table = keyed(b_records, "board_b")
    compared = []
    incomparable = []
    for identity in sorted(set(a_table) & set(b_table)):
        one, other = a_table[identity], b_table[identity]
        try:
            parasitics.require_comparable(one, other)
        except parasitics.ParasiticsError as error:
            incomparable.append({"identity": identity,
                                 "reason": str(error)})
            continue
        def magnitude(record):
            quantity = record["quantity"]
            if quantity["value"] is not None:
                return quantity["value"]
            return quantity["bound"]["value"]
        compared.append({
            "identity": identity,
            "phenomenon": one["phenomenon"],
            "semantics": one["quantity"]["semantics"],
            "units": one["quantity"]["units"],
            "a": magnitude(one),
            "b": magnitude(other),
            "delta_b_minus_a": round(
                magnitude(other) - magnitude(one), 6),
        })

    document = {
        "kind": "parasitics-ab",
        "candidate": seed_name,
        "board_a": {"metrics": a_records, "blocked": a_blocked},
        "board_b": {"metrics": b_records, "blocked": b_blocked},
        "comparison": {
            "compared": compared,
            "incomparable": incomparable,
            "a_only": sorted(set(a_table) - set(b_table)),
            "b_only": sorted(set(b_table) - set(a_table)),
            "meaning": "pairs compare only under "
                       "require_comparable (same phenomenon, "
                       "scope level, units, semantics, model and "
                       "fidelity); everything else is named, "
                       "never averaged in",
        },
        "producer_closure": freshness.closure({
            "parasitics_report.py": {
                "text_path": os.path.abspath(__file__)},
            "toolkit.parasitics": {"text_path": os.path.join(
                TOOLKIT_ROOT, "pcbqa", "parasitics.py")},
            "toolkit.coupling_geometry": {
                "text_path": os.path.join(
                    TOOLKIT_ROOT, "pcbqa",
                    "coupling_geometry.py")},
            "board_a_release_validation": {
                "json_path": release_validation_path},
            "gates_artifact": {"json_path": gates_path},
            "candidate_5v_result": (
                {"json_path": five_v_path}
                if os.path.isfile(five_v_path)
                else {"text": "absent"}),
            "candidate_board": {"text_path": candidate_board},
            "authoritative_board": {"text_path": os.path.join(
                REPO, "microphone_array_v2.kicad_pcb")},
        }),
    }
    out_path = os.path.join(seed_dir, "parasitics_ab.json")
    with io.open(out_path, "w", encoding="utf-8",
                 newline="\n") as handle:
        json.dump(document, handle, indent=1)
        handle.write("\n")
    print("A metrics:", len(a_records), "| blocked:",
          len(a_blocked))
    print("B metrics:", len(b_records), "| blocked:",
          len(b_blocked))
    print("compared pairs:", len(compared),
          "| incomparable:", len(incomparable))
    for pair in compared[:6]:
        print(" ", pair["identity"], pair["semantics"],
              "A", pair["a"], pair["units"], "| B", pair["b"],
              "| dB-A", pair["delta_b_minus_a"])
    print("report:", out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
