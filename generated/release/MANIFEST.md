# Fabrication release manifest

- generated: 2026-08-23T21:17:54.540789+00:00
- kicad: 10.0.5
- constraint profile: jlcpcb-4layer-assembled
- source closure sha256: `a9cb7987172982dbb9eac42b96e1ba3146345e03c2c578cfecb9fa60a6aacfbd`

## command

    kicad-cli.exe sch erc --output C:\Users\pentolope\Documents\GitHub\MicrophoneArray_PCB_V3_Improve\out\microphone-array-v2-live\attempts\20260823T211723Z-663d6a97\work\clean_run\reports\erc.json --format json --severity-all --severity-exclusions --exit-code-violations C:\Users\pentolope\Documents\GitHub\MicrophoneArray_PCB_V3_Improve\out\microphone-array-v2-live\attempts\20260823T211723Z-663d6a97\work\clean_run\fixture\project\microphone_array_v2.kicad_sch
    kicad-cli.exe pcb drc --output C:\Users\pentolope\Documents\GitHub\MicrophoneArray_PCB_V3_Improve\out\microphone-array-v2-live\attempts\20260823T211723Z-663d6a97\work\clean_run\reports\drc.json --format json --severity-all --severity-exclusions --all-track-errors --schematic-parity --refill-zones --save-board --exit-code-violations C:\Users\pentolope\Documents\GitHub\MicrophoneArray_PCB_V3_Improve\out\microphone-array-v2-live\attempts\20260823T211723Z-663d6a97\work\clean_run\fixture\project\microphone_array_v2.kicad_pcb
    kicad-cli.exe pcb export gerbers --output C:\Users\pentolope\Documents\GitHub\MicrophoneArray_PCB_V3_Improve\out\microphone-array-v2-live\attempts\20260823T211723Z-663d6a97\work\clean_run\generated\gerbers --layers F.Cu,In1.Cu,In2.Cu,B.Cu,F.Paste,B.Paste,F.Silkscreen,B.Silkscreen,F.Mask,B.Mask,Edge.Cuts --no-x2 --no-netlist --use-drill-file-origin --subtract-soldermask C:\Users\pentolope\Documents\GitHub\MicrophoneArray_PCB_V3_Improve\out\microphone-array-v2-live\attempts\20260823T211723Z-663d6a97\work\clean_run\fixture\project\microphone_array_v2.kicad_pcb
    kicad-cli.exe pcb export drill --output C:\Users\pentolope\Documents\GitHub\MicrophoneArray_PCB_V3_Improve\out\microphone-array-v2-live\attempts\20260823T211723Z-663d6a97\work\clean_run\generated\gerbers --format excellon --excellon-separate-th --drill-origin plot C:\Users\pentolope\Documents\GitHub\MicrophoneArray_PCB_V3_Improve\out\microphone-array-v2-live\attempts\20260823T211723Z-663d6a97\work\clean_run\fixture\project\microphone_array_v2.kicad_pcb
    kicad-cli.exe pcb export pos --output C:\Users\pentolope\Documents\GitHub\MicrophoneArray_PCB_V3_Improve\out\microphone-array-v2-live\attempts\20260823T211723Z-663d6a97\build\cpl.csv --format csv --units mm --side both --exclude-dnp C:\Users\pentolope\Documents\GitHub\MicrophoneArray_PCB_V3_Improve\out\microphone-array-v2-live\attempts\20260823T211723Z-663d6a97\work\clean_run\fixture\project\microphone_array_v2.kicad_pcb
    kicad-cli.exe sch export bom --output C:\Users\pentolope\Documents\GitHub\MicrophoneArray_PCB_V3_Improve\out\microphone-array-v2-live\attempts\20260823T211723Z-663d6a97\build\bom.csv --fields ${QUANTITY},Reference,Value,Footprint,LCSC --labels "Quantity,Designator,Comment,Footprint,LCSC Part #" --group-by Value,Footprint,LCSC --exclude-dnp --ref-range-delimiter  C:\Users\pentolope\Documents\GitHub\MicrophoneArray_PCB_V3_Improve\out\microphone-array-v2-live\attempts\20260823T211723Z-663d6a97\work\clean_run\fixture\project\microphone_array_v2.kicad_sch
    rename "13 file(s)"
    orient "103 placement(s)"
    relabel cpl.csv
    relabel bom.csv

## artifacts

- `microphone_array_v2-revA-fabrication.zip` sha256 `5752dbe5573c48a2ac12a09f17da26f0382891e8bd04ffc6d48191be57ee859e`
- `bom.csv` sha256 `a3970d865b3b76677afe0b11b6061156e3be633a50613167887ef8a54fe14563`
- `cpl.csv` sha256 `28091e4dbf5a05e30af72f833839194c67c0aa0f90c6f797d0434fce84e7e807`

## excluded from the archive

- `(none)`: every exported layer was approved
