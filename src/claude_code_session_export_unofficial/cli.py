"""
claude-code-session-export-unofficial

Copies Claude Code sessions (conversations, generated/history files, plans,
etc.) associated with a given workspace, from the ``~/.claude`` folder (or
equivalent) to an export folder inside the workspace, and converts each
session to readable Markdown.

Cross-platform (Windows and Linux; uses only pathlib/os from the standard
library).

Usage:
    claude-code-session-export-unofficial <home_or_.claude_dir> <workspace> [options]

Examples:
    # Windows
    claude-code-session-export-unofficial %USERPROFILE% H:\\repos\\my-project

    # Linux/macOS
    claude-code-session-export-unofficial "$HOME" ~/code/my-project

    # Explicitly target the .claude folder and choose the output location
    claude-code-session-export-unofficial C:\\Users\\me\\.claude . -o backup_sessions

Useful options:
    --session <id>   Only process one specific session (repeatable)
    -o, --output     Output folder (default: <workspace>/claude-sessions-export)
    -v, --verbose    Show operation details

The script always copies the raw conversation, its Markdown conversion, the
associated plans, and the file history (~/.claude/file-history): this
behavior cannot be disabled.

Anonymization (always applied, cannot be disabled):
    Two replacements are always applied throughout all copied/generated
    content:
    1. The workspace's absolute path, in all its forms, is replaced by
       "<workspace>". Example for "d:\\repos\\project":
           d:\\repos\\project        d:\\\\repos\\\\project (escaped, .jsonl)
           d:/repos/project          d--repos-project (project slug)
           /d/repos/project (Git Bash mount)
       including with the other drive-letter case (D:\\... / d:\\...).
    2. The system username (derived from the parent of ~/.claude, e.g. the
       "me" in "C:\\Users\\me\\.claude") is replaced by "<username>"
       everywhere this token appears verbatim in the text -- not just in a
       path, but also in command output such as `ls -la` which shows the
       file's owner.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from .anonymize import Anonymizer
from .discovery import find_project_dirs, resolve_claude_dir
from .export import export_session, session_preview, write_index


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Exports Claude Code sessions (conversations, plans, file history) for a "
        "workspace into that workspace, with systematic Markdown conversion and "
        "anonymization.",
    )
    parser.add_argument(
        "claude_home",
        help="The user's HOME directory (containing .claude), or the .claude directory itself.",
    )
    parser.add_argument(
        "workspace",
        help="The current workspace/project directory to retrieve sessions for.",
    )
    parser.add_argument(
        "-o",
        "--output",
        help="Output folder (default: <workspace>/claude-sessions-export).",
    )
    parser.add_argument(
        "--session",
        action="append",
        dest="session_ids",
        default=None,
        help="Only process one specific session (repeatable for multiple sessions).",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Show operation details.",
    )
    args = parser.parse_args()

    claude_dir = resolve_claude_dir(Path(args.claude_home))
    workspace = Path(args.workspace).expanduser().resolve()
    if not workspace.is_dir():
        raise SystemExit(f"Workspace does not exist: {workspace}")

    output_root = (
        Path(args.output).expanduser().resolve()
        if args.output
        else workspace / "claude-sessions-export"
    )
    output_root.mkdir(parents=True, exist_ok=True)

    # ~/.claude is necessarily under the user's HOME directory
    # (C:\Users\<user>\.claude, /home/<user>/.claude, /Users/<user>/.claude...):
    # we derive the system username to anonymize from it.
    username = claude_dir.resolve().parent.name or "user"

    anonymizer = Anonymizer(workspace=workspace, username=username)

    print(f"Claude directory     : {claude_dir}")
    print(f"Workspace             : {workspace}")
    print(f"Output directory      : {output_root}")
    replacements = ", ".join(f"'{orig}' -> '{repl}'" for orig, repl in anonymizer.labels)
    print(f"Anonymization         : {replacements}")
    print()

    project_dirs = find_project_dirs(claude_dir, workspace, args.verbose)
    if not project_dirs:
        raise SystemExit(
            "No Claude Code sessions found for this workspace. "
            "Check the workspace path, or use --session to target a specific session."
        )

    jsonl_files: list[Path] = []
    for d in project_dirs:
        jsonl_files.extend(sorted(d.glob("*.jsonl")))

    if args.session_ids:
        wanted = set(args.session_ids)
        jsonl_files = [f for f in jsonl_files if f.stem in wanted]
        missing = wanted - {f.stem for f in jsonl_files}
        if missing:
            print(f"Warning: sessions not found: {', '.join(sorted(missing))}")

    if not jsonl_files:
        raise SystemExit("No sessions to export.")

    previews = {f: session_preview(f) for f in jsonl_files}
    jsonl_files.sort(key=lambda f: previews[f][1] or "")

    print(f"{len(jsonl_files)} session(s) to export:")
    for f in jsonl_files:
        print(f"  - {f.stem} -> {previews[f][0]}")
    print()

    summaries: list[dict[str, Any]] = []
    for jsonl_file in jsonl_files:
        summary = export_session(
            jsonl_file=jsonl_file,
            claude_dir=claude_dir,
            workspace=workspace,
            output_root=output_root,
            anonymizer=anonymizer,
            verbose=args.verbose,
        )
        summaries.append(summary)

    write_index(output_root, workspace, summaries, anonymizer)
    print()
    print(f"Export complete: {output_root}")


if __name__ == "__main__":
    main()
