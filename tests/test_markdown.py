from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from claude_code_session_export_unofficial.markdown import (
    _guess_lang,
    escape_html_outside_code,
    fmt_ts,
    render_content_block,
    render_edit_diff,
    render_ide_selection,
    render_tool_input,
    replace_ide_selection,
    session_to_markdown,
    stringify_tool_result_content,
)


def test_escape_html_outside_code_preserves_fences_and_inline_code() -> None:
    text = "<workspace> is here\n```\n<workspace>\n```\nand `<username>` inline"
    result = escape_html_outside_code(text)
    assert "&lt;workspace&gt; is here" in result
    assert "```\n<workspace>\n```" in result
    assert "`<username>`" in result


def test_escape_html_outside_code_preserves_details_and_blockquote_prefix() -> None:
    text = "<details>\n<summary>Thinking</summary>\n\n> quoted <tag>\n\n</details>"
    result = escape_html_outside_code(text)
    assert "<details>" in result
    assert "</details>" in result
    assert "> quoted &lt;tag&gt;" in result


def test_guess_lang_known_and_unknown_extensions() -> None:
    assert _guess_lang("foo.py") == "python"
    assert _guess_lang("foo.unknownext") == ""


def test_render_ide_selection_known_template_range() -> None:
    inner = (
        "The user selected lines 3 to 5 from src/app.py:\ncode line\n"
        "This may or may not be related to the current task."
    )
    result = render_ide_selection(inner)
    assert "**Attachment**" in result
    assert "`src/app.py` — lines 3 to 5" in result
    assert "```python" in result
    assert "code line" in result


def test_render_ide_selection_known_template_single_line() -> None:
    inner = "The user selected line 7 from notes.md:\nsome text"
    result = render_ide_selection(inner)
    assert "line 7" in result
    assert "lines" not in result.split("`notes.md`")[1].split("\n")[0]


def test_render_ide_selection_generic_fallback() -> None:
    inner = "unstructured content"
    assert render_ide_selection(inner) == "**Attachment**\n\nunstructured content"


def test_replace_ide_selection_embeds_result_in_surrounding_text() -> None:
    text = "before <ide_selection>The user selected line 1 from a.py:\nx = 1</ide_selection> after"
    result = replace_ide_selection(text)
    assert result.startswith("before **Attachment**")
    assert result.endswith("after")


def test_render_edit_diff_produces_unified_diff() -> None:
    diff = render_edit_diff({"file_path": "a.py", "old_string": "a\nb", "new_string": "a\nc"})
    assert "--- a.py" in diff
    assert "+++ a.py" in diff
    assert "-b" in diff
    assert "+c" in diff


def test_render_edit_diff_identical_strings_returns_placeholder() -> None:
    diff = render_edit_diff({"file_path": "a.py", "old_string": "same", "new_string": "same"})
    assert diff == "# no textual difference detected for a.py"


def test_render_content_block_tool_result_error_and_result() -> None:
    err = render_content_block({"type": "tool_result", "content": "boom", "is_error": True})
    assert err.startswith("**Error:**")
    ok = render_content_block({"type": "tool_result", "content": "fine"})
    assert ok.startswith("**Result:**")


def test_render_content_block_thinking_empty_and_nonempty() -> None:
    assert render_content_block({"type": "thinking", "thinking": "   "}) == ""
    thinking = render_content_block({"type": "thinking", "thinking": "pondering"})
    assert "<summary>Thinking</summary>" in thinking
    assert "pondering" in thinking


def test_render_content_block_image_and_unknown() -> None:
    assert render_content_block({"type": "image"}) == "*[attached image]*"
    assert render_content_block({"type": "mystery"}) == "*[unhandled block: mystery]*"


def test_render_content_block_tool_use_shell_command() -> None:
    block = render_content_block(
        {"type": "tool_use", "name": "Bash", "input": {"command": "ls -la"}}
    )
    assert "**Tool call: `Bash`**" in block
    assert "```bash\nls -la\n```" in block


def test_render_content_block_tool_use_powershell_command() -> None:
    block = render_content_block(
        {"type": "tool_use", "name": "PowerShell", "input": {"command": "Get-ChildItem"}}
    )
    assert "```powershell\nGet-ChildItem\n```" in block


def test_render_content_block_tool_use_edit_produces_diff() -> None:
    block = render_content_block(
        {
            "type": "tool_use",
            "name": "Edit",
            "input": {"file_path": "a.py", "old_string": "a", "new_string": "b"},
        }
    )
    assert "```diff" in block


def test_render_content_block_tool_use_generic_json() -> None:
    block = render_content_block(
        {
            "type": "tool_use",
            "name": "Glob",
            "input": {"pattern": "*.py", "description": "find files"},
        }
    )
    assert "**Tool call: `Glob`** — find files" in block
    assert '"pattern": "*.py"' in block
    assert "description" not in block.split("```json")[1]


def test_render_content_block_tool_use_multiline_field_uses_collapsible_block() -> None:
    script = "export const meta = {\n  name: 'x',\n}\n"
    block = render_content_block(
        {
            "type": "tool_use",
            "name": "Workflow",
            "input": {"script": script, "description": "run it"},
        }
    )
    assert "**Tool call: `Workflow`** — run it" in block
    assert "<details>\n<summary>script</summary>" in block
    assert script in block
    # The raw script must not also appear JSON-escaped on a single line.
    assert "\\n" not in block


def test_render_tool_input_empty_dict_renders_empty_json() -> None:
    assert render_tool_input({}) == "```json\n{}\n```"


def test_render_tool_input_mixes_scalars_and_multiline_fields() -> None:
    rendered = render_tool_input({"pattern": "*.py", "prompt": "line one\nline two"})
    assert '```json\n{\n  "pattern": "*.py"\n}\n```' in rendered
    assert "<details>\n<summary>prompt</summary>\n\n```\nline one\nline two\n```\n\n</details>" in (
        rendered
    )


def test_stringify_tool_result_content_variants() -> None:
    assert stringify_tool_result_content("plain") == "plain"
    assert (
        stringify_tool_result_content([{"type": "text", "text": "hi"}, {"type": "image"}])
        == "hi\n[image]"
    )
    assert stringify_tool_result_content({"other": 1}) == '{"other": 1}'


def test_fmt_ts_none_empty_invalid_and_valid() -> None:
    assert fmt_ts(None) == ""
    assert fmt_ts("") == ""
    assert fmt_ts("not-a-date") == "not-a-date"
    formatted = fmt_ts("2026-01-01T10:00:00Z")
    assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$", formatted)


def test_session_to_markdown_includes_header_and_roles(tmp_path: Path) -> None:
    events: list[dict[str, Any]] = [
        {
            "type": "ai-title",
            "aiTitle": "My Session",
            "cwd": "/tmp/proj",
            "version": "1.0",
            "gitBranch": "main",
            "timestamp": "2026-01-01T10:00:00Z",
        },
        {
            "type": "user",
            "timestamp": "2026-01-01T10:00:01Z",
            "message": {"content": "Hello"},
        },
        {
            "type": "assistant",
            "timestamp": "2026-01-01T10:00:02Z",
            "message": {"content": [{"type": "text", "text": "Hi there"}]},
        },
    ]

    md = session_to_markdown(events, "sess-1", tmp_path)

    assert "# Claude Session — My Session" in md
    assert "**Session ID**: `sess-1`" in md
    assert "**Claude Code version**: 1.0" in md
    assert "**Git branch**: main" in md
    assert "## User" in md
    assert "## Assistant" in md
    assert "Hello" in md
    assert "Hi there" in md
