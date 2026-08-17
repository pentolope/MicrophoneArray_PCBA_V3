# Frozen orientation evidence

What JLCPCB's own library says each part's zero orientation is. The registry in
`verification/boards/live.json` declares an offset per LCSC number; these files
are what those offsets are derived from, and the release re-derives them rather
than believing the registry.

## Layout

    raw/<LCSC>.json     the response body from EasyEDA, byte for byte
    <LCSC>.json         a normalised extract of it

`raw/` holds exactly what the server sent: no re-encoding, no re-indenting, no
trailing newline. Each file is a single line of JSON. Do not reformat them -
their SHA-256 is the evidence.

The extract beside it **is not the response**, and nothing here calls it one.
It exists so the geometry can be read without parsing 8-40 kB of unrelated
product metadata, and every offline command re-derives it from `raw/` and fails
on a disagreement rather than trusting what it says.

## Fields of the extract

| Field | Meaning |
|---|---|
| `kind` | always `normalised extract` |
| `lcsc`, `mpn`, `package` | the part, as the raw response names it |
| `source_url` | the EasyEDA endpoint the body came from |
| `retrieved_utc` | when it was fetched |
| `raw_file`, `raw_bytes`, `raw_sha256` | the body this was derived from |
| `units` | millimetres, top view, Y up |
| `derivation` | which fields of the raw document produced `pads` |
| `pads` | `{pad number: [x, y]}` in millimetres |

`pads` is keyed by **pad number**, which is what ties an electrical pin to a
position: that mapping, not a visual marker, is what decides the offset. Every
pad votes when scoring, because a pin-1 match alone cannot tell a rotation from
a mirror.

EasyEDA stores footprint geometry in units of 10 mil with the canvas Y axis
running downward. Both conversions are applied: `x_mm = field3 * 0.254` and
`y_mm = -field4 * 0.254`.

## Commands

    tools/jlc_orientation.py report      score every frozen part      (offline)
    tools/jlc_orientation.py check       registry against evidence    (offline)
    tools/jlc_orientation.py freeze      re-fetch and rewrite both files
    tools/jlc_orientation.py check-live  frozen evidence vs JLC today

`report` and `check` never use the network, so neither the test suite nor a
clean release depends on EasyEDA being reachable. `check-live` is the only
routine reader of upstream, and it separates the two things that can be wrong:
exit 1 means the committed evidence is corrupt, exit 2 means JLC's library has
moved since it was frozen. Drift is not a release failure - it is a prompt to
re-freeze and re-review the affected offset.

Three ways to break an offset, all of which fail:

* edit `raw/<LCSC>.json`      - the recorded SHA-256 no longer matches
* edit `<LCSC>.json`          - the pads no longer re-derive from `raw/`
* edit the registry's offset  - it no longer matches the scored result
