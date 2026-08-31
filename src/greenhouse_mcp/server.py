"""FastMCP server with tool registration and CLI entry point."""

from __future__ import annotations

import functools
import inspect
import os
import sys
from collections.abc import Callable
from typing import Any

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from greenhouse_mcp.client import GreenhouseClient
from greenhouse_mcp.errors import build_error, config_error, internal_error
from greenhouse_mcp.permissions import UserPermissions, resolve_user_permissions
from greenhouse_mcp.shaping import shape_result

load_dotenv()

_client: GreenhouseClient | None = None
_user_permissions: UserPermissions | None = None


def _check_job_scope(
    perms: UserPermissions | None,
    job_id: int | None,
) -> None:
    """Raise PermissionError if the user is not permitted to write to this job."""
    if perms is None:
        return  # No user-scoped mode — skip check
    if job_id is None:
        return  # No job context to check
    if perms.permitted_job_ids is None:
        return  # Site admin — all jobs allowed
    if job_id in perms.permitted_job_ids:
        return
    raise PermissionError(
        f"User {perms.name} (ID {perms.user_id}) is not permitted "
        f"to write to job {job_id}. "
        f"They have access to jobs: {sorted(perms.permitted_job_ids)}"
    )


def get_client() -> GreenhouseClient:
    """Get or create the shared Greenhouse client."""
    global _client
    if _client is not None:
        return _client

    api_key = os.environ.get("GREENHOUSE_API_KEY")
    board_token = os.environ.get("GREENHOUSE_BOARD_TOKEN")
    on_behalf_of = os.environ.get("GREENHOUSE_ON_BEHALF_OF")
    client_id = os.environ.get("GREENHOUSE_CLIENT_ID")
    client_secret = os.environ.get("GREENHOUSE_CLIENT_SECRET")
    user_id = os.environ.get("GREENHOUSE_USER_ID")

    if not (client_id and client_secret) and not board_token:
        raise ValueError(
            "Harvest v3 requires GREENHOUSE_CLIENT_ID and GREENHOUSE_CLIENT_SECRET.\n"
            "Ask your Greenhouse admin to create API credentials: Configure > Dev Center >\n"
            "API Credential Management. GREENHOUSE_API_KEY was for Harvest v1, which\n"
            "Greenhouse switched off after 2026-08-31 and which no longer works.\n"
            "Board token (public job board only): your job board URL slug."
        )

    _client = GreenhouseClient(
        api_key=api_key,
        board_token=board_token,
        on_behalf_of=on_behalf_of,
        client_id=client_id,
        client_secret=client_secret,
        user_id=user_id,
    )
    return _client


def _make_tool_wrapper(
    fn: Callable[..., Any], is_write: bool = False,
) -> Callable[..., Any]:
    """Create a wrapper that injects get_client() and enforces job scope."""

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        # A missing or blank key is the likeliest failure for a non-technical
        # user, and must not surface as a raw traceback.
        try:
            client = get_client()
        except ValueError as e:
            return config_error(str(e), "/not-configured")
        if is_write and _user_permissions is not None:
            # Check job_id or new_job_id (move_application uses new_job_id).
            # NOTE: Many recruiter write tools operate on application_id or
            # candidate_id without a job_id param. For those, the scope check
            # passes (no job context to verify). Full application→job resolution
            # would require an extra API call per invocation. The current check
            # is best-effort: it blocks writes that explicitly target a job the
            # user doesn't have access to.
            job_id = kwargs.get("job_id") or kwargs.get("new_job_id")
            try:
                _check_job_scope(_user_permissions, job_id)
            except PermissionError as e:
                return build_error(403, str(e), f"/{fn.__name__}")
        try:
            result = await fn(client, *args, **kwargs)
        except Exception as e:  # noqa: BLE001 - never leak a traceback to the user
            return internal_error(f"{type(e).__name__}: {e}", f"/{fn.__name__}")
        # Shaped at the MCP boundary only, so composite tools calling list_*
        # internally still receive complete data.
        return shape_result(fn.__name__, result)

    # Remove the `client` parameter from the signature so FastMCP
    # doesn't expose it as a tool parameter.
    orig_sig = inspect.signature(fn)
    params = [p for p in orig_sig.parameters.values() if p.name != "client"]
    wrapper.__signature__ = orig_sig.replace(parameters=params)  # type: ignore[attr-defined]
    return wrapper


# ---------------------------------------------------------------------------
# Tool profiles
# ---------------------------------------------------------------------------

