"""Persistent connection history for sorting SSH profiles."""

from __future__ import annotations

import os
import sys
import time
import tomllib
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ConnectionHistory(BaseModel):
    """Last connection timestamps keyed by SSH alias."""

    model_config = ConfigDict(frozen=True)

    last_connected: dict[str, float] = Field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | None = None) -> "ConnectionHistory":
        """Load history from disk; a missing history file means no connections yet."""
        history_path = path or default_history_path()
        if not history_path.exists():
            return cls()
        with history_path.open("rb") as history_file:
            raw_history: dict[str, Any] = tomllib.load(history_file)
        return cls.model_validate(raw_history)

    def mark_connected(self, alias: str, *, connected_at: float | None = None) -> "ConnectionHistory":
        """Return updated immutable history with a new connection timestamp."""
        updated = dict(self.last_connected)
        updated[alias] = connected_at if connected_at is not None else time.time()
        return self.model_copy(update={"last_connected": updated})

    def save(self, path: Path | None = None) -> None:
        """Persist history to disk."""
        history_path = path or default_history_path()
        history_path.parent.mkdir(parents=True, exist_ok=True)
        lines = ["[last_connected]"]
        for alias, timestamp in sorted(self.last_connected.items()):
            lines.append(f"{_toml_string(alias)} = {timestamp:.6f}")
        lines.append("")
        history_path.write_text("\n".join(lines), encoding="utf-8")

    def timestamp_for(self, alias: str) -> float | None:
        """Return the last connection timestamp for an alias if known."""
        return self.last_connected.get(alias)

    def local_display_for(self, alias: str) -> str:
        """Return a local-time display string for an alias timestamp."""
        timestamp = self.timestamp_for(alias)
        if timestamp is None:
            return "never"
        local_dt = datetime.fromtimestamp(timestamp).astimezone()
        timezone_name = local_dt.tzname() or "local"
        return f"{local_dt:%Y-%m-%d %H:%M:%S} {timezone_name}"


def default_history_path() -> Path:
    """Return the platform-appropriate user state file path for ssh-starter history."""
    if sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            return Path(local_app_data).expanduser() / "ssh-starter" / "history.toml"
        return Path.home() / "AppData" / "Local" / "ssh-starter" / "history.toml"

    state_home = os.environ.get("XDG_STATE_HOME")
    if state_home:
        return Path(state_home).expanduser() / "ssh-starter" / "history.toml"
    return Path.home() / ".local" / "state" / "ssh-starter" / "history.toml"


def _toml_string(value: str) -> str:
    """Serialize a simple TOML string without adding a new dependency."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
