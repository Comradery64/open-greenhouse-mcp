"""Tests for result-size shaping."""
from __future__ import annotations

import json

import pytest

from greenhouse_mcp import shaping
from greenhouse_mcp.errors import build_error


@pytest.fixture(autouse=True)
def _isolate_diagnostics(tmp_path, monkeypatch):
    monkeypatch.setenv("GREENHOUSE_DIAGNOSTICS_FILE", str(tmp_path / "diag.jsonl"))


def _job(i: int, bloat: int = 1) -> dict:
    return {
        "id": 4000 + i,
        "name": f"Senior Engineer {i}",
        "requisition_id": f"REQ-{i}",
        "status": "open",
        "confidential": False,
        "created_at": "2026-01-05T00:00:00Z",
        # Harvest v3 shape: scalar department_id, office_ids[] — not the v1
        # departments[]/offices[] objects.
        "department_id": 1,
        "office_ids": [2],
        "opened_at": "2026-01-06T00:00:00Z",
        "closed_at": None,
        "hiring_team": [{"id": 9}],
        "openings": [{"id": j, "custom_fields": {"a": "x" * 80}} for j in range(4)],
        "custom_fields": {"notes": "y" * (600 * bloat)},
        "notes": "n" * (300 * bloat),
    }


def _page(items, **extra):
    return {"items": items, "has_next": True, "next_page": "p2", **extra}


class TestBudget:
    def test_default_is_used_when_unset(self, monkeypatch):
        monkeypatch.delenv("GREENHOUSE_MAX_RESULT_BYTES", raising=False)
        assert shaping.max_result_bytes() == shaping.DEFAULT_MAX_RESULT_BYTES

    @pytest.mark.parametrize("bad", ["", "abc", "0", "-5"])
    def test_invalid_override_falls_back_to_default(self, monkeypatch, bad):
        monkeypatch.setenv("GREENHOUSE_MAX_RESULT_BYTES", bad)
        assert shaping.max_result_bytes() == shaping.DEFAULT_MAX_RESULT_BYTES

    def test_valid_override_is_honoured(self, monkeypatch):
        monkeypatch.setenv("GREENHOUSE_MAX_RESULT_BYTES", "12345")
        assert shaping.max_result_bytes() == 12345


class TestPassthrough:
    def test_result_within_budget_is_returned_unchanged(self):
        payload = _page([_job(0)])
        assert shaping.shape_result("list_jobs", payload) is payload

    def test_errors_are_never_shaped(self):
        err = build_error(403, {"message": "x" * 200_000}, "/jobs")
        assert shaping.shape_result("list_jobs", err) is err

    def test_non_dict_results_survive(self):
        assert shaping.shape_result("list_jobs", ["a", "b"]) == ["a", "b"]
        assert shaping.shape_result("list_jobs", None) is None


class TestStaysWithinBudget:
    @pytest.mark.parametrize("n,bloat", [(60, 1), (500, 1), (500, 6), (4000, 1)])
    def test_oversized_pages_are_brought_under_budget(self, n, bloat):
        shaped = shaping.shape_result("list_jobs", _page([_job(i, bloat) for i in range(n)]))
        assert shaping._sizeof(shaped) <= shaping.max_result_bytes()

    def test_unknown_tool_without_a_projection_still_fits(self):
        items = [{"id": i, "blob": "k" * 3000} for i in range(400)]
        shaped = shaping.shape_result("some_new_tool", _page(items))
        assert shaping._sizeof(shaped) <= shaping.max_result_bytes()

    def test_text_heavy_result_without_an_item_list_still_fits(self):
        payload = {"candidate": {"id": 1}, "resume": {"content": "R" * 400_000}}
        shaped = shaping.shape_result("screen_candidate", payload)
        assert shaping._sizeof(shaped) <= shaping.max_result_bytes()

    def test_measurement_matches_indented_serialisation(self):
        """FastMCP renders dict results with indent=2; measuring compactly would
        let results land far over budget at the client."""
        shaped = shaping.shape_result("list_jobs", _page([_job(i) for i in range(500)]))
        as_client_sees_it = json.dumps(shaped, default=str, indent=2)
        assert len(as_client_sees_it.encode()) <= shaping.max_result_bytes()

    def test_never_shrinks_below_the_row_floor(self):
        huge = [{"id": i, "blob": "z" * 200_000} for i in range(50)]
        shaped = shaping.shape_result("some_new_tool", _page(huge))
        assert len(shaped["items"]) >= shaping._MIN_ITEMS


class TestProjection:
    def test_bulky_subfields_become_counts(self):
        shaped = shaping.shape_result("list_jobs", _page([_job(i) for i in range(60)]))
        item = shaped["items"][0]
        assert item["openings_count"] == 4
        assert "openings" not in item
        assert item["department_id"] == 1
        assert item["office_ids"] == [2]

    def test_identifying_fields_survive(self):
        shaped = shaping.shape_result("list_jobs", _page([_job(i) for i in range(60)]))
        for key in ("id", "name", "status", "requisition_id"):
            assert key in shaped["items"][0]

    def test_counts_and_totals_are_reported(self):
        shaped = shaping.shape_result("list_jobs", _page([_job(i) for i in range(500)]))
        assert shaped["returned"] == len(shaped["items"])
        assert shaped["total_found"] == 500
        assert shaped["returned"] < shaped["total_found"]


class TestNote:
    def test_truncated_result_tells_the_model_how_to_get_the_rest(self):
        shaped = shaping.shape_result("list_jobs", _page([_job(i) for i in range(500)]))
        note = shaped["result_note"]
        # v3 pages by opaque cursor; telling the model to increment `page` would
        # send it down a path Greenhouse rejects.
        assert "next_cursor" in note
        assert "department" in note

    def test_note_forbids_mentioning_flags_to_the_user(self):
        shaped = shaping.shape_result("list_jobs", _page([_job(i) for i in range(500)]))
        assert "not tell the user to use flags" in shaped["result_note"]

    def test_projection_only_result_still_explains_itself(self):
        shaped = shaping.shape_result("list_jobs", _page([_job(i) for i in range(60)]))
        assert "trimmed" in shaped["result_note"]


class TestDiagnostics:
    def test_shaping_is_recorded_with_real_byte_counts(self, tmp_path):
        shaping.shape_result("list_jobs", _page([_job(i) for i in range(500)]))
        entries = [
            json.loads(line)
            for line in (tmp_path / "diag.jsonl").read_text().strip().split("\n")
        ]
        # The synthetic job fixture omits some projected fields, so a
        # projection_mismatch may be recorded alongside the shaping entry.
        entry = next(e for e in entries if e["event"] == "result_shaped")
        assert entry["original_bytes"] > entry["shaped_bytes"]
        assert entry["tool"] == "list_jobs"

    def test_untouched_result_is_not_recorded(self, tmp_path):
        shaping.shape_result("list_jobs", _page([_job(0)]))
        assert not (tmp_path / "diag.jsonl").exists()
