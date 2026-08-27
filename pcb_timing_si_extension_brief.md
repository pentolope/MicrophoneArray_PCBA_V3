# PCB Timing / SI Validation Extension Brief

## Objective

Extend the existing `PCB_AutoDesignAndTest` toolkit with a reusable, board-agnostic timing / signal-integrity validation layer that can:

1. Measure physical interconnect paths from the KiCad PCB.
2. Compose complete electrical paths across copper nets and explicitly modeled series components.
3. Extract or consume physical stackup information.
4. Estimate propagation delay and interconnect skew using a lightweight analytic backend.
5. Support optional higher-fidelity EM extraction later without making EM tooling a mandatory dependency.
6. Integrate results into the toolkit's existing manifest, gate, provenance, clean-room, and consumer-test architecture.
7. Use `MicrophoneArray_PCB_V3` as the first real consumer, with the PDM clock tree as the first validated interface.

The work must preserve the toolkit's existing fail-closed and provenance-oriented design.

---

# Repository / Branching Requirements

The parent repository currently pins the toolkit submodule at:

```text
a1e2bc1c7da9056bffd1baab3d55992ec6eb4f40
```

Start the toolkit work from **that exact revision**, not from the current `PCB_AutoDesignAndTest/main`.

The parent repository already supports overriding the committed submodule through:

```text
PCB_TOOLKIT_PATH
```

Use this during development and consumer testing.

Do **not** begin by updating the parent repository's committed submodule pointer.

A sensible workflow is:

```text
PCB_AutoDesignAndTest
  branch from a1e2bc1c7da9056bffd1baab3d55992ec6eb4f40
        │
        ▼
  implement + self-test
        │
        ▼
MicrophoneArray_PCB_V3
  PCB_TOOLKIT_PATH=<local toolkit checkout>
        │
        ▼
  consumer validation
```

Only update the parent's submodule pointer after the toolkit implementation and consumer integration are independently working.

---

# Existing Architecture to Preserve

The current toolkit already provides the core framework required for this extension.

In particular:

- `run.py` has a centralized `_load_gates()` import point.
- Gate behavior is manifest-driven.
- Required manifest keys can make a gate `NOT_APPLICABLE`.
- Validation is fail-closed when a required calculation cannot be performed.
- Results already support measurements, limits, findings, status, JSON, and Markdown output.
- Board geometry and topology logic already exist.
- External-consumer testing already exists.
- Clean-room validation hashes configuration and implementation inputs.
- A manifest's additive policy keys are naturally included in configuration identity unless explicitly excluded by existing clean-room rules.

Do not redesign these systems unless an actual limitation is discovered.

Prefer adding narrowly scoped modules and abstractions that fit them.

---

# Critical Architecture Requirement: Electrical Paths Are Not Single Nets

Do **not** implement timing as merely:

```text
NetGraph + ps/mm
```

Real timing paths often cross components and therefore cross KiCad net boundaries.

For example, one PDM clock branch in `MicrophoneArray_PCB_V3` is approximately:

```text
U2 output
   │
   │  PDM_CLK_Y0
   │
  RC1
  33R
   │
   │  PDM_CLK_B0
   │
 ┌─┴─────┐
 │       │
MK1.3   MK2.3
```

The existing PDM topology measurement starts at the output side of the series resistor and therefore measures only the post-resistor copper.

A reusable timing implementation must support the whole path.

## Required abstraction

Add a generic electrical-path abstraction above the existing copper/net topology machinery.

Conceptually:

```text
ElectricalPath
   │
   ├── CopperPath(net=A)
   │
   ├── ComponentTraversal(ref=RC1, pin1→pin2)
   │
   ├── CopperPath(net=B)
   │
   └── Endpoint
```

The existing net graph should remain a primitive used by this higher-level path system.

Do not redefine the existing topology rules to mean electrical timing paths.

## Component traversals

The abstraction should be extensible enough that a path step may eventually represent:

```text
resistor
ferrite bead
connector
package
cable
ESD device
IBIS-derived delay
Touchstone network
custom model
```

For the first implementation, only the minimal component traversal support necessary for the PDM clock tree is required.

A series resistor may initially contribute zero propagation delay if no delay model is declared, while still joining two net-local copper paths into one logical electrical path.

The framework must not invent delay through components.

---

# Physical Stackup

The current structural stackup gate is intentionally limited to copper layer order and plane assignment.

Do not change its semantics.

Create a separate physical-stackup representation for SI/timing work.

## Preferred source order

Use physical stackup information in this order:

1. Native KiCad 10 stackup data where available.
2. Board-owned supplemental manifest/model data where native data are incomplete.
3. Otherwise return insufficient-data / NOT_APPLICABLE / ERROR according to whether the analysis is optional or required.

