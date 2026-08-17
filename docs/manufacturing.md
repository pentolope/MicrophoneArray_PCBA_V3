# Manufacturing and bring-up

## Release package

`generated/release/`, built by the clean-room release in a single run from one
board revision:

```bash
"C:/Program Files/KiCad/10.0/bin/python.exe" verification/run.py release verification/boards/live.json
```

It copies the project into an empty directory, purges every pre-existing
output, regenerates everything from the native KiCad files with `kicad-cli`,
re-runs the full validation against what it produced, and publishes only if
every gate passes. The BOM and CPL come from the schematic and the board, not
from the Python model that generated them - a released part list has to be
derived from the thing being manufactured.

| File | Contents |
|---|---|
| `microphone_array_v2-revA-fabrication.zip` | 11 Gerbers and 2 Excellon drills, Protel-named |
| `gerbers/` | the same layers unzipped, as exported |
| `bom.csv` | JLCPCB BOM, 15 lines |
| `cpl.csv` | JLCPCB pick-and-place, 103 placements, all top side |
| `MANIFEST.md` | SHA-256 of the archive, board and each data file |
| `reports/` | the ERC and DRC JSON the gates were run against |
| `validation.json`, `clean_room.json` | what was checked, and what produced it |
| `RECEIPT.json` | every file above, with its digest, written last |

Everything in that directory comes from one clean-room run, and
`PROV.RELEASE_COHERENCE` enforces it rather than asserting it:

```bash
"C:/Program Files/KiCad/10.0/bin/python.exe" verification/run.py coherence verification/boards/live.json
```

The check fails if the release manifest describes an archive that is not there,
if the validation report validated a different archive, if any file names a
different source closure from the rest, or if a file is present that the
receipt does not account for.

The review renders - top, bottom, the stacking plan and the four copper
layers - live in [generated/renders/](../generated/renders) rather than in the
release package. The clean-room run purges and never regenerates them, so
keeping them inside the package would contradict the statement in
`UNSEALED.txt` that every file there came from that run.

### Why the layers are named the way they are

The archive says how many copper layers this board has in its **filenames**,
and nowhere else:

| File | Layer | KiCad |
|---|---|---|
| `microphone_array_v2.GTL` | L1, top copper | `F.Cu` |
| `microphone_array_v2.G2L` | L2, inner ground | `In1.Cu`, exported as `.g1` |
| `microphone_array_v2.G3L` | L3, inner ground | `In2.Cu`, exported as `.g2` |
| `microphone_array_v2.GBL` | L4, bottom copper | `B.Cu` |

Layer order used to travel in the Gerber X2 `FileFunction` attributes while the
files themselves were called `GND1.gbr` and `GND2.gbr`. JLCPCB does not
reliably read those attributes, and a board whose inner layers are named after
the net they carry has nothing left that says they are copper at all - it can
be quoted and built as two-layer. So the export now passes `--no-x2`, ships
Protel extensions, and carries no job file: there is exactly one statement of
the stackup and it is the one the fabricator actually reads.

The mapping lives in `fabrication_naming` in
[verification/boards/live.json](../verification/boards/live.json). The release
renames by it, the archive gate admits by it, and `FAB.LAYER_IDENTITY` proves
after every release that the four copper files are present, distinguishable by
name, carrying real geometry, free of X2 attributes - and that `.G2L` and
`.G3L` are geometrically identical to a fresh single-layer export of `In1.Cu`
and `In2.Cu`. Renaming is all that is done to these files; their contents are
KiCad's, byte for byte.

Aperture macros are left enabled. Disabling them made KiCad draw each rounded
pad corner as a single chord instead of an arc, which cost the front mask 1.4%
of its area and chamfered pad corners by up to 0.073 mm. Nothing needed it -
the layer identification is carried entirely by the filenames - so the artwork
keeps its true pad shapes and the export matches the board to within 0.01 mm.

Gerbers, drills and the CPL share one origin. Verified rather than assumed:
`MK1` appears in the CPL at 53.220 mm from the board centre on the +X axis at
270 degrees, which is exactly where the placement puts it, and the Edge.Cuts
outline spans the matching 120 mm.

### Placement angles

Two separate things happen to a rotation on its way from KiCad into `cpl.csv`,
and they are worth keeping apart because confusing them turns parts.

**Normalisation** puts every angle in `[0, 360)`. KiCad writes rotations in
`(-180, 180]`, so half the array arrived as negative numbers; -157.5 and 202.5
are the same orientation, but only one of them goes through without an
engineering query. This changes no part's orientation and applies to all 103
placements.

**Library-zero offsets** do change orientation, and only for parts whose zero
in JLCPCB's library differs from the footprint's zero in KiCad.

