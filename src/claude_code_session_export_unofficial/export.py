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

_INDEX_LINE_RE = re.compile(
    r"^- \[(?P<checked>[ xX])\] `(?P<session_id>[^`]*)` — (?P<title>.*) — "
    r"updated (?P<updated>.*?) — (?P<plans>\d+) plan\(s\) copied\s*$"
)


def sanitize_filename_component(text: str, max_length: int = _MAX_DIR_NAME_LENGTH) -> str:
    cleaned = _INVALID_FILENAME_CHARS_RE.sub("_", text).strip().rstrip(". ")
    return cleaned[:max_length].rstrip(". ") or "untitled"


def build_session_dir_name(last_ts: str | None, title: str | None, session_id: str) -> str:
    # ":" is invalid in Windows folder names, so the timestamp is rendered
    # with dashes instead (still lexically sortable).
    date_part = fmt_ts(last_ts).replace(":", "-") if last_ts else "unknown-date"
    # The session ID is always appended, rather than only used as a title
    # fallback: neither the date (the conversation can be continued later)
    # nor the title (it can be rewritten) are stable across re-imports, so
    # the ID is what lets a re-import find and refresh the same folder
    # instead of creating a duplicate.
    session_part = sanitize_filename_component(session_id)
    budget = max(_MAX_DIR_NAME_LENGTH - len(date_part) - len(session_part) - 6, 1)
    title_part = (
        sanitize_filename_component(title, budget) if title and title.strip() else "untitled"
    )
    return f"{date_part} - {title_part} - {session_part}"


def find_existing_session_dir(output_root: Path, session_id: str) -> Path | None:
    """Finds a previously exported folder for this session, matched by its
    trailing " - <session_id>" suffix (the only part of the folder name that
    stays stable across re-imports)."""
    if not output_root.is_dir():
        return None
    suffix = f" - {sanitize_filename_component(session_id)}"
    for d in sorted(output_root.iterdir()):
        if d.is_dir() and d.name.endswith(suffix):
            return d
    return None


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
    existing_dir = find_existing_session_dir(output_root, session_id)
    if existing_dir is not None and existing_dir != session_out:
        existing_dir.rename(session_out)
        log(
            f"[{session_id}] already imported, refreshing: "
            f"{existing_dir.name} -> {session_out.name}",
            verbose,
        )
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

    return {"session_id": session_id, "title": title, "last_ts": last_ts, "plans": len(plan_paths)}


def parse_index(output_root: Path) -> dict[str, dict[str, Any]]:
    """Reads a previously written README.md, if any, to recover the sessions
    it already listed. Used so a session removed from ``~/.claude`` since the
    last export (and thus no longer discoverable there) still keeps its row,
    with its checkbox re-evaluated against the current export folder."""
    readme = output_root / "README.md"
    if not readme.is_file():
        return {}
    entries: dict[str, dict[str, Any]] = {}
    for line in readme.read_text(encoding="utf-8").splitlines():
        m = _INDEX_LINE_RE.match(line)
        if not m:
            continue
        title = m.group("title")
        session_id = m.group("session_id")
        entries[session_id] = {
            "session_id": session_id,
            "title": None if title == "-" else title,
            "updated": m.group("updated"),
            "plans": int(m.group("plans")),
            "checked": m.group("checked").lower() == "x",
        }
    return entries


def build_index_entries(
    output_root: Path,
    claude_dir: Path,
    all_jsonl_files: list[Path],
    summaries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Builds every README row: one for each session known for this
    workspace, whether or not it was (re-)exported in this run. A row is
    checked if and only if its export folder currently exists on disk, so
    that a manually deleted export un-checks itself on the next run."""
    exported_by_id = {s["session_id"]: s for s in summaries}
    previous = parse_index(output_root)
    seen_ids: set[str] = set()
    entries: list[dict[str, Any]] = []

    for jsonl_file in all_jsonl_files:
        session_id = jsonl_file.stem
        seen_ids.add(session_id)
        if session_id in exported_by_id:
            s = exported_by_id[session_id]
            entries.append(
                {
                    "session_id": session_id,
                    "title": s["title"],
                    "updated": fmt_ts(s["last_ts"]) or "-",
                    "sort_key": s["last_ts"] or "",
                    "plans": s["plans"],
                    "checked": True,
                }
            )
            continue
        events = load_jsonl(jsonl_file)
        title, last_ts = extract_title_and_last_ts(events)
        plans = len(collect_plan_paths(events, claude_dir))
        entries.append(
            {
                "session_id": session_id,
                "title": title,
                "updated": fmt_ts(last_ts) or "-",
                "sort_key": last_ts or "",
                "plans": plans,
                "checked": find_existing_session_dir(output_root, session_id) is not None,
            }
        )

    for session_id, old_entry in previous.items():
        if session_id in seen_ids:
            continue
        entries.append(
            {
                **old_entry,
                "sort_key": old_entry["updated"],
                "checked": find_existing_session_dir(output_root, session_id) is not None,
            }
        )

    entries.sort(key=lambda e: e["sort_key"])
    return entries


def write_index(
    output_root: Path,
    workspace: Path,
    entries: list[dict[str, Any]],
    anonymizer: Anonymizer,
) -> None:
    lines = ["# Claude Code Session Export", ""]
    lines.append(f"Workspace: `{workspace}`")
    lines.append("")
    for e in entries:
        checkbox = "x" if e["checked"] else " "
        title = e["title"] or "-"
        lines.append(
            f"- [{checkbox}] `{e['session_id']}` — {title} — updated {e['updated']} — "
            f"{e['plans']} plan(s) copied"
        )
    lines.append("")
    text = anonymizer.apply_markdown("\n".join(lines))
    (output_root / "README.md").write_text(text, encoding="utf-8")
