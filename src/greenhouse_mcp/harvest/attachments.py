"""Harvest API — Attachment reading tools (2 tools).

Harvest v3 removed `attachments` from the candidate object, so a resume can no
longer be read from a candidate in one call. It now takes three: candidate →
their applications → that application's attachments. `_fetch_candidate_resumes`
is the single place that knows this, because four call sites across three
modules used to read `candidate["attachments"]` directly.

**Unverified.** The filter parameter names (`candidate_id`, `application_id`) and
the attachment field names (`type`, `url`, `filename`) are taken from the
migration guide, which documents the move but not the schema. Nothing here has
been run against a live Greenhouse instance — see docs/harvest-v3-migration.md.
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

from pydantic import Field

from greenhouse_mcp.client import GreenhouseClient
from greenhouse_mcp.errors import build_error

# Attachment listing is now per-application, so a pipeline scan multiplies calls
# against a stricter v3 rate-limit window. Small gap between applications.
_INTER_APP_DELAY = 0.1


def _is_error(payload: Any) -> bool:
    return isinstance(payload, dict) and "error" in payload and "status_code" in payload


async def _fetch_candidate_resumes(
    client: GreenhouseClient,
    candidate_id: int,
    *,
    max_applications: int = 10,
) -> dict[str, Any]:
    """Return `{"resumes": [...]}` for a candidate, or a structured error.

    Distinguishing an empty result from a schema mismatch is the whole point of
    this function. If attachments come back but none carries a `type` field, the
    field was renamed and every caller would otherwise conclude "no resume" —
    the silent failure this migration is trying to avoid. That case returns an
    error instead of an empty list.
    """
    apps = await client.harvest_get(
        "/applications", params={"candidate_id": candidate_id, "per_page": 500}
    )
    if _is_error(apps):
        return apps

    app_items = [a for a in apps.get("items", []) if isinstance(a, dict)]
    resumes: list[dict[str, Any]] = []
    seen_attachments = 0
    seen_typed = 0

    for app in app_items[:max_applications]:
        app_id = app.get("id")
        if app_id is None:
            continue
        page = await client.harvest_get(
            "/attachments", params={"application_id": app_id, "per_page": 500}
        )
        if _is_error(page):
            return page
        for att in page.get("items", []):
            if not isinstance(att, dict):
                continue
            seen_attachments += 1
            if "type" in att:
                seen_typed += 1
                if att.get("type") == "resume":
                    resumes.append(att)
        await asyncio.sleep(_INTER_APP_DELAY)

    if seen_attachments and not seen_typed:
        return build_error(
            502,
            "Attachment records carried no `type` field, so a resume cannot be "
            "identified. The v3 attachment schema differs from what this "
            "connector expects.",
            "/attachments",
        )

    return {"resumes": resumes, "applications_checked": len(app_items[:max_applications])}


def _latest_resume(resumes: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Most recent resume — Greenhouse returns attachments in creation order."""
    return resumes[-1] if resumes else None


async def read_candidate_resume(
    client: GreenhouseClient,
    *,
    candidate_id: Annotated[int, Field(description="Greenhouse candidate ID")],
) -> dict[str, Any]:
    """Download and return a candidate's most recent resume text. Read-only.

    Users say "pull up Sarah's resume" or "show me John's CV." To find
    candidate_id: search_candidates_by_name. Returns extracted text from
    the most recent resume attachment. For batch reading, use batch_read_resumes.
    """
    candidate = await client.harvest_get_one(f"/candidates/{candidate_id}")
    if _is_error(candidate):
        return candidate

    found = await _fetch_candidate_resumes(client, candidate_id)
    if _is_error(found):
        return found

    resumes = found["resumes"]
    if not resumes:
        return {
            "error": "No resume found for this candidate.",
            "candidate_id": candidate_id,
            "candidate_name": f"{candidate.get('first_name', '')} {candidate.get('last_name', '')}",
            "applications_checked": found["applications_checked"],
        }

    resume = _latest_resume(resumes) or {}
    url = resume.get("url")
    if not url:
        return {"error": "Resume URL not available.", "candidate_id": candidate_id}

    content = await client.download_url(url)
    content["filename"] = resume.get("filename", "resume")
    content["candidate_id"] = candidate_id
    content["candidate_name"] = (
        f"{candidate.get('first_name', '')} {candidate.get('last_name', '')}"
    )
    return content


async def download_attachment(
    client: GreenhouseClient,
    *,
    url: Annotated[
        str,
        Field(
            description="Attachment URL — from candidate attachments or application data"
        ),
    ],
) -> dict[str, Any]:
    """Download content from a Greenhouse attachment URL. Read-only.

    Use when you have a specific attachment URL from a candidate or application
    record (e.g., from get_candidate's attachments array).
    """
    return await client.download_url(url)
