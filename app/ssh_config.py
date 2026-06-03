"""OpenSSH config discovery for ssh-starter."""

from __future__ import annotations

import fnmatch
import glob
import os
import shlex
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models import ParseWarning, SourceLocation, SshInventory, SshProfile
from app.routing import classify_route

StatementKind = Literal["host", "match", "option"]


class ConfigStatement(BaseModel):
    """One parsed SSH config statement."""

    model_config = ConfigDict(frozen=True)

    kind: StatementKind
    source: SourceLocation
    args: tuple[str, ...] = Field(default_factory=tuple)
    key: str | None = None
    value: str | None = None


class ParsedStatements(BaseModel):
    """Statements and warnings collected from SSH config files."""

    model_config = ConfigDict(frozen=True)

    statements: tuple[ConfigStatement, ...] = Field(default_factory=tuple)
    warnings: tuple[ParseWarning, ...] = Field(default_factory=tuple)


class HostAlias(BaseModel):
    """A concrete Host alias declared in a Host block."""

    model_config = ConfigDict(frozen=True)

    alias: str
    source: SourceLocation


class EffectiveOptions(BaseModel):
    """Effective options for one concrete alias."""

    model_config = ConfigDict(frozen=True)

    user: str | None = None
    hostname: str | None = None
    port: str | None = None
    identity_files: tuple[str, ...] = Field(default_factory=tuple)
    proxy_jump: str | None = None
    proxy_command: str | None = None


FIRST_VALUE_KEYS = {
    "user",
    "hostname",
    "port",
    "proxyjump",
    "proxycommand",
}

WILDCARD_MARKERS = ("*", "?", "[")


def load_inventory(config_path: Path) -> SshInventory:
    """Load concrete SSH profiles from an OpenSSH config file."""
    expanded_path = config_path.expanduser().resolve()
    parsed = _parse_config_file(expanded_path, seen=tuple())

    aliases = _discover_aliases(parsed.statements)
    profiles: list[SshProfile] = []
    for host_alias in aliases:
        options = _effective_options(parsed.statements, host_alias.alias)
        route_kind = classify_route(
            alias=host_alias.alias,
            hostname=options.hostname,
            proxy_jump=options.proxy_jump,
            proxy_command=options.proxy_command,
        )
        profiles.append(
            SshProfile(
                alias=host_alias.alias,
                user=options.user,
                hostname=options.hostname,
                port=options.port,
                identity_files=options.identity_files,
                proxy_jump=options.proxy_jump,
                proxy_command=options.proxy_command,
                source=host_alias.source,
                route_kind=route_kind,
            )
        )

    return SshInventory(
        config_path=expanded_path,
        profiles=tuple(sorted(profiles, key=lambda profile: profile.alias.lower())),
        warnings=parsed.warnings,
    )


def _parse_config_file(path: Path, seen: tuple[Path, ...]) -> ParsedStatements:
    if path.name == ".env":
        return ParsedStatements(
            warnings=(ParseWarning(message="Skipped .env file referenced by SSH Include."),)
        )

    if path in seen:
        return ParsedStatements(
            warnings=(ParseWarning(message=f"Skipped recursive Include: {path}"),)
        )

    if not path.exists():
        return ParsedStatements(
            warnings=(ParseWarning(message=f"SSH config file not found: {path}"),)
        )

    statements: list[ConfigStatement] = []
    warnings: list[ParseWarning] = []

    with path.open("r", encoding="utf-8") as config_file:
        for line_number, raw_line in enumerate(config_file, start=1):
            source = SourceLocation(path=path, line=line_number)
            try:
                tokens = shlex.split(raw_line, comments=True, posix=True)
            except ValueError as exc:
                warnings.append(ParseWarning(message=f"Could not parse line: {exc}", source=source))
                continue

            if not tokens:
                continue

            directive = tokens[0].lower()
            args = tuple(tokens[1:])

            if directive == "include":
                included = _parse_includes(path.parent, args, seen + (path,), source)
                statements.extend(included.statements)
                warnings.extend(included.warnings)
                continue

            if directive == "host":
                statements.append(ConfigStatement(kind="host", source=source, args=args))
                continue

            if directive == "match":
                warnings.append(
                    ParseWarning(
                        message="Match blocks are not evaluated; options in the block are ignored.",
                        source=source,
                    )
                )
                statements.append(ConfigStatement(kind="match", source=source, args=args))
                continue

            statements.append(
                ConfigStatement(
                    kind="option",
                    source=source,
                    key=directive,
                    value=" ".join(args),
                    args=args,
                )
            )

    return ParsedStatements(statements=tuple(statements), warnings=tuple(warnings))


