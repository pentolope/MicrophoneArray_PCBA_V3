---
name: accountability-review
description: Fresh-context review, before any push, of whether the work actually did what the user asked. A subagent that shares none of the session's assumptions measures the change against the prompt, using the session's own stated claims as the instrument. Runs up to twice.
---

# Accountability review

The question is not "are these claims true". The question is **did
this do what the user asked for**.

An in-context self-review cannot answer that honestly. By the end of
a cycle the author has substituted their own model of the task for
the request that started it, and they verify the thing they built
against the failure they imagined. This skill buys what an outside
reviewer has - fresh context, and no stake in the work being finished
- and spends it on the only question that decides whether the cycle
was worth running.

Claim accuracy matters, but it is the **instrument, not the object**.
A reviewer cannot see the work; they can only see the diff and what
the author says about it. An inflated claim is not primarily a lie -
it is an opaque pane over exactly the place where the work may have
drifted from the request. "Every net is routed" hides which nets are
not. So the drafts must be accurate in order for anyone to check
fidelity through them, and that is why the claim audit runs first.

## Procedure

1. **Write down what was asked.** Before anything else, reconstruct
   the user's request in their own words where possible: what they
   asked for, what they ruled out, what they said not to touch, and
   any constraint stated once and never repeated. This is the
   standard. If the request changed mid-cycle, record the latest
   form and the change.

2. **State the claim to the prompt.** An itemised statement, one row
   per thing the user asked for: the requirement, what was done
   about it, and the artifact that shows it. Include rows for
   requirements deliberately NOT met, and say why. This table is the
   thing the reviewer audits - it is how a reader who was not here
   can tell whether the request was answered.

3. **Run `/claim-audit`** if it has not run on the current drafts.
   Its job here is subordinate: every claim-bearing word binds to an
   artifact recomputed on the spot, so that the fidelity table above
   can be trusted as evidence rather than as assurance.

4. **Assemble the review package**, nothing else:
   - the request, as written down in step 1;
   - the claim to the prompt from step 2;
   - the pending diff against each remote tip
     (`git diff origin/<branch>` in each repo being pushed);
   - the drafted commit message(s) and report text;
   - the claim table from the audit;
   - the standing invariants, stated verbatim: never modify the
     authoritative Board A PCB; never touch `main`; no PRs; never
     submit an order; fail-closed over fail-open; waivers are bound
     to board bytes; the board file - never a tool log - is the
     arbiter; unmeasured never becomes zero.

5. **Spawn a general-purpose subagent** with only that package,
   **running on Opus 5** (pass the model override; user-directed
   2026-08-28): a different model family from the authoring session
   shares fewer of its blind spots. Its instructions, verbatim in
   spirit:
   - You are reviewing work you did not do, against a request you
     did not write. Your primary question is whether the change does
     what the request asked for. The diff and the repository
     artifacts are the only evidence; do not trust the drafts.
   - Hunt, in order of consequence: (a) **a requirement dropped,
     silently narrowed, deferred, or answered with something
     adjacent to what was asked**; (b) **a constraint violated** -
     something the user ruled out, or said not to touch, that the
     diff touches; (c) **scope taken that was not requested**;
     (d) claims that cannot be trusted, *because* an untrustworthy
     claim is where (a) and (b) hide - abstraction inflation, and
     fail-open defaults whose behaviour on silence, absence,
     staleness, forgery or reuse is a quiet pass.
   - Also check each standing invariant against the diff directly.
   - For every finding, name: the exact claim or code, which part of
     the request it bears on, why it is wrong, and the artifact or
     command that would prove or disprove it. Rank by consequence to
     the request, not by how clever the finding is. Say "no
     findings" only after stating what you checked.

6. **Triage against the request.** For each finding, the question is
   not "is the reviewer right" but **"does fixing this change
   whether the user got what they asked for"**.
   - If it does, fix it before the push. Being late in the cycle is
     not an argument.
   - If it does not - the finding is correct but immaterial to the
     request - record it with that reasoning and move on. A review
     is not a ratchet, and gold-plating a cycle is its own way of
     not doing what was asked.
   - If the reviewer misread the request, say so and cite the
     request. Dismissals carry their evidence.

7. **Review a second time when the fixes could have changed the
   answer.** Two passes exist to give two chances at catching a
   miss, and the second is owed when the first pass's fixes touched
   behaviour, scope, or the evidence the reviewer relied on - that
   is, when "did we do what was asked" might now answer differently
   than the text pass 1 read. Pure clarifications of wording that
   change no behaviour and no evidence do not earn a pass; when it
   is unclear, take the pass.

   The next reviewer gets the package refreshed, the previous
   pass's findings, and the diff of the drafts - not a narrative
   about what was changed. Its instructions gain one line: **check
   whether each fix is itself correct, and whether it moved the work
   closer to the request or merely closer to the last review.**

8. **A cycle is one user prompt.** Not one commit, not one push, not
   one repository - the whole of the work done in response to a
   single request, however many commits or repositories that takes.
   Splitting work across more commits buys no additional passes.

   **Not every prompt opens a cycle.** A prompt that asks a
   question, corrects a misunderstanding, or directs a small
   adjustment whose correctness the user is themselves asserting
   owes no review. The review exists to catch this session drifting
   from an intent it had to infer; when the user is specifying the
   change directly and is present to see the result, there is no gap
   for a fresh reviewer to find, and running one is ceremony.

   A cycle is opened by **actual work on the repository**: anything
   that changes what the board is, what the gates measure, what a
   release contains, what the tooling does, or what the
   documentation asserts on the project's behalf. When it is
   genuinely unclear, review - one pass is cheaper than a cycle
   nobody checked against the request. But do not manufacture a
   review for a prompt that only told you what to type.

9. **Two passes per cycle, and no more.** If the second pass finds
   something that means the user did not get what they asked for,
   fix it and push - the request outranks the review budget, and a
   cap that forbade correcting a real miss would defeat its own
   purpose. What the cap forbids is spending further passes
   polishing. Anything that reaches the push after the last review
   is named in the report.

10. **Push only after triage.** The report states: how many passes
   ran; each pass's findings, with which part of the request each
   bore on and how it was disposed of; and anything that went out
   after the last review. If a requirement was not met, that belongs
   in the report in plain words, not in an omission.

## Notes

- The subagent's value is its ignorance of the session, not of the
  task. Give it the request in full; withhold the session's
  reasoning, conclusions and excuses.
- If the subagent cannot run, or the user directs that it be
  skipped, say so in the report. An unreviewed push is a fact worth
  recording, not a gap to leave silent.
- Expect most cycles to use both passes. Adding the evidence a
  reviewer asked for usually changes what the next reviewer would
  see, and that is the case the second pass exists for.
- A finding that is true, sharp, and irrelevant to the request is
  still irrelevant to the request. Record it; do not let it redirect
  the cycle.
- The cap is two passes for the whole cycle as step 8 defines it -
  not two per repository, not two per commit, not two per round of
  fixes. A cycle that pushes both repos reviews both in one package.
- The copy in this repository is the authoritative one; it is
  versioned with the work it governs, so a reader can see which rule
  a given cycle was run under. A convenience copy may sit in
  `~/.claude/skills/accountability-review/`, but nothing makes the
  two track each other - edit both in one change, and prefer this
  one when they disagree.
