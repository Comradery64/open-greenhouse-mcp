# Changelog

## 0.5.5

Reliability and error-reporting pass, aimed at deployments where the end users
are recruiters rather than engineers.

> Versions 0.5.0 through 0.5.4 were never released. They were internal Claude
> Desktop bundle builds: the admin console requires each upload to carry a version
> strictly greater than the one already installed, so repacking during development
> consumed those numbers. This release starts above them so the published package
> and the bundle share one version from here on.

### Added
- **Result-size shaping** — Tool results are now measured and kept within a size
  budget instead of being returned at whatever size the API produced. A 500-job
  `/jobs` page runs to ~1.1MB and clients reject an oversized tool result
  outright, so the user got a bare failure instead of an answer. Shaping applies
  in four lazy stages: return untouched if it already fits, project list rows to
  the fields recruiters read, clamp free text, then drop rows. Results carry
  `returned`/`total_found` and a note telling the model to narrow by a real
  filter or walk pages and combine — explicitly not to mention flags or paging
  to the user. Composite tools calling `list_*` internally bypass shaping and
  still receive complete data. Budget defaults to 60KB, override with
  `GREENHOUSE_MAX_RESULT_BYTES`.
- **User-relayable errors** — Every failure now carries a plain-English
  `user_message`, a `support_code` (`GH<status>-<MMDD>-<HHMM>-<hash>`) the user
  can paste into a support request, and `user_can_resolve` so "check the
  spelling" is distinguished from "escalate, you cannot fix this". The code's
  hash covers status and masked endpoint, so repeat reports of the same failure
  group together while the timestamp still pins each one to a log line. An
  `action_for_claude` field instructs the model to surface the message and code
  verbatim rather than smoothing the error into "I could not find that".
