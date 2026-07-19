"""Conservative direct-network validation for peer sync."""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlparse


class DirectPeerError(ValueError):
    """A peer address is not on a directly reachable private network."""


@dataclass(frozen=True)
class DiscoveredPeer:
    name: str
    host: str
    port: int
    device_id: str = ""

    @property
    def url(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"http://{host}:{self.port}"


def is_direct_address(address: str) -> bool:
    """Accept loopback, link-local, RFC1918/ULA, and WireGuard private addresses."""
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    return bool(ip.is_private or ip.is_loopback or ip.is_link_local)


def resolve_direct_host(host: str) -> list[str]:
    if not host:
        raise DirectPeerError("peer URL has no host")
    try:
        addresses = sorted({item[4][0] for item in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)})
    except socket.gaierror as exc:
        raise DirectPeerError(f"could not resolve peer host: {host}") from exc
    if not addresses or any(not is_direct_address(address) for address in addresses):
        raise DirectPeerError("peer must resolve only to LAN, loopback, or WireGuard/private addresses")
    return addresses


def validate_direct_url(url: str) -> str:
    """Validate and normalize an HTTP URL without permitting Internet relays."""
    parsed = urlparse(url)
    if parsed.scheme != "http":
        raise DirectPeerError("direct peer sync requires an http URL on the trusted private network")
    if parsed.username or parsed.password or not parsed.hostname:
        raise DirectPeerError("invalid peer URL")
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise DirectPeerError("peer URL must not include a path, query, or fragment")
    try:
        port = parsed.port
    except ValueError as exc:
        raise DirectPeerError("invalid peer port") from exc
    resolve_direct_host(parsed.hostname)
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    return f"http://{host}" + (f":{port}" if port is not None else "")
