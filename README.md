# claude-code-session-export-unofficial

[![PyPI](https://img.shields.io/pypi/v/claude-code-session-export-unofficial)](https://pypi.org/project/claude-code-session-export-unofficial/)
[![Python versions](https://img.shields.io/pypi/pyversions/claude-code-session-export-unofficial)](https://pypi.org/project/claude-code-session-export-unofficial/)
[![License](https://img.shields.io/github/license/bansan85/claude-code-session-export-unofficial)](LICENSE)
[![CI](https://github.com/bansan85/claude-code-session-export-unofficial/actions/workflows/ci.yml/badge.svg)](https://github.com/bansan85/claude-code-session-export-unofficial/actions/workflows/ci.yml)

Export and anonymize [Claude Code](https://docs.claude.com/en/docs/claude-code) CLI session logs to readable Markdown.

> **Disclaimer**: this is an independent, community-maintained tool. It is
> **not affiliated with, endorsed by, or supported by Anthropic**. "Claude"
> and "Claude Code" are trademarks of Anthropic PBC.

## What it does

For a given workspace, this tool copies the Claude Code sessions
(conversations, generated/history files, plans, etc.) recorded under
`~/.claude` (or equivalent) into an export folder inside that workspace, and
converts each session into readable Markdown. It is cross-platform (Windows,
Linux, macOS) and depends only on the Python standard library.

Two anonymization replacements are always applied to all copied/generated
content, and cannot be disabled:

1. The workspace's absolute path, in every form it can appear in Claude Code
   files (raw, JSON-escaped, forward-slash, project-folder slug, Git Bash
   mount, either drive-letter case on Windows), is replaced with
   `<workspace>`.
2. The system username (derived from the parent folder of `~/.claude`) is
   replaced with `<username>` everywhere it appears verbatim in the text —
   not just inside a path, but also in command output such as `ls -la` that
   shows a file's owner.

## Installation

```bash
pip install claude-code-session-export-unofficial
```

## Usage

```bash
claude-code-session-export-unofficial <home_or_.claude_dir> <workspace> [options]

# Windows
claude-code-session-export-unofficial %USERPROFILE% H:\repos\my-project

# Linux/macOS
claude-code-session-export-unofficial "$HOME" ~/code/my-project

# Explicitly target the .claude folder and choose the output location
claude-code-session-export-unofficial C:\Users\me\.claude . -o backup_sessions
```

### Options

| Option | Description |
|---|---|
| `claude_home` | The user's HOME directory (containing `.claude`), or the `.claude` directory itself. |
| `workspace` | The workspace/project directory to retrieve sessions for. |
| `-o`, `--output` | Output folder (default: `<workspace>/claude-sessions-export`). |
| `--session <id>` | Only process one specific session (repeatable). |
| `-v`, `--verbose` | Show operation details. |

### Output layout

For each exported session, a `<output>/<session_id>/` folder is created containing:

- `session.jsonl` — the raw, anonymized conversation.
- `session.md` — the same conversation, converted to readable Markdown.
- `plans/` — plan files (if any) referenced by the session.
- `file-history/` — the file-history snapshot for the session (if any).
- `session-env/` — the session's recorded environment variables (if any).
- `session-info.json` — session metadata (if found).

A top-level `README.md` index summarizing all exported sessions is written to the output folder.

## Development

```bash
pip install -e ".[dev]"

ruff check .
ruff format --check .
mypy src
pytest
```

## License

MIT — see [LICENSE](LICENSE).
