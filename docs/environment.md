# Development environment

The canonical environment is **Ubuntu 24.04 LTS with the system Python 3**.
Windows is no longer supported: the generator, the validator, the router and
the release chain all run against distribution packages, and nothing in the
repository looks for a bundled KiCad interpreter any more.

The single fact that makes this simpler than the Windows arrangement it
replaces: on Ubuntu the `kicad` package installs `pcbnew.py` into
`/usr/lib/python3/dist-packages` and `kicad-cli` onto `PATH`, built together
from one source tree. The interpreter you already have is the supported one,
so `python3 tools/build.py` and `python3 .../run.py validate` are the whole
invocation.

## What is installed, and where it came from

Everything below is an Ubuntu package from `archive.ubuntu.com`, except KiCad,
which is explained in the next section. Recorded as verified on 2026-08-29.

| Package | Version |
|---|---|
| `git` | 1:2.43.0-1ubuntu7.3 |
| `curl` | 8.5.0-2ubuntu10.13 |
| `wget` | 1.21.4-1ubuntu4.5 |
| `unzip` | 6.0-28ubuntu4.1 |
| `jq` | 1.7.1-3ubuntu0.24.04.2 |
| `build-essential` | 12.10ubuntu1 (gcc/g++ 13.3.0, make 4.3) |
| `cmake` | 3.28.3-1build7 |
| `pkg-config` | 1.8.1-2build1 |
| `python3` | 3.12.3-0ubuntu2.1 |
| `python3-pip` | 24.0+dfsg-1ubuntu1.3 |
| `python3-venv` | 3.12.3-0ubuntu2.1 |
| `ngspice` | 42+ds-3build1 |
| `libngspice0` | 42+ds-3build1 |
| `verilator` | 5.020-1 |
| `xvfb` | 2:21.1.12-1ubuntu1.6 |
| `python3-numpy` | 1:1.26.4+ds-6ubuntu1 |
| `python3-scipy` | 1.11.4-6build1 |
| `python3-shapely` | 2.0.3-1build2 |
| `python3-pil` | 10.2.0-1ubuntu1.2 |
| `python3-wxgtk4.0` | 4.2.1+dfsg-3build2 |
| `rustc-1.85` / `cargo-1.85` | 1.85.1+dfsg0ubuntu2~bpo0-0ubuntu0.24.04.2 |
| `kicad`, `kicad-symbols`, `kicad-footprints`, `kicad-templates`, `kicad-libraries` | 10.0.5~ubuntu24.04.1 |

`python3-numpy`, `python3-scipy` and `python3-shapely` are what the toolkit and
KiCad Routing Tools import; `python3-pil` is used by the layer plotter and
`python3-wxgtk4.0` is KiCad's own dependency, which `pcbqa.headless` configures
so a wxWidgets assert can never raise a modal box on an unwatched screen.

## KiCad is pinned to 10.0.5

Ubuntu 24.04 ships KiCad 7, which is far too old; the KiCad project's own
Launchpad PPA currently publishes 10.0.6. This board is pinned to **10.0.5**,
so the five packages were taken from that PPA's pool at exactly
`10.0.5~ubuntu24.04.1` and installed as local `.deb` files:

```
de270bbac2bb26c698f2d8fce1b0b6fd186c7e0884660a91429195af13dc50ee  kicad_10.0.5~ubuntu24.04.1_amd64.deb
eced92573b69593fc1b1126310d2e67a5d836b7073acd1498d17f51893fb3197  kicad-footprints_10.0.5~ubuntu24.04.1_all.deb
50daee3aa572b90baf23ffb609f1f49b68305e79abc5fa50dab1441400b47a9b  kicad-libraries_10.0.5~ubuntu24.04.1_all.deb
2d93c3d277114561f8435001f6b95540f6df81c2660ec576bef543fd6a2ce702  kicad-symbols_10.0.5~ubuntu24.04.1_all.deb
0dccdffd1fabf5e0df92459f08270be40de6989b0bce929b9a0d1c623d2607a4  kicad-templates_10.0.5~ubuntu24.04.1_all.deb
```

The PPA is deliberately **not** added to `apt`'s sources. That is the strongest
available pin: with no source offering a newer KiCad, no upgrade can arrive by
accident. `apt-mark hold` is set on all five as a second, declared barrier.

The honest limit of this: 10.0.5 is superseded, so it is no longer covered by
the PPA's signed `Packages` index - only the current 10.0.6 is. The files were
fetched over HTTPS from Launchpad and confirmed byte-identical from two
independent Launchpad endpoints (the PPA pool and `launchpad.net/+files`), which
is corroboration, not an archive signature. `kicad-packages3d` is deliberately
omitted: 3.2 GB of 3D models that no gate, Gerber or DRC run reads.

## Rust, for the router

KiCad Routing Tools ships a Rust extension (`rust_router`, crate
`grid_router`). Ubuntu's default `cargo` is 1.75, which cannot parse that
crate's `Cargo.lock` - it is lockfile format v4, which needs cargo 1.78 or
newer. Ubuntu carries newer Rust as versioned packages in `noble-updates`, so
`rustc-1.85`/`cargo-1.85` are installed and symlinked into `/usr/local/bin`.
The unusable `rustc` and `cargo` 1.75 *binary* packages are removed; their
`libstd-rust-1.75` and `libstd-rust-dev` runtimes are still installed, because
nothing needed them gone and apt did not pull them. No `rustup`, no
third-party binary.

