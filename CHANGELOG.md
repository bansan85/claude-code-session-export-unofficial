# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-07-27

### Added

- Initial packaged release of the Claude Code session export tool.
- `claude-code-session-export-unofficial` console-script entry point.
- Systematic anonymization of workspace paths (all path-variant forms) and the system username.
- Markdown conversion of session transcripts, including IDE-selection attachment rendering and Edit-tool diff rendering.
- pytest test suite covering anonymization, Markdown rendering, project/session discovery, and export orchestration.

[0.1.0]: https://github.com/bansan85/claude-code-session-export-unofficial/releases/tag/v0.1.0
