"""Route classification helpers for SSH profiles."""

from __future__ import annotations

import ipaddress

from app.models import RouteKind

TAILSCALE_CGNAT = ipaddress.ip_network("100.64.0.0/10")


def classify_route(
    *,
    alias: str,
    hostname: str | None,
    proxy_jump: str | None,
    proxy_command: str | None,
) -> RouteKind:
    """Classify the visible SSH route without resolving DNS or reading secrets."""
    if proxy_jump or proxy_command:
        return RouteKind.PROXIED

    endpoint = (hostname or alias).strip().lower().rstrip(".")
    if not endpoint:
        return RouteKind.DIRECT_NAMED

    if endpoint.endswith(".ts.net"):
        return RouteKind.DIRECT_TAILSCALE

    try:
        ip_address = ipaddress.ip_address(endpoint.strip("[]"))
    except ValueError:
        if endpoint.endswith(".local"):
            return RouteKind.DIRECT_PRIVATE
        return RouteKind.DIRECT_NAMED

    if ip_address in TAILSCALE_CGNAT:
        return RouteKind.DIRECT_TAILSCALE
    if ip_address.is_private or ip_address.is_loopback or ip_address.is_link_local:
        return RouteKind.DIRECT_PRIVATE
    return RouteKind.DIRECT_PUBLIC
