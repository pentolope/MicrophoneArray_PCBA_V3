# Fabrication release manifest

- generated: 2026-08-29T18:16:45.187262+00:00
- kicad: 10.0.5
- constraint profile: jlcpcb-4layer-assembled
- source closure sha256: `f57c561ed2e703fba1ba9770aac8f8eef4b9dfb2574903c6cc92c9b143c1ed9d`

## command

    kicad-cli sch erc --output /home/pentolope/github/MicrophoneArray_PCB_V3/out/microphone-array-v2-live/attempts/20260829T181638Z-292cf28c/work/clean_run/reports/erc.json --format json --severity-all --severity-exclusions --exit-code-violations /home/pentolope/github/MicrophoneArray_PCB_V3/out/microphone-array-v2-live/attempts/20260829T181638Z-292cf28c/work/clean_run/fixture/project/microphone_array_v2.kicad_sch
    kicad-cli pcb drc --output /home/pentolope/github/MicrophoneArray_PCB_V3/out/microphone-array-v2-live/attempts/20260829T181638Z-292cf28c/work/clean_run/reports/drc.json --format json --severity-all --severity-exclusions --all-track-errors --schematic-parity --refill-zones --save-board --exit-code-violations /home/pentolope/github/MicrophoneArray_PCB_V3/out/microphone-array-v2-live/attempts/20260829T181638Z-292cf28c/work/clean_run/fixture/project/microphone_array_v2.kicad_pcb
    kicad-cli pcb export gerbers --output /home/pentolope/github/MicrophoneArray_PCB_V3/out/microphone-array-v2-live/attempts/20260829T181638Z-292cf28c/work/clean_run/generated/gerbers --layers F.Cu,In1.Cu,In2.Cu,B.Cu,F.Paste,B.Paste,F.Silkscreen,B.Silkscreen,F.Mask,B.Mask,Edge.Cuts --no-x2 --no-netlist --use-drill-file-origin --subtract-soldermask /home/pentolope/github/MicrophoneArray_PCB_V3/out/microphone-array-v2-live/attempts/20260829T181638Z-292cf28c/work/clean_run/fixture/project/microphone_array_v2.kicad_pcb
    kicad-cli pcb export drill --output /home/pentolope/github/MicrophoneArray_PCB_V3/out/microphone-array-v2-live/attempts/20260829T181638Z-292cf28c/work/clean_run/generated/gerbers --format excellon --excellon-separate-th --drill-origin plot /home/pentolope/github/MicrophoneArray_PCB_V3/out/microphone-array-v2-live/attempts/20260829T181638Z-292cf28c/work/clean_run/fixture/project/microphone_array_v2.kicad_pcb
    kicad-cli pcb export pos --output /home/pentolope/github/MicrophoneArray_PCB_V3/out/microphone-array-v2-live/attempts/20260829T181638Z-292cf28c/build/cpl.csv --format csv --units mm --side both --exclude-dnp /home/pentolope/github/MicrophoneArray_PCB_V3/out/microphone-array-v2-live/attempts/20260829T181638Z-292cf28c/work/clean_run/fixture/project/microphone_array_v2.kicad_pcb
    kicad-cli sch export bom --output /home/pentolope/github/MicrophoneArray_PCB_V3/out/microphone-array-v2-live/attempts/20260829T181638Z-292cf28c/build/bom.csv --fields ${QUANTITY},Reference,Value,Footprint,LCSC --labels "Quantity,Designator,Comment,Footprint,LCSC Part #" --group-by Value,Footprint,LCSC --exclude-dnp --ref-range-delimiter  /home/pentolope/github/MicrophoneArray_PCB_V3/out/microphone-array-v2-live/attempts/20260829T181638Z-292cf28c/work/clean_run/fixture/project/microphone_array_v2.kicad_sch
    rename "13 file(s)"
    orient "103 placement(s)"
    relabel cpl.csv
    relabel bom.csv

## artifacts

- `microphone_array_v2-revA-fabrication.zip` sha256 `2e6655cd3261f8bc751cbf703182e23e46aab511d3ada89706b88794bb583ff8`
- `bom.csv` sha256 `a3970d865b3b76677afe0b11b6061156e3be633a50613167887ef8a54fe14563`
- `cpl.csv` sha256 `28091e4dbf5a05e30af72f833839194c67c0aa0f90c6f797d0434fce84e7e807`

## excluded from the archive

- `(none)`: every exported layer was approved
