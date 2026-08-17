# Quarantined tools — do not run against the authoritative board

These three scripts edit routed copper in place. That is exactly what this
project's process forbids: a failure is fixed in whichever reproducible input
caused it — the generator, the rules, the keep-outs, or the routing plan — and
the board is built again from the beginning.

They are kept because they are part of how the committed board came to be, and
because `docs/status.md` refers to them. They are **not** part of the build
pipeline: `tools/build.py` does not call them, and nothing imports them except
`place_testpoints.py`, which imports `patch_board.py`.

| Script | What it does | Why it is here rather than deleted |
|---|---|---|
| `patch_board.py` | Edits the specific items DRC objects to, leaving other tracks alone | Imported by `place_testpoints.py`; referenced by `docs/status.md` |
| `place_testpoints.py` | Places probe pads on top of existing copper of their own net | Referenced by `docs/status.md`; the record of how the 14 placed test points got there |
| `cleanup_tracks.py` | Four-pass tidy of routed copper (snap, merge, prune, simplify) | Referenced by `docs/status.md` as the record of a pass the committed copper has been through |

## If you are tempted to run one

Don't. Running any of these invalidates the two approved DRC waivers in
`board/manifest.live.json`, because those are bound to the SHA-256 of
`microphone_array_v2.kicad_pcb`. The board would then fail validation for a
reason that has nothing to do with the change you meant to make.

If copper genuinely needs to change, change the input that produces it and
regenerate — then re-approve the waivers against the new board, with reasons.
