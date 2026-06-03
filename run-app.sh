#!/usr/bin/env bash
set -euo pipefail

SCRIPT_PATH="${BASH_SOURCE[0]}"
while [ -L "$SCRIPT_PATH" ]; do
  SCRIPT_DIR="$(cd -P "$(dirname "$SCRIPT_PATH")" && pwd)"
  LINK_TARGET="$(readlink "$SCRIPT_PATH")"
  if [[ "$LINK_TARGET" == /* ]]; then
    SCRIPT_PATH="$LINK_TARGET"
  else
    SCRIPT_PATH="$SCRIPT_DIR/$LINK_TARGET"
  fi
done

PROJECT_ROOT="$(cd -P "$(dirname "$SCRIPT_PATH")" && pwd)"

if [ ! -d "$PROJECT_ROOT/.venv" ]; then
  echo "gossh error: virtual environment is missing. Run $PROJECT_ROOT/setup.sh first." >&2
  exit 2
fi

exec uv run --project "$PROJECT_ROOT" --no-sync python -m app --app-config "$PROJECT_ROOT/config.toml" "$@"
