---
name: pre-push-review
description: Fresh-context adversarial review of the pending diff and report draft before any push. Spawns a subagent that shares none of the session's assumptions and attacks claims rather than admiring mechanisms.
---

# Pre-push adversarial review

An in-context self-review shares the session's blind spots: the
author verifies the mechanism just built against the failure just
imagined. This skill buys what an external reviewer has — fresh
context — before the push instead of after it.

## Procedure

1. **Run `/claim-audit` first** if it has not been run on the current
   drafts. Its claim table is an input to this review.

2. **Assemble the review package**, nothing else:
   - the pending diff against each remote tip
     (`git diff origin/<branch>` in each repo being pushed);
   - the drafted commit message(s) and report text;
   - the claim table from the audit;
   - the standing invariants, stated verbatim: never modify the
     authoritative Board A PCB; never touch `main`; no PRs; never
     submit an order; fail-closed over fail-open; waivers are bound
     to board bytes; the board file — never a tool log — is the
     arbiter; unmeasured never becomes zero.

3. **Spawn a general-purpose subagent** with only that package,
   **running on Opus 5** (pass the model override; user-directed
   2026-08-28): a different model family from the authoring session
   shares fewer of its blind spots. Its instructions, verbatim in
   spirit:
   - You are reviewing work you did not do. Do not trust any
     assertion in the drafts; the diff and the repository artifacts
     are the only evidence. You may run read-only commands to check
     anything; you must not edit files.
   - Hunt two failure classes specifically: (a) abstraction
     inflation — any summary word one level above what the cited
     artifact actually measures; (b) fail-open defaults — any new
     field, filename, label or default whose behavior on silence,
     absence, staleness, forgery or reuse is a quiet pass.
   - Also check each standing invariant against the diff directly.
   - For every finding, name: the exact claim or code, why it is
     wrong, and the artifact or command that would prove or disprove
     it. Rank findings by consequence. Say "no findings" only after
     stating what you checked.

4. **Triage.** Genuine findings are fixed before the push — never
   argued away because the cycle is late. Dismissed findings are
   recorded in the report with the reason and the evidence for the
   dismissal.

5. **Push only after triage**, and note in the report that the
   review ran, with its finding count and disposition.

## Notes

- The subagent's value is its ignorance; do not "brief" it with the
  session's reasoning, conclusions, or excuses beyond the package.
- If the subagent cannot run (no capacity), say so in the report
  rather than silently skipping — an unreviewed push is a fact worth
  recording.
