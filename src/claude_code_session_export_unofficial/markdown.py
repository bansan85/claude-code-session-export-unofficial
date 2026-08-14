"""Rendering of a Claude Code session's JSONL events as readable Markdown."""

from __future__ import annotations

import difflib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

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
    """
    in_fence = False
    out_lines: list[str] = []
    for line in text.split("\n"):
        if line.strip().startswith("```"):
            in_fence = not in_fence
            out_lines.append(line)
            continue
        if in_fence:
            out_lines.append(line)
            continue
        out_lines.append(_escape_line_outside_inline_code(line))
    return "\n".join(out_lines)


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


_COMMAND_TAG_RE = re.compile(r"<command-(message|name|args)>(.*?)</command-\1>", re.DOTALL)


def render_slash_command(text: str) -> str | None:
    """Formats a slash-command invocation (e.g. ``/deep-research``), which
    Claude Code stores in the raw user message as ``<command-message>``,
    ``<command-name>`` and ``<command-args>`` tags.

    Rendered through the generic text path, these show up as literal
    escaped tags, and a multi-line ``command-args`` value is often itself
    Markdown (headings, tables, code fences) that would otherwise bleed into
    the export's own heading hierarchy. Returns None if the text isn't
    (purely) such a block, so the caller falls back to normal rendering.
    """
    tags: dict[str, str] = {}
    remainder = text
    for match in _COMMAND_TAG_RE.finditer(text):
        tags[match.group(1)] = match.group(2)
        remainder = remainder.replace(match.group(0), "", 1)
    if "name" not in tags or remainder.strip():
        return None

    name = tags["name"].strip()
    message = tags.get("message", "").strip()
    args = tags.get("args", "").strip()

    header = f"**Slash command:** `{name}`"
    if message and message.lstrip("/") != name.lstrip("/"):
        header += f" — {message}"
    if not args:
        return header
    return f"{header}\n\n```\n{args}\n```"


_INVOKE_HEADER_RE = re.compile(
    r"Invoke:\s*(?P<tool>[A-Za-z_]\w*)\(\{(?P<body>.*)\}\)\s*\Z", re.DOTALL
)
_INVOKE_FIELD_RE = re.compile(r'(?P<key>\w+):\s*"(?P<value>(?:[^"\\]|\\.)*)"')


def render_tool_invocation(text: str) -> str | None:
    """Formats a trailing ``Invoke: Workflow({ name: "...", args: "..." })``
    call, which Claude Code appends (as plain text, not a real ``tool_use``
    block) to the system-injected description of a workflow-based skill.

    The ``args`` value is a JSON-escaped string -- often a multi-line prompt
    of thousands of characters -- so rendered as-is it shows up as a single
    line with literal ``\\n`` sequences. Splits the call into its fields and
    unescapes them, giving a long field like ``args`` its own labelled code
    block. Returns None if the text has no such trailing call, so the
    caller falls back to normal rendering; text preceding the call (already
    readable prose) is preserved as-is.
    """
    match = _INVOKE_HEADER_RE.search(text)
    if not match:
        return None

    preamble = text[: match.start()].rstrip()
    tool = match.group("tool")

    parts = [f"**Invoke:** `{tool}`"]
    for field_match in _INVOKE_FIELD_RE.finditer(match.group("body")):
        key = field_match.group("key")
        try:
            value = json.loads(f'"{field_match.group("value")}"')
        except ValueError:
            value = field_match.group("value")
        if "\n" in value:
            parts.append(f"**{key}**\n\n```\n{value}\n```")
        else:
            parts.append(f"- `{key}`: {value}")

    body_text = "\n\n".join(parts)
    return f"{preamble}\n\n{body_text}" if preamble else body_text


_TASK_NOTIFICATION_RE = re.compile(r"<task-notification>(.*?)</task-notification>", re.DOTALL)
_TAG_RE = re.compile(r"<([\w-]+)>(.*?)</\1>", re.DOTALL)
_RESULT_TRUNCATION_RE = re.compile(
    r"\n?\.\.\. \(truncated (\d+) chars?, full result in (.+?)\)\s*\Z", re.DOTALL
)


def _extract_tags(text: str) -> dict[str, str]:
    """Extracts a flat mapping of ``{tag_name: inner_text}`` for each
    top-level ``<tag>...</tag>`` pair found in *text*, in order of
    appearance (later duplicates win)."""
    return {m.group(1): m.group(2) for m in _TAG_RE.finditer(text)}


def _repair_truncated_json(text: str) -> str | None:
    """Best-effort repair of JSON *text* cut off mid-value: closes an
    unterminated string and any ``{``/``[`` left open at the point of
    truncation. Returns None if *text* doesn't look like a JSON object or
    array at all."""
    if not text or text[0] not in "{[":
        return None

    in_string = False
    escape = False
    stack: list[str] = []
    for ch in text:
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "{[":
            stack.append(ch)
        elif ch in "}]" and stack:
            stack.pop()

    closers = {"{": "}", "[": "]"}
    suffix = ('"' if in_string else "") + "".join(closers[c] for c in reversed(stack))
    return text + suffix


def _parse_json_maybe_truncated(text: str) -> tuple[Any, str | None]:
    """Parses *text* as JSON, tolerating the harness's own truncation marker
    (appended when a Workflow/Agent result is too large to inline) and the
    resulting mid-string/mid-structure cutoff.

    Returns the parsed value (``None`` if parsing fails outright) and an
    optional human-readable truncation note.
    """
    note = None
    match = _RESULT_TRUNCATION_RE.search(text)
    if match:
        chars, path = match.groups()
        note = f"harness output truncated at {int(chars):,} characters — full result in `{path}`"
        text = text[: match.start()]

    stripped = text.strip()
    try:
        return json.loads(stripped), note
    except ValueError:
        pass

    repaired = _repair_truncated_json(stripped)
    if repaired is not None:
        try:
            return json.loads(repaired), note
        except ValueError:
            pass
    return None, note


def _render_result_field(key: str, value: Any) -> str:
    if isinstance(value, str):
        if "\n" in value or len(value) > 150:
            return f"**{key}**\n\n```\n{value}\n```"
        return f"- **{key}**: {value}"
    if isinstance(value, dict | list):
        body = json.dumps(value, ensure_ascii=False, indent=2)
        return f"**{key}**\n\n```json\n{body}\n```"
    return f"- **{key}**: {json.dumps(value)}"


def render_task_notification_result(raw: str) -> str:
    """Decodes a task-notification's ``<result>`` payload (a JSON-stringified
    value, possibly truncated by the harness) into Markdown: a dict's
    top-level fields each get their own labelled subsection instead of being
    dumped as a single opaque JSON blob; any other JSON shape is
    pretty-printed whole; text that isn't JSON at all (or too badly
    truncated to repair) falls back to a plain code block."""
    value, note = _parse_json_maybe_truncated(raw)

    parts: list[str] = []
    if isinstance(value, dict) and value:
        parts.extend(_render_result_field(k, v) for k, v in value.items())
    elif value is not None:
        parts.append(f"```json\n{json.dumps(value, ensure_ascii=False, indent=2)}\n```")
    else:
        parts.append(f"```\n{raw.strip()}\n```")
    if note:
        parts.append(f"*({note})*")
    return "\n\n".join(parts)


def _format_duration_ms(ms: int) -> str:
    hours, remainder = divmod(ms // 1000, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m {seconds}s"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def _format_usage_line(usage: dict[str, str]) -> str | None:
    """Formats a task-notification's ``<usage>`` counters (agent/tool counts,
    token spend, wall-clock duration) as one compact human-readable line.
    Tolerates missing or non-numeric fields by simply omitting them, since
    this is a best-effort summary, not the source of truth for the data."""
    bits: list[str] = []
    try:
        if "agent_count" in usage and "agents_done" in usage:
            agents = f"{usage['agents_done']}/{usage['agent_count']} agents"
            details = []
            if "agents_error" in usage:
                details.append(f"{usage['agents_error']} errors")
            if "agents_skipped" in usage:
                details.append(f"{usage['agents_skipped']} skipped")
            if details:
                agents += f" ({', '.join(details)})"
            bits.append(agents)
        if "tool_uses" in usage:
            bits.append(f"{int(usage['tool_uses']):,} tool calls")
        if "subagent_tokens" in usage:
            bits.append(f"{int(usage['subagent_tokens']):,} subagent tokens")
        if "duration_ms" in usage:
            bits.append(_format_duration_ms(int(usage["duration_ms"])))
    except ValueError:
        return None
    return " · ".join(bits) if bits else None


def render_task_notification(text: str) -> str | None:
    """Formats a ``<task-notification>...</task-notification>`` block, which
    Claude Code's harness injects as a queued user message once a background
    Agent/Workflow tool call completes.

    Rendered through the generic text path, this shows up as a wall of
    escaped tags with an opaque, possibly truncated JSON ``<result>`` glued
    to its neighbours. This instead extracts the known fields into a
    compact header/metadata block and decodes ``<result>`` into one
    labelled subsection per top-level field (see
    ``render_task_notification_result``). Returns None if the text doesn't
    contain such a block, so the caller falls back to normal rendering.
    """
    match = _TASK_NOTIFICATION_RE.search(text)
    if not match:
        return None

    fields = _extract_tags(match.group(1))
    task_id = fields.get("task-id", "").strip()
    tool_use_id = fields.get("tool-use-id", "").strip()
    status = fields.get("status", "").strip()

    header = f"**Task notification** — `{task_id}`"
    if tool_use_id:
        header += f" (tool call `{tool_use_id}`)"
    if status:
        header += f" — status: **{status}**"
    parts = [header]

    summary = fields.get("summary", "").strip()
    if summary:
        parts.append(summary)

    meta: list[str] = []
    output_file = fields.get("output-file", "").strip()
    if output_file:
        meta.append(f"- **Output file**: `{output_file}`")
    usage_line = _format_usage_line(_extract_tags(fields.get("usage", "")))
    if usage_line:
        meta.append(f"- **Usage**: {usage_line}")
    if meta:
        parts.append("\n".join(meta))

    if "result" in fields:
        parts.append("**Result**")
        parts.append(render_task_notification_result(fields["result"]))

    if "diagnostics" in fields:
        diagnostics = fields["diagnostics"].strip()
        parts.append(f"**Diagnostics**\n\n```\n{diagnostics}\n```")

    return "\n\n".join(parts)


def render_user_text(text: str) -> str:
    """Renders freeform message text: a slash-command block, a trailing
    tool-invocation call, or a task-notification block each gets its
    dedicated formatting, everything else falls back to IDE-selection
    expansion."""
    slash_command = render_slash_command(text)
    if slash_command is not None:
        return slash_command
    invocation = render_tool_invocation(text)
    if invocation is not None:
        return invocation
    task_notification = render_task_notification(text)
    if task_notification is not None:
        return task_notification
    return replace_ide_selection(text)


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
    thousands of characters. Such values get their own labelled code block
    instead; the remaining scalar fields are kept together as compact JSON.
    """
    multiline = {k: v for k, v in input_.items() if isinstance(v, str) and "\n" in v}
    scalars = {k: v for k, v in input_.items() if k not in multiline}

    parts: list[str] = []
    if scalars:
        parts.append(f"```json\n{json.dumps(scalars, ensure_ascii=False, indent=2)}\n```")
    for key, value in multiline.items():
        parts.append(f"**{key}**\n\n```\n{value}\n```")
    return "\n\n".join(parts) if parts else "```json\n{}\n```"


def render_content_block(block: dict[str, Any]) -> str:
    btype = block.get("type")

    if btype == "text":
        return render_user_text(block.get("text", ""))

    if btype == "thinking":
        text = block.get("thinking", "")
        if not text.strip():
            return ""
        return f"**Thinking**\n\n{text}"

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
            blocks_text = [render_user_text(content)] if content.strip() else []
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
