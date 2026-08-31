"""Result-size shaping so tool results always fit the model's tool-result budget.

Greenhouse list endpoints return very large payloads (a 500-job `/jobs` page with
custom fields, openings and hiring teams runs to megabytes), which the client
rejects outright rather than truncating. End users are recruiters, not engineers,
so nothing here is exposed as a tool parameter — the shaping decides for itself
and explains in plain English what it did.

Degradation is staged and lazy: a result that already fits is returned byte-for-byte
unchanged, so composite tools keep their full resume text in the common case.

  stage 1  project list items down to the fields recruiters actually read
  stage 2  clamp long free-text strings
  stage 3  drop items from the tail

Applied at the MCP tool boundary only (see `server._make_tool_wrapper`), so
composites calling list_* internally still see complete data.
"""
from __future__ import annotations

import json
import os
from typing import Any

# ~15k tokens of JSON. Comfortably under every client's tool-result cap while
# still carrying a useful page of results.
DEFAULT_MAX_RESULT_BYTES = 60_000

# Never shrink a page below this — a result with no rows is worse than a big one.
_MIN_ITEMS = 3

# Headroom held back for the counts and `result_note` that `_finalize` appends
# after the fit checks; without it a shaped page lands just over budget.
_RESERVE_BYTES = 1_200

# Successive clamps applied to free-text fields in stage 2.
_STRING_CLAMPS = (4_000, 1_500, 400)

# Keys whose values are free text worth clamping rather than dropping.
_TEXT_KEYS = frozenset({
    "content", "body", "resume_text", "notes", "note", "text",
    "summary", "description", "cover_letter",
})


class ProjectionMismatch(RuntimeError):
    """A projected field was absent from every record in a non-empty page.

    Raised only when GREENHOUSE_STRICT_PROJECTION is set (tests, CI). At runtime
    the same condition is reported as a diagnostic plus a `schema_warning` on the
    result, because a false positive must not break a recruiter's tool call.
    """


def strict_projection() -> bool:
    """Whether a projection/payload mismatch should raise instead of warn."""
    return os.environ.get("GREENHOUSE_STRICT_PROJECTION", "").strip().lower() in {
        "1", "true", "yes", "on",
    }


def _absent_from_all(items: list[Any], fields: dict[str, Any]) -> list[str]:
    """Projected field names present in the spec but in none of the records.

    Absence from *some* records is ordinary optional data (`rejected_at` on an
    active application, `closed_at` on an open job). Absence from *every* record
    in a non-empty page is a schema mismatch — the field was renamed or moved,
    which is exactly the Harvest v3 failure that `_project_item`'s `continue`
    would otherwise turn into a silently emptier record.
    """
    dicts = [i for i in items if isinstance(i, dict)]
    if not dicts:
        return []
    return sorted(k for k in fields if not any(k in d for d in dicts))


def max_result_bytes() -> int:
    """Result budget in bytes, overridable via GREENHOUSE_MAX_RESULT_BYTES."""
    raw = os.environ.get("GREENHOUSE_MAX_RESULT_BYTES", "")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_MAX_RESULT_BYTES
    return value if value > 0 else DEFAULT_MAX_RESULT_BYTES


# --------------------------------------------------------------------------
# Field projections
# --------------------------------------------------------------------------
# Spec values:
#   True            keep the value as-is
#   ("a", "b")      keep only these sub-keys, elementwise for lists of dicts
#   "count"         replace the list with "<key>_count": len(value)

# Field names below are Harvest v3. The v1 names they replaced are noted inline
# because a stale name here does not error — it silently thins every record.
# See docs/harvest-v3-migration.md and TestStrictProjection in tests/.

_JOB_FIELDS: dict[str, Any] = {
    "id": True,
    "name": True,
    "requisition_id": True,
    "status": True,
    "confidential": True,
    "created_at": True,
    "opened_at": True,
    "closed_at": True,
    "department_id": True,   # v1: departments[] ({id, name})
    "office_ids": True,      # v1: offices[] ({id, name})
    "openings": "count",
    "hiring_team": "count",
}

_APPLICATION_FIELDS: dict[str, Any] = {
    "id": True,
    "candidate_id": True,
    "prospect": True,
    "status": True,
    "created_at": True,      # v1: applied_at
    "last_activity_at": True,
    "rejected_at": True,
    "jobs": ("id", "name"),
    "source": ("id", "public_name"),
    "referrer_id": True,     # v1: credited_to ({id, name})
    "rejection_reason": ("id", "name"),
    "answers": "count",
    # v3 moved `current_stage` and `attachments` out to their own endpoints, so
    # neither arrives inline any more. Listing them here would flag a mismatch on
    # every healthy call and train readers to ignore the warning.
}

