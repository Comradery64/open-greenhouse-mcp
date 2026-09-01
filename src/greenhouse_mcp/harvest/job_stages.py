"""Harvest API — Job Stages tools (3 tools)."""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field

from greenhouse_mcp.client import GreenhouseClient


async def list_job_stages(
    client: GreenhouseClient,
    *,
    per_page: Annotated[int, Field(description="Results per page (max 500)")] = 500,
    page: Annotated[int, Field(description="Page number (starts at 1)")] = 1,
    paginate: Annotated[
        str, Field(description="'single' for one page, 'all' to auto-fetch every page")
    ] = "single",
) -> dict[str, Any]:
    """List all job stages across all jobs. Read-only.

    For stages on a specific job in pipeline order, use list_job_stages_for_job
    instead — it's more useful for resolving stage names to IDs.
    """
    params: dict[str, Any] = {"per_page": per_page, "page": page}
    return await client.harvest_get("/job_stages", params=params, paginate=paginate)


async def list_job_stages_for_job(
    client: GreenhouseClient,
    *,
    job_id: Annotated[int, Field(description="Greenhouse job ID")],
) -> dict[str, Any]:
    """List pipeline stages for a specific job in order. Read-only.

    This is the primary tool for resolving stage names to stage IDs. When a
    user says "move to the onsite stage," use this to find the stage_id. To
    find the job_id first: list_jobs → match by name.
    """
    # v3 replaced the job-scoped /jobs/{id}/stages with a filtered top-level
    # collection. The filter is plural `job_ids`; `job_id` returns a 422.
    return await client.harvest_get(
        "/job_interview_stages", params={"job_ids": job_id, "per_page": 500}
    )


async def _stage_names_for_job(client: GreenhouseClient, job_id: int) -> dict[int, str]:
    """Map stage_id → stage name for one job.

    v3 stopped returning `current_stage` inline on applications; each carries a
    `stage_id` instead. Resolving that per application would cost two extra
    calls per row, so the stage list is fetched once per job and cached — one
    extra call regardless of pipeline size, which matters against v3's stricter
    rate-limit window.
    """
    result = await client.harvest_get_cached(
        "/job_interview_stages", params={"job_ids": job_id, "per_page": 500}
    )
    if "error" in result and "status_code" in result:
        return {}
    names: dict[int, str] = {}
    for stage in result.get("items", []):
        if isinstance(stage, dict) and stage.get("id") is not None:
            names[stage["id"]] = stage.get("name", "Unknown")
    return names


def _stage_name(app: dict[str, Any], names: dict[int, str]) -> str:
    """Resolve an application's stage name from its `stage_id`."""
    stage_id = app.get("stage_id")
    if stage_id is None:
        return "Unknown"
    return names.get(stage_id, "Unknown")


async def _stage_names_for_apps(
    client: GreenhouseClient, apps: list[dict[str, Any]]
) -> dict[int, str]:
    """Merged stage_id → name map covering every job the applications touch."""
    job_ids: set[int] = set()
    for app in apps:
        for job in app.get("jobs", []) or []:
            if isinstance(job, dict) and job.get("id") is not None:
                job_ids.add(job["id"])
    names: dict[int, str] = {}
    for jid in job_ids:
        names.update(await _stage_names_for_job(client, jid))
    return names


async def get_job_stage(
    client: GreenhouseClient,
    *,
    job_stage_id: Annotated[
        int, Field(description="Job stage ID — get from list_job_stages_for_job")
    ],
) -> dict[str, Any]:
    """Get a single stage by ID. Read-only.

    Returns stage name, configured interviews, and associated job.
    Usually list_job_stages_for_job is more useful — it gives all stages
    in pipeline order.
    """
    return await client.harvest_get_one(f"/job_stages/{job_stage_id}")
