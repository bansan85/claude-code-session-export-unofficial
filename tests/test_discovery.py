from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import FakeClaudeHome

from claude_code_session_export_unofficial.discovery import (
    collect_plan_paths,
    find_project_dirs,
    load_jsonl,
    normalize_path_for_compare,
    path_to_project_slug,
    resolve_claude_dir,
    scan_jsonl_for_cwd_match,
)


def test_resolve_claude_dir_accepts_home_directory(fake_claude_home: FakeClaudeHome) -> None:
    resolved = resolve_claude_dir(fake_claude_home.home)
    assert resolved == fake_claude_home.claude_dir.resolve()


def test_resolve_claude_dir_accepts_dot_claude_directly(fake_claude_home: FakeClaudeHome) -> None:
    resolved = resolve_claude_dir(fake_claude_home.claude_dir)
    assert resolved == fake_claude_home.claude_dir.resolve()


def test_resolve_claude_dir_raises_when_no_projects_dir(tmp_path: Path) -> None:
    empty_home = tmp_path / "nobody"
    empty_home.mkdir()
    with pytest.raises(SystemExit):
        resolve_claude_dir(empty_home)


def test_path_to_project_slug_replaces_separators() -> None:
    assert path_to_project_slug("d:\\repos\\project") == "d--repos-project"
    assert path_to_project_slug("/home/user/project") == "-home-user-project"


def test_normalize_path_for_compare() -> None:
    assert normalize_path_for_compare("D:\\Repos\\Project\\") == "d:/repos/project"
    assert normalize_path_for_compare("/home/User/Project/") == "/home/user/project"


def test_find_project_dirs_direct_slug_match(fake_claude_home: FakeClaudeHome) -> None:
    dirs = find_project_dirs(fake_claude_home.claude_dir, fake_claude_home.workspace, False)
    assert len(dirs) == 1
    assert dirs[0] == fake_claude_home.jsonl_file.parent


def test_find_project_dirs_fallback_by_cwd_scan(fake_claude_home: FakeClaudeHome) -> None:
    project_dir = fake_claude_home.jsonl_file.parent
    renamed_dir = project_dir.parent / "unrelated-folder-name"
    project_dir.rename(renamed_dir)

    dirs = find_project_dirs(fake_claude_home.claude_dir, fake_claude_home.workspace, False)
    assert dirs == [renamed_dir]


def test_scan_jsonl_for_cwd_match(tmp_path: Path) -> None:
    jsonl_file = tmp_path / "session.jsonl"
    jsonl_file.write_text(
        "\n".join(
            [
                "not json {{{",
                json.dumps({"cwd": "/some/other/path"}),
                json.dumps({"cwd": "/home/user/project"}),
            ]
        ),
        encoding="utf-8",
    )
    assert scan_jsonl_for_cwd_match(jsonl_file, "/home/user/project") is True
    assert scan_jsonl_for_cwd_match(jsonl_file, "/nonexistent") is False


def test_load_jsonl_skips_blank_and_malformed_lines(tmp_path: Path) -> None:
    jsonl_file = tmp_path / "session.jsonl"
    jsonl_file.write_text(
        "\n".join(
            [
                json.dumps({"type": "a"}),
                "",
                "not json",
                json.dumps({"type": "b"}),
            ]
        ),
        encoding="utf-8",
    )
    events = load_jsonl(jsonl_file)
    assert [e["type"] for e in events] == ["a", "b"]


def test_collect_plan_paths_filters_by_parent_and_existence(
    fake_claude_home: FakeClaudeHome,
) -> None:
    events = load_jsonl(fake_claude_home.jsonl_file)
    other_delta = {
        "type": "file-history-delta",
        "trackingPath": str(fake_claude_home.claude_dir / "somewhere-else" / "not-a-plan.md"),
    }
    plan_paths = collect_plan_paths([*events, other_delta], fake_claude_home.claude_dir)
    assert plan_paths == [fake_claude_home.plan_path.resolve()]
