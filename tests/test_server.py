import os
from unittest.mock import patch

import pytest

from greenhouse_mcp.server import create_server, get_client

_CRED_VARS = (
    "GREENHOUSE_API_KEY",
    "GREENHOUSE_BOARD_TOKEN",
    "GREENHOUSE_CLIENT_ID",
    "GREENHOUSE_CLIENT_SECRET",
)


class TestGetClient:
    def setup_method(self):
        """Reset client singleton between tests."""
        import greenhouse_mcp.server

        greenhouse_mcp.server._client = None

    @patch.dict(
        os.environ,
        {"GREENHOUSE_CLIENT_ID": "cid", "GREENHOUSE_CLIENT_SECRET": "secret"},
        clear=False,
    )
    def test_client_credentials_create_client(self):
        client = get_client()
        assert client.client_id == "cid"

    def test_board_token_creates_client(self):
        excluded = _CRED_VARS
        env = {k: v for k, v in os.environ.items() if k not in excluded}
        env["GREENHOUSE_BOARD_TOKEN"] = "my-board"
        with patch.dict(os.environ, env, clear=True):
            client = get_client()
            assert client.board_token == "my-board"

    def test_no_credentials_raises(self):
        env = {k: v for k, v in os.environ.items() if k not in _CRED_VARS}
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValueError, match="GREENHOUSE_CLIENT_ID"):
                get_client()

    def test_v1_api_key_alone_is_not_enough_for_harvest(self):
        """A v1 key no longer buys Harvest access — the error must say so."""
        env = {k: v for k, v in os.environ.items() if k not in _CRED_VARS}
        env["GREENHOUSE_API_KEY"] = "old-v1-key"
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValueError, match="Harvest v1"):
                get_client()

    @patch.dict(
        os.environ,
        {
            "GREENHOUSE_CLIENT_ID": "cid",
            "GREENHOUSE_CLIENT_SECRET": "secret",
            "GREENHOUSE_ON_BEHALF_OF": "user@co.com",
        },
        clear=False,
    )
    def test_on_behalf_of_passed(self):
        client = get_client()
        assert client.on_behalf_of == "user@co.com"


class TestCreateServer:
    def test_returns_fastmcp_instance(self):
        server = create_server()
        assert server.name == "Greenhouse"

    @patch.dict(os.environ, {"GREENHOUSE_API_KEY": "test-key"}, clear=False)
    def test_registers_migrated_harvest_plus_non_harvest_tools(self, monkeypatch):
        # Asks for "full" explicitly: this asserts the whole inventory, and the
        # default profile is deliberately the curated set.
        monkeypatch.setenv("GREENHOUSE_TOOL_PROFILE", "full")
        monkeypatch.delenv("GREENHOUSE_ALLOW_UNMIGRATED_TOOLS", raising=False)
        server = create_server()
        tools = server._tool_manager._tools
        assert "list_candidates" in tools        # migrated Harvest tool
        assert "get_board" in tools              # Job Board, unaffected by v3
        assert "webhook_list_rules" in tools
        # Withheld: a Harvest tool not yet checked against the v3 guides.
        assert "list_offices" not in tools

    def test_no_credentials_still_registers_tools(self):
        """Tools are registered at startup for introspection even without credentials."""
        excluded = _CRED_VARS
        env = {k: v for k, v in os.environ.items() if k not in excluded}
        # Asks for "full" explicitly: this asserts the whole inventory, and the
        # default profile is deliberately the curated set.
        env["GREENHOUSE_TOOL_PROFILE"] = "full"
        with patch.dict(os.environ, env, clear=True):
            server = create_server()
            tools = list(server._tool_manager._tools.keys())
            # Tools register regardless of credentials (checked at invocation).
            assert "get_board" in tools
            assert "list_board_jobs" in tools
            assert "list_candidates" in tools
            assert "list_jobs" in tools
            assert "screen_candidate" in tools
            assert "fetch_new_applications" in tools
            assert "search_pipeline_candidates" in tools
            assert "scan_pipeline_resumes" in tools
            assert "webhook_list_rules" in tools
            # Not yet migrated to v3, so deliberately absent — a caller gets
            # "no such tool" rather than a plausible-looking wrong answer.
            assert "scan_all_candidates" not in tools
            assert "batch_read_resumes" not in tools


