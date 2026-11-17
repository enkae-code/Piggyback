"""Which hosts and schemes a reference fetch may touch. Shared by every network read."""

import ipaddress
import socket
from urllib.parse import urlsplit

SCHEMES = ("http", "https")
BLOCKED_NAMES = ("localhost", ".localhost", ".local")


def ip_allowed(ip, allow_loopback=False):
    # Older 3.11 patch releases judge ::ffff:a.b.c.d by its IPv6 form and call 6to4 and Teredo
    # global, so unwrap the mapped form and refuse the tunnels (they can wrap any IPv4) here.
    if ip.version == 6:
        if ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        elif ip.sixtofour is not None or ip.teredo is not None:
            return False
    if ip.is_loopback:
        return allow_loopback
    # is_global rejects CGNAT, private and unspecified space; not multicast, reserved space such
    # as 4000::/3 or deprecated IPv6 site-local fec0::/10, so those stay explicit.
    site_local = ip.version == 6 and ip.is_site_local
    return ip.is_global and not (ip.is_multicast or ip.is_reserved or site_local)


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


def local_name(url):
    """`localhost` means 127.0.0.1 (same port and path). The policy refuses the name itself."""
    try:
        parts = urlsplit(url)
        if parts.hostname != "localhost":
            return url
        port = f":{parts.port}" if parts.port is not None else ""
    except ValueError:
        return url
    return parts._replace(netloc=f"127.0.0.1{port}").geturl()
