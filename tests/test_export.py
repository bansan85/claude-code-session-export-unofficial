from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

import pytest
from conftest import FakeClaudeHome

from claude_code_session_export_unofficial.anonymize import Anonymizer
from claude_code_session_export_unofficial.cli import main
from claude_code_session_export_unofficial.discovery import path_to_project_slug
from claude_code_session_export_unofficial.export import (
    build_index_entries,
    build_session_dir_name,
    copy_tree_if_exists,
    export_session,
    find_existing_session_dir,
    parse_index,
    sanitize_filename_component,
    session_preview,
    write_index,
)
from claude_code_session_export_unofficial.markdown import fmt_ts


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
    assert re.match(
        r"^\d{4}-\d{2}-\d{2} \d{2}-\d{2}-\d{2} - Test session - session-abc123$",
        session_out.name,
    )
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
    assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}-\d{2}-\d{2} - Test session - session-abc123$", name)


def test_build_session_dir_name_always_includes_session_id() -> None:
    # The session ID must always be present (not just as a title fallback),
    # since it is the only stable way to re-find this folder on a later
    # re-import once the date and/or title have changed.
    name = build_session_dir_name("2026-01-01T10:00:02Z", None, "session-abc123")
    assert name.endswith(" - session-abc123")
    assert "untitled" in name


def test_build_session_dir_name_falls_back_to_unknown_date_for_missing_timestamp() -> None:
    name = build_session_dir_name(None, "Test session", "session-abc123")
    assert name == "unknown-date - Test session - session-abc123"


def test_session_preview_matches_export_session_output(
    fake_claude_home: FakeClaudeHome,
) -> None:
    name, last_ts = session_preview(fake_claude_home.jsonl_file)
    assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}-\d{2}-\d{2} - Test session - session-abc123$", name)
    assert last_ts == "2026-01-01T10:00:02Z"


def test_sanitize_filename_component_replaces_invalid_chars_and_trims() -> None:
    assert sanitize_filename_component('a<b>c:d"e/f\\g|h?i*j') == "a_b_c_d_e_f_g_h_i_j"
    assert sanitize_filename_component("trailing dot. ") == "trailing dot"
    assert sanitize_filename_component("   ") == "untitled"
    assert sanitize_filename_component("x" * 200) == "x" * 150


