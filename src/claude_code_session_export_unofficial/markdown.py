"""Rendering of a Claude Code session's JSONL events as readable Markdown."""

from __future__ import annotations

import difflib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

_PROTECTED_HTML_TAGS = ("<details>", "<summary>", "</summary>", "</details>")

_BLOCKQUOTE_PREFIX_RE = re.compile(r"^(?:>\s?)+")


def _escape_html_chars(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _escape_line_outside_inline_code(line: str) -> str:
    # A Markdown blockquote prefix ("> ", possibly repeated/nested) is
    # syntax, not free text: its ">" must not be escaped.
    prefix_match = _BLOCKQUOTE_PREFIX_RE.match(line)
    prefix = prefix_match.group(0) if prefix_match else ""
    rest = line[len(prefix) :]
    # An inline code span `...` already protects its content: the Markdown
    # renderer will escape any HTML characters inside it itself. So we only
    # escape the portions outside the backticks (even indices).
    parts = rest.split("`")
    for i in range(0, len(parts), 2):
        parts[i] = _escape_html_chars(parts[i])
    return prefix + "`".join(parts)


def escape_html_outside_code(text: str) -> str:
    """Escapes '<', '>' and '&' in free Markdown text, outside of ``` code
    blocks and inline `...` code spans.

    Needed because our anonymization placeholders ("<workspace>",
    "<username>") look like HTML tags: in text outside a code block, a
    Markdown/HTML renderer would interpret them as such (best case they
    vanish, worst case an unclosed tag like <workspace> swallows the rest of
    the document). Inside a ``` block or a `...` span, this risk does not
    exist, so we leave it as-is.

    Preserves the <details>/<summary> tags we deliberately inject for
    collapsible ("Thinking") sections.
    """
    sentinels: dict[str, str] = {}
    protected = text
    for i, tag in enumerate(_PROTECTED_HTML_TAGS):
        if tag in protected:
            token = f"\x00TAG{i}\x00"
            protected = protected.replace(tag, token)
            sentinels[token] = tag

    in_fence = False
    out_lines: list[str] = []
    for line in protected.split("\n"):
        if line.strip().startswith("```"):
            in_fence = not in_fence
            out_lines.append(line)
            continue
        if in_fence:
            out_lines.append(line)
            continue
        out_lines.append(_escape_line_outside_inline_code(line))
    result = "\n".join(out_lines)

    for token, tag in sentinels.items():
        result = result.replace(token, tag)
    return result


def fmt_ts(ts: str | None) -> str:
    if not ts:
        return ""
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return ts


IDE_SELECTION_RE = re.compile(r"<ide_selection>(.*?)</ide_selection>", re.DOTALL)
IDE_SELECTION_DETAIL_RE = re.compile(
    r"The user selected (?:the )?lines? (\d+)(?: to (\d+))? from (.+?):\n(.*)",
    re.DOTALL,
)
IDE_SELECTION_BOILERPLATE = "This may or may not be related to the current task."

_LANG_BY_EXTENSION = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "jsx",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".json": "json",
    ".md": "markdown",
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".ps1": "powershell",
    ".psm1": "powershell",
    ".sh": "bash",
    ".bash": "bash",
    ".yml": "yaml",
    ".yaml": "yaml",
    ".java": "java",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".go": "go",
    ".rs": "rust",
    ".rb": "ruby",
    ".php": "php",
    ".sql": "sql",
    ".xml": "xml",
    ".toml": "toml",
    ".ini": "ini",
}


def _guess_lang(file_path: str) -> str:
    return _LANG_BY_EXTENSION.get(Path(file_path).suffix.lower(), "")


def render_ide_selection(inner: str) -> str:
    """Formats the IDE selection context that Claude Code automatically
    prepends to user messages (the ``<ide_selection>...</ide_selection>``
    tag), making clear that this is context automatically attached (a file
    excerpt attachment), not text typed by the user. Always shown in full
    (no collapsible fallback)."""
    inner = inner.strip()
    if inner.endswith(IDE_SELECTION_BOILERPLATE):
        inner = inner[: -len(IDE_SELECTION_BOILERPLATE)].rstrip()

    match = IDE_SELECTION_DETAIL_RE.match(inner)
    if match:
        start, end, file_path, selection = match.groups()
        end = end or start
        file_path = file_path.strip()
        label = f"line {start}" if start == end else f"lines {start} to {end}"
        lang = _guess_lang(file_path)
        code = selection.strip("\n")
        return f"**Attachment**\n\n`{file_path}` — {label}\n\n```{lang}\n{code}\n```"

    # Generic fallback if the content doesn't exactly match the known template.
    return f"**Attachment**\n\n{inner}"


def replace_ide_selection(text: str) -> str:
    return IDE_SELECTION_RE.sub(lambda m: render_ide_selection(m.group(1)), text)


def render_edit_diff(input_: dict[str, Any]) -> str:
    """Builds a unified diff (```diff style) between old_string and
    new_string for an Edit tool call."""
    file_path = input_.get("file_path", "")
    old_lines = input_.get("old_string", "").splitlines()
    new_lines = input_.get("new_string", "").splitlines()
    diff_lines = list(
        difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=file_path,
            tofile=file_path,
            lineterm="",
        )
    )
    if not diff_lines:
        diff_lines = [f"# no textual difference detected for {file_path}"]
    return "\n".join(diff_lines)


