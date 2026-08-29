---
name: pre-push-review
description: Fresh-context adversarial review of the pending diff and report draft before any push. Spawns a subagent that shares none of the session's assumptions and attacks claims rather than admiring mechanisms. Runs a second pass, capped at two, when the fixes from the first asserted something no reviewer has checked.
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

5. **Review again unless everything written after a pass only
   removed or weakened a claim.** A review verifies the text it was
   handed; every fix
   made afterwards is the least-reviewed text in the cycle. The
   trigger is not a feeling about size — it is direction, and the
   default is that text asserts.

   - **Safe:** a fix that only DELETES text *and* widens no
     surviving claim, or that strictly weakens an existing sentence
     to what the evidence already owns.
   - **Asserting — everything else.** Not a checklist to satisfy;
     examples: a new number, a new citation (attempt id, path, byte
     offset, line reference), a new causal explanation, a re-run
     cited for freshness whether or not its measurements moved, a
     universal negative ("appears nowhere", "the only", "invalidated
     nothing"), a widened scope, a dismissal rationale written under
     step 4, or any edit to code or configuration rather than prose.
   - **Deletion is not automatically safe.** Removing a scope
     caveat, a "measured on this machine" qualifier, or a recorded
     dismissal reason widens whatever survives it. That is an
     assertion made by subtraction.
   - **Tie-break:** if it is unclear which way a fix moved, it
     asserted.

   Record the call for each fix. A classification that authorises
   skipping a pass is itself unauditable unless it is written down.

   The case this rule was written from: a first pass rejected a
   causal explanation, the fix replaced it with a different
   explanation, and a second pass found the replacement wrong too.

   The next reviewer gets the package refreshed, the previous pass's
   findings, and **the diff of the drafts** — not the author's
   account of what they changed. The findings are another agent's
   output and are evidence; a summary of one's own fixes is the
   session's reasoning wearing a witness's coat, and it lets the
   author frame each fix for the reviewer judging it. Note also that
   a claim the previous pass cleared was cleared against the
   PRE-FIX text; neighbouring edits can have changed what it means.

6. **Two passes per cycle, and the window after the last pass is
   closed.** Two for the whole cycle — not two per repository, not
   two per round of fixes.

   After the final pass, only deletions that widen no surviving
   claim, and strict weakenings, may reach the push. A finding that could be answered only by a new
   assertion is answered by removing the claim instead. Without
   this, the cap is a hole rather than a bound: an author who wanted
   an unexamined assertion to ship would simply hold it for the last
   round, which is the one nobody reads.

7. **Push only after triage.** The report states how many passes
   ran, each pass's finding count and disposition, and — separately
   — every fix made after the LAST pass that reached the push, with
   its direction call. If no fix followed the last pass, say that.
   The disclosure is owed whenever text outran its review, not only
   when a second pass happened to run.

## Notes

- The subagent's value is its ignorance; do not "brief" it with the
  session's reasoning, conclusions, or excuses beyond the package.
  Step 5's hand-off obeys this: findings and a diff, never a
  narrative.
- If the subagent cannot run (no capacity), say so in the report
  rather than silently skipping — an unreviewed push is a fact worth
  recording.
- A second pass is cheap next to the thing it prevents, which is a
  wrong claim published as fact under the authority of having been
  reviewed. Expect most cycles to use both passes: adding the
  evidence a reviewer asked for is itself an assertion, so the
  common triage round trips the trigger by design.
- The cap is two passes for the whole cycle, not two per repository
  and not two per round of fixes. A cycle that pushes both repos
  reviews both in one package.
- This skill exists in two places — the project copy and
  `~/.claude/skills/pre-push-review/` — and nothing makes them track
  each other. Edit both in the same change, and note that the
  user-level copy's step 1 depends on `claim-audit` also being
  installed there.