Do not silently assume generic FR-4.

## Data of interest

The physical representation should be able to capture, where available:

```text
copper layer order
copper thickness
dielectric layer thickness
material
relative permittivity / Dk
loss tangent
overall thickness
reference-plane relationship
```

Preserve the distinction between:

```text
structural stackup
```

and:

```text
physical / electromagnetic stackup
```

---

# Analytic Propagation Backend

Implement a lightweight propagation backend before attempting full EM integration.

Its purpose is to provide fast, reproducible first-order delay and skew measurements.

## Required behavior

For each routed copper segment/path, derive enough geometry to estimate propagation velocity using:

```text
trace layer
trace width
reference-plane geometry
dielectric geometry
material / Dk
```

Then accumulate physical propagation delay through the electrical path.

## Important accuracy rule

Do **not** blindly use:

```text
v = c / sqrt(Dk)
```

for every layer.

That approximation is appropriate for a homogeneous dielectric model such as an idealized stripline, but not directly for outer-layer microstrip where the field is partly in air.

The implementation should either:

1. calculate an effective permittivity from geometry using a documented analytic approximation, or
2. consume an explicitly declared effective propagation model.

The output must record which model was used.

## Output provenance

Every timing result should record enough information to reproduce and audit the result, for example:

```text
path identifier
source endpoint
destination endpoint
copper length by layer
via count / via transitions
component traversals
physical stackup source
propagation model
model parameters
estimated delay
confidence / fidelity classification
```

---

# First Consumer: MicrophoneArray_PCB_V3 PDM Clock Tree

Use the PDM clock tree as the first real consumer.

The board uses an `SN74LVC244` fanout buffer and eight branch resistors.

Each branch crosses at least two nets:

```text
U2 output
→ PDM_CLK_Yn
→ RCn
→ PDM_CLK_Bn
→ microphone clock pads
```

## First useful gates

Implement board-agnostic primitives, then configure this consumer to produce results such as:

```text
TIMING.INTERCONNECT_DELAY
TIMING.INTERCONNECT_SKEW
```

Names may differ if there is a stronger convention in the codebase, but keep the concepts distinct.

### Interconnect delay

Measure passive PCB propagation delay between declared electrical endpoints.

### Interconnect skew

Compare passive PCB arrival-time differences among a declared endpoint group.

For the PDM tree, this can compare the branch paths from the relevant U2 outputs through each branch resistor to the microphone clock endpoints.

---

# Do Not Mislabel Interconnect Skew as Full Clock Skew

The PCB can provide passive interconnect delay.

It cannot, by geometry alone, provide total clock arrival time from silicon to receiver.

The full timing path may also depend on:

```text
buffer output-to-output skew
buffer propagation delay
driver slew
package delay
receiver threshold behavior
PVT variation
FPGA output timing
receiver setup / hold requirements
```

Therefore report different concepts separately.

For example:

```text
PCB interconnect skew
```

is not automatically:

```text
total clock arrival skew
```

If a gate only models the PCB interconnect, say so explicitly in the gate description and measurements.

---

# Setup / Hold Support

The long-term design should allow setup/hold checks, but do not fabricate timing requirements.

A setup/hold gate should only become applicable when the board manifest/model set contains enough endpoint information to evaluate it.

Possible required inputs include:

```text
source clock relationship
source tCO
receiver setup time
receiver hold time
clock uncertainty
device skew
package delay
PVT assumptions
```

If these are not supplied, a geometry-only interconnect gate may PASS while setup/hold gates are `NOT_APPLICABLE`.

That is desirable behavior.

---

# Manifest Design

Add timing/SI policy declaratively.

Do not put MicrophoneArray-specific assumptions in toolkit source.

A generic schema may eventually resemble:

```json
{
  "timing": {
    "interfaces": {
      "pdm_clock": {
        "paths": [],
        "limits": {}
      }
    }
  }
}
```

The exact schema should be chosen to fit the existing manifest style after inspecting the current parser and consumer manifests.

## Requirements

The manifest must be able to declare:

- named electrical endpoints;
- named endpoint groups;
- how electrical paths cross series components;
- expected reference clock / interface where relevant;
- skew or delay limits;
- optional device timing values;
- model files where applicable;
- optional solver/backend preferences.

Prefer stable identifiers over board-specific Python code.

---

# Model Files and Provenance

If timing data are contained directly in the manifest, the existing clean-room configuration identity should naturally cover them.

If separate files are introduced, such as:

```text
models/materials.json
models/devices.json
models/*.ibs
models/*.s2p
models/*.sNp
models/cables.json
```

they must participate in the existing source/provenance closure.