_CANDIDATE_FIELDS: dict[str, Any] = {
    "id": True,
    "first_name": True,
    "last_name": True,
    "title": True,
    "company": True,
    "created_at": True,
    "updated_at": True,
    "last_activity": True,
    "is_private": True,
    "tags": True,
    "email_addresses": ("value", "type"),
    "phone_numbers": ("value", "type"),
    "recruiter": ("id", "name"),
    "coordinator": ("id", "name"),
    "educations": "count",
    "employments": "count",
    # v3 removed `attachments`, `application_ids` and `applications[]` from
    # candidates — fetch attachments from /attachments and applications by
    # filtering /applications on candidate_id.
}

_SCORECARD_FIELDS: dict[str, Any] = {
    "id": True,
    "candidate_id": True,
    "application_id": True,
    "interview": True,
    "interviewed_at": True,
    "submitted_at": True,
    "candidate_rating": True,  # v1: overall_recommendation
    "submitter_id": True,      # v1: submitted_by ({id, name})
    "interviewer": ("id", "name"),
    "attributes": "count",
    "questions": "count",
}

_PROJECTIONS: dict[str, dict[str, Any]] = {
    "list_jobs": _JOB_FIELDS,
    "list_applications": _APPLICATION_FIELDS,
    "list_candidates": _CANDIDATE_FIELDS,
    "search_candidates_by_name": _CANDIDATE_FIELDS,
    "search_candidates_by_email": _CANDIDATE_FIELDS,
    "list_scorecards": _SCORECARD_FIELDS,
    "list_scorecards_for_application": _SCORECARD_FIELDS,
}

# Plain-English hint about the narrowing dimensions each tool supports, used to
# tell the model how to get the rest of what the user asked for.
_NARROW_HINTS: dict[str, str] = {
    "list_jobs": "a status (open/closed/draft), a department, an office, or a created-after date",
    "list_applications": "a job, a status, a stage, or a date range",
    "list_candidates": "a job, an updated-after date, or a specific name or email",
    "search_candidates_by_name": "a more specific name",
    "search_candidates_by_email": "a specific email address",
    "list_scorecards": "a single job or application",
}


def _sizeof(payload: Any) -> int:
    """Serialized byte size of a result, as the client will actually see it.

    FastMCP renders dict results with `indent=2`, which inflates a compact dump by
    roughly 2x — measuring compactly here would let results land far over budget.
    """
    try:
        return len(json.dumps(payload, default=str, indent=2).encode())
    except (TypeError, ValueError):
        return len(str(payload).encode())


def _project_value(value: Any, spec: Any) -> Any:
    """Apply a single field spec to a value."""
    if spec is True:
        return value
    if isinstance(spec, tuple):
        if isinstance(value, list):
            return [
                {k: v.get(k) for k in spec if k in v} if isinstance(v, dict) else v
                for v in value
            ]
        if isinstance(value, dict):
            return {k: value.get(k) for k in spec if k in value}
        return value
    return value


def _project_item(item: Any, fields: dict[str, Any]) -> Any:
    """Reduce one record to the projected fields, counting bulky sub-lists."""
    if not isinstance(item, dict):
        return item

    out: dict[str, Any] = {}
    for key, spec in fields.items():
        if key not in item:
            continue
        value = item[key]
        if spec == "count":
            out[f"{key}_count"] = len(value) if isinstance(value, (list, dict)) else 0
            continue
        out[key] = _project_value(value, spec)
    return out


def _clamp_strings(payload: Any, limit: int) -> Any:
    """Recursively clamp free-text string fields to `limit` characters."""
    if isinstance(payload, dict):
        out: dict[str, Any] = {}
        for key, value in payload.items():
            if key in _TEXT_KEYS and isinstance(value, str) and len(value) > limit:
                out[key] = value[:limit] + "… [trimmed to fit]"
            else:
                out[key] = _clamp_strings(value, limit)
        return out
    if isinstance(payload, list):
        return [_clamp_strings(v, limit) for v in payload]
    return payload


def _note_for(tool_name: str, kept: int, total: int, projected: bool) -> str:
    """Plain-English explanation of what was shaved, aimed at the model."""
    parts: list[str] = []
    if kept < total:
        parts.append(
            f"This is a large result, so only the first {kept} of {total} records "
            f"are shown here."
        )
    if projected:
        parts.append(
            "Records are trimmed to their key fields; ask for a specific record by "
            "ID to see everything."
        )
    hint = _NARROW_HINTS.get(tool_name)
    if kept < total and hint:
        parts.append(
            f"Do not tell the user to use flags or paging. Instead, either narrow by "
            f"{hint} and call this tool again, or call it again passing `cursor` set "
            f"to this result's `next_cursor` and combine the results, so the user "
            f"still gets the full answer they asked for. Pass the cursor on its own — "
            f"Greenhouse rejects a cursor sent together with filters."
        )
    return " ".join(parts)


