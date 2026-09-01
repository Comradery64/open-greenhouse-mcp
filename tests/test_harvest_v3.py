"""Contract tests for the Harvest v3 migration.

These cover the behaviour that did not exist under v1 — OAuth token exchange,
cursor paging, and the guards that stop a half-migrated server from returning
quietly wrong data. See docs/harvest-v3-migration.md.
"""
from __future__ import annotations

import base64
import json
import pathlib
import time

import httpx
import pytest
import respx

from greenhouse_mcp import shaping
from greenhouse_mcp.client import HARVEST_BASE, HARVEST_TOKEN_URL, GreenhouseClient

SRC = pathlib.Path(__file__).resolve().parent.parent / "src" / "greenhouse_mcp"


def _client(**kwargs):
    return GreenhouseClient(client_id="cid", client_secret="sekrit", **kwargs)


def _token(access_token="tok-1", expires_in=3600):
    return httpx.Response(
        200,
        json={"token_type": "Bearer", "access_token": access_token, "expires_in": expires_in},
    )


# ---------------------------------------------------------------------------
# Base URL
# ---------------------------------------------------------------------------

class TestBaseUrl:
    def test_harvest_targets_v3(self):
        assert HARVEST_BASE == "https://harvest.greenhouse.io/v3"

    def test_no_harvest_v1_url_remains_in_src(self):
        """Harvest v1 is switched off; any surviving reference is a live bug."""
        offenders = [
            str(p.relative_to(SRC))
            for p in SRC.rglob("*.py")
            if "harvest.greenhouse.io/v1" in p.read_text()
        ]
        assert offenders == []


# ---------------------------------------------------------------------------
# Token exchange
# ---------------------------------------------------------------------------

class TestTokenExchange:
    @respx.mock
    async def test_fetches_and_sends_bearer_token(self):
        respx.post(HARVEST_TOKEN_URL).mock(return_value=_token("abc"))
        route = respx.get(f"{HARVEST_BASE}/jobs").mock(
            return_value=httpx.Response(200, json=[{"id": 1}])
        )
        await _client().harvest_get("/jobs")
        assert route.calls[0].request.headers["authorization"] == "Bearer abc"

    @respx.mock
    async def test_token_request_uses_client_credentials_grant(self):
        token_route = respx.post(HARVEST_TOKEN_URL).mock(return_value=_token())
        respx.get(f"{HARVEST_BASE}/jobs").mock(return_value=httpx.Response(200, json=[]))
        await _client(user_id="99").harvest_get("/jobs")

        req = token_route.calls[0].request
        expected = base64.b64encode(b"cid:sekrit").decode()
        assert req.headers["authorization"] == f"Basic {expected}"
        assert req.headers["content-type"] == "application/x-www-form-urlencoded"
        body = req.content.decode()
        assert "grant_type=client_credentials" in body
        assert "sub=99" in body

    @respx.mock
    async def test_token_is_cached_across_calls(self):
        token_route = respx.post(HARVEST_TOKEN_URL).mock(return_value=_token())
        respx.get(f"{HARVEST_BASE}/jobs").mock(return_value=httpx.Response(200, json=[]))
        client = _client()
        await client.harvest_get("/jobs")
        await client.harvest_get("/jobs")
        assert token_route.call_count == 1

    @respx.mock
    async def test_expired_token_is_refetched(self):
        token_route = respx.post(HARVEST_TOKEN_URL).mock(return_value=_token())
        respx.get(f"{HARVEST_BASE}/jobs").mock(return_value=httpx.Response(200, json=[]))
        client = _client()
        await client.harvest_get("/jobs")
        client._token_expires_at = time.monotonic() - 1  # simulate expiry
        await client.harvest_get("/jobs")
        assert token_route.call_count == 2

    @respx.mock
    async def test_expiry_honours_safety_margin(self):
        """A token valid for 60s must not be treated as usable for the full 60s."""
        respx.post(HARVEST_TOKEN_URL).mock(return_value=_token(expires_in=60))
        respx.get(f"{HARVEST_BASE}/jobs").mock(return_value=httpx.Response(200, json=[]))
        client = _client()
        await client.harvest_get("/jobs")
        assert client._token_expires_at <= time.monotonic()

    @respx.mock
    async def test_401_refreshes_token_and_retries_once(self):
        token_route = respx.post(HARVEST_TOKEN_URL).mock(
            side_effect=[_token("stale"), _token("fresh")]
        )
        calls: list[str] = []

        def handler(request):
            calls.append(request.headers["authorization"])
            if len(calls) == 1:
                return httpx.Response(401, json={"message": "expired"})
            return httpx.Response(200, json=[{"id": 7}])

        respx.get(f"{HARVEST_BASE}/jobs").mock(side_effect=handler)
        result = await _client().harvest_get("/jobs")

        assert calls == ["Bearer stale", "Bearer fresh"]
        assert token_route.call_count == 2
        assert result["items"] == [{"id": 7}]

    @respx.mock
    async def test_missing_credentials_returns_error_not_exception(self):
        result = await GreenhouseClient(board_token="b").harvest_get("/jobs")
        assert result["status_code"] == 401
        assert "client ID" in result["error"]  # not "API key" — v1 wording is gone

    @respx.mock
    async def test_token_endpoint_failure_is_relayed_as_error(self):
        respx.post(HARVEST_TOKEN_URL).mock(return_value=httpx.Response(403, json={}))
        result = await _client().harvest_get("/jobs")
        assert result["status_code"] == 403

    @respx.mock
    async def test_token_response_without_access_token_is_an_error(self):
        respx.post(HARVEST_TOKEN_URL).mock(return_value=httpx.Response(200, json={"ok": True}))
        result = await _client().harvest_get("/jobs")
        assert result["status_code"] == 502

    @respx.mock
    async def test_secret_and_token_never_reach_the_log(self, caplog):
        """The token POST carries the client secret; the reply carries the token."""
        respx.post(HARVEST_TOKEN_URL).mock(return_value=_token("super-secret-token"))
        respx.get(f"{HARVEST_BASE}/jobs").mock(return_value=httpx.Response(200, json=[]))
        with caplog.at_level("DEBUG"):
            await _client().harvest_get("/jobs")
        logged = caplog.text
        assert "sekrit" not in logged
        assert "super-secret-token" not in logged
        assert "Basic" not in logged


