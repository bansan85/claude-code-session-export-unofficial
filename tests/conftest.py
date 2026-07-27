from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from claude_code_session_export_unofficial.discovery import path_to_project_slug


@dataclass
class FakeClaudeHome:
    home: Path
    claude_dir: Path
    workspace: Path
    session_id: str
    jsonl_file: Path
    plan_path: Path


@pytest.fixture
def fake_claude_home(tmp_path: Path) -> FakeClaudeHome:
    """Builds a minimal, self-contained fake ``~/.claude`` tree (plus a
    matching workspace) under ``tmp_path``, covering projects, plans,
    file-history, session-env and session metadata."""
    home = tmp_path / "testuser"
    claude_dir = home / ".claude"
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    workspace_resolved = str(workspace.resolve())

    slug = path_to_project_slug(workspace_resolved)
    project_dir = claude_dir / "projects" / slug
    project_dir.mkdir(parents=True)

    session_id = "session-abc123"
    jsonl_file = project_dir / f"{session_id}.jsonl"

    plans_dir = claude_dir / "plans"
    plans_dir.mkdir(parents=True)
    plan_path = plans_dir / "my-plan.md"
    plan_path.write_text(f"# Plan\n\nWorkspace: {workspace_resolved}\n", encoding="utf-8")

    events: list[dict[str, Any]] = [
        {
            "type": "ai-title",
            "aiTitle": "Test session",
            "cwd": workspace_resolved,
            "timestamp": "2026-01-01T10:00:00Z",
        },
        {
            "type": "user",
            "timestamp": "2026-01-01T10:00:01Z",
            "message": {"role": "user", "content": "Hello there"},
        },
        {
            "type": "assistant",
            "timestamp": "2026-01-01T10:00:02Z",
            "message": {"role": "assistant", "content": [{"type": "text", "text": "Hi!"}]},
        },
        {
            "type": "file-history-delta",
            "trackingPath": str(plan_path.resolve()),
        },
    ]
    jsonl_file.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")

    file_history_dir = claude_dir / "file-history" / session_id
    file_history_dir.mkdir(parents=True)
    (file_history_dir / "generated.txt").write_text(
        f"owner: testuser, path: {workspace_resolved}", encoding="utf-8"
    )

    session_env_dir = claude_dir / "session-env" / session_id
    session_env_dir.mkdir(parents=True)
    (session_env_dir / "env.json").write_text("{}", encoding="utf-8")

    sessions_meta_dir = claude_dir / "sessions"
    sessions_meta_dir.mkdir(parents=True)
    (sessions_meta_dir / f"{session_id}.json").write_text(
        json.dumps({"sessionId": session_id, "cwd": workspace_resolved}),
        encoding="utf-8",
    )

    return FakeClaudeHome(
        home=home,
        claude_dir=claude_dir,
        workspace=workspace,
        session_id=session_id,
        jsonl_file=jsonl_file,
        plan_path=plan_path,
    )


@pytest.fixture
def sample_events() -> list[dict[str, Any]]:
    return [
        {
            "type": "ai-title",
            "aiTitle": "Sample",
            "cwd": "/tmp/project",
            "version": "1.2.3",
            "gitBranch": "main",
            "timestamp": "2026-01-01T10:00:00Z",
        },
        {"type": "user", "timestamp": "2026-01-01T10:00:01Z", "message": {"content": "hi"}},
        {
            "type": "assistant",
            "timestamp": "2026-01-01T10:00:02Z",
            "message": {"content": [{"type": "text", "text": "hello"}]},
        },
    ]
