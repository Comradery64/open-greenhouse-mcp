#!/usr/bin/env python3
"""Read-only smoke check of the Harvest v3 migration against a live instance.

Run it through `op run` so credentials go from 1Password into the process
without touching a shell variable, a file, or the terminal:

    op run --env-file=env.op -- python scripts/verify_v3.py

What it prints is deliberately narrow: endpoint, HTTP status, row count, and
which *field names* came back. Never values. Candidate records are personal
data and an API secret is an API secret — neither belongs in a terminal
scrollback or a bug report.

Strictly GET. Nothing here advances, rejects, tags, or notes anything.
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from greenhouse_mcp.client import GreenhouseClient  # noqa: E402

# Fields whose absence is the migration failing. Each maps to a rename or a
# move documented in docs/harvest-v3-migration.md.
EXPECTED = {
    "/jobs": ["id", "name", "status", "department_id", "office_ids"],
    "/applications": ["id", "candidate_id", "status", "created_at", "stage_id"],
    "/candidates": ["id", "first_name", "last_name"],
    "/job_interview_stages": ["id", "name"],
    "/attachments": ["type", "url", "filename"],
}

# Fields that must NOT come back — their presence means we are talking to v1.
FORBIDDEN = {
    "/applications": ["applied_at", "current_stage", "credited_to"],
    "/candidates": ["attachments", "application_ids", "applications"],
}


def _report(label, status, items, note=""):
    keys: set[str] = set()
    for item in items[:25]:
        if isinstance(item, dict):
            keys |= set(item)
    exp = EXPECTED.get(label, [])
    missing = [f for f in exp if f not in keys] if items else []
    stale = [f for f in FORBIDDEN.get(label, []) if f in keys]

    ok = status == 200 and not missing and not stale
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {label:26} status={status} rows={len(items)} {note}")
    if missing:
        print(f"         missing expected field(s): {', '.join(missing)}")
    if stale:
        print(f"         v1 field(s) still present: {', '.join(stale)}")
    if not items and status == 200:
        print("         (no rows — field checks inconclusive, not a pass)")
    return ok and bool(items)


async def main() -> int:
    client_id = os.environ.get("GREENHOUSE_CLIENT_ID")
    client_secret = os.environ.get("GREENHOUSE_CLIENT_SECRET")
    if not client_id or not client_secret:
        print("Set GREENHOUSE_CLIENT_ID and GREENHOUSE_CLIENT_SECRET (via op run).")
        return 2

    client = GreenhouseClient(
        client_id=client_id,
        client_secret=client_secret,
        user_id=os.environ.get("GREENHOUSE_USER_ID"),
    )
    results: list[bool] = []
    print("Harvest v3 read-only verification\n")

    try:
        print("token exchange")
        try:
            await client._access_token()
            print("  [PASS] auth.greenhouse.io/token       obtained")
        except Exception as e:  # noqa: BLE001
            print(f"  [FAIL] auth.greenhouse.io/token       {type(e).__name__}")
            print("         Everything else depends on this. Check the client")
            print("         credentials and that scopes were granted.")
            return 1

        print("\ncore reads")
        jobs = await client.harvest_get("/jobs", params={"per_page": 5})
        job_items = jobs.get("items", []) if "error" not in jobs else []
        results.append(_report("/jobs", jobs.get("status_code", 200), job_items))

        apps = await client.harvest_get("/applications", params={"per_page": 5})
        app_items = apps.get("items", []) if "error" not in apps else []
        results.append(_report("/applications", apps.get("status_code", 200), app_items))

        cands = await client.harvest_get("/candidates", params={"per_page": 5})
        cand_items = cands.get("items", []) if "error" not in cands else []
        results.append(_report("/candidates", cands.get("status_code", 200), cand_items))

        print("\nendpoints v3 split out (the unverified ones)")
        if job_items:
            jid = job_items[0].get("id")
            st = await client.harvest_get(
                "/job_interview_stages", params={"job_ids": jid, "per_page": 5}
            )
            st_items = st.get("items", []) if "error" not in st else []
            results.append(
                _report("/job_interview_stages", st.get("status_code", 200), st_items,
                        note="(filter: job_ids)")
            )
        if app_items:
            cid = app_items[0].get("candidate_id")
            att = await client.harvest_get(
                "/attachments", params={"candidate_ids": cid, "per_page": 5}
            )
            att_items = att.get("items", []) if "error" not in att else []
            results.append(
                _report("/attachments", att.get("status_code", 200), att_items,
                        note="(filter: candidate_ids)")
            )

        print("\ncursor paging")
        page = await client.harvest_get("/candidates", params={"per_page": 2})
        cursor = page.get("next_cursor")
        if not page.get("has_next"):
            print("  [SKIP] not enough rows to page")
        elif not cursor:
            print("  [FAIL] has_next set but no cursor in the Link header")
            results.append(False)
        else:
            nxt = await client.harvest_get("/candidates", cursor=cursor)
            ok = "error" not in nxt
            print(f"  [{'PASS' if ok else 'FAIL'}] cursor resume            "
                  f"rows={len(nxt.get('items', []))}")
            if not ok:
                print(f"         status={nxt.get('status_code')} — a 422 here means the "
                      f"cursor was sent alongside other params")
            results.append(ok)
    finally:
        await client.close()

    print()
    if all(results) and results:
        print("All checks passed. This exercises reads only — writes "
              "(advance/reject/note/tag) remain unverified.")
        return 0
    print("Some checks failed. Report the endpoint and status above; do not "
          "paste response bodies, they contain candidate data.")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