# Write tools allowed in recruiter mode — core pipeline management, not admin.
_RECRUITER_WRITE_TOOLS: set[str] = {
    # Pipeline management
    "reject_application",
    "unreject_application",
    "advance_application",
    "move_application",
    "move_application_same_job",
    "hire_application",
    "create_application",
    "update_application",
    "update_rejection_reason",
    # Bulk operations
    "bulk_reject",
    "bulk_advance",
    "bulk_tag",
    # Candidate interaction
    "add_note_to_candidate",
    "add_email_note_to_candidate",
    "add_tag_to_candidate",
    "remove_tag_from_candidate",
    "add_attachment",
    "add_attachment_to_application",
    "update_candidate",
    # Prospects
    "add_prospect",
    "convert_prospect",
    # Interviews
    "create_interview",
    "update_interview",
    "delete_interview",
}

# Webhook tools that are read-only (safe in any profile)
_WEBHOOK_READ_TOOLS: set[str] = {
    "webhook_list_rules",
    "webhook_get_rule",
    "webhook_list_events",
}

# Curated slim tool set for the "assistant" profile — everyday recruiting
# workflows (screening, triage, pipeline search/hygiene, notes/tags, and the
# common pipeline-management writes), without the ~90 admin/config tools.
_ASSISTANT_TOOLS: set[str] = {
    # Composite workflow tools
    "screen_candidate", "fetch_new_applications", "scan_pipeline_resumes",
    "search_pipeline_candidates",
    "pipeline_summary", "candidates_needing_action", "stale_applications",
    "pipeline_metrics", "source_effectiveness", "time_to_hire",
    # Core reads
    "list_jobs", "get_job", "list_job_stages_for_job",
    "list_applications", "get_application",
    "list_candidates", "get_candidate",
    "search_candidates_by_name", "search_candidates_by_email",
    "read_candidate_resume", "download_attachment",
    "list_scorecards_for_application", "get_scorecard", "get_activity_feed",
    "list_rejection_reasons",
    # Core pipeline writes
    "advance_application", "reject_application", "unreject_application",
    "add_note_to_candidate", "add_tag_to_candidate",
    "bulk_reject", "bulk_advance", "bulk_tag",
}

# Method names that indicate a write operation
_WRITE_METHODS: set[str] = {
    "harvest_post",
    "harvest_patch",
    "harvest_put",
    "harvest_delete",
    "ingestion_post",
    "board_post",
}


# Tools whose Harvest endpoints and fields have been reviewed against the v3
# migration guides. Everything else is withheld rather than left registered and
# broken: an unmigrated tool against v3 either 404s or — worse — returns a payload
# whose renamed fields read as absent, which looks like a real but empty answer.
# Phase B moves names into this set module by module. See
# docs/harvest-v3-migration.md.
_V3_MIGRATED_TOOLS: set[str] = set(_ASSISTANT_TOOLS)

# Job Board and Ingestion are separate products on their own v1 paths and were not
# part of the Harvest sunset, so their tools are unaffected by the allowlist.
_NON_HARVEST_MODULE_PREFIXES = ("greenhouse_mcp.job_board", "greenhouse_mcp.ingestion")


def _v3_gate_enabled() -> bool:
    """Whether to withhold tools not yet migrated to Harvest v3.

    On by default. `GREENHOUSE_ALLOW_UNMIGRATED_TOOLS=1` restores the full set for
    someone doing Phase B work who needs an unmigrated tool to fail visibly.
    """
    return os.environ.get("GREENHOUSE_ALLOW_UNMIGRATED_TOOLS", "").strip().lower() not in {
        "1", "true", "yes", "on",
    }


def _is_harvest_tool(fn: Callable[..., Any]) -> bool:
    module = getattr(fn, "__module__", "") or ""
    return not module.startswith(_NON_HARVEST_MODULE_PREFIXES)


def _is_write_tool(fn: Callable[..., Any]) -> bool:
    """Check if a tool function calls any write client methods."""
    try:
        source = inspect.getsource(fn)
    except (OSError, TypeError):
        return False
    return any(m in source for m in _WRITE_METHODS)


def _should_register(name: str, fn: Callable[..., Any], profile: str) -> bool:
    """Decide whether a tool should be registered based on the active profile."""
    # The v3 gate sits above the profiles: a tool that would return wrong data is
    # withheld even from "full", which otherwise registers everything.
    if _v3_gate_enabled() and _is_harvest_tool(fn) and name not in _V3_MIGRATED_TOOLS:
        return False
    if profile == "full":
        return True
    if profile == "read-only":
        return not _is_write_tool(fn)
    if profile == "assistant":
        return name in _ASSISTANT_TOOLS
    # recruiter: allow reads + approved write tools
    if _is_write_tool(fn):
        return name in _RECRUITER_WRITE_TOOLS
    return True


