"""The shipped skills name MCP tools in prose; those tools must actually exist.

A skill instructs Claude to call tools by name. If a tool is renamed, or dropped
from the profile the bundle pins, the skill keeps citing it and fails at the point a
recruiter tries to use it — with no error anywhere in this repository. These tests
turn that into a failed build.

Tool names are discovered by matching the registered tool set against each skill's
text rather than from a hardcoded list, so a newly cited tool is covered
automatically.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from unittest.mock import patch

import pytest

from greenhouse_mcp.server import _ASSISTANT_TOOLS, create_server

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"


def _tools_for(profile: str) -> set[str]:
    excluded = ("GREENHOUSE_API_KEY", "GREENHOUSE_BOARD_TOKEN", "GREENHOUSE_TOOL_PROFILE")
    env = {k: v for k, v in os.environ.items() if k not in excluded}
    env["GREENHOUSE_API_KEY"] = "test-key"
    env["GREENHOUSE_TOOL_PROFILE"] = profile
    with patch.dict(os.environ, env, clear=True):
        return set(create_server()._tool_manager._tools)


def _cited_tools(text: str, known: set[str]) -> set[str]:
    return {tool for tool in known if re.search(rf"\b{re.escape(tool)}\b", text)}


def _skill_files() -> list[Path]:
    return sorted(SKILLS_DIR.glob("*/SKILL.md"))


def test_skills_directory_is_present():
    """Guards against the skills being dropped: the rest of this file would pass
    vacuously if the glob returned nothing."""
    assert SKILLS_DIR.is_dir(), f"{SKILLS_DIR} is missing"
    assert _skill_files(), "no SKILL.md files found — the other assertions would be vacuous"


@pytest.mark.parametrize("skill", _skill_files(), ids=lambda p: p.parent.name)
class TestSkill:
    def test_has_name_and_description_frontmatter(self, skill: Path):
        text = skill.read_text()
        assert text.startswith("---\n"), "missing YAML frontmatter"
        frontmatter = text.split("---", 2)[1]
        assert re.search(r"^name:\s*\S+", frontmatter, re.M), "frontmatter has no name"
        assert re.search(r"^description:\s*\S+", frontmatter, re.M), "no description"

    def test_frontmatter_name_matches_directory(self, skill: Path):
        frontmatter = skill.read_text().split("---", 2)[1]
        declared = re.search(r"^name:\s*(\S+)", frontmatter, re.M).group(1)
        assert declared == skill.parent.name, (
            f"frontmatter name {declared!r} does not match directory "
            f"{skill.parent.name!r} — the skill would be installed under a name that "
            f"does not match its folder"
        )

    def test_cites_at_least_one_real_tool(self, skill: Path):
        """A skill that cites no known tool is either misnamed or has drifted from the
        server it is meant to drive."""
        cited = _cited_tools(skill.read_text(), _tools_for("full"))
        assert cited, f"{skill.parent.name} names no tool this server registers"

    def test_every_cited_tool_exists_in_the_pinned_profile(self, skill: Path):
        """The bundle pins the assistant profile, so a tool outside it is unreachable
        for the recruiters these skills are written for."""
        cited = _cited_tools(skill.read_text(), _tools_for("full"))
        unreachable = sorted(cited - _tools_for("assistant"))
        assert not unreachable, (
            f"{skill.parent.name} cites {unreachable}, which the assistant profile "
            f"does not register — either add them to _ASSISTANT_TOOLS or stop citing them"
        )


def test_no_skill_cites_a_destructive_tool():
    """These skills are used by non-technical recruiters; none should be steering
    Claude toward an irreversible operation."""
    every = _tools_for("full")
    for skill in _skill_files():
        cited = _cited_tools(skill.read_text(), every)
        destructive = sorted(
            t for t in cited if t.startswith(("delete_", "remove_", "anonymize_", "merge_"))
        )
        assert not destructive, f"{skill.parent.name} cites destructive tools: {destructive}"


def test_curated_profile_covers_every_tool_the_skills_need():
    """The union across all skills, asserted against the profile the bundle ships."""
    every = _tools_for("full")
    cited: set[str] = set()
    for skill in _skill_files():
        cited |= _cited_tools(skill.read_text(), every)
    assert cited, "no tools cited across any skill"
    assert cited <= _ASSISTANT_TOOLS, sorted(cited - _ASSISTANT_TOOLS)
