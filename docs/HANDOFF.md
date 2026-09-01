# Handoff — Harvest v3 migration

**Written:** 2026-09-01. **For:** whoever picks this up with no prior context.

Read this first, then [harvest-v3-migration.md](harvest-v3-migration.md) for the
full endpoint contract. If you only read one thing here, read
[Rules learned the hard way](#rules-learned-the-hard-way).

---

## Where things stand

Harvest v1 and v2 were switched off after 2026-08-31. This repo is now on v3.

| Area | State |
|---|---|
| Client: OAuth, cursor paging, `/v3` base | Verified live |
| Reads: 9 tools end-to-end against a real instance | Verified live |
| Writes: `move` / `reject` / `unreject` routes exist | Verified live (safely) |
| Writes: note + tag body schemas | Learned from validation errors; test-pinned |
| Writes: any write actually taking effect | **Never verified** |
| Tool gate: 121 of 154 Harvest tools withheld | Working as designed |

`main` is green and pushed at `3106b2c`. Two commits are **local-only** —
`7529f60` and `682e255` — held back pending a decision on publishing the
incident write-up to a public repo (see [Decisions pending](#decisions-pending)).

Local: 450 tests pass, ruff clean, mypy clean on 71 files.

## Run it

Credentials live in 1Password; copy `scripts/env.op.example` to `env.op` and
point it at your vault and item. **Never read them, never put them in a shell
variable, never let them reach the terminal.** They go from 1Password into the
consuming process and nowhere else:

    op run --env-file=env.op -- .venv/bin/python scripts/verify_v3.py

    # add the write probe (path-id routes only — cannot mutate anything)
    op run --env-file=env.op -- .venv/bin/python scripts/verify_v3.py --writes

    .venv/bin/python -m pytest -q
    GREENHOUSE_STRICT_PROJECTION=1 .venv/bin/python -m pytest -q   # CI-strict
    .venv/bin/python -m ruff check src/ tests/ scripts/
    .venv/bin/python -m mypy src/greenhouse_mcp/ --ignore-missing-imports

`verify_v3.py` prints endpoint, status, row count and *field names* only. Never
values — these are real candidate records.

## The v3 contract, in one place

Everything below was established by probing a live instance. The migration
guides describe renames; they do not describe the structural changes, and those
are where the breakage was.

**Structure**

- **No `/{collection}/{id}` show endpoints.** `GET /jobs/{id}` 404s on an id
  `/jobs` just returned. Use `?ids=` — that is what `client.harvest_get_by_id`
  does.
- **No nested read paths.** `/jobs/{id}/job_posts`,
  `/applications/{id}/scorecards`, `/candidates/{id}/activity_feed`,
  `/users/{id}/permissions/jobs` all 404. Replacements:
  `/job_posts?job_ids=`, `/scorecards?application_ids=`, `/notes?candidate_ids=`,
  `/user_job_permissions?user_ids=`. Also `/scheduled_interviews` → `/interviews`.
- **Writes keep ids in the path.** Only reads went flat.

**Filters**

- Cross-references are **plural**: `candidate_ids`, `job_ids`, `application_ids`.
  Singular forms return 422.
- A collection filtered on **its own** ids uses `ids`, not `<entity>_ids`.
- Dates use bracket comparisons: `created_at[gte]`, `created_at[lte]`,
  `updated_at[gte]`, `last_activity_at[gte]`. Every `*_after` / `*_before` is
  rejected.
- `/jobs` keeps **singular** `department_id` and `office_id`. It is the exception.

**Fields**

`applied_at`→`created_at`, `credited_to`→`referrer_id`,
`submitted_by`→`submitter_id`, `overall_recommendation`→`candidate_rating`,
`departments[]`→`department_id`, `offices[]`→`office_ids[]`,
`disabled`→`deactivated`, `primary_email_address`→`primary_email`.
Removed from candidates: `attachments`, `application_ids`, `applications[]`.
Removed from applications: `current_stage` (use `stage_id` +
`/job_interview_stages`).

**Write bodies**

- `POST /notes` requires `note_type`, and the value is **`"NOTE"` upper-case** —
  even though the API's own error advertises `["email", "activity", "note"]` and
  rejects the lower-case form. `visibility` is also required.
- `POST /applied_candidate_tags` takes `candidate_tag_id`. Both `tag` and
  `tag_id` are rejected as disallowed additional properties. Resolve names via
  `/candidate_tags` first; v1's implicit tag creation is gone.

## Rules learned the hard way

**A 422 is ambiguous.** It means an invalid parameter *name* or an invalid
*value*. Only the response body distinguishes them (`Invalid query params: x`).
Reading the status alone produced a false negative that nearly sent the
migration down the wrong path.

**A 200 does not mean a filter works.** A silently-ignored filter also returns
200. Confirm by asking for a bound that must change the result — a far-future
date should return zero rows.

**A 404 does not mean a route is missing.** A missing route and a missing record
both return `Resource not found`. The discriminator is the `errors` key: only a
request that reached a handler names what it could not find. An earlier version
of the write probe classified on status alone and reported that a deliberately
bogus control route existed.

**Never probe a write whose id is in the body.** Greenhouse does not validate
that `candidate_id` refers to a real candidate. `POST /notes` against an absent
id creates a real, orphaned row — and there is no `DELETE /notes/{id}` to undo
it. Two exist in production because of this. Path-id writes are safe; body-id
writes are not, ever.

**On an unexpected write, stop.** Do not re-issue the request to study the
response. That is how the second orphaned note happened.

**A green test suite is not evidence.** The fixtures encode our understanding,
and that understanding was wrong three times — nested paths, plural filters,
write bodies. Each was caught only by a real request.

## Decisions pending

1. **Two orphaned notes** created in production during write probing (see
   [Rules learned the hard way](#rules-learned-the-hard-way)). v3 has no
   `DELETE /notes/{id}`, so removal needs the Greenhouse UI. Ids and timestamps
   are recorded outside this repo.
2. **Push `7529f60` and `682e255`?** They document the note incident in commit
   messages, and `main` is a public repo.
3. **Sandbox for write verification.** Pro-tier only. If the org is on Pro this
   is the clean finish; otherwise the options are Greenhouse Support or one
   deliberate low-stakes write on a controlled candidate.
4. **Release.** The `.mcpb` in `~/Dev/Work/greenhouse-mcp` is still the 0.5.5 v1
   build and is dead against v3. `manifest.json` and `pyproject.toml` are at
   0.6.0; cutting a release needs a `v0.6.0` tag push (release workflow is
   tag-triggered; PyPI publish is manual-only and intentionally so).
5. **Credential binding.** Greenhouse recommends an Integration System User for
   v3 credentials. A credential bound to a person breaks when that person is
   deactivated — and the startup guard added here refuses to start for a
   deactivated user, so it would take the connector down for everyone.
6. **Revoke `List application stages`** — granted but unused.

## Phase B

121 Harvest tools remain withheld by the v3 gate in `server.py`
(`_V3_MIGRATED_TOOLS`). They are deregistered rather than broken, so a caller
gets "no such tool" instead of a wrong answer. Set
`GREENHOUSE_ALLOW_UNMIGRATED_TOOLS=1` to see them while working.

The contract above is complete enough to convert them mechanically: swap show
endpoints for `?ids=`, flatten nested paths, pluralise id filters, bracket the
date filters. Add each name to `_V3_MIGRATED_TOOLS` only once it has been run
against a live instance — the gate is the last line of defence against a tool
that looks like it works.

## Map

- `client.py` — OAuth, cursor paging, `harvest_get_by_id`, retry/refresh
- `shaping.py` — result-size shaping and the strict-projection guard
- `server.py` — profiles and `_V3_MIGRATED_TOOLS` gate
- `permissions.py` — startup user lookup, deactivated-user guard
- `harvest/job_stages.py` — stage-name resolution (cached, one call per job)
- `harvest/attachments.py` — resume fetch via `/attachments?candidate_ids=`
- `scripts/verify_v3.py` — live read checks + safe write probe
- `tests/test_harvest_v3.py` — v3 contract tests, incl. pinned write bodies
- `tests/conftest.py` — `primed_client`, `mock_v3_side_calls`, `mock_resume_chain`
