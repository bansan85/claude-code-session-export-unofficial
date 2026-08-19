from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest
from conftest import FakeClaudeHome

from claude_code_session_export_unofficial.anonymize import Anonymizer
from claude_code_session_export_unofficial.cli import main
from claude_code_session_export_unofficial.discovery import path_to_project_slug
from claude_code_session_export_unofficial.export import (
    build_session_dir_name,
    copy_tree_if_exists,
    export_session,
    sanitize_filename_component,
    session_preview,
    write_index,
)


def test_export_session_produces_expected_files_and_anonymizes_everywhere(
    fake_claude_home: FakeClaudeHome, tmp_path: Path
) -> None:
    output_root = tmp_path / "out"
    output_root.mkdir()
    anonymizer = Anonymizer(workspace=fake_claude_home.workspace, username="testuser")

    summary = export_session(
        jsonl_file=fake_claude_home.jsonl_file,
        claude_dir=fake_claude_home.claude_dir,
        workspace=fake_claude_home.workspace,
        output_root=output_root,
        anonymizer=anonymizer,
        verbose=False,
    )

    session_dirs = [d for d in output_root.iterdir() if d.is_dir()]
    assert len(session_dirs) == 1
    session_out = session_dirs[0]
    assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}-\d{2}-\d{2} - Test session$", session_out.name)
    assert (session_out / "session.jsonl").is_file()
    assert (session_out / "session.md").is_file()
    assert (session_out / "plans" / fake_claude_home.plan_path.name).is_file()
    assert (session_out / "file-history" / "generated.txt").is_file()
    assert (session_out / "session-env" / "env.json").is_file()
    assert (session_out / "session-info.json").is_file()

    assert summary["session_id"] == fake_claude_home.session_id
    assert summary["title"] == "Test session"
    assert summary["plans"] == 1

    workspace_str = str(fake_claude_home.workspace.resolve())
    for produced in session_out.rglob("*"):
        if produced.is_file():
            text = produced.read_text(encoding="utf-8")
            assert workspace_str not in text
            assert "testuser" not in text


def test_build_session_dir_name_uses_date_and_title() -> None:
    name = build_session_dir_name("2026-01-01T10:00:02Z", "Test session", "session-abc123")
    assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}-\d{2}-\d{2} - Test session$", name)


def test_build_session_dir_name_falls_back_to_session_id_for_missing_title() -> None:
    name = build_session_dir_name("2026-01-01T10:00:02Z", None, "session-abc123")
    assert name.endswith(" - session-abc123")


def test_build_session_dir_name_falls_back_to_unknown_date_for_missing_timestamp() -> None:
    name = build_session_dir_name(None, "Test session", "session-abc123")
    assert name == "unknown-date - Test session"


def test_session_preview_matches_export_session_output(
    fake_claude_home: FakeClaudeHome,
) -> None:
    name, last_ts = session_preview(fake_claude_home.jsonl_file)
    assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}-\d{2}-\d{2} - Test session$", name)
    assert last_ts == "2026-01-01T10:00:02Z"


def test_sanitize_filename_component_replaces_invalid_chars_and_trims() -> None:
    assert sanitize_filename_component('a<b>c:d"e/f\\g|h?i*j') == "a_b_c_d_e_f_g_h_i_j"
    assert sanitize_filename_component("trailing dot. ") == "trailing dot"
    assert sanitize_filename_component("   ") == "untitled"
    assert sanitize_filename_component("x" * 200) == "x" * 150


def test_write_index_creates_anonymized_summary_table(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    anonymizer = Anonymizer(workspace=workspace, username="dave")
    summaries = [{"session_id": "s1", "title": "First", "plans": 2}]

    write_index(tmp_path, workspace, summaries, anonymizer)

    index_text = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert "# Claude Code Session Export" in index_text
    assert "| `s1` | First | 2 |" in index_text
    # The placeholder sits inside an inline code span (`` `<workspace>` ``),
    # so escape_html_outside_code() correctly leaves it un-escaped there.
    assert "`<workspace>`" in index_text


def test_copy_tree_if_exists_missing_source_returns_false(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    anonymizer = Anonymizer(workspace=workspace, username="erin")

    missing_src = tmp_path / "does-not-exist"
    assert copy_tree_if_exists(missing_src, tmp_path / "dst1", False, anonymizer) is False
    assert not (tmp_path / "dst1").exists()


def test_copy_tree_if_exists_copies_and_anonymizes_nested_files(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    anonymizer = Anonymizer(workspace=workspace, username="erin")

    src = tmp_path / "src"
    (src / "nested").mkdir(parents=True)
    (src / "nested" / "file.txt").write_text("erin was here", encoding="utf-8")
    dst = tmp_path / "dst2"

    assert copy_tree_if_exists(src, dst, False, anonymizer) is True
    copied = (dst / "nested" / "file.txt").read_text(encoding="utf-8")
    assert "<username>" in copied
    assert "erin" not in copied


def test_cli_main_end_to_end_smoke(
    fake_claude_home: FakeClaudeHome,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output_root = tmp_path / "cli-out"
    argv = [
        "prog",
        str(fake_claude_home.home),
        str(fake_claude_home.workspace),
        "-o",
        str(output_root),
    ]
    monkeypatch.setattr("sys.argv", argv)

    main()

    session_dirs = [d for d in output_root.iterdir() if d.is_dir()]
    assert len(session_dirs) == 1
    assert (session_dirs[0] / "session.md").is_file()
    out = capsys.readouterr().out
    assert f"  - {fake_claude_home.session_id} -> {session_dirs[0].name}" in out
    assert "Export complete" in out


def test_cli_main_succeeds_when_workspace_directory_was_deleted(
    fake_claude_home: FakeClaudeHome,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace_str = str(fake_claude_home.workspace.resolve())
    shutil.rmtree(fake_claude_home.workspace)

    output_root = tmp_path / "cli-out"
    argv = [
        "prog",
        str(fake_claude_home.home),
        workspace_str,
        "-o",
        str(output_root),
    ]
    monkeypatch.setattr("sys.argv", argv)

    main()

    session_dirs = [d for d in output_root.iterdir() if d.is_dir()]
    assert len(session_dirs) == 1
    session_md = (session_dirs[0] / "session.md").read_text(encoding="utf-8")
    assert workspace_str not in session_md
    out = capsys.readouterr().out
    assert "Export complete" in out


def test_cli_lists_sessions_sorted_by_date_not_by_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "testuser"
    claude_dir = home / ".claude"
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    workspace_resolved = str(workspace.resolve())

    project_dir = claude_dir / "projects" / path_to_project_slug(workspace_resolved)
    project_dir.mkdir(parents=True)

    def write_session(session_id: str, timestamp: str, title: str) -> None:
        events = [
            {
                "type": "ai-title",
                "aiTitle": title,
                "cwd": workspace_resolved,
                "timestamp": timestamp,
            },
        ]
        (project_dir / f"{session_id}.jsonl").write_text(
            "\n".join(json.dumps(e) for e in events), encoding="utf-8"
        )

    # IDs are alphabetically in the opposite order of their dates, so a
    # correct date-based sort must reverse the alphabetical order.
    write_session("session-a-recent", "2026-06-01T10:00:00Z", "Recent session")
    write_session("session-z-old", "2026-01-01T10:00:00Z", "Old session")

    argv = ["prog", str(home), str(workspace), "-o", str(tmp_path / "cli-out")]
    monkeypatch.setattr("sys.argv", argv)

    main()

    out = capsys.readouterr().out
    assert out.index("session-z-old") < out.index("session-a-recent")
