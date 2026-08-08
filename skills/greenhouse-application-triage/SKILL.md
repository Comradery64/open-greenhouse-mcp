---
name: greenhouse-application-triage
description: Triage new/recent Greenhouse job applications into a prioritized summary using the Greenhouse MCP tools. Use this whenever the user asks to check, review, catch up on, or triage new applicants, candidates, or applications — phrases like "what's new in Greenhouse", "catch me up on applications", "anything urgent in my pipelines", "check this week's applicants", or "what came in overnight" all mean this skill applies, even if they don't name a specific job or say "triage" outright. Also use it as the starting point for a recurring daily/weekly recruiting check-in.
---

# Greenhouse application triage

Recruiters get flooded with new applications across multiple open jobs. This skill turns a pile of raw application records into a short, scannable briefing so the recruiter can decide what needs attention today versus what can wait.

## When this applies

Trigger on requests to check for new applications, catch up on a pipeline, do a daily/weekly recruiting review, or find out "what's new." If the user names a specific job, scope the triage to that job; if not, cover all jobs they have visibility into.

## How to do it

1. Call the `fetch_new_applications` tool (optionally scoped by job or date range if the user specified one — default to the last 24-48 hours for a "daily" check, or the last 7 days for a "weekly" one, if they don't say).
2. For each application returned, note: candidate name, job/pipeline stage, source (referral, job board, etc.), and how long it's been sitting without action.
3. Group the results into three buckets:
   - **Needs action today** — anything sitting unreviewed for a while, flagged as a referral, or from a role marked urgent/high-priority (infer urgency from job title or recency of the req if nothing else signals it — don't guess if genuinely ambiguous).
   - **Worth a look this week** — normal-priority new applications.
   - **FYI only** — duplicate applications, clearly out-of-scope candidates, or ones already advanced past the "new" stage.
4. Present the briefing as a short table or bulleted list per bucket — not a raw dump of every field the API returned. Recruiters want signal, not data.
5. End with a one-line suggestion of what to do next (e.g., "want me to pull full screening summaries for the 3 in 'needs action today'?" — see the candidate-screening skill for that).

## Output format

Use this shape, adapting bucket names if the user asked for something more specific:

```
## Application triage — [date range / job scope]

### Needs action today (N)
- **[Candidate name]** — [job] — [stage] — [why it's flagged]

### Worth a look this week (N)
- **[Candidate name]** — [job] — [stage]

### FYI only (N)
- [one-line summary, can be condensed to a count if there are many]

Suggested next step: [...]
```

## Important: this is read-only

Fetching and summarizing applications never mutates Greenhouse data, so this skill never needs write permission and never requires confirmation. If the user asks you to act on something you found during triage (advance a candidate, add a note, reject someone), that's a write action — stop and follow the confirmation rule in `greenhouse-candidate-screening`'s "Before any write action" section rather than doing it inline here.

If the connected Greenhouse API key doesn't have access to a job or returns nothing, say so plainly rather than implying the pipeline is empty — a permissions gap and an empty pipeline look identical in the data but mean very different things to the recruiter.
