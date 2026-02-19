#!/usr/bin/env bash
# PostToolUse hook: auto-format files after Edit/Write
set -euo pipefail

INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')

# Nothing to do if no file path
[[ -z "$FILE_PATH" ]] && exit 0

# Only format files inside the project
[[ "$FILE_PATH" != "$CLAUDE_PROJECT_DIR"/* ]] && exit 0

# Skip non-existent files (e.g. after a failed write)
[[ ! -f "$FILE_PATH" ]] && exit 0

case "$FILE_PATH" in
    *.py)
        uv run ruff check --fix --quiet "$FILE_PATH" 2>/dev/null || true
        uv run ruff format --quiet "$FILE_PATH" 2>/dev/null
        ;;
    *.md)
        uv run mdformat "$FILE_PATH" 2>/dev/null
        ;;
esac
