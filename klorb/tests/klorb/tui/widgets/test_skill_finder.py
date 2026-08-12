# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tui.widgets.skill_finder."""

from klorb.tui.widgets.skill_finder import (
    SkillMatch,
    SkillQuery,
    detect_skill_query,
    filter_skills,
)


class TestDetectSkillQuery:
    def test_finds_query_at_start_of_line(self) -> None:
        line = "/code-review"
        assert detect_skill_query(line, len(line)) == SkillQuery(
            start_column=0, query="code-review")

    def test_finds_query_after_whitespace(self) -> None:
        line = "help /skill"
        assert detect_skill_query(line, len(line)) == SkillQuery(
            start_column=5, query="skill")

    def test_query_is_empty_right_after_the_slash(self) -> None:
        line = "hello /"
        assert detect_skill_query(line, len(line)) == SkillQuery(
            start_column=6, query="")

    def test_no_query_without_a_slash(self) -> None:
        line = "just plain text"
        assert detect_skill_query(line, len(line)) is None

    def test_whitespace_before_cursor_ends_the_query(self) -> None:
        line = "/foo bar"
        assert detect_skill_query(line, len(line)) is None

    def test_slash_in_url_does_not_trigger(self) -> None:
        line = "https://example.com/path"
        assert detect_skill_query(line, len(line)) is None

    def test_slash_mid_word_does_not_trigger(self) -> None:
        line = "path/to/file"
        assert detect_skill_query(line, len(line)) is None

    def test_cursor_before_the_slash_finds_nothing(self) -> None:
        assert detect_skill_query("/foo", 0) is None

    def test_slash_at_start_of_line_triggers(self) -> None:
        line = "/skill"
        assert detect_skill_query(line, len(line)) == SkillQuery(
            start_column=0, query="skill")

    def test_slash_after_tab_triggers(self) -> None:
        line = "\t/skill"
        assert detect_skill_query(line, len(line)) == SkillQuery(
            start_column=1, query="skill")


SKILLS = [
    SkillMatch(name="code-review", namespace="internal", description="Review code changes"),
    SkillMatch(name="write-plan", namespace="internal", description="Write an implementation plan"),
    SkillMatch(name="add-cli-flag", namespace="workspace", description="Add a new CLI flag"),
    SkillMatch(name="debug-with-evidence", namespace="internal", description="Debug with evidence"),
]


class TestFilterSkills:
    def test_empty_query_returns_all_skills(self) -> None:
        result = filter_skills(SKILLS, "")
        assert result == SKILLS

    def test_empty_query_respects_limit(self) -> None:
        result = filter_skills(SKILLS, "", limit=2)
        assert len(result) == 2

    def test_fuzzy_match_on_name(self) -> None:
        result = filter_skills(SKILLS, "review")
        assert len(result) >= 1
        assert result[0].name == "code-review"

    def test_fuzzy_match_on_description(self) -> None:
        result = filter_skills(SKILLS, "implementation")
        assert len(result) >= 1
        assert result[0].name == "write-plan"

    def test_no_match_returns_empty(self) -> None:
        result = filter_skills(SKILLS, "zzzzzzzz")
        assert result == []

    def test_respects_limit(self) -> None:
        result = filter_skills(SKILLS, "e", limit=1)
        assert len(result) == 1

    def test_exact_name_beats_fuzzy_description(self) -> None:
        """An exact name match should rank above a fuzzy description match."""
        skills = [
            SkillMatch(name="alpha", namespace="internal", description="review code"),
            SkillMatch(name="review", namespace="internal", description="alpha task"),
        ]
        result = filter_skills(skills, "review")
        assert result[0].name == "review"
