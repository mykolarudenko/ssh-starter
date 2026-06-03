#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_DIR="$HOME/.local/bin"
TARGET_LINK="$TARGET_DIR/gossh"

mkdir -p "$TARGET_DIR"
ln -sfn "$PROJECT_ROOT/run-app.sh" "$TARGET_LINK"

echo "Installed gossh symlink: $TARGET_LINK -> $PROJECT_ROOT/run-app.sh"
echo "If gossh does not start, run: $PROJECT_ROOT/setup.sh"
