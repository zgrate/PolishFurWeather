"""Which IP family outbound requests to IMGW/Open-Meteo are allowed to use.

Off by default: ``network.ip_family = "auto"`` leaves the resolver alone, which
is what every ordinary dual-stack host wants.

Pinning it is for hosts that only have one family. The pin is applied by
replacing urllib3's ``allowed_gai_family`` hook -- the address family it hands
``getaddrinfo`` -- which is the override point urllib3 provides for exactly
this. It is process-wide by design: the point of "IPv6 only" is that nothing
quietly falls back to IPv4.

A warning before anyone turns this on
-------------------------------------
If any upstream here turns out to be IPv4-only the same problem DWD used to
have applies: an IPv6-only container cannot reach it directly, and every fetch
fails with ``Network is unreachable``. On such a host the fix is NAT64/DNS64
(the resolver synthesises an AAAA and a gateway does the translation), or an
outbound HTTP proxy that has IPv4 -- not this setting. The preflight below
says so out loud at startup rather than leaving it to be guessed from a wall
of connection errors.
"""

from __future__ import annotations

import logging
import socket
from typing import Dict, List, Optional

from urllib3.util import connection as urllib3_connection

logger = logging.getLogger(__name__)

#: Accepted values for ``network.ip_family``.
FAMILIES = ("auto", "ipv4", "ipv6")

_SOCKET_FAMILY = {
    "ipv4": socket.AF_INET,
    "ipv6": socket.AF_INET6,
}

#: How each family is spelled in log lines. "IPV6" reads as a typo.
_LABEL = {"auto": "auto", "ipv4": "IPv4", "ipv6": "IPv6"}

#: Every host this application talks to. Used by the preflight only.
UPSTREAM_HOSTS = (
    "danepubliczne.imgw.pl",
    "api.open-meteo.com",
    "air-quality-api.open-meteo.com",
)

#: urllib3's own hook, kept so a pin can be lifted again (mainly for tests).
_original_allowed_gai_family = urllib3_connection.allowed_gai_family

_applied: Optional[str] = None


def normalise(family: Optional[str]) -> str:
    """Fold a configured value to one of FAMILIES, complaining about junk."""
    value = (family or "auto").strip().lower()
    # Tolerate the spellings people actually type.
    value = {"4": "ipv4", "v4": "ipv4", "inet": "ipv4", "6": "ipv6", "v6": "ipv6", "inet6": "ipv6"}.get(
        value, value
    )
    if value not in FAMILIES:
        logger.warning("Unknown network.ip_family %r -- falling back to 'auto'", family)
        return "auto"
    return value


def apply_ip_family(family: str) -> str:
    """Pin outbound connections to one address family. Returns what was applied.

    Idempotent, so it is safe to call from wherever the session happens to be
    built first.
    """
    global _applied

    family = normalise(family)
    if family == _applied:
        return family

    if family == "auto":
        urllib3_connection.allowed_gai_family = _original_allowed_gai_family
    else:
        af = _SOCKET_FAMILY[family]
        urllib3_connection.allowed_gai_family = lambda: af
        logger.info("Outbound requests pinned to %s", _LABEL[family])

    _applied = family
    return family


def applied() -> str:
    """The family currently in force, whether or not anything was pinned."""
    return _applied or "auto"


def resolves(host: str, family: str) -> bool:
    """Does ``host`` have an address of this family? Never raises."""
    af = _SOCKET_FAMILY.get(family, socket.AF_UNSPEC)
    try:
        return bool(socket.getaddrinfo(host, 443, af, socket.SOCK_STREAM))
    except OSError:
        return False


def preflight(hosts: Optional[List[str]] = None) -> Dict[str, bool]:
    """Check the upstream hosts against the pinned family and say so.

    Only worth doing when a pin is in force: under "auto" the resolver's answer
    is the whole story and there is nothing to warn about. The return value is
    ``host -> reachable in the pinned family``, so /api/health can show it.
    """
    family = applied()
    if family == "auto":
        return {}

    names = list(hosts if hosts is not None else UPSTREAM_HOSTS)
    result = {host: resolves(host, family) for host in names}

    missing = [host for host, ok in result.items() if not ok]
    if missing:
        logger.error(
            "network.ip_family is %r but %s publish no %s address. Requests to them "
            "will fail with 'Network is unreachable'. On an IPv6-only host you need "
            "DNS64/NAT64 (or an outbound proxy with IPv4).",
            family,
            ", ".join(missing),
            "AAAA" if family == "ipv6" else "A",
        )
    else:
        logger.info("All upstream hosts resolve over %s", _LABEL[family])

    return result
