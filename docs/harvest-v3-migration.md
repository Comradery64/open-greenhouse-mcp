# Harvest v3 migration — handoff

**Status:** Phase A implemented; **reads verified against a live instance
2026-08-31. Writes remain unverified** — they were deliberately not exercised
against production data.
**Hard deadline:** Harvest **v1 and v2** are unavailable after **2026-08-31**.
**Written:** 2026-08-18. **Phase A implemented:** 2026-08-30.

Every Harvest call now targets `v3` with OAuth client credentials and cursor
paging, and the 33 curated tools have had their paths and fields checked against
the migration guides. What has *not* happened is a single real request: no v3
credentials existed when this was written, so every endpoint below is verified
only against the documentation, not against Greenhouse.

**Before trusting this in front of a recruiter**, get credentials and run the
[Verification](#verification) steps. A green test suite is not evidence — the
fixtures assert the contract as documented, which is exactly the thing that
might be wrong.

### The v3 shape, established by probing a live instance

The migration guides describe renames but not the structural changes. All of
the following were found by probing, and each one is a 404 or 422 away from a
silent or noisy failure:

| Rule | Evidence |
|---|---|
| No `/{collection}/{id}` show endpoints | `GET /jobs/{id}` 404s on an id `/jobs` just returned. Use `?ids=`. |
| No nested paths | `/jobs/{id}/job_posts`, `/applications/{id}/scorecards`, `/candidates/{id}/activity_feed`, `/users/{id}/permissions/jobs` all 404 |
| Cross-reference filters are plural | `candidate_id` → 422; `candidate_ids` works |
| A collection filtered on itself uses `ids` | `candidate_ids` on `/candidates` → 422 |
| Date ranges use bracket comparisons | `created_at[gte]`, `[lte]`; every `*_after`/`*_before` → 422 |
| `/jobs` keeps singular `department_id`/`office_id` | inconsistent with every other collection |
| Writes keep ids in the path | `POST /applications/{id}/move` — only reads went flat |

Replacements, all confirmed returning 200:
`/jobs/{id}/job_posts` → `/job_posts?job_ids=`,
`/applications/{id}/scorecards` → `/scorecards?application_ids=`,
`/candidates/{id}/activity_feed` → `/notes?candidate_ids=`,
`/scheduled_interviews` → `/interviews`,
`/users/{id}/permissions/jobs` → `/user_job_permissions?user_ids=`,
`/candidates/{id}/tags` → `/applied_candidate_tags?candidate_ids=`.

**A 422 is ambiguous** — it means an invalid parameter *name* or an invalid
*value*. The response body distinguishes them (`Invalid query params: x`).
Reading only the status code produces false negatives; one early probe did
exactly that.

**A 200 is not proof a filter works.** Date filters were confirmed by asking
for a far-future bound and checking the row count actually dropped, since a
silently-ignored filter also returns 200.

### What Phase A changed

| Item | Where |
|---|---|
| A2 token auth (`client_credentials`, cached, refresh-on-401) | `client.py` |
| A3 cursor paging; `page` removed from the migrated tools | `client.py`, `harvest/{jobs,applications,candidates,rejection_reasons,search}.py` |
| A5 base URL → `/v3` | `client.py` |
| A7 field renames | `shaping.py` |
| A8 unmigrated tools withheld (181 → 60 registered) | `server.py` |
| Mitigation: strict projection | `shaping.py`, `GREENHOUSE_STRICT_PROJECTION` |

A second sweep on 2026-08-31 found 8 of the 33 curated tools still carrying v1
assumptions that the strict-projection guard could not see, because the
composites read API fields directly rather than through a projection. Notably a
deactivated user could start the server, resumes could not be read at all, and
every pipeline stage read as "Unknown". See the 0.6.0 changelog entry.

Two renames the table below originally missed, found in the migration guide while
implementing: `application_ids` and `applications[]` are **removed** from
candidates, and `primary_email_address` → `primary_email` on users.

## Plan

**Phase A (before 2026-08-31)** — migrate the 33 tools in `_ASSISTANT_TOOLS`. That
is the profile the shipped bundle pins, so it is exactly what recruiters can reach.
21 endpoints, not 157 tools.

**Phase B (after)** — migrate the remaining ~124 tools incrementally. Anything not
yet migrated must be **deregistered, not left registered and broken**, so a caller
gets "no such tool" instead of a plausible-looking wrong answer.

## Verified facts

| | v1 (current) | v3 |
|---|---|---|
| Base URL | `https://harvest.greenhouse.io/v1` | `https://harvest.greenhouse.io/v3/` |
| Auth | Basic, `base64(api_key + ":")` | `Authorization: Bearer <token>` |
| Token | n/a | `POST https://auth.greenhouse.io/token`, Basic `client_id:client_secret`, form body `grant_type=client_credentials&sub=<user_id>`, returns `{token_type, access_token, expires_in}` |
| Paging | `page` + `per_page` | `cursor` only, **and it must be the sole query parameter** — combining it with filters or `per_page` returns 422 |
| Next page | `Link` header `rel="next"` | `Link` header `rel="next"` (**unchanged**) |
| Page size | `per_page` | `per_page`, default 100, max 500 (first request only) |
| Rate limit | 50 req / 10s rolling | 30s fixed window, `X-RateLimit-Remaining`, honour `Retry-After` |

Sources: [Harvest API overview](https://support.greenhouse.io/hc/en-us/articles/360029266032-Harvest-API-overview),
[authentication](https://harvestdocs.greenhouse.io/docs/authentication.md),
[pagination](https://harvestdocs.greenhouse.io/docs/pagination.md),
[READ migration](https://harvestdocs.greenhouse.io/docs/step-by-step-migration-instructions.md),
[WRITE migration](https://harvestdocs.greenhouse.io/docs/write-endpoint-migration-guide.md).

### What is smaller than feared

- `Link`/`rel="next"` is unchanged, so `_parse_next_link` and the `paginate="all"`
  loop in `_paginated_get` survive nearly as-is. The `next` URL already carries the
  cursor, and that loop already re-requests it with no extra params — which is what
  v3 requires.
- No concurrent page fan-out exists in this repo to unpick (`harvest_get_all_pages`
  was in the old vendored 0.3.3 bundle, not here).
- The base URL is one constant; Basic auth is built in one method.

## Silent failure mode

**Read this before deciding to do a partial migration.**

`shaping._project_item` skips absent keys:

```python
if key not in item:
    continue
```

So a renamed field is not an error — it vanishes. Combined with shaping only
engaging on results over the size budget, a half-migrated server returns
**intermittently near-empty records with no error anywhere**. A recruiter sees a
short, plausible answer and has no reason to doubt it.

9 of the 56 fields projected in `shaping.py` are affected:

| projected field | v3 |
|---|---|
| `applied_at` | renamed `created_at` |
| `credited_to` | renamed `referrer_id` |
| `submitted_by` | renamed `submitter_id` |
| `overall_recommendation` | renamed `candidate_rating` |
| `attachments` | removed from candidates; separate `/v3/attachments` |
| `current_stage` | separate endpoint / nested object |
| `departments` (array) | `department_id` (scalar) |
| `offices` (array) | `office_ids` (array of ids) |
| `status` | on openings, becomes `open` (boolean, was enum) |

Also renamed elsewhere in the API: `priority`→`sort_order`,
`disabled`→`deactivated`. Removed: `render_as=tree`, `child_ids`,
`child_department_external_ids`.

**Mitigation to build first:** make projection strict. If a projection names a
field absent from the payload, fail loudly (or log a diagnostic) rather than
dropping it. Without that, every later step is unverifiable.

## Phase A work items, in order

### A1. Blocked on admin — start today
See [Human dependencies](#human-dependencies). Nothing else can be integration-tested
until credentials exist.

### A2. Token-based auth (`client.py`)
- Replace `_harvest_auth_header` (Basic, cached in `__init__`) with an async
  `_bearer_header()` that fetches and caches a token.
- Cache on `expires_in` with a safety margin; refresh on 401 and retry once.
  `_request` already has a retry loop for 429 — extend it rather than adding a
  second mechanism.
- Token fetch is `POST auth.greenhouse.io/token`, Basic `client_id:client_secret`,
  `Content-Type: application/x-www-form-urlencoded`, body
  `grant_type=client_credentials&sub=<user_id>`.
- New config: `GREENHOUSE_CLIENT_ID`, `GREENHOUSE_CLIENT_SECRET`,
  `GREENHOUSE_USER_ID`. `GREENHOUSE_API_KEY` becomes v1-only — keep it working
  behind a flag if a fallback period is wanted, otherwise remove it.
- **Never log the token or the secret.** `logging.log_api_call` records the full
  URL; confirm no credential can reach it or the diagnostics file.

### A3. Cursor pagination (`client.py`)
- `paginate="all"`: largely intact — verify the loop passes no params alongside
  the cursor URL.
- `paginate="single"`: must return the opaque next URL/cursor unchanged and must
  not let a caller combine `cursor` with filters (422).
- Remove `page` from every signature; keep `per_page` for the *first* request only.
  Affects **112 occurrences across 26 modules** — of the curated 33, only
  `list_applications`, `list_candidates`, `list_jobs`, `list_rejection_reasons`,
  and `search_candidates_by_name` take paging params.
- Decide the tool-facing contract for resuming: exposing a raw cursor to the model
  is ugly, but the alternative (server-side cursor state) is worse. Prefer
  returning the cursor inside the existing `result_note`/envelope, since
  `shaping.py` already tells the model how to continue.

### A4. Rate limiting (`client.py`)
`_request` already retries 429 up to `_MAX_RETRIES = 3` and honours `Retry-After`,
which carries over unchanged. There is no concurrency cap in this repo, so nothing
is mis-tuned for the old window — but v3 exposes `X-RateLimit-Remaining` and moves
to a 30s fixed window, so consider throttling proactively rather than only reacting
to a 429. This matters more once `current_stage` and `attachments` become extra
calls (see [Open questions](#open-questions)).

### A5. Base URL
`HARVEST_BASE` → `https://harvest.greenhouse.io/v3`. Trivial, but do it last: it
turns every unmigrated endpoint into a 404, which is the desired loud failure.

### A6. Endpoints — the 33 tools

21 distinct paths. Verify each against the READ/WRITE migration guides for path,
fields, and nesting. Known changes flagged.

| v1 path | tools | notes |
|---|---|---|
| `/candidates` | `list_candidates`, `search_candidates_by_email`, `search_candidates_by_name`, `scan_pipeline_resumes`, `search_pipeline_candidates` | `attachments` removed → `/v3/attachments` |
| `/candidates/{id}` | `get_candidate`, `read_candidate_resume` | same; resume path needs `/v3/attachments` |
| `/candidates/{id}/activity_feed` | `get_activity_feed` | |
| `/candidates/{id}/activity_feed/notes` | `add_note_to_candidate` | WRITE guide |
| `/candidates/{id}/tags`, `/candidates/{id}/tags/{tag_id}` | `bulk_tag`, `add_tag_to_candidate` | WRITE guide |
| `/applications` | `list_applications`, `fetch_new_applications`, `stale_applications`, `pipeline_summary`, `pipeline_metrics`, `source_effectiveness`, `time_to_hire`, `candidates_needing_action` | `current_stage` now a separate call; `applied_at`→`created_at` |
| `/applications/{id}` | `get_application`, `screen_candidate` | |
| `/applications/{id}/advance`, `/reject`, `/unreject` | `advance_application`, `reject_application`, `unreject_application`, `bulk_advance`, `bulk_reject` | WRITE guide |
| `/applications/{id}/scorecards` | `list_scorecards_for_application` | `submitted_by`→`submitter_id`, `overall_recommendation`→`candidate_rating` |
| `/scorecards/{id}` | `get_scorecard` | same renames |
| `/jobs` | `list_jobs` | `departments[]`→`department_id`, `offices[]`→`office_ids[]` |
| `/jobs/{id}` | `get_job`, `pipeline_summary` | same |
| `/jobs/{id}/stages` | `list_job_stages_for_job`, `pipeline_metrics`, `pipeline_summary` | **likely `/v3/job_interview_stages`** — guide shows `/v1/job_stages` → `/v3/job_interview_stages` |
| `/jobs/{id}/job_posts` | `screen_candidate` | |
| `/rejection_reasons` | `list_rejection_reasons` | |
| `/scheduled_interviews` | `candidates_needing_action` | |

### A7. Update `shaping.py` projections
Apply the rename table. Where a field moved to its own endpoint (`attachments`,
`current_stage`), decide per tool whether to make the extra call or drop the field —
extra calls multiply against the rate limit.

### A8. Deregister everything unmigrated
Add a v3-migrated allowlist and register only those tools. The curated profile
already does exactly this shape of filtering — reuse `_should_register`.

## Phase B
Work module by module through `harvest/` (35 modules, 157 tools), moving names from
the unmigrated set into the migrated allowlist as each is verified. Priority order:
whatever the `recruiter` profile adds over `assistant`, then admin/config tools.
Ingestion and Job Board tools are out of scope until their status is confirmed —
see [Open questions](#open-questions).

## Human dependencies — begin immediately

These gate everything and are not engineering work.

1. **New credentials.** v3 needs a client ID and secret created by someone with
   *"Can manage ALL organization's API Credentials"*, with **scopes granted per
   endpoint**. Existing per-user Harvest keys do not carry over. Budget for admin
   coordination, not a five-minute task.
2. **A security decision.** Today each recruiter's own key stays on their own
   machine. `client_credentials` + `sub=<user_id>` means one client secret with
   per-user attribution — so every installed bundle would carry the organisation's
   shared secret. That is a material change in blast radius if a laptop is
   compromised. The alternative, per-user client credentials, preserves isolation
   at higher admin cost. **Decide deliberately; do not let the default decide.**
3. **Greenhouse's credential export** (April 2026 release) lists existing API
   credentials — use it to confirm nothing else in the org still depends on v1.

## Verification

- `tests/` currently mocks v1 shapes with `respx`. Those fixtures encode the old
  contract; update them alongside each endpoint or they will keep passing while
  production breaks.
- Add a test asserting no `harvest.greenhouse.io/v1` string remains in `src/`.
- Make projection strict (see [Silent failure mode](#silent-failure-mode)) before
  migrating endpoints, so a missed rename fails a test instead of quietly emptying
  a record.
- `extension-uv/smoke_test.py` only checks that tools *register*. It cannot catch a
  broken endpoint — it uses a fake key and never reaches the API. Do not treat a
  green bundle build as evidence the migration works.
- Real end-to-end verification needs live credentials against a real Greenhouse
  instance. Note the sandbox is Pro-plan only; Core and Plus test against live data,
  so prefer read-only tools first and be careful with the WRITE endpoints.

## Open questions

1. **Are the Job Board and Ingestion APIs affected?** `BOARD_BASE`
   (`boards-api.greenhouse.io/v1/boards`, 13 tools) and `INGESTION_BASE`
   (`api.greenhouse.io/v1/partner`, 6 tools) are separate products that also carry
   `/v1` in the path. Re-read 2026-08-30: the Harvest API overview says only
   "Harvest API v1 and v2 will be deprecated and unavailable after August 31,
   2026" and does not mention either product. That is an *absence* of a notice,
   not an assurance. Both were left on v1 and excluded from the v3 tool gate, so
   if this assumption is wrong those 19 tools break — loudly, with 404s, which is
   the acceptable failure. **Still worth confirming with Greenhouse support.**
2. ~~**Does `/jobs/{id}/stages` become `/v3/job_interview_stages`?**~~
   **Resolved 2026-08-31.** Two independent signals: the scope Greenhouse grants
   is named "Job interview stages", and the migration guide says resolving a
   stage name requires that endpoint. All three callers were moved to
   `/job_interview_stages?job_id=`.
3. **Is there a v1 fallback window?** If v3 access can be obtained before the
   cutoff, running both briefly would de-risk the switch. Unknown whether both
   credential types can be active at once.
4. **Rate-limit budget for the new multi-call patterns.** `current_stage` and
   `attachments` becoming separate endpoints turns one call into three for common
   screening flows, against a stricter window.