def test_write_index_creates_anonymized_checklist(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    anonymizer = Anonymizer(workspace=workspace, username="dave")
    entries = [
        {
            "session_id": "s1",
            "title": "First",
            "updated": "2026-01-01 10:00:00",
            "plans": 2,
            "checked": True,
        },
        {
            "session_id": "s2",
            "title": "Second",
            "updated": "-",
            "plans": 0,
            "checked": False,
        },
    ]

    write_index(tmp_path, workspace, entries, anonymizer)

    index_text = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert "# Claude Code Session Export" in index_text
    assert "- [x] `s1` — First — updated 2026-01-01 10:00:00 — 2 plan(s) copied" in index_text
    assert "- [ ] `s2` — Second — updated - — 0 plan(s) copied" in index_text
    # The placeholder sits inside an inline code span (`` `<workspace>` ``),
    # so escape_html_outside_code() correctly leaves it un-escaped there.
    assert "`<workspace>`" in index_text


def test_parse_index_reads_back_previously_written_rows(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    anonymizer = Anonymizer(workspace=workspace, username="dave")
    entries: list[dict[str, Any]] = [
        {
            "session_id": "s1",
            "title": "First",
            "updated": "2026-01-01 10:00:00",
            "plans": 2,
            "checked": True,
        },
        {
            "session_id": "s2",
            "title": None,
            "updated": "-",
            "plans": 0,
            "checked": False,
        },
    ]
    write_index(tmp_path, workspace, entries, anonymizer)

    parsed = parse_index(tmp_path)

    assert parsed["s1"] == {
        "session_id": "s1",
        "title": "First",
        "updated": "2026-01-01 10:00:00",
        "plans": 2,
        "checked": True,
    }
    assert parsed["s2"]["title"] is None
    assert parsed["s2"]["checked"] is False


def test_parse_index_returns_empty_dict_when_no_readme(tmp_path: Path) -> None:
    assert parse_index(tmp_path) == {}


def test_find_existing_session_dir_matches_by_trailing_session_id(tmp_path: Path) -> None:
    (tmp_path / "2026-01-01 10-00-00 - Old title - session-abc123").mkdir()

    found = find_existing_session_dir(tmp_path, "session-abc123")

    assert found is not None
    assert found.name == "2026-01-01 10-00-00 - Old title - session-abc123"
    assert find_existing_session_dir(tmp_path, "session-other") is None


def test_export_session_renames_existing_folder_when_title_and_date_change(
    fake_claude_home: FakeClaudeHome, tmp_path: Path
) -> None:
    # Simulates a re-import after the conversation continued (later
    # timestamp) and got a new AI-generated title: the previous export
    # folder must be refreshed in place, not duplicated.
    output_root = tmp_path / "out"
    output_root.mkdir()
    anonymizer = Anonymizer(workspace=fake_claude_home.workspace, username="testuser")
    old_dir = output_root / "2025-12-01 09-00-00 - Old title - session-abc123"
    old_dir.mkdir()
    (old_dir / "stale.txt").write_text("stale", encoding="utf-8")

    export_session(
        jsonl_file=fake_claude_home.jsonl_file,
        claude_dir=fake_claude_home.claude_dir,
        workspace=fake_claude_home.workspace,
        output_root=output_root,
        anonymizer=anonymizer,
        verbose=False,
    )

    session_dirs = [d for d in output_root.iterdir() if d.is_dir()]
    assert len(session_dirs) == 1
    assert session_dirs[0].name.endswith(" - session-abc123")
    assert session_dirs[0].name != old_dir.name
    assert (session_dirs[0] / "session.md").is_file()
    assert (session_dirs[0] / "stale.txt").is_file()


def test_build_index_entries_first_export_checks_every_session(
    fake_claude_home: FakeClaudeHome, tmp_path: Path
) -> None:
    output_root = tmp_path / "out"
    output_root.mkdir()
    summary = {
        "session_id": fake_claude_home.session_id,
        "title": "Test session",
        "last_ts": "2026-01-01T10:00:02Z",
        "plans": 1,
    }

    entries = build_index_entries(
        output_root, fake_claude_home.claude_dir, [fake_claude_home.jsonl_file], [summary]
    )

    assert len(entries) == 1
    assert entries[0]["checked"] is True
    assert entries[0]["session_id"] == fake_claude_home.session_id


def test_build_index_entries_unchecks_session_whose_folder_was_deleted(
    fake_claude_home: FakeClaudeHome, tmp_path: Path
) -> None:
    # The session is discovered on disk (still has a .jsonl under ~/.claude)
    # but is skipped this run (not in `summaries`), and its export folder no
    # longer exists: it must show up unchecked, not be dropped from the list.
    output_root = tmp_path / "out"
    output_root.mkdir()

    entries = build_index_entries(
        output_root, fake_claude_home.claude_dir, [fake_claude_home.jsonl_file], []
    )

    assert len(entries) == 1
    assert entries[0]["session_id"] == fake_claude_home.session_id
    assert entries[0]["checked"] is False


def test_build_index_entries_keeps_previously_known_session_no_longer_discovered(
    tmp_path: Path,
) -> None:
    # A session that was exported before but whose .jsonl has since been
    # removed from ~/.claude must still be listed, using the README as the
    # source of truth for its title/date, with its checkbox re-evaluated.
    output_root = tmp_path / "out"
    output_root.mkdir()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    anonymizer = Anonymizer(workspace=workspace, username="dave")
    write_index(
        output_root,
        workspace,
        [
            {
                "session_id": "gone-session",
                "title": "Gone session",
                "updated": "2025-06-01 08:00:00",
                "plans": 0,
                "checked": True,
            }
        ],
        anonymizer,
    )
    (output_root / "2025-06-01 08-00-00 - Gone session - gone-session").mkdir()

    entries = build_index_entries(output_root, tmp_path / ".claude", [], [])

    assert len(entries) == 1
    assert entries[0]["session_id"] == "gone-session"
    assert entries[0]["title"] == "Gone session"
    assert entries[0]["checked"] is True

    shutil.rmtree(output_root / "2025-06-01 08-00-00 - Gone session - gone-session")
    entries_after_deletion = build_index_entries(output_root, tmp_path / ".claude", [], [])
    assert entries_after_deletion[0]["checked"] is False


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

    readme = (output_root / "README.md").read_text(encoding="utf-8")
    assert f"- [x] `{fake_claude_home.session_id}` — Test session" in readme


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


def _write_session_jsonl(
    project_dir: Path, workspace: Path, session_id: str, timestamp: str, title: str
) -> Path:
    events = [
        {
            "type": "ai-title",
            "aiTitle": title,
            "cwd": str(workspace.resolve()),
            "timestamp": timestamp,
        },
    ]
    jsonl_file = project_dir / f"{session_id}.jsonl"
    jsonl_file.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
    return jsonl_file


def _setup_two_session_workspace(tmp_path: Path) -> tuple[Path, Path, Path]:
    home = tmp_path / "testuser"
    claude_dir = home / ".claude"
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    workspace_resolved = str(workspace.resolve())

    project_dir = claude_dir / "projects" / path_to_project_slug(workspace_resolved)
    project_dir.mkdir(parents=True)

    _write_session_jsonl(
        project_dir, workspace, "session-one", "2026-01-01T10:00:00Z", "First session"
    )
    _write_session_jsonl(
        project_dir, workspace, "session-two", "2026-02-01T10:00:00Z", "Second session"
    )
    return home, claude_dir, workspace


def test_cli_reimport_refreshes_folder_and_readme_when_conversation_continued(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "testuser"
    claude_dir = home / ".claude"
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    project_dir = claude_dir / "projects" / path_to_project_slug(str(workspace.resolve()))
    project_dir.mkdir(parents=True)
    output_root = tmp_path / "cli-out"
    argv = ["prog", str(home), str(workspace), "-o", str(output_root)]

    _write_session_jsonl(
        project_dir, workspace, "session-one", "2026-01-01T10:00:00Z", "Original title"
    )
    monkeypatch.setattr("sys.argv", argv)
    main()

    dirs_before = [d for d in output_root.iterdir() if d.is_dir()]
    assert len(dirs_before) == 1
    assert dirs_before[0].name == build_session_dir_name(
        "2026-01-01T10:00:00Z", "Original title", "session-one"
    )

    # The conversation continues: later timestamp, retitled by the AI.
    _write_session_jsonl(
        project_dir, workspace, "session-one", "2026-03-15T12:30:00Z", "Updated title"
    )
    main()

    dirs_after = [d for d in output_root.iterdir() if d.is_dir()]
    assert len(dirs_after) == 1, "re-import must refresh the folder in place, not duplicate it"
    assert dirs_after[0].name == build_session_dir_name(
        "2026-03-15T12:30:00Z", "Updated title", "session-one"
    )

    readme = (output_root / "README.md").read_text(encoding="utf-8")
    assert readme.count("session-one") == 1
    assert (
        f"- [x] `session-one` — Updated title — updated {fmt_ts('2026-03-15T12:30:00Z')}" in readme
    )


def test_cli_first_readme_with_session_filter_checks_only_the_imported_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, claude_dir, workspace = _setup_two_session_workspace(tmp_path)
    output_root = tmp_path / "cli-out"
    argv = [
        "prog",
        str(home),
        str(workspace),
        "-o",
        str(output_root),
        "--session",
        "session-two",
    ]
    monkeypatch.setattr("sys.argv", argv)

    main()

    readme = (output_root / "README.md").read_text(encoding="utf-8")
    assert "- [x] `session-two` — Second session" in readme
    assert "- [ ] `session-one` — First session" in readme
    # Only the imported session actually got a folder.
    session_dirs = [d for d in output_root.iterdir() if d.is_dir()]
    assert len(session_dirs) == 1
    assert session_dirs[0].name.endswith("session-two")


def test_cli_second_run_with_filter_keeps_other_export_checked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, claude_dir, workspace = _setup_two_session_workspace(tmp_path)
    output_root = tmp_path / "cli-out"
    base_argv = ["prog", str(home), str(workspace), "-o", str(output_root)]

    monkeypatch.setattr("sys.argv", base_argv)
    main()
    readme = (output_root / "README.md").read_text(encoding="utf-8")
    assert "- [x] `session-one`" in readme
    assert "- [x] `session-two`" in readme

    # Re-import only session-two; session-one's export folder is untouched
    # and must remain checked.
    monkeypatch.setattr("sys.argv", [*base_argv, "--session", "session-two"])
    main()

    readme = (output_root / "README.md").read_text(encoding="utf-8")
    assert "- [x] `session-one`" in readme
    assert "- [x] `session-two`" in readme
    session_dirs = [d for d in output_root.iterdir() if d.is_dir()]
    assert len(session_dirs) == 2


def test_cli_unchecks_manually_deleted_export_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, claude_dir, workspace = _setup_two_session_workspace(tmp_path)
    output_root = tmp_path / "cli-out"
    base_argv = ["prog", str(home), str(workspace), "-o", str(output_root)]

    monkeypatch.setattr("sys.argv", base_argv)
    main()

    session_one_dir = next(
        d for d in output_root.iterdir() if d.is_dir() and d.name.endswith("session-one")
    )
    shutil.rmtree(session_one_dir)

    # Re-run, only touching session-two: session-one's row must flip to
    # unchecked since its folder no longer exists.
    monkeypatch.setattr("sys.argv", [*base_argv, "--session", "session-two"])
    main()

    readme = (output_root / "README.md").read_text(encoding="utf-8")
    assert "- [ ] `session-one`" in readme
    assert "- [x] `session-two`" in readme