Do not allow a timing PASS to depend on an external model whose bytes are not represented in validation provenance.

---

# Optional openEMS Backend

openEMS is a future/high-fidelity backend, not the initial architecture.

Do not make openEMS a global toolkit dependency.

The current toolkit intentionally runs in KiCad's Python environment and preflight focuses on dependencies owned by KiCad / the validator environment.

An EM solver has a very different dependency profile.

## Preferred integration

Treat openEMS as an optional backend, preferably behind a subprocess boundary.

Conceptually:

```text
ElectricalPath / selected geometry
            │
            ├── analytic backend
            │
            └── openEMS backend
                     │
                     ▼
                 S-parameters
```

Both should feed a common higher-level timing/SI result layer.

## Availability behavior

If a manifest requires openEMS and it is unavailable:

```text
ERROR / fail closed
```

If openEMS is optional and an analytic backend is allowed:

```text
use analytic backend
```

and record the backend used.

Do not import openEMS at normal toolkit module-import time.

Do not make ordinary PCB validation fail solely because openEMS is not installed.

---

# Solver / Model Fidelity

Results should explicitly describe fidelity.

A useful classification could be:

```text
geometry-only
analytic transmission-line estimate
quasi-static extracted
full-wave extracted
device-aware timing
```

Exact names are flexible.

The point is that a PASS derived from first-order microstrip math must not appear indistinguishable from a PASS derived from a validated broadband EM/device model.

---

# Via Treatment

For the first analytic backend, include via vertical propagation length in physical path length where possible.

Do not invent via inductance or discontinuity delay unless using an explicit model.

Record:

```text
via count
layer transitions
vertical length
via delay model used
```

A simple geometric delay contribution is acceptable initially if clearly labelled.

---

# Reference Planes and Unsupported Cases

The analytic backend must fail clearly rather than silently return dubious numbers.

Examples that may require refusal or reduced-confidence handling:

```text
no identifiable reference plane
split-plane crossing
ambiguous return path
coplanar geometry not supported by the selected formula
broadside coupling
differential path using a single-ended model
missing dielectric geometry
unknown material properties
nonuniform stackup not represented by the model
```

Do not silently substitute defaults.

---

# Tests Required in PCB_AutoDesignAndTest

Add toolkit-level tests before relying on the microphone board.

At minimum include synthetic fixtures for:

1. Single-net copper path length.
2. Path with a via/layer transition.
3. Electrical path crossing a zero-delay series component.
4. Two paths with known length mismatch and expected skew ordering.
5. Missing timing policy → `NOT_APPLICABLE`.
6. Required timing policy with insufficient stackup data → fail closed.
7. Missing external model file → fail closed.
8. Model file participates in source/configuration provenance.
9. Existing gates remain unchanged.
10. Existing selftest still passes.

Prefer small deterministic fixtures where expected results are easy to calculate independently.

Do not base correctness solely on the microphone board.

---

# Consumer Tests Required in MicrophoneArray_PCB_V3

With the local toolkit selected through `PCB_TOOLKIT_PATH`:

1. Run toolkit preflight.
2. Run toolkit selftest.
3. Run normal parent validation.
4. Verify existing gates do not regress.
5. Add timing policy for the PDM clock tree.
6. Produce passive PCB delay/skew measurements.
7. Confirm all eight fanout branches are represented.
8. Confirm each branch path traverses the appropriate `RCn` resistor instead of beginning after it.
9. Check that result provenance identifies the physical-stackup source and analytic model.
10. Run any existing external-consumer test mechanism.

If the board's native KiCad stackup lacks enough physical material information, add the smallest explicit board-owned supplemental data necessary.

Do not insert guessed values merely to obtain PASS.

---

# Suggested Module Boundaries

Use judgment after inspecting the codebase, but a clean design may look approximately like:

```text
pcbqa/
    electrical_path.py
    stackup_physical.py
    propagation.py
    gates/
        g_timing.py
```

Possible responsibilities:

## `electrical_path.py`

- Endpoint identity.
- Copper path segments.
- Component traversal.
- Composition across nets.
- Path measurement.
- Reuse existing net/topology graph internally.

## `stackup_physical.py`

- Native KiCad stackup extraction.
- Supplemental-model merge/validation.
- Layer-to-reference geometry.

## `propagation.py`

- Analytic transmission-line models.
- Effective permittivity.
- Delay accumulation.
- Fidelity/model metadata.

## `g_timing.py`

- Manifest-facing gates.
- Limits.
- Results/findings.
- NOT_APPLICABLE / fail-closed behavior.

Do not force this exact file layout if the current repository has a stronger convention.

---

# Non-Goals for the First Implementation

