---
name: greenhouse-pipeline-search
description: Search and filter candidates across one or more Greenhouse pipelines by criteria like skills, experience level, location, or stage, using the Greenhouse MCP tools. Use this whenever the user wants to find candidates matching some criteria across a job or pipeline — phrases like "find candidates with X years of Y experience," "who do we have in the pipeline that knows Z," "show me everyone in the [role] pipeline," or "search our candidates for..." all mean this skill applies. This is for searching/filtering across many candidates at once, not for evaluating one specific named candidate (use greenhouse-candidate-screening for that) or for reviewing a whole unfiltered batch of resumes (use greenhouse-resume-batch-review for that).
---

# Greenhouse pipeline search

Finds candidates across a pipeline (or across all pipelines) matching criteria the recruiter cares about, without them having to manually scroll through Greenhouse.

## When this applies

Trigger when the user is looking for candidates that match some filter — a skill, years of experience, location, current stage, source, or a combination. If they name one specific person, that's `greenhouse-candidate-screening` instead. If they want you to review an entire unfiltered batch of resumes for a role (no specific filter, just "look at everyone"), that's `greenhouse-resume-batch-review`.

## How to do it

1. Clarify scope if it's ambiguous: which job/pipeline (or "all"), and what the actual filter criteria are. If the user says something vague like "find good candidates," ask what "good" means for this search (specific skills? years of experience? something else?) rather than inventing criteria — a wrong guess wastes their time reviewing results that don't match what they meant.
2. Use `search_pipeline_candidates` for criteria-based search within a pipeline, or `scan_pipeline_resumes` when the match criteria live in resume content itself (e.g. specific technologies, certifications, or experience described in free text rather than structured fields).
3. If the user's criteria span multiple pipelines/jobs, run the search per job and combine — don't silently narrow to just one job because it's easier.
4. Rank or order the results in a way that's actually useful — e.g. best-match first, or by pipeline stage — rather than returning them in raw API order.

## Output format

```
## Pipeline search — [criteria] in [job/pipeline scope]

Found N matching candidates:

| Candidate | Stage | Why they match |
|---|---|---|
| [name] | [stage] | [specific evidence from resume/profile, not a generic restatement of the criteria] |
```

If zero candidates match, say so plainly and suggest loosening the criteria — don't pad the response with near-misses unless you also label them clearly as near-misses.

## Notes

- This is read-only — searching and filtering never mutates Greenhouse data, so no confirmation is needed before running a search itself.
- If the user wants to act on someone found in the results (advance them, add a note, move them to a different pipeline), hand off to `greenhouse-candidate-screening`'s confirmation rule for that specific candidate rather than doing it as a blanket action across the whole result set — bulk write actions on search results are exactly the kind of thing that should never happen without an explicit per-candidate (or at minimum, explicit whole-list) confirmation.
- Large result sets can blow past what's useful to read in one response — if a search returns dozens of matches, summarize the pattern (e.g. "23 candidates match, mostly concentrated in the Phone Screen stage") and show a representative top slice rather than every single one, and offer to show more.
