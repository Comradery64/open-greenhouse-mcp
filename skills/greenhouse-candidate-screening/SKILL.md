---
name: greenhouse-candidate-screening
description: Produce a structured screening summary for a specific Greenhouse candidate (resume, application history, current pipeline stage, and a recommendation) using the Greenhouse MCP tools. Use this whenever the user asks to screen, review, evaluate, assess, or "take a look at" a named candidate or applicant, wants a "candidate summary," asks "should we move this person forward," or pastes in a candidate's name/resume/application link expecting an assessment. This is also the right skill for any follow-up action on a specific candidate (advance, reject, add a note) once they've been screened — those are write actions and this skill's confirmation rule governs them.
---

# Greenhouse candidate screening

Turns raw Greenhouse data about one candidate into a decision-ready summary, and safely handles any follow-up action the recruiter wants to take on that candidate.

## When this applies

Trigger when the user names a specific candidate (by name, application ID, or a link) and wants an assessment, summary, or help deciding what to do with them. If the user instead wants a sweep across many candidates or a whole pipeline, use `greenhouse-pipeline-search` or `greenhouse-resume-batch-review` instead — this skill is for one candidate at a time.

## How to do it

1. Identify the candidate. If the user gave a name but there are multiple matches (common candidate names, or the same person applying to multiple jobs), ask which one before proceeding rather than guessing.
2. Call `screen_candidate` for that candidate — it's the composite tool built for exactly this, pulling resume, application, and pipeline stage together in one call rather than making you stitch together several raw API calls.
3. Read the resume and application content it returns, not just the metadata. The recruiter wants your read on fit, not a reformatted copy of their resume.
4. Note anything that would change how a recruiter should act on this: gaps in employment, mismatch between resume and job requirements, how long they've been sitting in the current stage, internal notes or scorecards already on file if the tool surfaces them.
5. Give a plain-language recommendation — advance, hold, or pass — with the reasoning, not just a verdict. The recruiter is accountable for the hiring decision; your job is to make that decision easy to reach quickly, not to make it for them silently.

## Output format

```
## Screening summary — [Candidate name]
**Job:** [job title] · **Stage:** [current pipeline stage] · **Applied:** [date/how long ago]

**Background:** [2-4 sentence synthesis of resume + application, focused on relevance to the role]

**Notable:** [anything that stands out — strong match, gap, overqualified, unclear fit, red flag worth double-checking — omit if nothing stands out]

**Recommendation:** [Advance / Hold / Pass] — [one or two sentence reasoning]
```

## Before any write action

Screening itself is read-only. But recruiters often follow up with "ok, move them to the next stage" or "reject them" or "add a note saying X" — those are real hiring decisions with an audit trail attached to the recruiter's own Greenhouse account, so:

1. **Always restate the specific action in plain language and get an explicit yes before calling any write tool** (advance_application, reject_application, add_note_to_candidate, or similar) — e.g. "Just to confirm: you want me to move Jane Doe from Phone Screen to Onsite for the Backend Engineer role — go ahead?" A "yes" to the screening summary is not the same as a "yes" to a write action; ask separately.
2. **If the write fails because the connected API key doesn't have permission** (the user's key may be scoped to a read-only or recruiter-safe profile), say so directly — e.g. "I don't have permission to do that with your current Greenhouse access — you may need to do this step yourself in Greenhouse, or ask your admin about your key's permissions." Don't retry with a different tool to work around a permissions restriction; it's there on purpose.
3. **Never guess at destructive actions.** Rejecting a candidate or removing them from a pipeline can't be casually undone — if there's any ambiguity about which candidate or which action the user means, ask rather than assume.
