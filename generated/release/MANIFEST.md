# Fabrication release manifest

- generated: 2026-08-24T15:19:47.359937+00:00
- kicad: 10.0.5
- constraint profile: jlcpcb-4layer-assembled
- source closure sha256: `19863d7318946f5fd1eb531f6c78a0c9e3c55e8afa70fefbe4803b7d50848009`

## command

    kicad-cli.exe sch erc --output C:\Users\pentolope\Documents\GitHub\MicrophoneArray_PCB_V3_Improve\out\microphone-array-v2-live\attempts\20260824T151938Z-359ddffc\work\clean_run\reports\erc.json --format json --severity-all --severity-exclusions --exit-code-violations C:\Users\pentolope\Documents\GitHub\MicrophoneArray_PCB_V3_Improve\out\microphone-array-v2-live\attempts\20260824T151938Z-359ddffc\work\clean_run\fixture\project\microphone_array_v2.kicad_sch
    kicad-cli.exe pcb drc --output C:\Users\pentolope\Documents\GitHub\MicrophoneArray_PCB_V3_Improve\out\microphone-array-v2-live\attempts\20260824T151938Z-359ddffc\work\clean_run\reports\drc.json --format json --severity-all --severity-exclusions --all-track-errors --schematic-parity --refill-zones --save-board --exit-code-violations C:\Users\pentolope\Documents\GitHub\MicrophoneArray_PCB_V3_Improve\out\microphone-array-v2-live\attempts\20260824T151938Z-359ddffc\work\clean_run\fixture\project\microphone_array_v2.kicad_pcb
    kicad-cli.exe pcb export gerbers --output C:\Users\pentolope\Documents\GitHub\MicrophoneArray_PCB_V3_Improve\out\microphone-array-v2-live\attempts\20260824T151938Z-359ddffc\work\clean_run\generated\gerbers --layers F.Cu,In1.Cu,In2.Cu,B.Cu,F.Paste,B.Paste,F.Silkscreen,B.Silkscreen,F.Mask,B.Mask,Edge.Cuts --no-x2 --no-netlist --use-drill-file-origin --subtract-soldermask C:\Users\pentolope\Documents\GitHub\MicrophoneArray_PCB_V3_Improve\out\microphone-array-v2-live\attempts\20260824T151938Z-359ddffc\work\clean_run\fixture\project\microphone_array_v2.kicad_pcb
    kicad-cli.exe pcb export drill --output C:\Users\pentolope\Documents\GitHub\MicrophoneArray_PCB_V3_Improve\out\microphone-array-v2-live\attempts\20260824T151938Z-359ddffc\work\clean_run\generated\gerbers --format excellon --excellon-separate-th --drill-origin plot C:\Users\pentolope\Documents\GitHub\MicrophoneArray_PCB_V3_Improve\out\microphone-array-v2-live\attempts\20260824T151938Z-359ddffc\work\clean_run\fixture\project\microphone_array_v2.kicad_pcb
    kicad-cli.exe pcb export pos --output C:\Users\pentolope\Documents\GitHub\MicrophoneArray_PCB_V3_Improve\out\microphone-array-v2-live\attempts\20260824T151938Z-359ddffc\build\cpl.csv --format csv --units mm --side both --exclude-dnp C:\Users\pentolope\Documents\GitHub\MicrophoneArray_PCB_V3_Improve\out\microphone-array-v2-live\attempts\20260824T151938Z-359ddffc\work\clean_run\fixture\project\microphone_array_v2.kicad_pcb
    kicad-cli.exe sch export bom --output C:\Users\pentolope\Documents\GitHub\MicrophoneArray_PCB_V3_Improve\out\microphone-array-v2-live\attempts\20260824T151938Z-359ddffc\build\bom.csv --fields ${QUANTITY},Reference,Value,Footprint,LCSC --labels "Quantity,Designator,Comment,Footprint,LCSC Part #" --group-by Value,Footprint,LCSC --exclude-dnp --ref-range-delimiter  C:\Users\pentolope\Documents\GitHub\MicrophoneArray_PCB_V3_Improve\out\microphone-array-v2-live\attempts\20260824T151938Z-359ddffc\work\clean_run\fixture\project\microphone_array_v2.kicad_sch
    rename "13 file(s)"
    orient "103 placement(s)"
    relabel cpl.csv
    relabel bom.csv

## artifacts

- `microphone_array_v2-revA-fabrication.zip` sha256 `66f8e2e7523bf3da2c1dbc217b5d48fc501b8fdfb3687e2dc16a3fe9ca2337fa`
- `bom.csv` sha256 `a3970d865b3b76677afe0b11b6061156e3be633a50613167887ef8a54fe14563`
- `cpl.csv` sha256 `28091e4dbf5a05e30af72f833839194c67c0aa0f90c6f797d0434fce84e7e807`

## excluded from the archive

- `(none)`: every exported layer was approved
