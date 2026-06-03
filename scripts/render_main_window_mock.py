#!/usr/bin/env python3
"""Generate an anonymized demo SSH config and capture the real ssh-starter UI."""

from __future__ import annotations

import asyncio
import math
import random
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.config import AppConfig
from app.history import ConnectionHistory
from app.models import RouteKind
from app.ssh_config import load_inventory
from app.ui import SshLauncherApp

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SOURCE_SSH_CONFIG = Path("~/.ssh/config").expanduser()
DEMO_CONFIG = PROJECT_ROOT / "docs" / "demo" / "ssh_config"
SCREENSHOT_DIR = PROJECT_ROOT / "docs" / "assets"
SCREENSHOT_SVG_NAME = "main-window.svg"
SCREENSHOT_PNG_NAME = "main-window.png"
SVG_VIEWBOX_PATTERN = re.compile(r'viewBox="0 0 (?P<width>[0-9.]+) (?P<height>[0-9.]+)"')
MAX_DEMO_PROFILES = 48
RANDOM_SEED = 20260602

ALIAS_PREFIXES = (
    "atlas",
    "aurora",
    "boreal",
    "cedar",
    "delta",
    "ember",
    "falcon",
    "harbor",
    "indigo",
    "juniper",
    "kestrel",
    "lumen",
    "meridian",
    "nova",
    "onyx",
    "prairie",
    "quartz",
    "raven",
    "solstice",
    "tundra",
    "vector",
    "willow",
)
ALIAS_ROLES = ("edge", "db", "worker", "gateway", "media", "lab", "cache", "build", "backup", "core")
USERS = ("operator", "root", "deploy", "ops", "admin")
PRIVATE_NETS = ("10.44", "172.20", "192.168")
DOC_NETS = ("192.0.2", "198.51.100", "203.0.113")
KEY_NAMES = ("id_ed25519_atlas", "id_ed25519_lab", "id_rsa_legacy", "id_ed25519_ops")


@dataclass(frozen=True)
class DemoProfile:
    """A synthetic SSH Host entry based on a real profile's shape."""

    alias: str
    hostname: str
    user: str
    port: str
    identity_file: str | None
    proxy_jump: str | None


def main() -> int:
    """Create demo assets for README."""
    DEMO_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

    profiles = generate_demo_profiles(SOURCE_SSH_CONFIG)
    DEMO_CONFIG.write_text(render_demo_config(profiles), encoding="utf-8")
    screenshot_path = Path(asyncio.run(capture_screenshot()))
    png_path = render_png_screenshot(screenshot_path)
    print(f"Generated {DEMO_CONFIG.relative_to(PROJECT_ROOT)}")
    print(f"Generated {screenshot_path.relative_to(PROJECT_ROOT)}")
    print(f"Generated {png_path.relative_to(PROJECT_ROOT)}")
    return 0


def generate_demo_profiles(source_config: Path) -> list[DemoProfile]:
    """Build synthetic profiles while preserving broad route/group/user shapes."""
    if not source_config.exists():
        raise FileNotFoundError(f"OpenSSH config not found: {source_config}")

    inventory = load_inventory(source_config)
    real_profiles = list(inventory.profiles[:MAX_DEMO_PROFILES])
    if not real_profiles:
        raise RuntimeError(f"OpenSSH config contains no concrete Host profiles: {source_config}")

    rng = random.Random(RANDOM_SEED)
    alias_by_target: dict[str, str] = {}
    used_aliases: set[str] = set()
    demo_profiles: list[DemoProfile] = []

    for index, profile in enumerate(real_profiles):
        target_slug = alias_by_target.setdefault(profile.target_key, unique_slug(index, used_aliases, rng))
        alias = f"{target_slug}-{profile_user_suffix(profile.display_user, index)}"
        alias = unique_alias(alias, used_aliases)
        hostname = fake_hostname(profile.route_kind, target_slug, index)
        demo_profiles.append(
            DemoProfile(
                alias=alias,
                hostname=hostname,
                user=fake_user(profile.display_user, index),
                port=profile.port or "22",
                identity_file=fake_identity(profile.identity_files, index),
                proxy_jump=fake_proxy_jump(profile.proxy_jump, profile.proxy_command, index),
            )
        )
    return demo_profiles


def unique_slug(index: int, used_aliases: set[str], rng: random.Random) -> str:
    """Create a deterministic target slug."""
    while True:
        candidate = f"{rng.choice(ALIAS_PREFIXES)}-{rng.choice(ALIAS_ROLES)}-{index + 1:02d}"
        if candidate not in used_aliases:
            used_aliases.add(candidate)
            return candidate


def unique_alias(alias: str, used_aliases: set[str]) -> str:
    """Ensure aliases are unique after adding user suffixes."""
    if alias not in used_aliases:
        used_aliases.add(alias)
        return alias
    suffix = 2
    while f"{alias}-{suffix}" in used_aliases:
        suffix += 1
    unique = f"{alias}-{suffix}"
    used_aliases.add(unique)
    return unique


