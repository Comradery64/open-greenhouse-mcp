"""Tests for user-relayable error payloads."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from greenhouse_mcp import errors


@pytest.fixture(autouse=True)
def _isolate_diagnostics(tmp_path, monkeypatch):
    """Keep these tests from writing to the real diagnostics file."""
    monkeypatch.setenv("GREENHOUSE_DIAGNOSTICS_FILE", str(tmp_path / "diag.jsonl"))


class TestEndpointMasking:
    @pytest.mark.parametrize(
        "url,expected",
        [
            ("https://harvest.greenhouse.io/v3/jobs", "/jobs"),
            ("https://harvest.greenhouse.io/v3/jobs/12345", "/jobs/{id}"),
            ("https://harvest.greenhouse.io/v3/jobs?per_page=500&page=2", "/jobs"),
            ("https://api.greenhouse.io/v1/partner/candidates", "/candidates"),
            ("https://harvest.greenhouse.io/v3/applications/1/offers/2",
             "/applications/{id}/offers/{id}"),
            (None, "unknown"),
        ],
    )
    def test_masks_ids_and_drops_query(self, url, expected):
        assert errors._endpoint_of(url) == expected

    def test_query_string_is_dropped_so_signed_urls_do_not_leak(self):
        signed = "https://s3.example.com/resume.pdf?X-Amz-Signature=deadbeefsecret"
        assert "deadbeef" not in errors._endpoint_of(signed)


class TestSupportCode:
    def test_shape(self):
        when = datetime(2026, 7, 30, 14, 21, tzinfo=timezone.utc)
        assert errors.support_code(403, "/jobs", when) .startswith("GH403-0730-1421-")

    def test_same_failure_yields_same_suffix(self):
        a = errors.support_code(403, "/jobs", datetime(2026, 1, 1, tzinfo=timezone.utc))
        b = errors.support_code(403, "/jobs", datetime(2026, 6, 6, tzinfo=timezone.utc))
        assert a.split("-")[-1] == b.split("-")[-1]

    def test_different_endpoints_differ(self):
        when = datetime(2026, 7, 30, tzinfo=timezone.utc)
        assert errors.support_code(403, "/jobs", when) != errors.support_code(
            403, "/candidates", when
        )


class TestBuildError:
    @pytest.mark.parametrize(
        "status,fixable",
        [(400, False), (401, False), (403, False), (404, True), (409, True),
         (422, True), (429, True), (500, True), (503, True)],
    )
    def test_user_can_resolve_is_set_per_status(self, status, fixable):
        assert errors.build_error(status, url="/jobs")["user_can_resolve"] is fixable

    def test_unfixable_errors_tell_the_user_to_escalate(self):
        payload = errors.build_error(403, url="/jobs")
        assert "support code" in payload["user_message"].lower()

    def test_carries_the_keys_the_model_needs_to_relay(self):
        payload = errors.build_error(500, {"m": "boom"}, "/jobs")
        for key in ("error", "status_code", "user_message", "support_code",
                    "occurred_at_utc", "greenhouse_endpoint", "user_can_resolve",
                    "action_for_claude", "technical_detail"):
            assert key in payload

    def test_error_and_user_message_agree(self):
        payload = errors.build_error(429, url="/jobs")
        assert payload["error"] == payload["user_message"]

    def test_empty_detail_is_omitted(self):
        assert "technical_detail" not in errors.build_error(500, None, "/jobs")
        assert "technical_detail" not in errors.build_error(500, {}, "/jobs")

    def test_unknown_status_still_produces_a_payload(self):
        payload = errors.build_error(418, url="/jobs")
        assert payload["status_code"] == 418
        assert payload["support_code"].startswith("GH418-")

    def test_writes_a_diagnostics_record(self, tmp_path):
        payload = errors.build_error(403, {"m": "no"}, "/jobs")
        written = (tmp_path / "diag.jsonl").read_text()
        assert payload["support_code"] in written


class TestConfigAndInternalErrors:
    def test_config_error_does_not_blame_the_network(self):
        payload = errors.config_error("no key", "/not-configured")
        assert "client ID" in payload["user_message"]
        assert "network" not in payload["user_message"].lower()

    def test_internal_error_absolves_the_user(self):
        payload = errors.internal_error("RuntimeError: boom", "/list_jobs")
        assert "not a mistake you made" in payload["user_message"]

    def test_both_are_flagged_unfixable_by_the_user(self):
        assert errors.config_error("x")["user_can_resolve"] is False
        assert errors.internal_error("x")["user_can_resolve"] is False