The router is built from source rather than downloaded:

```bash
cd /home/pentolope/github/KiCadRoutingTools && python3 build_router.py --from-source
```

That writes `rust_router/grid_router.so`, which is `.gitignore`d, so building
leaves the KiCadRoutingTools checkout's tracked tree untouched. A clean
rebuild takes under 30 s of wall time - the one run with a surviving log
reports `Finished release profile in 26.80s`, 26.89 s wall - with the cargo
registry already populated; `--clean` removes `target/` and the `.so`, not the
registry.

One property worth knowing before you rebuild casually: the crate is **not
bit-reproducible**, measured rather than assumed. Two clean rebuilds
(`--clean` then `--from-source`) in this environment, same source, same
`cargo 1.85.1`, eighteen minutes apart, gave `cf62ad5d..` and `97db6617..`.

The two binaries are the same length and differ in exactly **24 of 1,150,552
bytes**, across three contiguous runs from two causes, neither of them code:

- **20 bytes at `0x280`** - the ELF `.note.gnu.build-id` descriptor (the
  section starts at `0x270`; 16 bytes of note header precede it), regenerated
  at every link: `bbb84a28..` against `5e50da68..`.
- **4 bytes inside the string at `0xc4a87`** - an ASCII `__TIME__` in
  `.rodata`, `13:13:18` against `13:31:42`. Only the differing digits show as
  runs, at `0xc4a8a` and `0xc4a8d`. It is mimalloc's: `libmimalloc-sys` is the
  only C-compiling dependency in `Cargo.lock`, and the literal that follows it
  in `.rodata` is mimalloc's own `option '%s': %ld %s`. The adjacent
  `__DATE__` matched only because both builds ran on one day.

Scope that result honestly. It says these two builds differed in these two
ways; it does not say a rebuild anywhere would. Absolute paths *are* embedded
in the binary - 33 `/home/pentolope/.cargo/registry/...` panic locations and
27 `/build/rustc-1.85-...` strings - and they held still only because both
builds ran under one `$HOME` and one `CARGO_HOME`. A rebuild under a different
home, registry or toolchain path would differ by far more than 24 bytes. What
does *not* appear at all is the crate's own checkout path, which is embedded
zero times.

`pcbqa.krt.identity_digest` hashes this binary, so every rebuild is a
different router identity by design, and any routing-derived artifact bound to
the old one goes stale with it.

No artifact in this repository binds to a binary built here. The committed
records that carry a `grid_router` digest are five benchmark artifacts under
`benchmark/`, and every one names a Windows `grid_router.pyd` from an earlier
machine (`bc71d260..8218a031`, in `benchmark/boardB/candidates/seed{36,37}`
and `benchmark/krt_bench/results/seed{13,17,34}/krt0213-a`). Those record runs
that happened elsewhere. The two local hashes appear nowhere except this
paragraph. A rebuild therefore invalidates nothing today - which stops being
true the moment a routed candidate is recorded against a locally built router.

## Where the board finds its tools

`board/toolchain.json` is the only place a tool location is declared:

- `kicad.cli` and `kicad.python` are **bare command names**, resolved on
  `PATH`. `pcbqa.preflight.resolve_tool` turns a bare name into the absolute
  path that actually ran, and the run records that.

  `board/manifest.live.json` deliberately does the opposite and names
  `/usr/bin/kicad-cli` in full. That string is inside
  `constraint_manifest_sha256`, so a bare name there would leave the
  intended binary bound by no digest at all. It costs nothing: the DRC
  gate's canonical command reduces argv[0] to a basename, so the waivers'
  `approved_command_sha256` is the same either way. Even so, recording is
  not binding - no digest in the waiver set distinguishes two kicad-cli
  builds; what the run guarantees is that it says which one answered.
- `kicad.stock_footprint_libraries` and `kicad.stock_symbol_libraries` point at
  `/usr/share/kicad/`, and `tools/gen_pcb.py` and `tools/gen_schematic.py`
  derive every library path from them rather than restating any.
- `router.development_checkout` names the KiCadRoutingTools checkout;
  `router.plugin_dirs` is the Linux KiCad user base, `~/.local/share/kicad/`.

## Reproducing this environment

```bash
sudo apt-get install -y --no-install-recommends git curl wget unzip jq build-essential cmake pkg-config python3 python3-pip python3-venv ngspice verilator xvfb python3-numpy python3-scipy python3-shapely python3-pil python3-wxgtk4.0 rustc-1.85 cargo-1.85
```

Then install the five pinned KiCad 10.0.5 `.deb` files listed above from
`https://ppa.launchpadcontent.net/kicad/kicad-10.0-releases/ubuntu/pool/main/k/`,
`apt-mark hold` them, and check the result:

```bash
python3 tooling/PCB_AutoDesignAndTest/run.py preflight board/manifest.live.json
```
