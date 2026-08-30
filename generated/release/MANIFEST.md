# Fabrication release manifest

- generated: 2026-08-29T23:44:55.374594+00:00
- kicad: 10.0.5
- constraint profile: jlcpcb-4layer-assembled
- source closure sha256: `538b3fdaae5e375e10714e1ba519f5da07e5e2edcde133627063e7a2e169a956`

## command

    kicad-cli sch erc --output /home/pentolope/github/MicrophoneArray_PCB_V3/out/microphone-array-v2-live/attempts/20260829T234448Z-e7a42ee1/work/clean_run/reports/erc.json --format json --severity-all --severity-exclusions --exit-code-violations /home/pentolope/github/MicrophoneArray_PCB_V3/out/microphone-array-v2-live/attempts/20260829T234448Z-e7a42ee1/work/clean_run/fixture/project/microphone_array_v2.kicad_sch
    kicad-cli pcb drc --output /home/pentolope/github/MicrophoneArray_PCB_V3/out/microphone-array-v2-live/attempts/20260829T234448Z-e7a42ee1/work/clean_run/reports/drc.json --format json --severity-all --severity-exclusions --all-track-errors --schematic-parity --refill-zones --save-board --exit-code-violations /home/pentolope/github/MicrophoneArray_PCB_V3/out/microphone-array-v2-live/attempts/20260829T234448Z-e7a42ee1/work/clean_run/fixture/project/microphone_array_v2.kicad_pcb
    kicad-cli pcb export gerbers --output /home/pentolope/github/MicrophoneArray_PCB_V3/out/microphone-array-v2-live/attempts/20260829T234448Z-e7a42ee1/work/clean_run/generated/gerbers --layers F.Cu,In1.Cu,In2.Cu,B.Cu,F.Paste,B.Paste,F.Silkscreen,B.Silkscreen,F.Mask,B.Mask,Edge.Cuts --no-x2 --no-netlist --use-drill-file-origin --subtract-soldermask /home/pentolope/github/MicrophoneArray_PCB_V3/out/microphone-array-v2-live/attempts/20260829T234448Z-e7a42ee1/work/clean_run/fixture/project/microphone_array_v2.kicad_pcb
    kicad-cli pcb export drill --output /home/pentolope/github/MicrophoneArray_PCB_V3/out/microphone-array-v2-live/attempts/20260829T234448Z-e7a42ee1/work/clean_run/generated/gerbers --format excellon --excellon-separate-th --drill-origin plot /home/pentolope/github/MicrophoneArray_PCB_V3/out/microphone-array-v2-live/attempts/20260829T234448Z-e7a42ee1/work/clean_run/fixture/project/microphone_array_v2.kicad_pcb
    kicad-cli pcb export pos --output /home/pentolope/github/MicrophoneArray_PCB_V3/out/microphone-array-v2-live/attempts/20260829T234448Z-e7a42ee1/build/cpl.csv --format csv --units mm --side both --exclude-dnp /home/pentolope/github/MicrophoneArray_PCB_V3/out/microphone-array-v2-live/attempts/20260829T234448Z-e7a42ee1/work/clean_run/fixture/project/microphone_array_v2.kicad_pcb
    kicad-cli sch export bom --output /home/pentolope/github/MicrophoneArray_PCB_V3/out/microphone-array-v2-live/attempts/20260829T234448Z-e7a42ee1/build/bom.csv --fields ${QUANTITY},Reference,Value,Footprint,LCSC --labels "Quantity,Designator,Comment,Footprint,LCSC Part #" --group-by Value,Footprint,LCSC --exclude-dnp --ref-range-delimiter  /home/pentolope/github/MicrophoneArray_PCB_V3/out/microphone-array-v2-live/attempts/20260829T234448Z-e7a42ee1/work/clean_run/fixture/project/microphone_array_v2.kicad_sch
    rename "13 file(s)"
    orient "103 placement(s)"
    relabel cpl.csv
    relabel bom.csv

## artifacts

- `microphone_array_v2-revA-fabrication.zip` sha256 `b620a245122ff1116ca1a5d394af4eb57a492d7e3775ddcd8346f49b0e91edb1`
- `bom.csv` sha256 `a3970d865b3b76677afe0b11b6061156e3be633a50613167887ef8a54fe14563`
- `cpl.csv` sha256 `28091e4dbf5a05e30af72f833839194c67c0aa0f90c6f797d0434fce84e7e807`

## excluded from the archive

- `(none)`: every exported layer was approved