Do not attempt all of the following at once:

- full IBIS simulation;
- transistor-level source modeling;
- full-board FDTD;
- automated EMI compliance prediction;
- arbitrary S-parameter circuit solving;
- package field solving;
- crosstalk signoff;
- simultaneous switching-noise modeling;
- comprehensive DDR timing;
- universal transmission-line geometry support.

The first milestone is deliberately narrower:

```text
generic electrical path
+ physical stackup
+ analytic passive propagation
+ reproducible delay/skew gates
+ real PDM consumer
```

---

# Implementation Sequence

Follow this order unless repository inspection exposes a concrete reason to change it.

## Phase 1 — Inspect and lock interfaces

- Confirm the exact pinned toolkit revision.
- Inspect existing graph/topology helpers.
- Inspect manifest access patterns.
- Inspect gate registration and result conventions.
- Inspect configuration/source-closure behavior.
- Inspect consumer-manifest test hook.
- Inspect KiCad stackup APIs in the supported runtime.

No implementation assumptions should be made where the current code already provides an abstraction.

## Phase 2 — Electrical path abstraction

Implement a generic electrical path composed from:

```text
endpoint
copper subpath
component traversal
copper subpath
endpoint
```

Use current topology code rather than duplicating PCB connectivity logic.

Add deterministic unit/fixture tests.

## Phase 3 — Physical stackup

Extract native KiCad physical stackup where available.

Add supplemental model support only where necessary.

Add validation for incomplete/ambiguous data.

## Phase 4 — Analytic propagation

Implement documented microstrip/stripline-effective propagation estimates.

Calculate:

```text
length by layer
via vertical length
estimated delay
```

Record model metadata.

## Phase 5 — Generic timing gates

Add manifest-driven passive delay/skew gates.

Preserve existing gate semantics and reporting patterns.

## Phase 6 — Microphone consumer

Describe the PDM clock electrical paths declaratively.

Ensure the paths traverse `RC1..RC8`.

Measure and compare passive PCB propagation delays.

## Phase 7 — Provenance and release integration

Confirm:

```text
timing policy → configuration identity
external timing models → source closure
implementation modules → implementation closure where appropriate
```

Run clean-room/release-related tests.

## Phase 8 — Optional EM interface skeleton

Only after the analytic path works, define a backend interface suitable for later openEMS integration.

A stub/interface plus tests is sufficient unless openEMS is already readily testable in the local environment.

Do not block the core timing implementation on openEMS.

---

# Acceptance Criteria

The task is complete when all of the following are true.

## Toolkit

- Existing tests pass.
- New timing tests pass.
- Existing topology/stackup gate semantics are unchanged.
- No MicrophoneArray-specific names or assumptions are hardcoded in generic toolkit modules.
- A timing gate not configured by a consumer is non-applicable rather than disruptive.
- Required timing analysis fails closed when required physical/model inputs are unavailable.
- Results identify the model/backend used.
- External model files are provenance-covered.

## Microphone consumer

- Existing board validation still works.
- The local toolkit override can validate the board before changing the committed submodule pointer.
- Eight PDM fanout branches are represented.
- Each complete branch includes its series resistor crossing.
- Passive interconnect delay is reported.
- Passive interconnect skew is reported.
- The output does not claim full clock timing unless device timing models are supplied.
- No material or device timing value is silently guessed.

## Maintainability

- New abstractions are reusable for unrelated boards.
- Component-crossing electrical paths can later support connectors, packages, cables, IBIS, and Touchstone without replacing the path representation.
- openEMS can later be added as another backend without rewriting the gates or board manifest structure.

---

# Engineering Rules

1. **Inspect before modifying.**
2. **Preserve existing public behavior unless required.**
3. **Do not guess electrical parameters.**
4. **Do not hardcode this board into the toolkit.**
5. **Do not make openEMS mandatory.**
6. **Do not conflate passive PCB delay with device-aware timing.**
7. **Do not weaken fail-closed behavior.**
8. **Do not bypass existing provenance machinery.**
9. **Prefer tests with independently calculable expected results.**
10. **Keep each commit logically reviewable.**

---

# Deliverables

Produce:

1. Toolkit implementation on a branch based on the pinned toolkit revision.
2. Toolkit tests/fixtures.
3. Parent-board timing policy/models.
4. Parent-board consumer tests/results.
5. Any necessary documentation.
6. A concise summary of:
   - architecture added;
   - assumptions;
   - supported geometries;
   - unsupported geometries;
   - fidelity of the analytic model;
   - what remains for device-aware timing;
   - what remains for openEMS integration.

Do not update the parent repository's committed submodule pointer until the implementation has passed testing via `PCB_TOOLKIT_PATH`.