class TestUserCentricDescriptions:
    """Verify tool descriptions encode lookup chains for ID resolution."""

    def test_tool_count_stable(self):
        """Full inventory is 181 once the v3 gate is lifted."""
        excluded = _CRED_VARS + ("GREENHOUSE_TOOL_PROFILE",)
        env = {k: v for k, v in os.environ.items() if k not in excluded}
        env["GREENHOUSE_CLIENT_ID"] = "cid"
        env["GREENHOUSE_CLIENT_SECRET"] = "secret"
        env["GREENHOUSE_ALLOW_UNMIGRATED_TOOLS"] = "1"
        # Ask for "full" explicitly: these assertions are about the whole tool
        # inventory, and the default profile is deliberately not "full".
        env["GREENHOUSE_TOOL_PROFILE"] = "full"
        with patch.dict(os.environ, env, clear=True):
            server = create_server()
            tools = list(server._tool_manager._tools.keys())
            assert len(tools) == 181, (
                f"Expected 181, got {len(tools)}: check for phantom or missing tools"
            )

    def test_candidate_id_params_mention_search(self):
        """Tools with candidate_id should reference search_candidates_by_name."""
        excluded = ("GREENHOUSE_API_KEY", "GREENHOUSE_BOARD_TOKEN", "GREENHOUSE_TOOL_PROFILE")
        env = {k: v for k, v in os.environ.items() if k not in excluded}
        env["GREENHOUSE_API_KEY"] = "test-key"
        # Ask for "full" explicitly: these assertions are about the whole tool
        # inventory, and the default profile is deliberately not "full".
        env["GREENHOUSE_TOOL_PROFILE"] = "full"
        with patch.dict(os.environ, env, clear=True):
            server = create_server()
            tools = server._tool_manager._tools
            # Tools that ARE the search tools or don't need hints
            exempt = {
                "search_candidates_by_name",
                "search_candidates_by_email",
                "list_candidates",
                "screen_candidate",
                "fetch_new_applications",
                "scan_pipeline_resumes",
                "search_pipeline_candidates",
                "scan_all_candidates",
                "batch_read_resumes",
            }
            missing = []
            for name, tool in tools.items():
                if name in exempt:
                    continue
                schema = tool.parameters or {}
                props = schema.get("properties", {})
                if "candidate_id" not in props:
                    continue
                desc = (tool.description or "").lower()
                param_desc = (props["candidate_id"].get("description") or "").lower()
                combined = desc + " " + param_desc
                if "search_candidates_by_name" not in combined:
                    missing.append(name)
            assert not missing, f"Tools with candidate_id missing search hint: {missing}"

    def test_job_id_params_mention_list_jobs(self):
        """Tools with job_id should reference list_jobs."""
        excluded = ("GREENHOUSE_API_KEY", "GREENHOUSE_BOARD_TOKEN", "GREENHOUSE_TOOL_PROFILE")
        env = {k: v for k, v in os.environ.items() if k not in excluded}
        env["GREENHOUSE_API_KEY"] = "test-key"
        # Ask for "full" explicitly: these assertions are about the whole tool
        # inventory, and the default profile is deliberately not "full".
        env["GREENHOUSE_TOOL_PROFILE"] = "full"
        with patch.dict(os.environ, env, clear=True):
            server = create_server()
            tools = server._tool_manager._tools
            exempt = {
                "list_jobs",
                "list_board_jobs",
                "get_board_job",
                "retrieve_ingestion_jobs",
                "post_tracking_link",
                "submit_application",
                "post_candidate",
                "pipeline_metrics",
                "source_effectiveness",
                "time_to_hire",
                "pipeline_summary",
                "candidates_needing_action",
                "stale_applications",
                "fetch_new_applications",
                "search_pipeline_candidates",
                "scan_pipeline_resumes",
            }
            missing = []
            for name, tool in tools.items():
                if name in exempt:
                    continue
                schema = tool.parameters or {}
                props = schema.get("properties", {})
                if "job_id" not in props:
                    continue
                desc = (tool.description or "").lower()
                param_desc = (props["job_id"].get("description") or "").lower()
                combined = desc + " " + param_desc
                if "list_jobs" not in combined:
                    missing.append(name)
            assert not missing, f"Tools with job_id missing list_jobs hint: {missing}"

    def test_no_empty_docstrings(self):
        """Every registered tool must have a non-empty description."""
        excluded = ("GREENHOUSE_API_KEY", "GREENHOUSE_BOARD_TOKEN", "GREENHOUSE_TOOL_PROFILE")
        env = {k: v for k, v in os.environ.items() if k not in excluded}
        env["GREENHOUSE_API_KEY"] = "test-key"
        # Ask for "full" explicitly: these assertions are about the whole tool
        # inventory, and the default profile is deliberately not "full".
        env["GREENHOUSE_TOOL_PROFILE"] = "full"
        with patch.dict(os.environ, env, clear=True):
            server = create_server()
            tools = server._tool_manager._tools
            empty = [name for name, tool in tools.items() if not (tool.description or "").strip()]
            assert not empty, f"Tools with empty descriptions: {empty}"


