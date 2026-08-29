"""Derive each part's library-zero offset from JLCPCB's own footprint.

A placement file says how far to turn a part from the orientation the assembly
house holds it in. That orientation is a property of *their* library, not of
ours, and where the two disagree every instance of the part is fitted turned by
the difference. Guessing it from a package name is how a regulator ends up
backwards: JLC's SOT-23-5 and SOT-23-6 libraries do not share a zero, so any
table indexed by "SOT-23" is wrong for one of them.

So nothing here is guessed. For each LCSC number this fetches the footprint
JLCPCB actually uses - EasyEDA is their EDA and serves it - normalises the pads
into a top view in millimetres, and compares them pad number by pad number
against the same part's KiCad footprint as placed on this board. Each candidate
offset is scored by the worst angular disagreement over all pads, so the answer
is decided by every pin rather than by pin 1 alone: a pin-1 match cannot tell a
rotation from a mirror.

Two files are frozen per part under fabrication/jlc_orientation/. The response
body exactly as served is kept under raw/, and beside it a normalised extract
recording the source URL, the retrieval date, the response length and its
SHA-256. The extract is not the response and is never described as one: it is
derived from it, and every offline command re-derives it rather than trusting
it, so tampering with the raw payload, with the extraction, or with the
registry's offset all fail rather than pass.

    tools/jlc_orientation.py freeze [LCSC ...]   fetch and record the evidence
    tools/jlc_orientation.py report              score every frozen part
    tools/jlc_orientation.py check               non-zero if any offset moved
    tools/jlc_orientation.py check-live          frozen evidence vs JLC today

Only freeze and check-live use the network. report and check read the frozen
files alone, so the test suite and the clean release never reach upstream, and
JLC changing their library is reported as upstream drift rather than as
corruption of what is committed here.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import math
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _toolkit                                      # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(HERE, "fabrication", "jlc_orientation")
BOARD = os.path.join(HERE, "microphone_array_v2.kicad_pcb")
SOURCE = "https://easyeda.com/api/products/{lcsc}/components?version=6.4.19.5"

# EasyEDA stores footprint geometry in units of 10 mil, with the canvas Y axis
# running downward like a screen. Both facts are needed to read a pad position
# as a point in a top view.
UNIT_MM = 0.254
CANDIDATES = (0.0, 90.0, 180.0, 270.0)
# Two land patterns for one part differ in pad size and row spacing, so pad
# directions never agree exactly. They agree to a few degrees under the right
# rotation and are 90 or 180 out under any other, so the gap between best and
# runner-up is what decides, not the absolute error.
MAX_BEST_ERROR_DEG = 20.0
MIN_MARGIN_DEG = 45.0

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 "
                   "Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://easyeda.com/",
    "Origin": "https://easyeda.com",
}


# ---------------------------------------------------------------------------
# JLC's library, normalised
# ---------------------------------------------------------------------------

def fetch(lcsc, timeout=45):
    """The raw response body for one part, exactly as served."""
    url = SOURCE.format(lcsc=lcsc)
    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return url, response.read()


def extract(lcsc, raw):
    """Read a raw response body. {"mpn", "package", "pads"}, nothing else.

    Deliberately a pure function of the bytes: this is what makes the frozen
    extract checkable rather than merely present. EasyEDA's PAD record is a
    tilde-separated row whose third and fourth fields are the pad centre and
    whose ninth is the pad number. Y is negated here so that a larger value
    means further up, which is how every other coordinate in this project is
    read.
    """
    document = json.loads(raw.decode("utf-8"))
    result = document.get("result") or {}
    package = result.get("packageDetail") or {}
    data = package.get("dataStr") or {}
    shapes = data.get("shape") or []
    parameters = (data.get("head") or {}).get("c_para") or {}

    pads = {}
    for shape in shapes:
        if not shape.startswith("PAD~"):
            continue
        field = shape.split("~")
        number = field[8].strip()
        if not number:
            continue
        pads[number] = [round(float(field[2]) * UNIT_MM, 6),
                        round(-float(field[3]) * UNIT_MM, 6)]
    if not pads:
        raise ValueError("{}: the response carries no pads".format(lcsc))

    return {
        "mpn": result.get("title", "").strip(),
        "package": parameters.get("package", "").strip(),
        "pads": dict(sorted(pads.items(), key=_pad_sort)),
    }


def normalise(lcsc, url, raw, retrieved_utc=None):
    """The extract, plus where the bytes it came from were obtained."""
    fields = extract(lcsc, raw)
    return {
        "kind": "normalised extract",
        "note": "Derived from the raw response body, which is committed "
                "verbatim at raw_file. This file is not the response. Every "
                "offline command re-derives these pads from that file and "
                "fails on a disagreement.",
        "lcsc": lcsc,
        "mpn": fields["mpn"],
        "package": fields["package"],
        "source_url": url,
        "retrieved_utc": retrieved_utc or datetime.datetime.now(
            datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "raw_file": os.path.relpath(raw_path(lcsc), HERE).replace("\\", "/"),
        "raw_bytes": len(raw),
        "raw_sha256": hashlib.sha256(raw).hexdigest(),
        "units": "millimetres, top view, Y up; EasyEDA stores 10-mil units "
                 "with Y down and both conversions are applied here",
        "derivation": "PAD~ records of result.packageDetail.dataStr.shape; "
                      "field 3 and field 4 are the pad centre in 10-mil units "
                      "and field 9 is the pad number",
        "pads": fields["pads"],
    }


def _pad_sort(item):
    try:
        return (0, int(item[0]))
    except ValueError:
        return (1, item[0])


def fixture_path(lcsc):
    return os.path.join(FIXTURES, "{}.json".format(lcsc))


def raw_path(lcsc):
    # Derived from FIXTURES rather than fixed at import, because the release
    # gate repoints FIXTURES at its own copy of the project.
    return os.path.join(FIXTURES, "raw", "{}.json".format(lcsc))


def load(lcsc):
    with open(fixture_path(lcsc), encoding="utf-8") as fh:
        return json.load(fh)


def frozen_parts():
    if not os.path.isdir(FIXTURES):
        return []
    return sorted(name[:-5] for name in os.listdir(FIXTURES)
                  if name.endswith(".json"))


def verify(lcsc):
    """Check one part's frozen evidence end to end. (problems, pads).

    The pads returned are the ones re-derived from the committed raw body, not
    the ones the extract states, so scoring downstream cannot be fooled by an
    edited extract even if this check were skipped.
    """
    problems = []
    try:
        record = load(lcsc)
    except (OSError, ValueError) as exc:
        return [{"lcsc": lcsc, "issue": "the normalised extract cannot be "
                                        "read", "detail": str(exc)}], None
    path = raw_path(lcsc)
    if not os.path.isfile(path):
        return problems + [{
            "lcsc": lcsc,
            "issue": "the raw response body is not committed, so the extract "
                     "cannot be re-derived from anything",
            "expected_file": os.path.relpath(path, HERE).replace("\\", "/"),
        }], None
    with open(path, "rb") as fh:
        raw = fh.read()

    digest = hashlib.sha256(raw).hexdigest()
    if digest != record.get("raw_sha256"):
        problems.append({"lcsc": lcsc,
                         "issue": "the committed raw response does not match "
                                  "the digest recorded for it",
                         "recorded": record.get("raw_sha256"),
                         "on_disk": digest})
    if len(raw) != record.get("raw_bytes"):
        problems.append({"lcsc": lcsc,
                         "issue": "the committed raw response is not the "
                                  "recorded length",
                         "recorded": record.get("raw_bytes"),
                         "on_disk": len(raw)})
    try:
        derived = extract(lcsc, raw)
    except (ValueError, KeyError, IndexError) as exc:
        return problems + [{"lcsc": lcsc,
                            "issue": "the committed raw response cannot be "
                                     "read as a footprint",
                            "detail": str(exc)}], None

    for field in ("mpn", "package"):
        if record.get(field) != derived[field]:
            problems.append({"lcsc": lcsc,
                             "issue": "the extract's {} is not what the raw "
                                      "response says".format(field),
                             "extract": record.get(field),
                             "raw": derived[field]})
    if record.get("pads") != derived["pads"]:
        stated, actual = record.get("pads") or {}, derived["pads"]
        differing = sorted(n for n in set(stated) | set(actual)
                           if stated.get(n) != actual.get(n))
        problems.append({"lcsc": lcsc,
                         "issue": "the extract's pads are not what the raw "
                                  "response derives",
                         "pads_disagreeing": differing[:8],
                         "pad_count": "{} stated, {} derived".format(
                             len(stated), len(actual))})
    return problems, derived["pads"]


# ---------------------------------------------------------------------------
# the board's own footprints
# ---------------------------------------------------------------------------

def board_parts(board_path=BOARD, part_number_field="LCSC"):
    """{lcsc: {"references": [...], "pads": {num: [x, y]}, "footprint": ...}}

    Pads are taken from the footprint as the library draws it, with the
    placement rotation removed, so what is compared is one library zero
    against another rather than one placement against another.
    """
    import pcbnew
    board = pcbnew.LoadBoard(board_path)
    parts = {}
    for footprint in board.Footprints():
        lcsc = ""
        for field in footprint.GetFields():
            if field.GetName() == part_number_field:
                lcsc = field.GetText().strip()
        if not lcsc:
            continue
        entry = parts.setdefault(lcsc, {
            "references": [],
            "footprint": footprint.GetFPIDAsString(),
            "pads": {},
        })
        entry["references"].append(footprint.GetReference())
        if entry["pads"]:
            continue
        origin = footprint.GetPosition()
        angle = math.radians(footprint.GetOrientationDegrees())
        flipped = footprint.IsFlipped()
        for pad in footprint.Pads():
            position = pad.GetPosition()
            x = (position.x - origin.x) / 1e6
            y = -(position.y - origin.y) / 1e6
            # Undo the placement rotation, and the mirroring of a part on the
            # far side, to recover the footprint's own zero orientation.
            if flipped:
                x = -x
            rx = x * math.cos(-angle) - y * math.sin(-angle)
            ry = x * math.sin(-angle) + y * math.cos(-angle)
            entry["pads"][pad.GetNumber()] = [round(rx, 6), round(ry, 6)]
    for entry in parts.values():
        entry["references"].sort()
    return parts


# ---------------------------------------------------------------------------
# scoring
# ---------------------------------------------------------------------------

def _centred(pads):
    """Pad directions about the pad cloud's own centre.

    Centring on the pads rather than on either library's declared origin keeps
    the comparison to shape alone: the two need not agree about where the
    part's anchor sits, only about which way it faces.
    """
    usable = {n: p for n, p in pads.items()
              if math.hypot(p[0], p[1]) > 0 or True}
    cx = sum(p[0] for p in usable.values()) / len(usable)
    cy = sum(p[1] for p in usable.values()) / len(usable)
    return {n: (p[0] - cx, p[1] - cy) for n, p in usable.items()}


def score(jlc_pads, kicad_pads):
    """Worst pad-direction disagreement for each candidate offset."""
    jlc, kicad = _centred(jlc_pads), _centred(kicad_pads)
    shared = sorted(set(jlc) & set(kicad), key=_pad_sort_key)
    if not shared:
        raise SystemExit("no pad numbers in common")
    results = {}
    for offset in CANDIDATES:
        angle = math.radians(-offset)
        worst, worst_pad = 0.0, None
        for number in shared:
            kx, ky = kicad[number]
            rx = kx * math.cos(angle) - ky * math.sin(angle)
            ry = kx * math.sin(angle) + ky * math.cos(angle)
            jx, jy = jlc[number]
            na = math.hypot(rx, ry)
            nb = math.hypot(jx, jy)
            if na < 1e-9 or nb < 1e-9:
                continue          # a centred pad has no direction to compare
            cosine = max(-1.0, min(1.0, (rx * jx + ry * jy) / (na * nb)))
            error = math.degrees(math.acos(cosine))
            if error > worst:
                worst, worst_pad = error, number
        results[offset] = {"worst_deg": round(worst, 3), "worst_pad": worst_pad}
    ranked = sorted(results.items(), key=lambda kv: kv[1]["worst_deg"])
    best, runner_up = ranked[0], ranked[1]
    return {
        "pads_compared": len(shared),
        "per_candidate": {str(int(k)): v for k, v in sorted(results.items())},
        "best_offset_deg": _signed(best[0]),
        "best_worst_deg": best[1]["worst_deg"],
        "margin_deg": round(runner_up[1]["worst_deg"] - best[1]["worst_deg"], 3),
        "decisive": (best[1]["worst_deg"] <= MAX_BEST_ERROR_DEG
                     and runner_up[1]["worst_deg"] - best[1]["worst_deg"]
                     >= MIN_MARGIN_DEG),
    }


def _pad_sort_key(number):
    try:
        return (0, int(number))
    except ValueError:
        return (1, number)


def _signed(offset):
    """270 and -90 are the same turn; report the shorter way round."""
    offset %= 360.0
    return offset - 360.0 if offset > 180.0 else offset


def derive(part_number_field="LCSC", board_path=BOARD):
    """Score every frozen part against the board. {lcsc: {...}}"""
    board = board_parts(board_path, part_number_field)
    out = {}
    for lcsc in frozen_parts():
        problems, pads = verify(lcsc)
        if pads is None:
            out[lcsc] = {"error": problems[0]["issue"],
                         "evidence_problems": problems}
            continue
        evidence = load(lcsc)
        if lcsc not in board:
            out[lcsc] = {"error": "no footprint on the board carries this "
                                  "part number",
                         "evidence_problems": problems}
            continue
        entry = dict(score(pads, board[lcsc]["pads"]))
        entry.update({
            "mpn": evidence["mpn"],
            "package": evidence["package"],
            "kicad_footprint": board[lcsc]["footprint"],
            "references": board[lcsc]["references"],
            "evidence_file": os.path.relpath(
                fixture_path(lcsc), HERE).replace("\\", "/"),
            "raw_file": os.path.relpath(
                raw_path(lcsc), HERE).replace("\\", "/"),
            "evidence_sha256": evidence["raw_sha256"],
            "evidence_problems": problems,
        })
        out[lcsc] = entry
    return out


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

def cmd_freeze(args):
    os.makedirs(os.path.join(FIXTURES, "raw"), exist_ok=True)
    wanted = args.lcsc or sorted(board_parts(BOARD, args.field))
    for lcsc in wanted:
        url, raw = fetch(lcsc)
        # The body goes down byte for byte. Nothing is re-encoded, re-indented
        # or given a trailing newline: an evidence file that is not what the
        # server sent proves nothing about what the server sent.
        with open(raw_path(lcsc), "wb") as fh:
            fh.write(raw)
        record = normalise(lcsc, url, raw)
        with open(fixture_path(lcsc), "w", encoding="utf-8", newline="\n") as fh:
            json.dump(record, fh, indent=2, sort_keys=False)
            fh.write("\n")
        print("  froze {:<10} {:<28} {:>2} pads  {:>6} B  sha256 {}".format(
            lcsc, record["mpn"][:28], len(record["pads"]),
            record["raw_bytes"], record["raw_sha256"][:16]))
    return 0


def cmd_report(args):
    derived = derive(args.field)
    print("%-11s %-26s %5s %8s %8s  %s" % (
        "LCSC", "part", "offset", "worst", "margin", "references"))
    bad = 0
    for lcsc, row in sorted(derived.items()):
        if "error" in row:
            print("%-11s %s" % (lcsc, row["error"]))
            bad += 1
            continue
        print("%-11s %-26s %5.0f %7.1f%s %7.1f%s  %s" % (
            lcsc, row["mpn"][:26], row["best_offset_deg"],
            row["best_worst_deg"], "d", row["margin_deg"], "d",
            ",".join(row["references"][:3])
            + ("..." if len(row["references"]) > 3 else "")))
        if not row["decisive"]:
            print("%-11s   NOT DECISIVE - candidates are too close to choose"
                  % "")
        for problem in row.get("evidence_problems", []):
            print("%-11s   EVIDENCE: %s" % ("", problem["issue"]))
            bad += 1
    return 1 if bad else 0


def _shared_registry(spec, _registry_path=None):
    """The validator's own Registry, not a second opinion about it.

    Whether an entry may be used is one decision, and this command must not
    make it differently from the code that generates and validates a release.
    The toolkit is reached the way every other tool here reaches it - through
    the pinned submodule - rather than by deriving a path from wherever the
    registry document happens to sit. It used to be the latter, which stopped
    resolving when the validator moved out of the project and into a
    submodule.
    """
    _toolkit.install()
    from pcbqa.orientation import Registry
    return Registry(spec)


def cmd_check(args):
    """Re-derive every offset and compare with what the registry declares."""
    with open(args.registry, encoding="utf-8") as fh:
        registry = json.load(fh)["release_generation"]["cpl_orientation"]
    declared = {row["lcsc"]: float(row["offset_deg"])
                for row in registry["registry"]}
    shared = _shared_registry(registry, args.registry)
    reviewed = set(shared.entries)
    derived = derive(registry.get("part_number_field", "LCSC"))
    problems = []
    for lcsc, row in sorted(derived.items()):
        for problem in row.get("evidence_problems", []):
            problems.append("{}: {}".format(lcsc, problem["issue"]))
        if "error" in row:
            problems.append("{}: {}".format(lcsc, row["error"]))
            continue
        if lcsc in declared and lcsc not in reviewed:
            problems.append(
                "{}: no reviewed orientation mapping is available - "
                "review_status is {} rather than exactly '{}'".format(
                    lcsc, shared.describe_status(
                        shared.unusable.get(lcsc, {})),
                    shared.USABLE_STATUS))
        if lcsc not in declared:
            problems.append("{}: evidence is frozen but the registry has no "
                            "entry".format(lcsc))
            continue
        if not row["decisive"]:
            problems.append("{}: the evidence does not decide an offset "
                            "(best {}d, margin {}d)".format(
                                lcsc, row["best_worst_deg"], row["margin_deg"]))
        if abs(((row["best_offset_deg"] - declared[lcsc] + 180) % 360) - 180) > 1:
            problems.append(
                "{}: registry says {:+.0f} but the frozen evidence derives "
                "{:+.0f}".format(lcsc, declared[lcsc], row["best_offset_deg"]))
    for lcsc in sorted(declared):
        if lcsc not in derived:
            problems.append("{}: the registry declares an offset with no "
                            "frozen evidence behind it".format(lcsc))
    for problem in problems:
        print("  " + problem)
    print("{} part(s) checked, {} problem(s)".format(len(derived), len(problems)))
    return 1 if problems else 0


def cmd_check_live(args):
    """Compare the frozen evidence with what JLC serves today.

    Kept apart from `check` on purpose, and given its own exit code. JLC
    revising a footprint is news, but it is not the committed evidence being
    wrong, and a release must not start failing because an upstream site
    changed under it. Corruption of what is committed here still reports as
    corruption, and outranks drift.
    """
    corrupt, drift = [], []
    for lcsc in frozen_parts():
        problems, pads = verify(lcsc)
        corrupt.extend("{}: {}".format(lcsc, p["issue"]) for p in problems)
        if pads is None:
            continue
        record = load(lcsc)
        try:
            url, raw = fetch(lcsc, timeout=args.timeout)
        except Exception as exc:                       # noqa: BLE001 - network
            drift.append("{}: could not be retrieved ({})".format(lcsc, exc))
            continue
        digest = hashlib.sha256(raw).hexdigest()
        if digest == record["raw_sha256"]:
            print("  {:<11} unchanged upstream".format(lcsc))
            continue
        try:
            now = extract(lcsc, raw)
        except (ValueError, KeyError, IndexError) as exc:
            drift.append("{}: today's response cannot be read ({})".format(
                lcsc, exc))
            continue
        moved = sorted(n for n in set(pads) | set(now["pads"])
                       if pads.get(n) != now["pads"].get(n))
        if moved:
            drift.append("{}: JLC's footprint geometry has CHANGED - pads {} "
                         "differ; re-freeze and re-review the offset".format(
                             lcsc, ", ".join(moved[:8])))
        else:
            drift.append("{}: response body changed but the pad geometry is "
                         "identical, so the derived offset is unaffected"
                         .format(lcsc))
    for line in corrupt:
        print("  COMMITTED EVIDENCE CORRUPT  " + line)
    for line in drift:
        print("  UPSTREAM DRIFT              " + line)
    print("{} part(s) compared with {}: {} corrupt, {} drifted".format(
        len(frozen_parts()), SOURCE.split("/api")[0], len(corrupt), len(drift)))
    if corrupt:
        return 1
    return 2 if drift else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--field", default="LCSC",
                        help="footprint field holding the part number")
    sub = parser.add_subparsers(dest="command", required=True)

    freeze = sub.add_parser("freeze", help="fetch and record the evidence")
    freeze.add_argument("lcsc", nargs="*")
    freeze.set_defaults(func=cmd_freeze)

    report = sub.add_parser("report", help="score every frozen part")
    report.set_defaults(func=cmd_report)

    check = sub.add_parser("check", help="registry against frozen evidence")
    check.add_argument("--registry", default=os.path.join(
        HERE, "board", "manifest.live.json"))
    check.set_defaults(func=cmd_check)

    live = sub.add_parser("check-live",
                          help="frozen evidence against JLC today (network); "
                               "exit 1 corrupt, 2 upstream drift")
    live.add_argument("--timeout", type=int, default=45)
    live.set_defaults(func=cmd_check_live)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