def _parse_includes(
    base_dir: Path,
    include_patterns: tuple[str, ...],
    seen: tuple[Path, ...],
    source: SourceLocation,
) -> ParsedStatements:
    statements: list[ConfigStatement] = []
    warnings: list[ParseWarning] = []

    for include_pattern in include_patterns:
        pattern_path = Path(include_pattern).expanduser()
        if not pattern_path.is_absolute():
            pattern_path = base_dir / pattern_path

        matches = [Path(match).resolve() for match in glob.glob(os.fspath(pattern_path))]
        if not matches:
            warnings.append(ParseWarning(message=f"Include matched no files: {include_pattern}", source=source))
            continue

        for include_path in sorted(matches):
            if include_path.name == ".env":
                warnings.append(ParseWarning(message=f"Skipped .env Include: {include_path}", source=source))
                continue
            parsed = _parse_config_file(include_path, seen=seen)
            statements.extend(parsed.statements)
            warnings.extend(parsed.warnings)

    return ParsedStatements(statements=tuple(statements), warnings=tuple(warnings))


def _discover_aliases(statements: tuple[ConfigStatement, ...]) -> tuple[HostAlias, ...]:
    discovered: dict[str, HostAlias] = {}
    for statement in statements:
        if statement.kind != "host":
            continue
        for pattern in statement.args:
            if pattern.startswith("!") or _contains_wildcard(pattern):
                continue
            discovered.setdefault(pattern, HostAlias(alias=pattern, source=statement.source))
    return tuple(discovered.values())


def _effective_options(statements: tuple[ConfigStatement, ...], alias: str) -> EffectiveOptions:
    active = True
    values: dict[str, str] = {}
    identity_files: list[str] = []

    for statement in statements:
        if statement.kind == "host":
            active = _host_patterns_match(alias, statement.args)
            continue
        if statement.kind == "match":
            active = False
            continue
        if not active or statement.key is None:
            continue

        key = statement.key.lower()
        if key == "identityfile" and statement.value:
            identity = _expand_identity_file(statement.value, alias, values)
            if identity not in identity_files:
                identity_files.append(identity)
            continue

        if key in FIRST_VALUE_KEYS and statement.value and key not in values:
            values[key] = statement.value

    return EffectiveOptions(
        user=values.get("user"),
        hostname=values.get("hostname"),
        port=values.get("port"),
        identity_files=tuple(identity_files),
        proxy_jump=values.get("proxyjump"),
        proxy_command=values.get("proxycommand"),
    )


def _host_patterns_match(alias: str, patterns: tuple[str, ...]) -> bool:
    matched_positive = False
    for pattern in patterns:
        is_negated = pattern.startswith("!")
        host_pattern = pattern[1:] if is_negated else pattern
        if fnmatch.fnmatchcase(alias, host_pattern):
            if is_negated:
                return False
            matched_positive = True
    return matched_positive


def _contains_wildcard(pattern: str) -> bool:
    return any(marker in pattern for marker in WILDCARD_MARKERS)


def _expand_identity_file(value: str, alias: str, values: dict[str, str]) -> str:
    hostname = values.get("hostname", alias)
    user = values.get("user", "")
    port = values.get("port", "22")
    expanded = value.replace("%%", "\0")
    expanded = expanded.replace("%h", hostname).replace("%n", alias).replace("%r", user).replace("%p", port)
    expanded = expanded.replace("\0", "%")
    return os.path.expanduser(expanded)
