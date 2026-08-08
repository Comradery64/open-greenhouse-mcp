# open-greenhouse-mcp

<!-- mcp-name: io.github.benmonopoli/greenhouse-mcp -->

[![PyPI](https://img.shields.io/pypi/v/open-greenhouse-mcp)](https://pypi.org/project/open-greenhouse-mcp/)
[![CI](https://github.com/benmonopoli/open-greenhouse-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/benmonopoli/open-greenhouse-mcp/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![open-greenhouse-mcp MCP server](https://glama.ai/mcp/servers/benmonopoli/open-greenhouse-mcp/badges/score.svg)](https://glama.ai/mcp/servers/benmonopoli/open-greenhouse-mcp)

Production-ready MCP server for Greenhouse, designed for recruiters and hiring teams.

Most Greenhouse MCP servers mirror the API endpoint by endpoint. This one is built for recruiting teams: safe defaults, role-based profiles, and workflow tools that turn multi-step API operations into single actions.

## Choose a Profile

| Profile | Tools | Can write? | Recommended for |
|---|---|---|---|
| `read-only` | 103 | No | First-time setup, reporting, hiring managers |
| `recruiter` **(default)** | 127 | Yes (safe ops) | Day-to-day recruiting work |
| `full` | 181 | Yes (all) | Admins, ops, advanced automation |

## Quick Start

```bash
pip install open-greenhouse-mcp
```

Add to your MCP client config (Claude Desktop: `~/Library/Application Support/Claude/claude_desktop_config.json`, Cursor: Settings > MCP):

```json
{
  "mcpServers": {
    "greenhouse": {
      "command": "open-greenhouse-mcp",
      "env": {
        "GREENHOUSE_API_KEY": "your-harvest-api-key",
        "GREENHOUSE_TOOL_PROFILE": "read-only"
      }
    }
  }
}
```

Start in read-only mode to validate connectivity and tool behaviour, then switch to `recruiter` or `full` when you need write access.

Your API key is in Greenhouse under Configure > Dev Center > API Credential Management.

## What You Can Ask

- "Show me the pipeline for our Senior Engineer role"
- "Who needs my attention this week?"
- "What are our conversion rates for the Backend Intern role?"
- "Find Sarah Chen and pull up her resume"
- "Which sources are actually producing hires?"
- "Bulk reject everything inactive for 30+ days on the Account Manager role"
- "Screen this candidate for the Backend Engineer role — give me the full picture"
- "Search our engineering pipelines for anyone with Rust and distributed systems experience"
- "What new applications came in since yesterday?"

See [more examples with full output](docs/examples.md).

### See it in action

![Demo](docs/demo.gif)

## Safety

- Access is limited by your Greenhouse API key permissions
- Read-only profile is recommended for first setup
- Destructive actions require explicit IDs — the server never infers targets
- Write operations support audit attribution via `GREENHOUSE_ON_BEHALF_OF`
- Bulk actions are rate-limited to stay within API limits

## Compatibility

| Client | Status |
|---|---|
| [Claude Desktop](https://claude.ai/download) | Supported |
| [Claude Code](https://docs.anthropic.com/en/docs/claude-code) | Supported |
| [Cursor](https://docs.cursor.com/context/model-context-protocol) | Supported |
| Transport | stdio |
| Python | 3.10+ |

## Startup

When the server starts, it logs its configuration:

```
open-greenhouse-mcp v0.4.0
Profile: recruiter | Tools: 127 | Writes: recruiter-safe | APIs: harvest, ingestion
```

## What's Included

- **Screening & sourcing tools** — 6 tools for candidate screening, resume search with boolean keywords, daily digest, and location detection
- **Recruiter workflow tools** — 13 composite tools for pipeline views, analytics, search, and bulk operations
- **Harvest API coverage** — 148 tools across candidates, applications, jobs, offers, interviews, and more
- **Job Board API** — 13 tools for public job listings and application submission
- **Optional webhooks and ingestion** — 14 tools for event-driven workflows and partner integrations

---

## Reference

### Screening & Sourcing Tools

Tools for candidate evaluation and proactive talent search.

| Tool | What it does |
|---|---|
| `screen_candidate` | Complete screening package — profile, resume text, location, screening answers, job description, history |
| `fetch_new_applications` | Applications since a date, grouped by job — the daily recruiter digest |
| `scan_pipeline_resumes` | Search resume text across pipelines with boolean keywords (required/preferred/exclude) |
| `search_pipeline_candidates` | Search pipelines by structured fields — title, company, education, experience, tags |
| `scan_all_candidates` | Database-wide candidate search by structured fields with date bounds |
| `batch_read_resumes` | Batch-fetch and extract resume text for multiple candidates |

### Composite Tools

High-level tools that combine multiple API calls into single operations.

| Tool | What it does |
|---|---|
| `pipeline_summary` | Full pipeline view — candidates grouped by stage with names and days-in-stage |
| `candidates_needing_action` | Find stale applications and interviews missing scorecards |
| `stale_applications` | Applications with no activity for N days, sorted by stalest |
| `pipeline_metrics` | Conversion rates, hire/rejection rates, time-in-stage per stage |
| `source_effectiveness` | Which candidate sources produce the best hire rates |
| `time_to_hire` | Average, median, min, max days from application to hire |
| `bulk_reject` | Reject multiple applications in one call with rate-limit handling |
| `bulk_tag` | Tag multiple candidates in one call |
| `bulk_advance` | Advance multiple applications to next stage |
| `search_candidates_by_name` | Find candidates by first or last name |
| `search_candidates_by_email` | Look up a candidate by exact email |
| `read_candidate_resume` | Download and return a candidate's most recent resume |
| `download_attachment` | Download any Greenhouse attachment by URL |

### Profile Details

**Recruiter** includes all read tools, all screening/sourcing tools, all composite workflows, and recruiter-safe writes: reject, advance, hire, move, tag, notes, attachments, interviews, prospects, and bulk operations. It excludes job creation, user management, custom field configuration, candidate deletion, and webhook management.

**Read-only** skips all write operations. `GREENHOUSE_READ_ONLY=true` also works as a shorthand.

### Configuration

| Variable | Required | Description |
|---|---|---|
| `GREENHOUSE_API_KEY` | Yes* | Harvest API key |
| `GREENHOUSE_BOARD_TOKEN` | Yes* | Job board URL slug. *At least one required |
| `GREENHOUSE_TOOL_PROFILE` | No | `recruiter` (default), `read-only`, or `full` |
| `GREENHOUSE_ON_BEHALF_OF` | No | Greenhouse user ID for write audit trail |
| `GREENHOUSE_LOG_LEVEL` | No | `debug`, `info`, `warning` (default), `error` |
| `GREENHOUSE_LOG_FILE` | No | Log file path (defaults to stderr) |
| `GREENHOUSE_MAX_RESULT_BYTES` | No | Tool-result size budget in bytes (default `60000`) |
| `GREENHOUSE_DIAGNOSTICS_FILE` | No | Diagnostics file path (defaults beside Claude's logs) |
| `GREENHOUSE_DIAGNOSTICS` | No | Set `off` to disable the diagnostics file |

### Logging

Structured JSON logging for observability. Set `GREENHOUSE_LOG_LEVEL=info` to enable:

```json
{"ts": "2026-04-14T12:31:58", "level": "info", "event": "api_call", "method": "GET", "url": "...", "status": 200, "latency_ms": 245.0}
```

### More Documentation

- **[API Reference](docs/api-reference.md)** — Full tool breakdown by category
- **[Usage Examples](docs/examples.md)** — Real conversations with full output
- **[Advanced Setup](docs/advanced.md)** — Webhook receiver, ingestion API, board-token mode
- **[Development](docs/development.md)** — Contributing, testing, project structure

## Changelog

Current version: **0.5.5**. Full detail for every release lives in
[CHANGELOG.md](CHANGELOG.md); this is the short version.

### 0.5.5 — reliability and error reporting

Aimed at deployments where the people using the tools are recruiters, not
engineers, so a failure has to be self-explanatory and reportable.

- **Result-size shaping** — results are measured and kept within a size budget
  (60KB default, `GREENHOUSE_MAX_RESULT_BYTES` to override). A 500-job `/jobs`
  page runs to ~1.1MB and clients reject an oversized tool result outright, so
  the user saw a bare failure instead of an answer. Shaping degrades lazily:
  untouched if it already fits, then field projection, then text clamping, then
  dropping rows — attaching `returned`/`total_found` and a note telling the model
  to narrow by a real filter or walk pages, rather than telling the user about
  flags. Composite tools calling `list_*` internally still get complete data.
- **User-relayable errors** — every failure carries a plain-English
  `user_message`, a `support_code` like `GH403-0730-1421-7F2D` the user can paste
  into a support request, and `user_can_resolve` to separate "check the spelling"
  from "escalate, you cannot fix this".
- **Always-on diagnostics file** — notable events append to a JSON-lines file at
  a fixed path, so support is "send me this file" instead of asking a recruiter
  to reproduce with logging turned up. `GREENHOUSE_DIAGNOSTICS=off` to disable.
- **Default profile is now `recruiter`, not `full`** — an unset or unrecognised
  `GREENHOUSE_TOOL_PROFILE` used to register every tool with writes enabled,
  including destructive ones. Explicit values, including `full`, are unchanged.
- **Fixed: 400 and 409 responses were treated as success data** — only an
  enumerated set of statuses became errors, so a rejected filter value came back
  looking like a real record. Any status >= 400 is now an error.

### 0.4.0 — screening and sourcing

`screen_candidate`, `fetch_new_applications`, `search_pipeline_candidates`,
`scan_all_candidates`, `batch_read_resumes`, and `scan_pipeline_resumes`, plus
server-side PDF/DOCX resume text extraction and a 5-step location detection
cascade.

### 0.3.0 — profiles and logging

Tool profiles via `GREENHOUSE_TOOL_PROFILE` (full / recruiter / read-only) and
structured JSON logging with per-call method, status, and latency.

### 0.2.1 — packaging

PyPI metadata: keywords, classifiers, and project URLs.

### 0.2.0 — composite tools

13 composite tools for recruiter workflows.

### 0.1.0 — initial release

Harvest, Job Board, and Ingestion API coverage.

## Skills

`skills/` holds the Claude skills that drive these tools for recruiters — application
triage, candidate screening, pipeline search, and resume batch review. Skills are
distributed separately from the extension bundle: upload each directory to Claude as
a skill, alongside installing the `.mcpb`.

They name MCP tools in prose, so a rename or a tool dropped from the bundle's pinned
profile would break them silently, at the moment a recruiter tried to use one.
`tests/test_skills.py` matches the registered tool set against each skill's text and
fails the build instead. Cited tools are discovered from the files rather than a
hardcoded list, so newly cited tools are covered automatically.

## Relationship to upstream

This project began as a fork of
[benmonopoli/open-greenhouse-mcp](https://github.com/benmonopoli/open-greenhouse-mcp)
(MIT, Copyright © 2026 Ben Monopoli), which remains the origin of the great majority
of this code. The `LICENSE` file is unchanged and continues to carry that notice.

Changes made here, released as 0.5.5:

- Result-size shaping, so a large tool result is trimmed to fit rather than rejected
- User-relayable error messages carrying a support code a non-technical user can pass on
- An always-on diagnostics file, so support does not depend on reproducing a failure
- A curated `assistant` tool profile, and a safe default profile instead of `full`
- A fix for error statuses being treated as success data
- An upper bound on `mcp`, without which a clean install resolves 2.0.0 and the
  package cannot be imported at all
- A release workflow producing a cross-platform Claude Desktop bundle

To pull in future upstream work:

```sh
git remote add upstream https://github.com/benmonopoli/open-greenhouse-mcp.git
git fetch upstream && git merge upstream/main
```

## Feedback

- **Bugs and features:** [Open an issue](https://github.com/benmonopoli/open-greenhouse-mcp/issues)
- **Questions:** [Start a discussion](https://github.com/benmonopoli/open-greenhouse-mcp/discussions)
- **Security:** See [SECURITY.md](SECURITY.md)
- **Contributing:** See [CONTRIBUTING.md](CONTRIBUTING.md)

## License

MIT License -- Ben Monopoli. See [LICENSE](LICENSE).
