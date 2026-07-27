from __future__ import annotations

from pathlib import Path

import pytest
from conftest import FakeClaudeHome

from claude_code_session_export_unofficial.anonymize import Anonymizer
from claude_code_session_export_unofficial.cli import main
from claude_code_session_export_unofficial.export import (
    copy_tree_if_exists,
    export_session,
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

    session_out = output_root / fake_claude_home.session_id
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

    assert (output_root / fake_claude_home.session_id / "session.md").is_file()
    out = capsys.readouterr().out
    assert "Export complete" in out
