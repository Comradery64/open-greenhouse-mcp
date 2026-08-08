---
name: greenhouse-resume-batch-review
description: Review and rank an entire batch of resumes/candidates for a Greenhouse requisition (not filtered by specific criteria), producing a shortlist, using the Greenhouse MCP tools. Use this whenever the user wants to go through "everyone" or "all" candidates/applicants for a role — phrases like "review all the resumes for [role]," "help me shortlist candidates for [job]," "go through the applicants for this req," or "rank everyone who applied" all mean this skill applies. This differs from greenhouse-pipeline-search, which filters by specific stated criteria — this skill is for an open-ended read-through-everything-and-rank task, and from greenhouse-candidate-screening, which handles one named candidate at a time.
---

# Greenhouse resume batch review

Reads through a whole batch of resumes for a requisition and produces a ranked shortlist with reasoning, so the recruiter doesn't have to open every application individually.

## When this applies

Trigger when the user wants a sweep across all (or most) candidates for a role, with no specific filter criteria — the goal is "help me find the best of everyone who applied," not "find people who match X." If they give you a specific filter (a skill, years of experience, location), that's `greenhouse-pipeline-search` instead — it's a much cheaper and more precise operation than reading every resume.

## How to do it

1. Confirm the scope: which job/requisition, and whether "everyone" means all-time applicants or a specific window (e.g. this round of applications). If there could be hundreds of applicants, mention that up front and ask if they want the full batch or a recent slice — reading hundreds of resumes is slow and burns a lot of the conversation's budget for not much extra signal once you're past the first couple hundred.
2. Use `scan_pipeline_resumes`, scoped to the job/requisition, to pull the batch efficiently rather than fetching resumes one at a time.
3. Read each resume for genuine signal relative to the role — not just keyword presence. A resume that lists a skill in a bullet point is weaker evidence than one that shows real depth (years of hands-on use, a relevant project, seniority appropriate to the role).
4. Rank candidates into a shortlist, and briefly explain why each shortlisted person made the cut. For everyone not shortlisted, a short rollup is enough — the recruiter doesn't need individual writeups for people who clearly aren't a fit.

## Output format

```
## Resume batch review — [job/requisition] (N resumes reviewed)

### Shortlist (top candidates)
1. **[Name]** — [why they stand out, specific to the role]
2. **[Name]** — [why they stand out]
...

### Also reviewed, not shortlisted (N)
[Brief rollup — e.g. "Most lacked required X experience or were clearly junior for a senior-level req."]
```

## Notes

- This is read-only — reviewing and ranking resumes doesn't mutate Greenhouse data, so no confirmation is needed for the review itself.
- Any follow-up action on a shortlisted candidate (advance them, reach out, add a note) is a write action on that specific person — hand off to `greenhouse-candidate-screening`'s confirmation rule rather than acting on the whole shortlist at once. Advancing five people because a shortlist said so, without the recruiter confirming each one (or at least explicitly confirming the batch), removes the human judgment step that's the entire point of this skill.
- Be honest about limits: if the batch is large enough that reviewing every resume in depth isn't practical in one pass, say what you actually did (e.g. "I reviewed all 40, but gave the closest read to the ones with relevant keywords already in the summary Greenhouse provides") rather than implying uniform depth of review across all of them.