# ---------------------------------------------------------------------------
# TestProfileFallback
# ---------------------------------------------------------------------------

class TestProfileFallback:
    """An operator who has chosen nothing must not get every destructive tool.

    Regression: an unset or unrecognised GREENHOUSE_TOOL_PROFILE selected "full".
    """

    def _profile_for(self, monkeypatch, value):
        import importlib

        from greenhouse_mcp import server as server_module

        if value is None:
            monkeypatch.delenv("GREENHOUSE_TOOL_PROFILE", raising=False)
        else:
            monkeypatch.setenv("GREENHOUSE_TOOL_PROFILE", value)
        monkeypatch.setenv("GREENHOUSE_API_KEY", "test-key")
        monkeypatch.delenv("GREENHOUSE_READ_ONLY", raising=False)
        monkeypatch.delenv("GREENHOUSE_USER_ID", raising=False)
        importlib.reload(server_module)
        return server_module

    def test_unset_falls_back_to_assistant(self, monkeypatch, capsys):
        self._profile_for(monkeypatch, None)
        assert "Profile: assistant" in capsys.readouterr().err

    def test_unrecognised_value_falls_back_to_assistant(self, monkeypatch, capsys):
        self._profile_for(monkeypatch, "nonsense")
        assert "Profile: assistant" in capsys.readouterr().err

    def test_unsubstituted_bundle_placeholder_falls_back(self, monkeypatch, capsys):
        self._profile_for(monkeypatch, "${user_config.tool_profile}")
        assert "Profile: assistant" in capsys.readouterr().err

    def test_explicit_recruiter_is_still_honoured(self, monkeypatch, capsys):
        self._profile_for(monkeypatch, "recruiter")
        assert "Profile: recruiter" in capsys.readouterr().err

    def test_assistant_registers_only_the_curated_set(self, monkeypatch):
        from greenhouse_mcp.server import _ASSISTANT_TOOLS, create_server

        excluded = ("GREENHOUSE_API_KEY", "GREENHOUSE_BOARD_TOKEN", "GREENHOUSE_TOOL_PROFILE")
        env = {k: v for k, v in os.environ.items() if k not in excluded}
        env["GREENHOUSE_API_KEY"] = "test-key"
        env["GREENHOUSE_TOOL_PROFILE"] = "assistant"
        with patch.dict(os.environ, env, clear=True):
            tools = set(create_server()._tool_manager._tools)
        # Every curated tool must exist, or the profile silently ships a short set.
        assert _ASSISTANT_TOOLS - tools == set()
        assert tools == _ASSISTANT_TOOLS

    def test_assistant_excludes_destructive_tools(self, monkeypatch):
        from greenhouse_mcp.server import _ASSISTANT_TOOLS

        for name in _ASSISTANT_TOOLS:
            assert not name.startswith(("delete_", "remove_", "anonymize_", "merge_"))

    def test_explicit_full_is_still_honoured(self, monkeypatch, capsys):
        self._profile_for(monkeypatch, "full")
        assert "Profile: full" in capsys.readouterr().err

    def test_explicit_read_only_is_still_honoured(self, monkeypatch, capsys):
        self._profile_for(monkeypatch, "read-only")
        assert "Profile: read-only" in capsys.readouterr().err
