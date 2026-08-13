# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`claude-code-session-export-unofficial` — a standalone CLI tool (published under this name on PyPI) that exports Claude Code session logs for a given workspace from `~/.claude` into a readable, anonymized Markdown export. It is an independent, community-maintained tool, not affiliated with Anthropic. Stdlib-only, no runtime dependencies.

## Commands

```bash
# Install in editable mode with dev tooling (ruff, mypy, pytest, build)
pip install -e ".[dev]"

# Lint / format / type-check / test — all must pass clean (this is exactly what CI runs)
ruff check .
ruff format --check .      # use `ruff format .` (no --check) to auto-fix formatting
mypy                        # checks both src/ and tests/, per [tool.mypy] in pyproject.toml
pytest

# Run a single test
pytest tests/test_markdown.py::test_fmt_ts_none_empty_invalid_and_valid

# Build the distributable wheel + sdist
python -m build
```

If `ruff`/`mypy`/`pytest`/`python -m build` aren't found on `PATH` (e.g. a shell where the venv isn't activated), fall back to the project's local `.venv` interpreter: `.venv/Scripts/python.exe -m ruff check .` on Windows, `.venv/bin/python -m ruff check .` on Unix (`.venv` is gitignored, so it may not exist — run `pip install -e ".[dev]"` first if it doesn't).

```bash
# Run the CLI locally after editable install
claude-code-session-export-unofficial <home_or_.claude_dir> <workspace> [options]
```

CI (`.github/workflows/ci.yml`) runs `ruff check`, `ruff format --check`, `mypy src`, and `pytest` on a Ubuntu/Windows × Python 3.10–3.13 matrix, then builds the wheel and uploads it as a workflow artifact.

## Architecture

Source lives under `src/claude_code_session_export_unofficial/` (src-layout, `pythonpath = ["src"]` in pytest config so tests import it without needing an install). The module split is a strict, one-directional dependency chain — never introduce a cycle:

```
_util  (log())
  ├─→ discovery.py   (finding ~/.claude, project dirs, sessions, plans on disk)
  └─→ markdown.py    (independent of discovery; pure text → Markdown rendering)
        └─→ anonymize.py  (uses discovery.path_to_project_slug + markdown.escape_html_outside_code)
              └─→ export.py  (orchestrates discovery + anonymize + markdown to write a session's output)
                    └─→ cli.py  (argparse entry point; only module allowed to print/exit)
```

`log()` lives in `_util.py` specifically because both `discovery.py` and `export.py` need it — putting it in `cli.py` would create a circular import. Use relative imports (`from .discovery import ...`) throughout.

Key responsibilities per module:
- **`discovery.py`**: resolves `~/.claude` from either the HOME dir or `.claude` itself, finds the project folder matching a workspace (first by exact folder-name slug match, falling back to scanning each session's recorded `cwd` field), loads/parses `.jsonl` session files, and locates plan files referenced by a session.
- **`markdown.py`**: converts a session's JSONL events into readable Markdown — renders tool calls (including unified diffs for `Edit` calls via `difflib`), tool results, IDE-selection attachments, and thinking blocks. `escape_html_outside_code()` is the one subtle piece: it HTML-escapes `<`/`>`/`&` in free text but *not* inside fenced/inline code, because the anonymization placeholders (`<workspace>`, `<username>`) look like HTML tags and would otherwise corrupt rendering.
- **`anonymize.py`**: the `Anonymizer` class replaces the workspace's absolute path (in every form it can appear — raw, JSON-escaped, forward-slash, project slug, Git Bash `/c/...` mount, either Windows drive-letter case) and the system username, everywhere in copied/generated content. This anonymization is systematic and cannot be disabled — do not add an opt-out.
- **`export.py`**: orchestrates one session's full export (raw `.jsonl`, converted `.md`, referenced plans, file-history, session-env, session metadata) and writes the top-level export index.
- **`cli.py`**: the only module with argparse/print/`SystemExit` calls — all user-facing text lives here or is threaded up from lower modules as return values, never printed from within `discovery`/`export`.

## Conventions specific to this repo

- **Everything in English** — code, comments, docstrings, CLI help text, and all generated Markdown output. Two exceptions must stay exactly as-is because they parse Claude Code's actual generated input rather than producing our own output: `markdown.IDE_SELECTION_DETAIL_RE` and `markdown.IDE_SELECTION_BOILERPLATE`.
- **Fully typed, `mypy --strict` clean** — every new function needs complete parameter/return annotations. `[[tool.mypy.overrides]]` relaxes `disallow_untyped_defs` for `tests.*` only.
- Ruff config (`[tool.ruff]` / `[tool.ruff.lint]` in `pyproject.toml`): line length 100, target `py310`, rule set `E, F, I, UP, B, SIM, C4`.
- Tests use `tmp_path` exclusively (no real filesystem/network); `tests/conftest.py`'s `fake_claude_home` fixture builds a complete fake `~/.claude` tree (projects, plans, file-history, session-env, session metadata) for integration-style tests in `test_export.py`. Because there's no `tests/__init__.py`, cross-file imports use `from conftest import FakeClaudeHome` (bare module import), not a relative import.
- The version string lives in exactly one place: `__version__` in `src/claude_code_session_export_unofficial/__init__.py` — `pyproject.toml` reads it dynamically via `[tool.hatch.version]`, don't add a second version string anywhere.

## Commit messages

Follow the classic Git conventions: subject line ≤50 characters, body lines wrapped at ≤70 characters. Prefer fitting the whole message on the subject line alone (no body) whenever it's enough to convey the change.
