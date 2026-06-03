"""Typed models for SSH profiles and parser output."""

from __future__ import annotations

from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class RouteKind(str, Enum):
    """High-level network route classification."""

    DIRECT_PUBLIC = "direct_public"
    DIRECT_PRIVATE = "direct_private"
    DIRECT_TAILSCALE = "direct_tailscale"
    DIRECT_NAMED = "direct_named"
    PROXIED = "proxied"

    @property
    def label(self) -> str:
        labels = {
            RouteKind.DIRECT_PUBLIC: "direct public",
            RouteKind.DIRECT_PRIVATE: "direct local private network",
            RouteKind.DIRECT_TAILSCALE: "direct tailscale",
            RouteKind.DIRECT_NAMED: "direct named/unclassified",
            RouteKind.PROXIED: "proxied",
        }
        return labels[self]


class SourceLocation(BaseModel):
    """A config file location."""

    model_config = ConfigDict(frozen=True)

    path: Path
    line: int

    def display(self) -> str:
        return f"{self.path}:{self.line}"


class SshProfile(BaseModel):
    """A concrete, connectable SSH Host alias."""

    model_config = ConfigDict(frozen=True)

    alias: str
    user: str | None = None
    hostname: str | None = None
    port: str | None = None
    identity_files: tuple[str, ...] = Field(default_factory=tuple)
    proxy_jump: str | None = None
    proxy_command: str | None = None
    source: SourceLocation
    route_kind: RouteKind = RouteKind.DIRECT_NAMED

    @property
    def target_host(self) -> str:
        return self.hostname or self.alias

    @property
    def target_key(self) -> str:
        port = self.port or "22"
        return f"{self.target_host.lower()}:{port}"

    @property
    def display_user(self) -> str:
        return self.user or "default local user"

    @property
    def is_proxied(self) -> bool:
        return bool(self.proxy_jump or self.proxy_command)

    def search_text(self) -> str:
        parts = [
            self.alias,
            self.display_user,
            self.target_host,
            self.port or "",
            self.route_kind.label,
            self.proxy_jump or "",
            self.proxy_command or "",
            " ".join(self.identity_files),
        ]
        return " ".join(parts).lower()


class ParseWarning(BaseModel):
    """A non-fatal parser warning to show to the operator."""

    model_config = ConfigDict(frozen=True)

    message: str
    source: SourceLocation | None = None

    def display(self) -> str:
        if self.source is None:
            return self.message
        return f"{self.source.display()}: {self.message}"


class SshInventory(BaseModel):
    """Discovered SSH profiles plus parser warnings."""

    model_config = ConfigDict(frozen=True)

    config_path: Path
    profiles: tuple[SshProfile, ...] = Field(default_factory=tuple)
    warnings: tuple[ParseWarning, ...] = Field(default_factory=tuple)

    def alternatives_for(self, profile: SshProfile) -> tuple[SshProfile, ...]:
        return tuple(
            candidate
            for candidate in self.profiles
            if candidate.alias != profile.alias and candidate.target_key == profile.target_key
        )

    def connection_groups(self) -> tuple["SshConnectionGroup", ...]:
        """Group profiles that target the same visible server and port."""
        grouped_profiles: dict[str, list[SshProfile]] = {}
        for profile in self.profiles:
            grouped_profiles.setdefault(profile.target_key, []).append(profile)

        groups = [
            SshConnectionGroup(
                target_key=target_key,
                target_host=profiles[0].target_host,
                port=profiles[0].port or "22",
                profiles=tuple(profiles),
            )
            for target_key, profiles in grouped_profiles.items()
        ]
        return tuple(
            sorted(
                groups,
                key=lambda group: (
                    group.target_host.lower(),
                    group.port.zfill(8) if group.port.isdigit() else group.port,
                ),
            )
        )


class SshConnectionGroup(BaseModel):
    """Profiles that connect to the same visible server target."""

    model_config = ConfigDict(frozen=True)

    target_key: str
    target_host: str
    port: str = "22"
    profiles: tuple[SshProfile, ...] = Field(default_factory=tuple)

    @property
    def default_profile(self) -> SshProfile:
        """Return the first discovered profile for model-level display helpers."""
        if not self.profiles:
            raise ValueError("Connection group has no profiles.")
        return self.profiles[0]

    @property
    def selection_profiles(self) -> tuple[SshProfile, ...]:
        """Return profiles with the default profile first for the user picker."""
        default_profile = self.default_profile
        return (default_profile,) + tuple(
            profile for profile in self.profiles if profile.alias != default_profile.alias
        )

    @property
    def display_user(self) -> str:
        return self.default_profile.display_user

    @property
    def display_name(self) -> str:
        return self.default_profile.alias

    @property
    def target_display_name(self) -> str:
        if self.port == "22":
            return self.target_host
        return f"{self.target_host}:{self.port}"

    @property
    def profile_count(self) -> int:
        return len(self.profiles)

    def search_text(self) -> str:
        profile_text = " ".join(profile.search_text() for profile in self.profiles)
        return f"{self.display_name} {self.target_key} {profile_text}".lower()


class SshRunResult(BaseModel):
    """Result of one foreground ssh process run."""

    model_config = ConfigDict(frozen=True)

    alias: str
    command: tuple[str, ...]
    exit_code: int
    term_string: str
    error_message: str | None = None
