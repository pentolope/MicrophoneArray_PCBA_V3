---
name: claim-audit
description: Pre-publish audit that binds every claim-bearing word in a drafted commit message or report to an artifact recomputed on the spot. Run before any push of cycle work, and before writing a final report.
---

# Claim audit

Every genuine external review finding in this project's history falls
into two classes: **abstraction inflation** (the machinery measured X,
the summary claimed X′ one level up — "has tracks" became "routed",
a benchmark-set fraction became "fully connected", a per-net
inventory spread became "the clock tree is tighter") and **fail-open
defaults inside fail-closed designs**. This audit exists to catch
both before publication instead of a cycle later.

## Procedure

1. **Collect the drafts.** The commit message(s) and the report text
   about to be published, plus any README/doc sentences changed this
   cycle.

2. **Extract claim-bearing words.** Scan the drafts for:
   `complete`, `routed`, `connected`, `valid`, `verified`, `proven`,
   `all`, `every`, `fully`, `better`, `tighter`, `passes`,
   `unchanged`, `reproduces`, `bit-identical`, `identical`,
   `deterministic`, `fixed`, `solved`, `works`, `zero`, `never`,
   `always`. Each occurrence that asserts a fact about the work is a
   claim.

3. **Bind each claim to a recomputed artifact.** Build a claim table:
   claim → artifact path → command → recomputed result. The
   recomputation must read the ARTIFACT, never the process that made
   it: the board file over the router log, `validation.json` over a
   runner's tail, `classify_net` over track counts, the clean-clone
   file over the working tree. Run the commands NOW; a binding from
   memory is not a binding.

4. **Downgrade what cannot be bound.** A claim with no artifact is
   rewritten one abstraction level down to exactly what the evidence
   owns, or the missing evidence is produced first. Deleting the
   sentence is always acceptable; inflating it never is.

5. **Semantic-diff sweep.** If any load-bearing word changed meaning
   this cycle (as `routed` → `connectivity-complete` once did), grep
   every consumer of the old meaning — scripts, artifacts, docs,
   committed JSON — and list each as fixed or genuinely unaffected.
   A consumer you did not check is not unaffected.

6. **Fail-open hunt.** For every field, default, filename or label
   introduced this cycle, answer explicitly: what happens on
   silence, absence, staleness, forgery, and reuse? Each answer must
   be a refusal or an explicit recorded state — never a quiet pass.

7. **Publish the table.** The claim table (or its residue: "all N
   claims bound") goes into the final report. Claims removed or
   downgraded during the audit are noted as such — the audit is
   itself evidence.

## Notes

- The audit applies to BOTH repositories' drafts when a cycle pushes
  both.
- Numbers quoted in prose (counts, percentages, millimetres) are
  claims too; each needs a source artifact.
- "X is unchanged" claims bind to a digest comparison, not to the
  absence of an edit you remember making.