def create_server() -> FastMCP:
    """Create and configure the FastMCP server with all tools."""
    mcp = FastMCP("Greenhouse")
    mcp.description = "Comprehensive MCP server for the full Greenhouse API (~175 tools)"  # type: ignore[attr-defined]

    # --- Determine tool profile ---
    profile_raw = os.environ.get("GREENHOUSE_TOOL_PROFILE", "").lower().strip()
    read_only = os.environ.get("GREENHOUSE_READ_ONLY", "").lower() in (
        "true", "1", "yes",
    )
    user_id_raw = os.environ.get("GREENHOUSE_USER_ID", "").strip()

    if user_id_raw:
        import asyncio

        client = get_client()
        try:
            user_id = int(user_id_raw)
        except ValueError:
            print(
                f"ERROR: GREENHOUSE_USER_ID must be a numeric Greenhouse "
                f"user ID, got: {user_id_raw!r}",
                file=sys.stderr,
            )
            sys.exit(1)

        try:
            perms = asyncio.run(
                resolve_user_permissions(client, user_id=user_id),
            )
        except ValueError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            sys.exit(1)

        global _user_permissions
        _user_permissions = perms
        profile = perms.profile
        client.set_on_behalf_of(str(user_id))

        jobs_info = (
            f" | Jobs: {len(perms.permitted_job_ids)}"
            if perms.permitted_job_ids is not None
            else ""
        )
        print(
            f"User: {perms.name} ({perms.email}) | "
            f"Admin: {perms.site_admin} | "
            f"Derived profile: {profile}{jobs_info}",
            file=sys.stderr,
        )
    elif profile_raw in ("full", "recruiter", "read-only", "assistant"):
        profile = profile_raw
    elif read_only:
        profile = "read-only"
    else:
        # Fall back to the recruiter-safe set, not "full". An unset or
        # unrecognised value previously exposed every tool with writes enabled,
        # including destructive ones — the wrong default when the operator has
        # not chosen anything. An unsubstituted "${user_config.tool_profile}"
        # placeholder from a packaged bundle lands here too.
        profile = "assistant"
        if profile_raw:
            from greenhouse_mcp.diagnostics import record

            record("unknown_tool_profile", requested=profile_raw, fell_back_to=profile)

    # --- Harvest tools ---
    from greenhouse_mcp.harvest import (
        activity_feed,
        analytics,
        applications,
        approvals,
        attachments,
        batch,
        candidates,
        close_reasons,
        custom_fields,
        demographics,
        departments,
        education,
        eeoc,
        email_templates,
        hiring_team,
        interviews,
        job_openings,
        job_posts,
        job_stages,
        jobs,
        offers,
        offices,
        prospect_pools,
        rejection_reasons,
        scorecards,
        screening,
        search,
        sources,
        sourcing,
        tags,
        tracking_links,
        user_permissions,
        user_roles,
        users,
        workflows,
    )

    harvest_modules = [
        candidates,
        applications,
        jobs,
        job_posts,
        job_stages,
        job_openings,
        offers,
        scorecards,
        interviews,
        users,
        user_permissions,
        departments,
        offices,
        custom_fields,
        sources,
        rejection_reasons,
        email_templates,
        tags,
        activity_feed,
        eeoc,
        demographics,
        approvals,
        hiring_team,
        prospect_pools,
        close_reasons,
        tracking_links,
        user_roles,
        education,
        workflows,
        analytics,
        batch,
        search,
        attachments,
        screening,
        sourcing,
    ]

    # --- Job Board tools ---
    from greenhouse_mcp.job_board import (
        applications as board_applications,
    )
    from greenhouse_mcp.job_board import (
        board,
    )
    from greenhouse_mcp.job_board import (
        departments as board_departments,
    )
    from greenhouse_mcp.job_board import (
        educations as board_educations,
    )
    from greenhouse_mcp.job_board import (
        jobs as board_jobs,
    )
    from greenhouse_mcp.job_board import (
        offices as board_offices,
    )
    from greenhouse_mcp.job_board import (
        prospects as board_prospects,
    )

    board_modules = [
        board,
        board_jobs,
        board_departments,
        board_offices,
        board_prospects,
        board_educations,
        board_applications,
    ]

    # --- Ingestion tools ---
    from greenhouse_mcp.ingestion import (
        candidates as ing_candidates,
    )
    from greenhouse_mcp.ingestion import (
        jobs as ing_jobs,
    )
    from greenhouse_mcp.ingestion import (
        prospects as ing_prospects,
    )
    from greenhouse_mcp.ingestion import (
        retrieve as ing_retrieve,
    )
    from greenhouse_mcp.ingestion import (
        tracking as ing_tracking,
    )
    from greenhouse_mcp.ingestion import (
        users as ing_users,
    )

    ingestion_modules = [
        ing_candidates,
        ing_jobs,
        ing_prospects,
        ing_retrieve,
        ing_tracking,
        ing_users,
    ]

    api_key = os.environ.get("GREENHOUSE_API_KEY")
    board_token = os.environ.get("GREENHOUSE_BOARD_TOKEN")

    # Always register all tool definitions so MCP clients can discover
    # available tools. Credentials are checked at invocation time.
    api_modules = harvest_modules + ingestion_modules + board_modules

    registered = 0
    for module in api_modules:
        for name, fn in inspect.getmembers(module, inspect.isfunction):
            if name.startswith("_"):
                continue
            if fn.__module__ != module.__name__:
                continue
            if not _should_register(name, fn, profile):
                continue
            is_write = _is_write_tool(fn)
            wrapper = _make_tool_wrapper(fn, is_write=is_write)
            mcp.tool(name=name, description=fn.__doc__ or name)(wrapper)
            registered += 1

    # --- Webhook tools ---
    from pathlib import Path

    from greenhouse_mcp.webhook_receiver.models import WebhookDB
    from greenhouse_mcp.webhook_tools import events, rules, setup, testing

    _webhook_db: WebhookDB | None = None

    def get_webhook_db() -> WebhookDB:
        nonlocal _webhook_db
        if _webhook_db is None:
            db_path = os.environ.get(
                "WEBHOOK_DB_PATH",
                str(Path.home() / ".open-greenhouse-mcp" / "webhooks.db"),
            )
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
            _webhook_db = WebhookDB(db_path)
        return _webhook_db

    def _make_webhook_tool_wrapper(fn: Callable[..., Any]) -> Callable[..., Any]:
        """Create a wrapper that injects get_webhook_db() and has the correct signature."""

        @functools.wraps(fn)
        async def wrapper(*args: Any, _fn: Callable[..., Any] = fn, **kwargs: Any) -> Any:
            db = get_webhook_db()
            return await _fn(db, *args, **kwargs)

        orig_sig = inspect.signature(fn)
        params = [p for p in orig_sig.parameters.values() if p.name != "db"]
        wrapper.__signature__ = orig_sig.replace(parameters=params)  # type: ignore[attr-defined]
        return wrapper

    webhook_modules = [rules, events, testing, setup]
    for module in webhook_modules:
        for name, fn in inspect.getmembers(module, inspect.isfunction):
            if name.startswith("_") or not name.startswith("webhook_"):
                continue
            # The assistant profile is an explicit, curated list; webhook tools are
            # not on it, and registering them anyway would silently widen it.
            if profile == "assistant":
                continue
            if profile != "full" and name not in _WEBHOOK_READ_TOOLS:
                continue

            sig = inspect.signature(fn)
            params = list(sig.parameters.values())

            if params and params[0].name == "db":
                wrapper = _make_webhook_tool_wrapper(fn)
                mcp.tool(name=name, description=fn.__doc__ or name)(wrapper)
            else:
                # No db parameter (like webhook_list_events)
                mcp.tool(name=name, description=fn.__doc__ or name)(fn)
            registered += 1

    # --- Startup banner ---
    from importlib.metadata import version as pkg_version

    from greenhouse_mcp.logging import logger

    try:
        ver = pkg_version("open-greenhouse-mcp")
    except Exception:
        ver = "dev"

    apis = []
    if api_key:
        apis.append("harvest")
        apis.append("ingestion")
    if board_token:
        apis.append("job-board")
    if not apis:
        apis.append("none (tools registered, credentials needed at invocation)")
    write_modes = {
        "full": "enabled",
        "recruiter": "recruiter-safe",
        "read-only": "disabled",
        "assistant": "curated-safe",
    }
    writes = write_modes.get(profile, "disabled")

    api_str = ", ".join(apis)
    print(
        f"open-greenhouse-mcp v{ver}\n"
        f"Profile: {profile} | Tools: {registered} | Writes: {writes} | APIs: {api_str}",
        file=sys.stderr,
    )

    logger.info(
        "server_started",
        version=ver,
        profile=profile,
        tools_registered=registered,
        writes=writes,
        apis=api_str,
    )

    return mcp


# Global server instance
mcp = create_server()


def main() -> None:
    """CLI entry point."""
    mcp.run()
