"""Which hosts and schemes a reference fetch may touch. Shared by every network read."""

import ipaddress
import socket
from urllib.parse import urlsplit

SCHEMES = ("http", "https")
BLOCKED_NAMES = ("localhost", ".localhost", ".local")


def ip_allowed(ip, allow_loopback=False):
    if ip.is_loopback:  # IPv4-mapped loopback (::ffff:127.0.0.1) counts too
        return allow_loopback
    # is_global already rejects CGNAT, private, unspecified, 6to4, Teredo and IPv4-mapped
    # private space (same answers as IPv4 on 3.11-3.14). It does not reject multicast or
    # reserved space such as 4000::/3, so those stay explicit.
    return ip.is_global and not (ip.is_multicast or ip.is_reserved)


def resolve_allowed(host, allow_loopback=False):
    """Return one address to connect to, or None. Every address the name resolves to must pass.

    Callers connect to the returned literal and never resolve the name again: that closes DNS
    rebinding for the connection.
    """
    name = host.lower().rstrip(".")
    if not name or name.endswith(BLOCKED_NAMES):  # BLOCKED_NAMES includes bare "localhost"
        return None
    try:
        try:
            addresses = [ipaddress.ip_address(name)]
        except ValueError:
            infos = socket.getaddrinfo(name, None)
            addresses = [ipaddress.ip_address(str(info[4][0])) for info in infos]
    except (OSError, ValueError):
        return None
    if not addresses or not all(ip_allowed(ip, allow_loopback) for ip in addresses):
        return None
    return str(addresses[0])


def host_allowed(host, allow_loopback=False):
    return resolve_allowed(host, allow_loopback) is not None


def url_allowed(url, allow_loopback=False):
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return parts.scheme in SCHEMES and host_allowed(parts.hostname or "", allow_loopback)