Every populated part carries an explicit reviewed entry in the registry at
`release_generation.cpl_orientation.registry` in
[verification/boards/live.json](../verification/boards/live.json) - all fifteen
part numbers, **including the twelve whose offset is zero**. "Nobody has looked
at this one" and "looked at, needs nothing" are different statements and only
the second may ship, so there is no default: generation refuses a part with no
entry rather than quietly assuming zero. An entry counts only when its
`review_status` is exactly `reviewed` - `pending`, `unreviewed`, blank or
absent all block generation, because a half-written entry looks like coverage
and is worse than none. Three parts need a turn:

| LCSC | Part | Package | Offset | JLC library zero vs KiCad |
|---|---|---|---:|---|
| `C80670` | LP5907MFX-3.3 | SOT-23-5 | +180 | pins 1-3 on the right, pin 1 lowest |
| `C7668` | SN74LVC244APWR | TSSOP-20 | -90 | pins 1-10 along the bottom |
| `C111212` | USBLC6-4SC6 | SOT-23-6 | -90 | pins 1-3 along the bottom, pin 1 left |

Entries are looked up by **LCSC number, not by footprint name**, because that
is what the difference is a property of. It matters here: a footprint-regex
table would lump SOT-23-5 in with SOT-23-6 and give the regulator -90 when it
needs 180. Looking them up by part number also makes it impossible for U3 and U4 - the
same device in two places - to be corrected differently.

Each offset was derived from JLCPCB's own library footprint, read through the
EasyEDA API, by comparing every pad against the KiCad footprint rather than
just pin 1: a pin-1 match alone cannot tell a rotation from a mirror. The
retrieval and scoring live in
[tools/jlc_orientation.py](../tools/jlc_orientation.py).

The evidence itself is committed, not summarised. Under
[fabrication/jlc_orientation/](../fabrication/jlc_orientation) each part has two
files: `raw/<LCSC>.json`, the response body byte for byte, and `<LCSC>.json`, a
normalised extract of it recording the source URL, the retrieval date, and the
length and SHA-256 of that body. The extract is a convenience and never an
authority - every offline command re-derives the pads from the raw file and
fails if the extract disagrees. Three things could move an offset and all three
fail: editing the raw body breaks its digest, editing the extract makes it
disagree with the body, and editing the registry makes it disagree with the
score.

```bash
"C:/Program Files/KiCad/10.0/bin/python.exe" tools/jlc_orientation.py check
```

That is offline, as is `report`. Neither the test suite nor a clean release
touches the network, so EasyEDA being down cannot fail a release. The one
command that does look upstream is separate and reports the two failures
apart - exit 1 means the committed evidence is corrupt, exit 2 means JLC has
changed their library since it was frozen, which is a prompt to re-freeze and
re-review rather than a release failure:

```bash
"C:/Program Files/KiCad/10.0/bin/python.exe" tools/jlc_orientation.py check-live
```

`CPL.ORIENTATION` re-scores that frozen evidence on every release and requires
the registry to agree with it, checks the registry covers everything the BOM
and the board say will be fitted, and recomputes each shipped angle as the
board angle plus the reviewed offset, normalised into `[0, 360)` - half-open,
so `0` is a placement angle and `360` is not, including when rounding would
otherwise carry a value up to it. Checking the shipped file against the table
the generator used would only prove a program can apply its own table twice.
`PROV.SOURCE_CLOSURE` separately requires the script, this schema and both
evidence files for all fifteen entries to be inside the release's source
closure, so an input cannot quietly stop being tracked - and, because these
offsets are *derived* rather than read off the board, the code that derives
them is tracked with them. `pcbqa.orientation`, `pcbqa.cleanroom`,
`pcbqa.gates.g_orientation` and `tools/jlc_orientation.py` are hashed from the
modules that were actually imported, not from files at a path, so a stale copy
sitting at a tracked path cannot stand in for the code that ran. Editing any of
them changes the source-closure digest and invalidates every report bound to
it.

The closure is an identity, so it has to mean the same thing on every machine
and a different thing whenever the release would differ. Four things make that
true. Its globs are explicit and exclude the validator's own tree, this board's
output and the routing scratch, so it cannot change because a previous attempt
is still on disk. The files named in `sources` are added by name as well, so
the selected board and schematic are covered because they were selected and not
because a glob happened to reach them - point the release at a different board
and the closure changes. Its digests are canonical, so a checkout with either
line ending is the same design. And the manifest enters as its *content* rather
than as its file digest, minus exactly the leaves the clean room assigns, so
the reports a run produces can still be checked from the repository that
produced them.

That exclusion list is owned by `pcbqa.cleanroom`, not by the board file: a
manifest may name a subset of it and nothing else, so no board can excuse its
own release-affecting configuration from its own provenance. The release
compares the origin and clean-room closures and refuses to publish if they
disagree.

Nothing else takes an offset, and that is measured rather than assumed. When
JLCPCB revised the first production upload they corrected 11 of the 16
microphones, 11 of 16 damping resistors and so on - and the five left alone in
each family were exactly those already sitting at a multiple of 45 degrees. A
library mismatch is a property of the part, so it would have turned all 16.
The clearest case is `C15850`: `C3` at 0 degrees was untouched while `CB1`-`CB4`
- the same part in the same footprint, at fractional angles - were revised. The
frozen library evidence agrees, deriving a zero offset for every one of them.

