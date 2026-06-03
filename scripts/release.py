#!/usr/bin/env python3
"""Create a version bump commit and tag for a PyPI release.

PyPI publishing is handled by the GitHub Actions workflow that runs on v* tags.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT_PATH = PROJECT_ROOT / "pyproject.toml"
INIT_PATH = PROJECT_ROOT / "app" / "__init__.py"
VERSION_PATTERN = re.compile(r'(?m)^version = "(?P<version>\d+\.\d+\.\d+)"$')
INIT_VERSION_PATTERN = re.compile(r'(?m)^__version__ = "(?P<version>\d+\.\d+\.\d+)"$')


@dataclass(frozen=True)
class Version:
    """A simple semantic version without pre-release suffixes."""

    major: int
    minor: int
    patch: int

    @classmethod
    def parse(cls, raw_version: str) -> "Version":
        """Parse a major.minor.patch version string."""
        parts = raw_version.split(".")
        if len(parts) != 3 or not all(part.isdigit() for part in parts):
            raise ValueError(f"Unsupported version format: {raw_version!r}")
        return cls(major=int(parts[0]), minor=int(parts[1]), patch=int(parts[2]))

    def bumped(self, part: str) -> "Version":
        """Return the next version for the requested bump part."""
        if part == "major":
            return Version(self.major + 1, 0, 0)
        if part == "minor":
            return Version(self.major, self.minor + 1, 0)
        if part == "patch":
            return Version(self.major, self.minor, self.patch + 1)
        raise ValueError(f"Unsupported bump part: {part}")

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


def build_parser() -> argparse.ArgumentParser:
    """Build command-line arguments for the release helper."""
    parser = argparse.ArgumentParser(description="Bump version, build, commit, tag, and push a release.")
    bump_group = parser.add_mutually_exclusive_group(required=True)
    bump_group.add_argument("--major", action="store_true", help="Increment major version and reset minor/patch.")
    bump_group.add_argument("--minor", action="store_true", help="Increment minor version and reset patch.")
    bump_group.add_argument("--patch", action="store_true", help="Increment patch version.")
    bump_group.add_argument("--version", help="Set an explicit major.minor.patch version.")
    parser.add_argument("--dry-run", action="store_true", help="Show the planned release without changing files.")
    parser.add_argument("--no-push", action="store_true", help="Create commit/tag locally but do not push.")
    parser.add_argument("--skip-build", action="store_true", help="Skip uv build before committing the release.")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the release process."""
    args = build_parser().parse_args(argv)
    current_version = read_current_version()
    next_version = explicit_or_bumped_version(args, current_version)
    tag_name = f"v{next_version}"

    print(f"Current version: {current_version}")
    print(f"Next version:    {next_version}")
    print(f"Release tag:     {tag_name}")

    if args.dry_run:
        print("Dry run only. No files changed, no build, no commit, no tag, no push.")
        return 0

    ensure_clean_worktree()
    ensure_tag_absent(tag_name)

    write_version(next_version)

    if not args.skip_build:
        run(("uv", "build"))

    run(("git", "add", str(PYPROJECT_PATH.relative_to(PROJECT_ROOT)), str(INIT_PATH.relative_to(PROJECT_ROOT))))
    run(("git", "commit", "-m", f"Release v{next_version}"))
    run(("git", "tag", "-a", tag_name, "-m", f"Release v{next_version}"))

    if args.no_push:
        print("Release commit and tag created locally. Push manually to publish through GitHub Actions.")
        return 0

    branch_name = current_branch()
    run(("git", "push", "origin", branch_name))
    run(("git", "push", "origin", tag_name))
    print("Release tag pushed. GitHub Actions will publish the package to PyPI.")
    return 0


def read_current_version() -> Version:
    """Read and validate pyproject/app version values."""
    pyproject_text = PYPROJECT_PATH.read_text(encoding="utf-8")
    init_text = INIT_PATH.read_text(encoding="utf-8")

    pyproject_match = VERSION_PATTERN.search(pyproject_text)
    init_match = INIT_VERSION_PATTERN.search(init_text)
    if pyproject_match is None:
        raise RuntimeError("Could not find version in pyproject.toml.")
    if init_match is None:
        raise RuntimeError("Could not find __version__ in app/__init__.py.")

    pyproject_version = Version.parse(pyproject_match.group("version"))
    init_version = Version.parse(init_match.group("version"))
    if pyproject_version != init_version:
        raise RuntimeError(
            "Version mismatch: "
            f"pyproject.toml has {pyproject_version}, app/__init__.py has {init_version}."
        )
    return pyproject_version


def explicit_or_bumped_version(args: argparse.Namespace, current_version: Version) -> Version:
    """Return the requested next version."""
    if args.version:
        return Version.parse(args.version)
    if args.major:
        return current_version.bumped("major")
    if args.minor:
        return current_version.bumped("minor")
    return current_version.bumped("patch")


def write_version(version: Version) -> None:
    """Write the new version to pyproject.toml and app/__init__.py."""
    version_text = str(version)
    pyproject_text = PYPROJECT_PATH.read_text(encoding="utf-8")
    init_text = INIT_PATH.read_text(encoding="utf-8")

    pyproject_text, pyproject_count = VERSION_PATTERN.subn(f'version = "{version_text}"', pyproject_text)
    init_text, init_count = INIT_VERSION_PATTERN.subn(f'__version__ = "{version_text}"', init_text)
    if pyproject_count != 1 or init_count != 1:
        raise RuntimeError("Version replacement failed.")

    PYPROJECT_PATH.write_text(pyproject_text, encoding="utf-8")
    INIT_PATH.write_text(init_text, encoding="utf-8")


def ensure_clean_worktree() -> None:
    """Stop if the release would include unrelated local changes."""
    completed = run(("git", "status", "--porcelain"), capture=True)
    if completed.stdout.strip():
        raise RuntimeError("Git worktree is not clean. Commit or stash changes before releasing.")


def ensure_tag_absent(tag_name: str) -> None:
    """Stop if the release tag already exists locally or remotely."""
    local_tag = run(("git", "tag", "--list", tag_name), capture=True).stdout.strip()
    if local_tag:
        raise RuntimeError(f"Local tag already exists: {tag_name}")

    remote_tag = run(("git", "ls-remote", "--tags", "origin", tag_name), capture=True).stdout.strip()
    if remote_tag:
        raise RuntimeError(f"Remote tag already exists: {tag_name}")


def current_branch() -> str:
    """Return the current branch name."""
    branch = run(("git", "branch", "--show-current"), capture=True).stdout.strip()
    if not branch:
        raise RuntimeError("Could not determine current git branch.")
    return branch


def run(command: tuple[str, ...], *, capture: bool = False) -> subprocess.CompletedProcess[str]:
    """Run a command from the project root."""
    print("+ " + " ".join(command))
    return subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        check=True,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"release error: {exc}", file=sys.stderr)
        raise SystemExit(2)