def render_tool_input(input_: dict[str, Any]) -> str:
    """Renders a tool call's input fields (minus ``description``, already
    shown in the header) as Markdown.

    A naive ``json.dumps`` of the whole dict turns any multi-line string
    value (e.g. a Workflow script, a long prompt) into a single escaped
    line with literal ``\\n`` sequences -- unreadable, and can span tens of
    thousands of characters. Such values get their own collapsible block
    (same ``<details>`` pattern as "Thinking" sections) instead; the
    remaining scalar fields are kept together as compact JSON.
    """
    multiline = {k: v for k, v in input_.items() if isinstance(v, str) and "\n" in v}
    scalars = {k: v for k, v in input_.items() if k not in multiline}

    parts: list[str] = []
    if scalars:
        parts.append(f"```json\n{json.dumps(scalars, ensure_ascii=False, indent=2)}\n```")
    for key, value in multiline.items():
        parts.append(f"<details>\n<summary>{key}</summary>\n\n```\n{value}\n```\n\n</details>")
    return "\n\n".join(parts) if parts else "```json\n{}\n```"


def render_content_block(block: dict[str, Any]) -> str:
    btype = block.get("type")

    if btype == "text":
        return replace_ide_selection(block.get("text", ""))

    if btype == "thinking":
        text = block.get("thinking", "")
        if not text.strip():
            return ""
        return f"<details>\n<summary>Thinking</summary>\n\n{text}\n\n</details>"

    if btype == "tool_use":
        name = block.get("name", "tool")
        input_ = block.get("input", {})
        description = input_.get("description") if isinstance(input_, dict) else None
        header = f"**Tool call: `{name}`**"
        if isinstance(description, str) and description.strip():
            header += f" — {description.strip()}"

        if (
            name == "Edit"
            and isinstance(input_, dict)
            and isinstance(input_.get("old_string"), str)
            and isinstance(input_.get("new_string"), str)
        ):
            diff_body = render_edit_diff(input_)
            return f"{header}\n```diff\n{diff_body}\n```"
        if isinstance(input_, dict) and "command" in input_ and isinstance(input_["command"], str):
            body = input_["command"]
            lang = "powershell" if name.lower() == "powershell" else "bash"
            return f"{header}\n```{lang}\n{body}\n```"

        if isinstance(input_, dict):
            rest = {k: v for k, v in input_.items() if k != "description"}
            return f"{header}\n{render_tool_input(rest)}"

        body = json.dumps(input_, ensure_ascii=False, indent=2)
        return f"{header}\n```json\n{body}\n```"

    if btype == "tool_result":
        content = block.get("content", "")
        is_error = block.get("is_error", False)
        text = stringify_tool_result_content(content)
        label = "Error" if is_error else "Result"
        return f"**{label}:**\n```\n{text}\n```"

    if btype == "image":
        return "*[attached image]*"

    return f"*[unhandled block: {btype}]*"


def stringify_tool_result_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
            elif isinstance(item, dict) and item.get("type") == "image":
                parts.append("[image]")
            else:
                parts.append(json.dumps(item, ensure_ascii=False))
        return "\n".join(parts)
    return json.dumps(content, ensure_ascii=False)


def session_to_markdown(events: list[dict[str, Any]], session_id: str, workspace: Path) -> str:
    title = None
    cwd = None
    version = None
    git_branch = None
    first_ts = None
    last_ts = None

    for ev in events:
        if ev.get("type") == "ai-title" and ev.get("aiTitle"):
            title = ev["aiTitle"]
        if ev.get("cwd") and cwd is None:
            cwd = ev["cwd"]
        if ev.get("version") and version is None:
            version = ev["version"]
        if ev.get("gitBranch") and git_branch is None:
            git_branch = ev["gitBranch"]
        ts = ev.get("timestamp")
        if ts:
            if first_ts is None:
                first_ts = ts
            last_ts = ts

    lines: list[str] = []
    lines.append(f"# Claude Session — {title or session_id}")
    lines.append("")
    lines.append(f"- **Session ID**: `{session_id}`")
    lines.append(f"- **Workspace**: `{cwd or workspace}`")
    if version:
        lines.append(f"- **Claude Code version**: {version}")
    if git_branch:
        lines.append(f"- **Git branch**: {git_branch}")
    if first_ts:
        lines.append(f"- **Start**: {fmt_ts(first_ts)}")
    if last_ts:
        lines.append(f"- **End**: {fmt_ts(last_ts)}")
    lines.append("")
    lines.append("---")
    lines.append("")

    for ev in events:
        etype = ev.get("type")
        if etype not in ("user", "assistant"):
            continue

        message = ev.get("message", {})
        content = message.get("content", "")
        ts = fmt_ts(ev.get("timestamp"))
        role_label = "User" if etype == "user" else "Assistant"

        blocks_text: list[str]
        if isinstance(content, str):
            blocks_text = [replace_ide_selection(content)] if content.strip() else []
        elif isinstance(content, list):
            blocks_text = [render_content_block(b) for b in content]
            blocks_text = [b for b in blocks_text if b.strip()]
        else:
            blocks_text = []

        if not blocks_text:
            continue

        header = f"## {role_label}"
        if ts:
            header += f" — {ts}"
        lines.append(header)
        lines.append("")
        for b in blocks_text:
            lines.append(b)
            lines.append("")
        lines.append("---")
        lines.append("")

    return "\n".join(lines)
