from __future__ import annotations

import sys
from pathlib import Path

import pytest

from claude_code_session_export_unofficial.anonymize import Anonymizer, build_path_variants
from claude_code_session_export_unofficial.discovery import path_to_project_slug


def test_build_path_variants_includes_forward_slash_escaped_and_slug_forms(
    tmp_path: Path,
) -> None:
    variants = build_path_variants(tmp_path)
    resolved = str(tmp_path.resolve())

    assert resolved in variants
    assert resolved.replace("\\", "/") in variants
    assert resolved.replace("\\", "\\\\") in variants
    assert path_to_project_slug(resolved) in variants
    assert variants == sorted(variants, key=len, reverse=True)


@pytest.mark.skipif(sys.platform != "win32", reason="drive-letter forms are Windows-specific")
def test_build_path_variants_drive_letter_case_swap_and_git_bash_mount(tmp_path: Path) -> None:
    resolved = str(tmp_path.resolve())
    variants = build_path_variants(tmp_path)

    swapped = resolved[0].swapcase() + resolved[1:]
    assert swapped in variants

    drive = resolved[0].lower()
    rest = resolved[2:].replace("\\", "/")
    assert f"/{drive}{rest}" in variants


def test_anonymizer_apply_replaces_workspace_and_username(tmp_path: Path) -> None:
    workspace = tmp_path / "proj"
    workspace.mkdir()
    anonymizer = Anonymizer(workspace=workspace, username="alice")
    resolved = str(workspace.resolve())

    result = anonymizer.apply(f"path={resolved} owner=alice")

    assert "<workspace>" in result
    assert "<username>" in result
    assert resolved not in result
    assert "alice" not in result


def test_anonymizer_apply_markdown_escapes_outside_code_but_not_inside(tmp_path: Path) -> None:
    workspace = tmp_path
    anonymizer = Anonymizer(workspace=workspace, username="bob")
    resolved = str(workspace.resolve())
    text = f"Free text with {resolved}\n\n```\n{resolved}\n```"

    result = anonymizer.apply_markdown(text)

    assert "Free text with &lt;workspace&gt;" in result
    assert "```\n<workspace>\n```" in result


def test_anonymizer_copy_file_anonymizes_text_and_passes_through_binary(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    anonymizer = Anonymizer(workspace=workspace, username="carol")

    src_text = tmp_path / "src.txt"
    src_text.write_text(f"owner carol at {workspace.resolve()}", encoding="utf-8")
    dst_text = tmp_path / "dst.txt"
    anonymizer.copy_file(src_text, dst_text)
    content = dst_text.read_text(encoding="utf-8")
    assert "<username>" in content
    assert "<workspace>" in content

    src_bin = tmp_path / "src.bin"
    src_bin.write_bytes(bytes([0xFF, 0xFE, 0x00, 0x01]))
    dst_bin = tmp_path / "dst.bin"
    anonymizer.copy_file(src_bin, dst_bin)
    assert dst_bin.read_bytes() == src_bin.read_bytes()


def test_anonymizer_copy_markdown_file_escapes_placeholders(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    anonymizer = Anonymizer(workspace=workspace, username="dave")

    src = tmp_path / "plan.md"
    src.write_text(f"Workspace path: {workspace.resolve()}", encoding="utf-8")
    dst = tmp_path / "plan-out.md"
    anonymizer.copy_markdown_file(src, dst)

    content = dst.read_text(encoding="utf-8")
    assert "&lt;workspace&gt;" in content
