#!/usr/bin/env python3
"""Read-only smoke check of the Harvest v3 migration against a live instance.

Run it through `op run` so credentials go from 1Password into the process
without touching a shell variable, a file, or the terminal:

    op run --env-file=env.op -- python scripts/verify_v3.py

What it prints is deliberately narrow: endpoint, HTTP status, row count, and
which *field names* came back. Never values. Candidate records are personal
data and an API secret is an API secret — neither belongs in a terminal
scrollback or a bug report.

By default strictly GET. Nothing advances, rejects, tags or notes anything.

`--writes` adds a write-path probe that still mutates nothing: every request
targets an id this script has just *confirmed does not exist*, so there is no
record for the API to change. What that buys is the failure shape — whether the
route exists at all, and which body fields it expects — without putting a real
candidate anywhere near a stage transition.

It proves the door opens. It does not prove what is behind it: a successful
write could still record the wrong thing, and only a sandbox shows that.
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


# An id no Greenhouse instance will have issued. Confirmed unreachable at
# runtime anyway — this is a starting guess, not the safety mechanism.
_ABSENT_ID = 999_999_999_999


async def _confirm_absent(client, collection: str, resource_id: int) -> bool:
    """True only if the collection genuinely has no such record.

    This is the safety interlock: no write is attempted until a read has proved
    there is nothing at that id to damage.
    """
    probe = await client.harvest_get(collection, params={"ids": resource_id})
    if "error" in probe and "status_code" in probe:
        return False  # cannot prove absence — treat as unsafe
    return not probe.get("items")


def _classify(status: int, detail) -> str:
    """Turn a failure into a verdict about the route, not the record.

    The discriminator is the `errors` key, not the status. A missing route and a
    missing record both return 404 `Resource not found` — an earlier version of
    this check treated the two as identical and cheerfully reported that a
    nonexistent control route "exists". Only a request that reached a handler
    gets as far as naming what it could not find:

        no route   -> {"message": "Resource not found"}
        no record  -> {"message": "Resource not found",
                       "errors": "Application not found"}
    """
    text = str(detail)
    has_errors = isinstance(detail, dict) and detail.get("errors")
    if 200 <= status < 300:
        return "UNEXPECTED SUCCESS — investigate, something may have been created"
    if status == 405:
        return "ROUTE MISSING (method not allowed)"
    if status in (400, 422):
        return f"route exists, body rejected: {text[:70]}"
    if status == 404:
        if has_errors:
            return "route exists (handler reached, record absent as intended)"
        return "ROUTE MISSING (no handler reached)"
    return f"status {status}: {text[:60]}"


async def probe_writes(client) -> int:
    """Probe write routes against a confirmed-absent id. Mutates nothing."""
    print("\nwrite routes (probed against a confirmed-absent id — no mutation)")

    if not await _confirm_absent(client, "/applications", _ABSENT_ID):
        print("  [ABORT] could not confirm the probe id is unused; refusing to write")
        return 1
    if not await _confirm_absent(client, "/candidates", _ABSENT_ID):
        print("  [ABORT] could not confirm the probe candidate id is unused")
        return 1
    print(f"  id {_ABSENT_ID} confirmed absent from /applications and /candidates\n")

    # A control: a route that certainly does not exist, so "record absent" can be
    # told apart from "route absent" rather than assumed.
    ctl = await client.harvest_post(f"/applications/{_ABSENT_ID}/__no_such_action__")
    print(f"  [control] bogus action -> "
          f"{_classify(ctl.get('status_code', 0), ctl.get('technical_detail'))}\n")

    # ONLY path-id writes. A write whose id lives in the *body* cannot be made
    # safe this way: Greenhouse does not check that candidate_id refers to a real
    # candidate, so POST /notes with an absent id still creates an orphaned row.
    # That is not hypothetical — this probe created two of them on 2026-09-01
    # before that was understood, and v3 has no DELETE /notes/{id} to undo it.
    # Schemas for /notes and /applied_candidate_tags must come from the docs or a
    # sandbox, never from production.
    probes = [
        ("advance_application", "POST", f"/applications/{_ABSENT_ID}/move", {}),
        ("reject_application", "POST", f"/applications/{_ABSENT_ID}/reject", {}),
        ("unreject_application", "POST", f"/applications/{_ABSENT_ID}/unreject", None),
    ]
    suspicious = 0
    for name, _method, path, body in probes:
        r = await client.harvest_post(path, json_data=body)
        status = r.get("status_code", 200)
        verdict = _classify(status, r.get("technical_detail"))
        if any(w in verdict for w in ("MISSING", "UNEXPECTED", "body rejected")):
            suspicious += 1
        print(f"  {name:24} {path:44} {verdict}")
    print()
    if suspicious:
        print(f"{suspicious} write route(s) look wrong — see above.")
    else:
        print("Every probed write route exists. This does NOT prove a successful "
              "write behaves correctly — only a sandbox shows that.")
    return 1 if suspicious else 0


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
        if "--writes" in sys.argv:
            results.append(await probe_writes(client) == 0)
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
