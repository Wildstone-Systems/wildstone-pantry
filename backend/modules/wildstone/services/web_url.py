"""PantryOS's public web address, as told to Wildstone at pairing (ADR 0026 §7).

It comes from configuration (``PANTRYOS_PUBLIC_URL``) and nowhere else: the
backend cannot know where its frontend is served, so the request's host or
the API port is never used as a guess.

Wildstone silently ignores a value it can't use (ADR-W044 A6), so the same
rules are applied here first: a value Wildstone would drop is not sent, and
the admin screen says why.
"""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlsplit

from app.core.config import settings

MAX_LENGTH = 512

# Keystone's privateHost: loopback, private (RFC 1918 and IPv6 unique-local),
# link-local, and the tailnet's CGNAT range.
_CGNAT = ipaddress.ip_network("100.64.0.0/10")
_PRIVATE = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7")]
_HOUSEHOLD_SUFFIXES = (".local", ".lan", ".internal", ".ts.net")
# A DNS name: dot-separated labels of letters, digits and inner hyphens.
_LABEL = re.compile(r"(?!-)[A-Za-z0-9-]{1,63}(?<!-)")


def _valid_host(host: str) -> bool:
    """An IP address or a well-formed DNS name. Stricter than a URL parser on
    purpose: an address that might not work is simply not sent."""
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    labels = host.split(".")
    return len(host) <= 253 and all(_LABEL.fullmatch(label) for label in labels)


def _household_host(host: str) -> bool:
    """On this machine or this network. Wrong in the safe direction: an
    unrecognised public name is not household."""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        name = host.lower()
        return name == "localhost" or "." not in name or name.endswith(_HOUSEHOLD_SUFFIXES)
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return (
        ip.is_loopback
        or ip.is_link_local
        or any(ip in net for net in _PRIVATE if net.version == ip.version)
        or (ip.version == 4 and ip in _CGNAT)
    )


def check(raw: str) -> tuple[str | None, str | None]:
    """``(url, None)`` when usable (trailing ``/`` trimmed), ``(None, why)``
    when not, ``(None, None)`` when unset."""
    value = (raw or "").strip()
    if not value:
        return None, None
    value = value.rstrip("/")
    if len(value) > MAX_LENGTH:
        return None, f"longer than {MAX_LENGTH} characters"
    try:
        parts = urlsplit(value)
        host = parts.hostname
        parts.port  # noqa: B018 — raises on a malformed port
    except ValueError:
        return None, "not a valid address"
    if parts.scheme not in ("http", "https") or not host:
        return None, "must be an absolute http:// or https:// address"
    if any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value) or not _valid_host(host):
        return None, "not a valid address"
    if parts.username is not None or parts.password is not None:
        return None, "must not contain a user name or password"
    if parts.query or parts.fragment or "?" in value or "#" in value:
        return None, "must not have a query (?) or fragment (#)"
    if parts.scheme == "http" and not _household_host(host):
        return None, "plain http is only for household-network addresses; use https"
    return value, None


def public_web_url() -> str | None:
    """The address to give Wildstone, or None to say nothing."""
    return check(settings.pantryos_public_url)[0]


def status() -> dict:
    """For the admin screen: what Wildstone will be told, or why not."""
    url, problem = check(settings.pantryos_public_url)
    return {"web_url": url, "web_url_problem": problem}