# ---------------------------------------------------------------------------
# Cursor pagination
# ---------------------------------------------------------------------------

class TestCursorPagination:
    @respx.mock
    async def test_cursor_is_returned_not_the_raw_url(self):
        respx.post(HARVEST_TOKEN_URL).mock(return_value=_token())
        respx.get(f"{HARVEST_BASE}/jobs").mock(
            return_value=httpx.Response(
                200,
                json=[{"id": 1}],
                headers={"Link": f'<{HARVEST_BASE}/jobs?cursor=OPAQUE123>; rel="next"'},
            )
        )
        result = await _client().harvest_get("/jobs")
        assert result["next_cursor"] == "OPAQUE123"
        assert result["has_next"] is True

    @respx.mock
    async def test_cursor_request_sends_cursor_as_sole_parameter(self):
        """v3 returns 422 if `cursor` is combined with filters or per_page."""
        respx.post(HARVEST_TOKEN_URL).mock(return_value=_token())
        route = respx.get(f"{HARVEST_BASE}/jobs").mock(
            return_value=httpx.Response(200, json=[])
        )
        await _client().harvest_get(
            "/jobs", params={"status": "open", "per_page": 100}, cursor="CUR"
        )
        assert dict(route.calls[0].request.url.params) == {"cursor": "CUR"}

    @respx.mock
    async def test_paginate_all_follows_next_without_extra_params(self):
        respx.post(HARVEST_TOKEN_URL).mock(return_value=_token())
        seen: list[dict] = []

        def handler(request):
            seen.append(dict(request.url.params))
            if len(seen) == 1:
                return httpx.Response(
                    200,
                    json=[{"id": 1}],
                    headers={"Link": f'<{HARVEST_BASE}/jobs?cursor=NEXT>; rel="next"'},
                )
            return httpx.Response(200, json=[{"id": 2}])

        respx.get(f"{HARVEST_BASE}/jobs").mock(side_effect=handler)
        result = await _client().harvest_get(
            "/jobs", params={"per_page": 100}, paginate="all"
        )
        assert result["items"] == [{"id": 1}, {"id": 2}]
        assert seen[0] == {"per_page": "100"}  # first page may size itself
        assert seen[1] == {"cursor": "NEXT"}   # subsequent pages: cursor alone

    @respx.mock
    async def test_cursor_is_part_of_the_cache_key(self):
        respx.post(HARVEST_TOKEN_URL).mock(return_value=_token())
        route = respx.get(f"{HARVEST_BASE}/jobs").mock(
            return_value=httpx.Response(200, json=[{"id": 1}])
        )
        client = _client()
        await client.harvest_get_cached("/jobs")
        await client.harvest_get_cached("/jobs", cursor="PAGE2")
        assert route.call_count == 2


# ---------------------------------------------------------------------------
# Strict projection — the silent-failure guard
# ---------------------------------------------------------------------------

def _big_page(items):
    return {"items": items, "total": len(items)}


def _v3_application(i):
    """An application as v3 returns it: created_at, not applied_at."""
    return {
        "id": i,
        "candidate_id": i,
        "prospect": False,
        "status": "active",
        "created_at": "2026-01-01T00:00:00Z",
        "last_activity_at": "2026-01-02T00:00:00Z",
        "rejected_at": None,
        "current_stage": {"id": 1, "name": "Interview"},
        "jobs": [{"id": 1, "name": "Engineer " + "x" * 200}],
        "source": {"id": 1, "public_name": "Referral"},
        "referrer_id": 5,
        "rejection_reason": None,
        "answers": [],
    }