- **Always-on diagnostics file** — Notable events append to a JSON-lines file at
  a fixed, predictable path (beside Claude's own logs on macOS), so support is
  "send me this file" rather than asking a recruiter to reproduce with logging
  raised. Independent of `GREENHOUSE_LOG_LEVEL` and stderr, both of which make
  capture unreliable in a packaged client. Rotates at 2MB, clips long detail,
  and swallows all errors so it can never break a tool call. Configure with
  `GREENHOUSE_DIAGNOSTICS_FILE`, disable with `GREENHOUSE_DIAGNOSTICS=off`.

### Changed
- **Default tool profile is now `recruiter`, not `full`.** An unset or
  unrecognised `GREENHOUSE_TOOL_PROFILE` previously registered every tool with
  writes enabled, including destructive ones — the worst available default for
  an operator who has chosen nothing, and easy to reach accidentally: a packaged
  bundle that fails to substitute its `${user_config.tool_profile}` placeholder
  passes an unrecognised string and landed there silently. Such a rejected value
  now also writes an `unknown_tool_profile` diagnostic so the substitution
  failure is visible. Explicit values, including `full`, are unchanged.
- Missing credentials, unexpected exceptions inside a tool, and job-scope denials
  now return structured relayable errors instead of a raw `ValueError`
  traceback, an unhandled exception, or a bare `{"error": ...}` dict.

### Fixed
- **Error statuses outside the enumerated list were treated as success data.**
  `_handle_response` special-cased 401/403/404/422/429/5xx and let everything
  else fall through to `_parse_body`, so a 400 or 409 body was wrapped by
  `_paginated_get` as `{"items": [{"message": ...}]}` — a rejected filter value
  came back looking like a genuine record. Any status >= 400 is now an error.

## 0.4.0

### Added
- **`screen_candidate` tool** — Assembles a complete, analysis-ready screening package for a candidate in a single call. Returns decoded candidate profile, plain-text job description, screening answers, full resume text (PDF/DOCX extracted), detected location, and application history. Replaces 4-5 separate tool calls.
- **`fetch_new_applications` tool** — Fetches applications created after a date, grouped by job with candidate names and screening answers. The "what's new since yesterday" query for daily recruiter workflows. Supports `job_id` filtering.
- **`search_pipeline_candidates` tool** — Search within job pipelines for candidates matching structured criteria (title, company, education, experience years, tags). Resurface past applicants or find internal candidates for similar roles.
- **`scan_all_candidates` tool** — Database-wide candidate search using structured fields with optional date bounds. For proactive sourcing across the entire ATS.
- **`batch_read_resumes` tool** — Batch-fetch and extract resume text for multiple candidates. Use after narrowing with structured search to check for skills, technologies, or other details only found in resumes.
- **`scan_pipeline_resumes` tool** — The primary sourcing tool: searches resume text within job pipelines for specific skills and keywords. Supports boolean search — `required_keywords` (AND gate), `keywords` (OR ranking), and `exclude_keywords` (NOT filter). Returns matched candidates with context snippets around each keyword hit. Handles the reality that ~90% of candidate data lives in resumes, not structured fields.
- **Resume text extraction** — PDF and DOCX resumes are extracted to plain text server-side using pdfplumber and python-docx.
- **Location detection** — 5-step cascade detects candidate location from screening answers, application fields, candidate addresses, resume text patterns, and phone dial codes (150+ countries).

### Dependencies
- Added `pdfplumber>=0.11.0` for PDF text extraction
- Added `python-docx>=1.1.0` for DOCX text extraction

## 0.3.0

### Added
- **Tool profiles** (`GREENHOUSE_TOOL_PROFILE`): full (175 tools), recruiter (121 tools), read-only (97 tools)
  - Recruiter profile includes pipeline management, bulk operations, and candidate interaction
  - Recruiter profile excludes admin operations (job creation, user management, custom fields, candidate deletion)
  - `GREENHOUSE_READ_ONLY=true` continues to work as shorthand for read-only profile
- **Structured JSON logging** to stderr or file
  - `GREENHOUSE_LOG_LEVEL` (debug, info, warning, error) controls verbosity
  - `GREENHOUSE_LOG_FILE` for file output instead of stderr
  - Every API call logged with method, URL, status, and latency
  - Auto-escalation: info for 2xx, warning for 4xx, error for 5xx

## 0.2.1

### Improved
- PyPI metadata: added keywords, classifiers, and project URLs for better discoverability

## 0.2.0

### Added
- **13 composite tools** for recruiter workflows:
  - `pipeline_summary` — full pipeline view with candidates grouped by stage
  - `candidates_needing_action` — find stale applications and missing scorecards
  - `stale_applications` — applications with no activity for N days
  - `pipeline_metrics` — conversion rates, hire/rejection rates per stage
  - `source_effectiveness` — which candidate sources produce the best results
  - `time_to_hire` — average, median, min, max days from application to hire
  - `bulk_reject`, `bulk_tag`, `bulk_advance` — batch operations with rate-limit handling
  - `search_candidates_by_name`, `search_candidates_by_email` — candidate lookup
  - `read_candidate_resume`, `download_attachment` — attachment reading
- `paginate="all"` option on list endpoints to auto-fetch every page
- `force_refresh` on cached reference data (departments, offices, rejection reasons)
- `harvest_get_one()` for clean single-resource responses without pagination wrapper
- `On-Behalf-Of` header on all write operations for audit trail
- Tool gating — board-token-only mode registers only Job Board tools
- Retry jitter on 429 rate limit responses
- Webhook forward failure logging
- Partial results with warnings on mid-flow API errors in composite tools
- Batch candidate name resolution (fixes numeric ID display)
- Cross-references between atomic and composite tools for better routing
- CODE_OF_CONDUCT.md, CONTRIBUTING.md, SECURITY.md
- GitHub issue templates for bugs and feature requests

## 0.1.0

### Added
- 148 Harvest API tools covering all endpoints
- 13 Job Board API tools
- 6 Ingestion API tools
- 8 webhook management tools
- Webhook receiver with HMAC verification and SQLite routing
- CI pipeline with pytest and ruff
- README with quick start and tool reference