def shape_result(tool_name: str, result: Any) -> Any:
    """Return `result` shrunk to fit the result budget, or unchanged if it fits.

    Never raises — on any unexpected shape the original result is returned.
    """
    try:
        original_bytes = _sizeof(result)
        shaped = _shape_result(tool_name, result)
        if shaped is not result:
            # Recorded so the budget can eventually be set from observed sizes
            # rather than a guess. See diagnostics.py.
            from greenhouse_mcp.diagnostics import record

            record(
                "result_shaped",
                tool=tool_name,
                original_bytes=original_bytes,
                shaped_bytes=_sizeof(shaped),
                budget_bytes=max_result_bytes(),
                returned=shaped.get("returned") if isinstance(shaped, dict) else None,
                total_found=shaped.get("total_found") if isinstance(shaped, dict) else None,
            )
        return shaped
    except ProjectionMismatch:
        # Deliberately not swallowed: strict mode exists so a schema drift fails a
        # test rather than quietly returning a thinner record.
        raise
    except Exception:  # pragma: no cover — shaping must never break a tool call
        return result


def _shape_result(tool_name: str, result: Any) -> Any:
    hard_budget = max_result_bytes()
    if _sizeof(result) <= hard_budget:
        return result

    # Errors are small and must pass through verbatim.
    if isinstance(result, dict) and "error" in result and "status_code" in result:
        return result

    # Shape against a reduced target so `_finalize`'s additions still fit.
    budget = max(hard_budget - _RESERVE_BYTES, hard_budget // 2)

    if not isinstance(result, dict) or not isinstance(result.get("items"), list):
        # No item list to page down — all we can do is clamp free text.
        clamped: Any = result
        for limit in _STRING_CLAMPS:
            clamped = _clamp_strings(result, limit)
            if _sizeof(clamped) <= budget:
                break
        return clamped

    items: list[Any] = result["items"]
    reported_total = result.get("total")
    total: int = reported_total if isinstance(reported_total, int) else len(items)
    envelope = {k: v for k, v in result.items() if k != "items"}

    # Stage 1 — project list items to the fields recruiters read.
    fields = _PROJECTIONS.get(tool_name)
    projected = False
    if fields:
        missing = _absent_from_all(items, fields)
        if missing:
            if strict_projection():
                raise ProjectionMismatch(
                    f"{tool_name}: projected field(s) {', '.join(missing)} absent from "
                    f"every one of {len(items)} records — the API schema has changed."
                )
            from greenhouse_mcp.diagnostics import record

            record("projection_mismatch", tool=tool_name, missing=missing)
            envelope["schema_warning"] = (
                f"These records are missing expected field(s): {', '.join(missing)}. "
                f"The data shown may be incomplete — tell the user this answer could "
                f"not be fully verified rather than presenting it as complete."
            )
        candidate = [_project_item(i, fields) for i in items]
        # Guard against a projection that matched nothing useful.
        if any(candidate):
            items = candidate
            projected = True
    shaped: dict[str, Any] = {**envelope, "items": items}
    if _sizeof(shaped) <= budget:
        return _enforce(
            _finalize(tool_name, shaped, len(items), total, projected),
            tool_name, hard_budget, total, projected,
        )

    # Stage 2 — clamp free text.
    for limit in _STRING_CLAMPS:
        shaped = {**envelope, "items": _clamp_strings(items, limit)}
        if _sizeof(shaped) <= budget:
            return _enforce(
                _finalize(tool_name, shaped, len(shaped["items"]), total, projected),
                tool_name, hard_budget, total, projected,
            )
    items = shaped["items"]

    # Stage 3 — drop items from the tail until the page fits.
    overhead = _sizeof({**envelope, "items": []})
    kept = len(items)
    while kept > _MIN_ITEMS:
        kept = max(_MIN_ITEMS, kept // 2)
        if overhead + _sizeof(items[:kept]) <= budget:
            break
    # Grow back one at a time to keep as many rows as actually fit.
    while kept < len(items) and overhead + _sizeof(items[: kept + 1]) <= budget:
        kept += 1

    shaped = {**envelope, "items": items[:kept]}
    return _enforce(
        _finalize(tool_name, shaped, kept, total, projected),
        tool_name, hard_budget, total, projected,
    )


def _enforce(
    shaped: dict[str, Any],
    tool_name: str,
    hard_budget: int,
    total: int,
    projected: bool,
) -> dict[str, Any]:
    """Last-resort guarantee that the finalized result is within the hard budget."""
    items = shaped.get("items")
    if not isinstance(items, list):
        return shaped
    while _sizeof(shaped) > hard_budget and len(items) > _MIN_ITEMS:
        items = items[: len(items) - max(1, len(items) // 10)]
        shaped = _finalize(
            tool_name, {**shaped, "items": items}, len(items), total, projected
        )
    return shaped


def _finalize(
    tool_name: str,
    shaped: dict[str, Any],
    kept: int,
    total: int,
    projected: bool,
) -> dict[str, Any]:
    """Attach the counts and the plain-English note describing the shaping."""
    shaped["returned"] = kept
    shaped["total_found"] = total
    if kept < total:
        shaped["has_next"] = True
    note = _note_for(tool_name, kept, total, projected)
    if note:
        shaped["result_note"] = note
    return shaped
