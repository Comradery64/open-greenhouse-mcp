"""Harvest API — Rejection Reasons tools (1 tool)."""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field

from greenhouse_mcp.client import GreenhouseClient


async def list_rejection_reasons(
    client: GreenhouseClient,
    *,
    per_page: Annotated[int, Field(description="Results per page (max 500)")] = 500,
    cursor: Annotated[
        str | None,
        Field(
            description=(
                "Opaque cursor from a previous call's `next_cursor`, to fetch the "
                "next page. When set, all other filters are ignored — they are "
                "already baked into the cursor."
            )
        ),
    ] = None,
    force_refresh: Annotated[bool, Field(description="Bypass cache and fetch fresh data")] = False,
) -> dict[str, Any]:
    """List all rejection reasons. Read-only.

    Resolves rejection reason names to IDs. When a user says "reject for
    'not enough experience'," use this to find the ID, then pass it to
    reject_application or bulk_reject.
    """
    params: dict[str, Any] = {"per_page": per_page}
    return await client.harvest_get_cached(
        "/rejection_reasons", params=params, force_refresh=force_refresh, cursor=cursor
    )
