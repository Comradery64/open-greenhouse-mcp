"""Tests for the always-on diagnostics file."""
from __future__ import annotations

import json

from greenhouse_mcp import diagnostics


class TestDiagnosticsPath:
    def test_env_override_is_honoured(self, tmp_path, monkeypatch):
        target = tmp_path / "diag.jsonl"
        monkeypatch.setenv("GREENHOUSE_DIAGNOSTICS_FILE", str(target))
        assert diagnostics.diagnostics_path() == target

    def test_can_be_disabled(self, monkeypatch):
        monkeypatch.setenv("GREENHOUSE_DIAGNOSTICS", "off")
        assert diagnostics.diagnostics_path() is None

    def test_disabled_write_is_a_noop(self, tmp_path, monkeypatch):
        target = tmp_path / "diag.jsonl"
        monkeypatch.setenv("GREENHOUSE_DIAGNOSTICS_FILE", str(target))
        monkeypatch.setenv("GREENHOUSE_DIAGNOSTICS", "off")
        diagnostics.record("api_error", status=500)
        assert not target.exists()


class TestRecord:
    def test_appends_one_json_line_per_record(self, tmp_path, monkeypatch):
        target = tmp_path / "diag.jsonl"
        monkeypatch.setenv("GREENHOUSE_DIAGNOSTICS_FILE", str(target))
        diagnostics.record("api_error", status=403, endpoint="/jobs")
        diagnostics.record("result_shaped", tool="list_jobs", original_bytes=10)

        lines = target.read_text().strip().split("\n")
        assert len(lines) == 2
        first = json.loads(lines[0])
        assert first["event"] == "api_error"
        assert first["status"] == 403
        assert first["ts"].endswith("Z")

    def test_creates_parent_directory(self, tmp_path, monkeypatch):
        target = tmp_path / "nested" / "deeper" / "diag.jsonl"
        monkeypatch.setenv("GREENHOUSE_DIAGNOSTICS_FILE", str(target))
        diagnostics.record("api_error", status=500)
        assert target.exists()

    def test_none_values_are_dropped(self, tmp_path, monkeypatch):
        target = tmp_path / "diag.jsonl"
        monkeypatch.setenv("GREENHOUSE_DIAGNOSTICS_FILE", str(target))
        diagnostics.record("api_error", status=500, detail=None)
        assert "detail" not in json.loads(target.read_text())

    def test_long_detail_is_clipped(self, tmp_path, monkeypatch):
        target = tmp_path / "diag.jsonl"
        monkeypatch.setenv("GREENHOUSE_DIAGNOSTICS_FILE", str(target))
        diagnostics.record("api_error", detail="x" * 50_000)
        entry = json.loads(target.read_text())
        assert len(entry["detail"]) < 3_000
        assert "clipped" in entry["detail"]

    def test_rotates_past_the_size_cap(self, tmp_path, monkeypatch):
        target = tmp_path / "diag.jsonl"
        monkeypatch.setenv("GREENHOUSE_DIAGNOSTICS_FILE", str(target))
        target.write_text("x" * (diagnostics._MAX_BYTES + 1))
        diagnostics.record("api_error", status=500)
        assert target.with_suffix(".jsonl.1").exists()
        assert len(target.read_text().strip().split("\n")) == 1

    def test_unwritable_path_does_not_raise(self, monkeypatch):
        monkeypatch.setenv("GREENHOUSE_DIAGNOSTICS_FILE", "/proc/nope/diag.jsonl")
        diagnostics.record("api_error", status=500)  # must not raise
