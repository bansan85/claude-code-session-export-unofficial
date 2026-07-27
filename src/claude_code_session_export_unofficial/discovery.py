"""Discovery of the Claude Code ``.claude`` directory, its project folders,
and the session/plan files stored within them.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ._util import log

SEP_CHARS = (":", "\\", "/")


def resolve_claude_dir(raw: Path) -> Path:
    """Accepts either the HOME directory (which contains ``.claude``), or
    ``.claude`` itself."""
    raw = raw.expanduser()
    candidates: list[Path] = []
    if raw.name == ".claude" and raw.is_dir():
        candidates.append(raw)
    candidates.append(raw / ".claude")
    candidates.append(raw)

    for c in candidates:
        if c.is_dir() and (c / "projects").is_dir():
            return c.resolve()

    raise SystemExit(
        f"Could not find a valid '.claude' directory (with a 'projects' "
        f"subdirectory) starting from: {raw}"
    )


def path_to_project_slug(path_str: str) -> str:
    """Reproduces Claude Code's algorithm for naming folders under
    ``~/.claude/projects/``: every separator (``:``, ``\\``, ``/``) becomes
    ``-``.
    """
    return "".join("-" if ch in SEP_CHARS else ch for ch in path_str)


def normalize_path_for_compare(path_str: str) -> str:
    p = path_str.strip().replace("\\", "/")
    if len(p) >= 2 and p[1] == ":":
        p = p[0].lower() + p[1:]
    return p.rstrip("/").lower()


def find_project_dirs(claude_dir: Path, workspace: Path, verbose: bool) -> list[Path]:
    projects_root = claude_dir / "projects"
    workspace_resolved = str(workspace.resolve())
    slug = path_to_project_slug(workspace_resolved)

    # 1) Direct match by slug (case-insensitive comparison)
    direct_matches = [
        d for d in projects_root.iterdir() if d.is_dir() and d.name.lower() == slug.lower()
    ]
    if direct_matches:
        log(f"Project found by name match: {direct_matches[0].name}", verbose)
        return direct_matches

    # 2) Fallback: scan every project and inspect the "cwd" field recorded
    #    in the .jsonl files to find the ones matching the workspace.
    log("No direct folder name match, searching by content (cwd)...", verbose)
    target = normalize_path_for_compare(workspace_resolved)
    matches: list[Path] = []
    if projects_root.is_dir():
        for d in projects_root.iterdir():
            if not d.is_dir():
                continue
            for jsonl_file in d.glob("*.jsonl"):
                if scan_jsonl_for_cwd_match(jsonl_file, target):
                    matches.append(d)
                    break
    return matches


def scan_jsonl_for_cwd_match(jsonl_file: Path, target_normalized: str) -> bool:
    try:
        with jsonl_file.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                cwd = obj.get("cwd")
                if cwd and normalize_path_for_compare(cwd) == target_normalized:
                    return True
    except OSError:
        return False
    return False


def load_jsonl(jsonl_file: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    with jsonl_file.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def collect_plan_paths(events: list[dict[str, Any]], claude_dir: Path) -> list[Path]:
    plans_dir = (claude_dir / "plans").resolve()
    found: set[Path] = set()
    for ev in events:
        if ev.get("type") != "file-history-delta":
            continue
        tracking_path = ev.get("trackingPath")
        if not tracking_path:
            continue
        p = Path(tracking_path)
        try:
            if p.resolve().parent == plans_dir and p.is_file():
                found.add(p.resolve())
        except OSError:
            continue
    return sorted(found)
