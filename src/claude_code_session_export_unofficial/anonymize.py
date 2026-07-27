"""Anonymization of workspace paths and the system username in copied
session content.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from .discovery import path_to_project_slug
from .markdown import escape_html_outside_code


def build_path_variants(path: Path) -> list[str]:
    """Builds every form under which an absolute path (e.g. the workspace)
    can appear in Claude Code files (raw, JSON-escaped, forward-slash,
    project-folder slug, Git Bash/MSYS-style mount "/c/Users/..."), for both
    possible drive-letter cases on Windows. Sorted by descending length so
    that the longest forms (e.g. the escaped path) are replaced before the
    shorter forms they contain (e.g. the raw path).
    """
    resolved = str(path.resolve())
    bases = {resolved}
    if len(resolved) >= 2 and resolved[1] == ":":
        bases.add(resolved[0].swapcase() + resolved[1:])

    variants: set[str] = set()
    for base in bases:
        variants.add(base)
        variants.add(base.replace("\\", "/"))
        variants.add(base.replace("\\", "\\\\"))
        variants.add(path_to_project_slug(base))
        if len(base) >= 2 and base[1] == ":":
            drive = base[0].lower()
            rest = base[2:].replace("\\", "/")
            variants.add(f"/{drive}{rest}")
    variants.discard("")
    return sorted(variants, key=len, reverse=True)


class Anonymizer:
    """Replaces in text:
    - the workspace's absolute path, in all its forms (raw, JSON-escaped,
      forward-slash, slug, Git Bash mount), with "<workspace>";
    - the system username (derived from ~/.claude), replaced with
      "<username>" everywhere it appears verbatim in the text (not just in
      a path: e.g. in command output like `ls -la`, which shows the file's
      owner).
    """

    labels: list[tuple[str, str]]
    replacements: list[tuple[str, str]]

    def __init__(self, workspace: Path, username: str) -> None:
        self.labels = [
            (str(workspace.resolve()), "<workspace>"),
            (username, "<username>"),
        ]
        entries: list[tuple[str, str]] = []
        for variant in build_path_variants(workspace):
            entries.append((variant, "<workspace>"))
        if username:
            entries.append((username, "<username>"))
        # Global descending length: the most specific forms (e.g. the
        # escaped path, or whichever target is longest) are applied first,
        # so that a short token (e.g. the username) doesn't fragment a
        # longer path already replaced in one go.
        entries.sort(key=lambda pair: len(pair[0]), reverse=True)
        self.replacements = entries

    def apply(self, text: str) -> str:
        for variant, replacement in self.replacements:
            if variant in text:
                text = text.replace(variant, replacement)
        return text

    def apply_markdown(self, text: str) -> str:
        """Like apply(), for text destined to be rendered as Markdown:
        additionally escapes the HTML-like tags introduced by the
        placeholders (<workspace>, <username>) outside of code
        blocks/spans."""
        return escape_html_outside_code(self.apply(text))

    def copy_file(self, src: Path, dst: Path) -> None:
        """Copies src to dst, anonymizing the content if it's text,
        otherwise a raw copy (binary file)."""
        try:
            text = src.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            shutil.copy2(src, dst)
            return
        dst.write_text(self.apply(text), encoding="utf-8")

    def copy_markdown_file(self, src: Path, dst: Path) -> None:
        """Like copy_file(), for a .md file (with extra HTML escaping)."""
        try:
            text = src.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            shutil.copy2(src, dst)
            return
        dst.write_text(self.apply_markdown(text), encoding="utf-8")