def profile_user_suffix(display_user: str, index: int) -> str:
    """Return a synthetic suffix that keeps root/default-user shapes visible."""
    normalized = display_user.lower()
    if normalized == "root":
        return "root"
    return USERS[index % len(USERS)]


def fake_user(display_user: str, index: int) -> str:
    """Return a synthetic user while preserving root defaults when present."""
    normalized = display_user.lower()
    if normalized == "root":
        return "root"
    return USERS[index % len(USERS)]


def fake_hostname(route_kind: RouteKind, slug: str, index: int) -> str:
    """Return a fake hostname/IP matching the broad route class."""
    if route_kind == RouteKind.DIRECT_PRIVATE:
        prefix = PRIVATE_NETS[index % len(PRIVATE_NETS)]
        return f"{prefix}.{20 + index % 80}.{10 + (index * 7) % 200}"
    if route_kind == RouteKind.DIRECT_TAILSCALE:
        return f"{slug}.tail-example.ts.net"
    if route_kind == RouteKind.DIRECT_PUBLIC:
        prefix = DOC_NETS[index % len(DOC_NETS)]
        return f"{prefix}.{10 + (index * 7) % 200}"
    return f"{slug}.example.net"


def fake_identity(identity_files: tuple[str, ...], index: int) -> str | None:
    """Return a synthetic identity path if the real profile had one."""
    if not identity_files:
        return None
    return f"~/.ssh/{KEY_NAMES[index % len(KEY_NAMES)]}"


def fake_proxy_jump(proxy_jump: str | None, proxy_command: str | None, index: int) -> str | None:
    """Return a synthetic jump host when the real profile was proxied."""
    if not proxy_jump and not proxy_command:
        return None
    return f"jump-{index % 5 + 1:02d}.example.net"


def render_demo_config(profiles: list[DemoProfile]) -> str:
    """Render the synthetic OpenSSH config."""
    lines = [
        "# Demo SSH config generated for ssh-starter documentation.",
        "# All aliases, users, hostnames, IP addresses, and key paths are synthetic.",
        "",
    ]
    for profile in profiles:
        lines.extend(
            [
                f"Host {profile.alias}",
                f"    HostName {profile.hostname}",
                f"    User {profile.user}",
                f"    Port {profile.port}",
            ]
        )
        if profile.identity_file:
            lines.append(f"    IdentityFile {profile.identity_file}")
        if profile.proxy_jump:
            lines.append(f"    ProxyJump {profile.proxy_jump}")
        lines.append("")
    return "\n".join(lines)


async def capture_screenshot() -> str:
    """Run the real Textual app with the demo config and save a screenshot."""
    inventory = load_inventory(DEMO_CONFIG)
    app_config = AppConfig(ssh_config_path=str(DEMO_CONFIG), term_string="xterm-256color")
    history = demo_history(inventory.profiles)
    app = SshLauncherApp(inventory, app_config, PROJECT_ROOT / "config.toml", history)

    async with app.run_test(headless=True, size=(124, 36)) as pilot:
        await pilot.pause(0.2)
        return app.save_screenshot(filename=SCREENSHOT_SVG_NAME, path=str(SCREENSHOT_DIR))


def render_png_screenshot(svg_path: Path) -> Path:
    """Render the Textual SVG screenshot to PNG with a local Chromium-compatible browser."""
    chrome = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")
    if chrome is None:
        raise RuntimeError("Could not render PNG screenshot: google-chrome or chromium is required.")

    png_path = SCREENSHOT_DIR / SCREENSHOT_PNG_NAME
    width, height = svg_viewport_size(svg_path)
    subprocess.run(
        (
            chrome,
            "--headless",
            "--disable-gpu",
            "--hide-scrollbars",
            f"--screenshot={png_path}",
            f"--window-size={width},{height}",
            f"file://{svg_path.resolve()}",
        ),
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    return png_path


def svg_viewport_size(svg_path: Path) -> tuple[int, int]:
    """Return a Chrome viewport size that exactly matches the SVG viewBox."""
    svg_head = svg_path.read_text(encoding="utf-8")[:300]
    match = SVG_VIEWBOX_PATTERN.search(svg_head)
    if match is None:
        raise RuntimeError(f"Could not read SVG viewBox from {svg_path}.")
    return math.ceil(float(match.group("width"))), math.ceil(float(match.group("height")))


def demo_history(profiles: tuple) -> ConnectionHistory:
    """Create deterministic fake recency for a realistic sorted first page."""
    history = ConnectionHistory()
    base_timestamp = 1_800_000_000.0
    for index, profile in enumerate(profiles[:12]):
        history = history.mark_connected(profile.alias, connected_at=base_timestamp - index * 7200)
    return history


if __name__ == "__main__":
    raise SystemExit(main())
