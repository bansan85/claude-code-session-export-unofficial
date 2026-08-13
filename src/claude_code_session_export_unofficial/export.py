"""Orchestration of a single session's export, and of the export index."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ._util import log
from .anonymize import Anonymizer
from .discovery import collect_plan_paths, load_jsonl
from .markdown import fmt_ts, session_to_markdown

_INVALID_FILENAME_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_MAX_DIR_NAME_LENGTH = 150


def sanitize_filename_component(text: str) -> str:
    cleaned = _INVALID_FILENAME_CHARS_RE.sub("_", text).strip().rstrip(". ")
    return cleaned[:_MAX_DIR_NAME_LENGTH].rstrip(". ") or "untitled"


def build_session_dir_name(last_ts: str | None, title: str | None, session_id: str) -> str:
    # ":" is invalid in Windows folder names, so the timestamp is rendered
    # with dashes instead (still lexically sortable).
    date_part = fmt_ts(last_ts).replace(":", "-") if last_ts else "unknown-date"
    title_part = title.strip() if title and title.strip() else session_id
    return sanitize_filename_component(f"{date_part} - {title_part}")


def extract_title_and_last_ts(
    events: list[dict[str, Any]],
) -> tuple[str | None, str | None]:
    title = next(
        (e.get("aiTitle") for e in events if e.get("type") == "ai-title" and e.get("aiTitle")),
        None,
    )
    last_ts = next((e["timestamp"] for e in reversed(events) if e.get("timestamp")), None)
    return title, last_ts


def session_preview(jsonl_file: Path) -> tuple[str, str | None]:
    """Returns (dir_name, last_ts) for a session, loading its events once."""
    events = load_jsonl(jsonl_file)
    title, last_ts = extract_title_and_last_ts(events)
    return build_session_dir_name(last_ts, title, jsonl_file.stem), last_ts


def copy_tree_if_exists(src: Path, dst: Path, verbose: bool, anonymizer: Anonymizer) -> bool:
    if not src.exists():
        return False
    dst.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        for item in src.rglob("*"):
            if item.is_file():
                rel = item.relative_to(src)
                target = dst / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                anonymizer.copy_file(item, target)
    else:
        anonymizer.copy_file(src, dst / src.name)
    log(f"  copied: {src} -> {dst}", verbose)
    return True


def export_session(
    jsonl_file: Path,
    claude_dir: Path,
    workspace: Path,
    output_root: Path,
    anonymizer: Anonymizer,
    verbose: bool,
) -> dict[str, Any]:
    session_id = jsonl_file.stem
    events = load_jsonl(jsonl_file)
    title, last_ts = extract_title_and_last_ts(events)

    session_out = output_root / build_session_dir_name(last_ts, title, session_id)
    session_out.mkdir(parents=True, exist_ok=True)

    # 1) Raw conversation
    anonymizer.copy_file(jsonl_file, session_out / "session.jsonl")
    log(f"[{session_id}] conversation copied (session.jsonl)", verbose)

    # 2) Markdown conversion
    md = session_to_markdown(events, session_id, workspace)
    md = anonymizer.apply_markdown(md)
    (session_out / "session.md").write_text(md, encoding="utf-8")
    log(f"[{session_id}] conversation converted to Markdown (session.md)", verbose)

    # 3) Associated plans (files under ~/.claude/plans referenced by the session)
    plan_paths = collect_plan_paths(events, claude_dir)
    if plan_paths:
        plans_out = session_out / "plans"
        plans_out.mkdir(parents=True, exist_ok=True)
        for p in plan_paths:
            anonymizer.copy_markdown_file(p, plans_out / p.name)
        log(f"[{session_id}] {len(plan_paths)} plan(s) copied", verbose)

    # 4) Generated/modified file history (~/.claude/file-history/<session_id>)
    fh_src = claude_dir / "file-history" / session_id
    copy_tree_if_exists(fh_src, session_out / "file-history", verbose, anonymizer)

    # 5) Session environment variables (~/.claude/session-env/<session_id>)
    env_src = claude_dir / "session-env" / session_id
    copy_tree_if_exists(env_src, session_out / "session-env", verbose, anonymizer)

    # 6) Session metadata (~/.claude/sessions/*.json containing this sessionId)
    sessions_meta_dir = claude_dir / "sessions"
    if sessions_meta_dir.is_dir():
        for meta_file in sessions_meta_dir.glob("*.json"):
            try:
                data = json.loads(meta_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if data.get("sessionId") == session_id:
                info_text = anonymizer.apply(json.dumps(data, ensure_ascii=False, indent=2))
                (session_out / "session-info.json").write_text(info_text, encoding="utf-8")

    return {"session_id": session_id, "title": title, "plans": len(plan_paths)}


def write_index(
    output_root: Path,
    workspace: Path,
    summaries: list[dict[str, Any]],
    anonymizer: Anonymizer,
) -> None:
    lines = ["# Claude Code Session Export", ""]
    lines.append(f"Workspace: `{workspace}`")
    lines.append("")
    lines.append("| Session ID | Title | Plans copied |")
    lines.append("|---|---|---|")
    for s in summaries:
        lines.append(f"| `{s['session_id']}` | {s['title'] or '-'} | {s['plans']} |")
    lines.append("")
    text = anonymizer.apply_markdown("\n".join(lines))
    (output_root / "README.md").write_text(text, encoding="utf-8")