class TestStrictProjection:
    def test_v3_payload_projects_cleanly(self, monkeypatch):
        monkeypatch.setenv("GREENHOUSE_STRICT_PROJECTION", "1")
        page = _big_page([_v3_application(i) for i in range(400)])
        shaped = shaping.shape_result("list_applications", page)
        assert "schema_warning" not in shaped

    def test_v1_field_names_are_caught_in_strict_mode(self, monkeypatch):
        """The exact regression the migration doc warns about."""
        monkeypatch.setenv("GREENHOUSE_STRICT_PROJECTION", "1")
        stale = []
        for i in range(400):
            item = _v3_application(i)
            item["applied_at"] = item.pop("created_at")  # back to the v1 name
            stale.append(item)
        with pytest.raises(shaping.ProjectionMismatch, match="created_at"):
            shaping.shape_result("list_applications", _big_page(stale))

    def test_runtime_warns_rather_than_failing_the_call(self, monkeypatch):
        monkeypatch.delenv("GREENHOUSE_STRICT_PROJECTION", raising=False)
        stale = []
        for i in range(400):
            item = _v3_application(i)
            item["applied_at"] = item.pop("created_at")
            stale.append(item)
        shaped = shaping.shape_result("list_applications", _big_page(stale))
        assert "created_at" in shaped["schema_warning"]
        assert shaped["items"], "a warning must not empty the result"

    def test_optional_field_absent_from_some_records_is_not_a_mismatch(self, monkeypatch):
        """`rejected_at` is legitimately absent on active applications."""
        monkeypatch.setenv("GREENHOUSE_STRICT_PROJECTION", "1")
        items = [_v3_application(i) for i in range(400)]
        for item in items[:200]:
            del item["rejected_at"]
        shaped = shaping.shape_result("list_applications", _big_page(items))
        assert "schema_warning" not in shaped


# ---------------------------------------------------------------------------
# Write bodies
# ---------------------------------------------------------------------------

class TestWriteBodies:
    """The v3 write shapes, pinned.

    Each of these was wrong in the first migration pass and was corrected only
    after Greenhouse's own validation errors named the right field. They are
    asserted exactly because the cost of relearning them was creating two
    undeletable notes in a production ATS.
    """

    @respx.mock
    async def test_note_sends_uppercase_note_type(self):
        from greenhouse_mcp.harvest.candidates import add_note_to_candidate

        respx.post(HARVEST_TOKEN_URL).mock(return_value=_token())
        route = respx.post(f"{HARVEST_BASE}/notes").mock(
            return_value=httpx.Response(201, json={"id": 1})
        )
        await add_note_to_candidate(_client(), candidate_id=42, body="hello")

        sent = json.loads(route.calls[0].request.content)
        # Upper-case, despite the API's error advertising lower-case values.
        assert sent["note_type"] == "NOTE"
        assert sent["candidate_id"] == 42
        assert sent["visibility"] == "private"

    @respx.mock
    async def test_tag_sends_candidate_tag_id(self):
        from greenhouse_mcp.harvest.tags import add_tag_to_candidate

        respx.post(HARVEST_TOKEN_URL).mock(return_value=_token())
        route = respx.post(f"{HARVEST_BASE}/applied_candidate_tags").mock(
            return_value=httpx.Response(201, json={"id": 1})
        )
        await add_tag_to_candidate(_client(), candidate_id=42, tag_id=7)

        sent = json.loads(route.calls[0].request.content)
        assert sent == {"candidate_id": 42, "candidate_tag_id": 7}
        assert "tag" not in sent  # rejected as a disallowed additional property

    @respx.mock
    async def test_bulk_tag_resolves_name_to_id(self):
        from greenhouse_mcp.harvest.batch import bulk_tag

        respx.post(HARVEST_TOKEN_URL).mock(return_value=_token())
        respx.get(f"{HARVEST_BASE}/candidate_tags").mock(
            return_value=httpx.Response(200, json=[{"id": 9, "name": "Referred"}])
        )
        route = respx.post(f"{HARVEST_BASE}/applied_candidate_tags").mock(
            return_value=httpx.Response(201, json={"id": 1})
        )
        # Case-insensitive: recruiters do not type the tag exactly as stored.
        result = await bulk_tag(_client(), candidate_ids=[42], tag_name="referred")

        assert result["succeeded"] == 1
        assert json.loads(route.calls[0].request.content)["candidate_tag_id"] == 9

    @respx.mock
    async def test_bulk_tag_unknown_name_is_an_error_not_a_new_tag(self):
        """v1 created missing tags implicitly; v3 cannot, so say so."""
        from greenhouse_mcp.harvest.batch import bulk_tag

        respx.post(HARVEST_TOKEN_URL).mock(return_value=_token())
        respx.get(f"{HARVEST_BASE}/candidate_tags").mock(
            return_value=httpx.Response(200, json=[{"id": 9, "name": "Referred"}])
        )
        created = respx.post(f"{HARVEST_BASE}/applied_candidate_tags").mock(
            return_value=httpx.Response(201, json={"id": 1})
        )
        result = await bulk_tag(_client(), candidate_ids=[42], tag_name="Nonexistent")

        assert result["status_code"] == 404
        assert "Nonexistent" in result["error"]
        assert not created.called, "must not apply anything when the tag is unknown"