The other 48 references JLCPCB revised keep the fractional angles this board
submitted - 22.5-degree multiples, because the array is 16-way polar - with
negative values normalised into `[0, 360)` and nothing else done to them. No
rounding policy is invented for them: rounding to a value nobody has seen would
move sixteen microphones off the array geometry to match a guess. **It cannot
be confirmed that those 48 now match what JLCPCB produced**; that would need
their revised CPL, which was never supplied. The list is recorded under
`undocumented_production_edits` in the manifest.

**This is a release candidate, not an approved production file.** Upload the
package and step through JLCPCB's Gerber and pick-and-place previews before
paying for it.

## Order options

| Option | Value |
|---|---|
| Layers | 4 |
| Dimensions | 120 mm diameter circle |
| Thickness | 1.6 mm |
| Outer copper | 1 oz |
| Inner copper | 0.5 oz |
| Surface finish | ENIG |
| Solder mask | Green |
| Silkscreen | White |
| Via covering | Plugged (routing vias only) |
| Assembly | Top side only |
| Impedance control | Not required |

## Order remark

```
Four-layer board: L1 top signal / L2 GND / L3 GND / L4 bottom signal.
Both inner layers are solid ground planes; this is intentional.
Plug only the routing vias, which are 0.30 mm finished hole with 0.45 mm pads
and are tented on both sides. Keep every plated through-hole for the two 1x24
module sockets and the 2x13 pin header open, and keep the four 3.2 mm NPTH
mounting holes open. Follow the supplied layer order and the confirm-production
file.
```

## Assembly notes

- All surface-mount parts are on the **top** side. There are no bottom-side
  SMT parts.
- `J1`, `J2` and `J3` are marked DNP and are excluded from the BOM and CPL.
  They are hand-soldered by the customer from the bottom side.
- The four mounting holes and the twenty-four test pads carry no parts.

### Microphone handling

The `MSM261DHP006` datasheet imposes handling limits that must reach the
assembler:

- Moisture sensitivity level 1.
- Maximum three reflow cycles, peak 260 C.
- **Do not board-wash or clean after reflow.** No aqueous wash, no solvents,
  no ultrasonic cleaning.
- Do not apply compressed air or vacuum over the acoustic port, and do not
  place a pick-up nozzle over the port hole.

The first two lines of the top silkscreen carry `PORTS FACE UP - DO NOT WASH`
for this reason. If the assembler's standard process includes a wash step, the
order must be flagged as no-clean.

## Hand-soldered parts

| Ref | Part | Side | Notes |
|---|---|---|---|
| `J2`, `J3` | 1x24 2.54 mm female header | bottom | 0.900 in row spacing; 1.0 mm drills give about 0.18 mm radial slack per pin |
| `J1` | 2x13 2.54 mm pin header | bottom | pin 1 marked by a square pad and a silkscreen triangle |

Fit the module sockets first, then check the Tang Nano 9K seats freely before
soldering the host header.

## Bring-up sequence

1. Inspect the assembled board: all sixteen microphone ports clear, no residue
   over any port, `U1` and `D1` orientation against the silkscreen.
2. With **no** Tang Nano 9K fitted and the host cable disconnected, apply 5 V to
   test pad `5V` through a current-limited supply. Expect well under 5 mA.
   Confirm `3V3A` reads 3.3 V and `3V3C` reads 3.3 V.
3. Fit the Tang Nano 9K. Power from the Pi. Confirm `TANG` reads 3.3 V.
4. Load a bitstream over the module's USB-C. `D1` prevents the USB supply from
   back-feeding the Pi, so both may be connected.
5. Confirm 24.576 MHz on `MCLK` and 3.072 MHz on `PCLK`.
6. Confirm activity on `D0` to `D7`.

The Raspberry Pi must be fed from a supply of at least 1.5 A. This board adds
roughly 200 mA to the Pi's 5 V rail, which an original Model B's input polyfuse
can pass but a marginal phone charger cannot.

### USB-C access

The module hangs below the board on its sockets, with its USB-C connector
facing +X and about 15 mm of carrier overhanging it. With standard 8.5 mm
sockets there is enough gap for a slim USB-C plug, but a thick overmoulded
cable may not fit. Load the bitstream before final assembly, or use a slim or
right-angle cable.

## Regenerating the design

The schematic and the board are both generated from `tools/netlist.py`, which
is the single source of truth. Nothing in the KiCad files should be hand-edited.

```bash
python tools/gen_symbols.py
python tools/gen_schematic.py
python tools/check_netlist_parity.py
python tools/gen_pcb.py
python tools/check_placement.py
python tools/check_routes.py
```

`check_netlist_parity.py` proves the generated schematic carries exactly the
intended netlist. KiCad's own `--schematic-parity` DRC then proves the board
matches the schematic. Note that regenerating the board discards routing, so
run the generators before routing, not after.
