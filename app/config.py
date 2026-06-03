"""Application configuration loader."""

from __future__ import annotations

import os
import sys
import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AppConfig(BaseModel):
    """Typed application settings loaded from config.toml."""

    model_config = ConfigDict(frozen=True)

    ssh_config_path: str = Field(default="~/.ssh/config")
    term_string: str = Field(default="xterm-256color", min_length=1, pattern=r"^[A-Za-z0-9_.+-]+$")
    preferred_users: tuple[str, ...] = Field(default=("root",))

    @field_validator("preferred_users", mode="before")
    @classmethod
    def validate_preferred_users(cls, value: object) -> tuple[str, ...]:
        """Normalize the preferred SSH users list from config.toml."""
        if isinstance(value, str):
            raw_users = (value,)
        elif isinstance(value, (list, tuple)):
            raw_users = tuple(value)
        else:
            raise ValueError("preferred_users must be a string array.")

        normalized_users: list[str] = []
        for raw_user in raw_users:
            if not isinstance(raw_user, str):
                raise ValueError("preferred_users entries must be strings.")
            user = raw_user.strip()
            if not user:
                raise ValueError("preferred_users entries must not be empty.")
            if user not in normalized_users:
                normalized_users.append(user)
        return tuple(normalized_users)

    @classmethod
    def load(cls, path: Path | None = None) -> "AppConfig":
        """Load config.toml without silently substituting a missing file."""
        config_path = (path or default_config_path()).expanduser()
        if not config_path.exists():
            raise FileNotFoundError(
                f"Config file not found: {config_path}. "
                "Create config.toml or pass --app-config explicitly."
            )
        with config_path.open("rb") as config_file:
            raw_config: dict[str, Any] = tomllib.load(config_file)
        return cls.model_validate(raw_config)

    @classmethod
    def create_default(cls, path: Path | None = None) -> "AppConfig":
        """Create and save a default config.toml for first-run tool installs."""
        config = cls()
        config.save(path)
        return config

    def expanded_ssh_config_path(self) -> Path:
        """Return the configured SSH config path with ~ expanded."""
        return Path(self.ssh_config_path).expanduser()

    def save(self, path: Path | None = None) -> None:
        """Save the typed config to config.toml."""
        config_path = (path or default_config_path()).expanduser()
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            "\n".join(
                (
                    f"ssh_config_path = {_toml_string(self.ssh_config_path)}",
                    f"term_string = {_toml_string(self.term_string)}",
                    f"preferred_users = {_toml_array(self.preferred_users)}",
                    "",
                )
            ),
            encoding="utf-8",
        )


def default_config_path() -> Path:
    """Return the user-level ssh-starter config.toml path."""
    return default_config_dir() / "config.toml"


def default_config_dir() -> Path:
    """Return the platform-appropriate user config directory for ssh-starter."""
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata).expanduser() / "ssh-starter"
        return Path.home() / "AppData" / "Roaming" / "ssh-starter"

    xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
    if xdg_config_home:
        return Path(xdg_config_home).expanduser() / "ssh-starter"
    return Path.home() / ".config" / "ssh-starter"


def _toml_string(value: str) -> str:
    """Serialize a simple TOML string without adding a new dependency."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _toml_array(values: tuple[str, ...]) -> str:
    """Serialize a simple TOML string array without adding a new dependency."""
    return "[" + ", ".join(_toml_string(value) for value in values) + "]"
