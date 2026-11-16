"""Which hosts and schemes a reference fetch may touch. Shared by every network read."""

import ipaddress
import socket
from urllib.parse import urlsplit

CGNAT = ipaddress.ip_network("100.64.0.0/10")
SCHEMES = ("http", "https")
BLOCKED_NAMES = ("localhost", ".localhost", ".local")


def _embedded(ip):
    # IPv4 addresses hidden inside IPv6 forms that a plain is_global check would miss.
    if ip.version == 4:
        return [ip]
    if ip.ipv4_mapped:
        return [ip.ipv4_mapped]
    if ip.sixtofour:
        return [ip.sixtofour]
    if ip.teredo:
        return list(ip.teredo)
    return [ip]


def ip_allowed(ip, allow_loopback=False):
    for candidate in _embedded(ip):
        if candidate.is_loopback:
            if not allow_loopback:
                return False
        elif (
            candidate in CGNAT
            or candidate.is_multicast
            or candidate.is_reserved
            or candidate.is_unspecified
            or not candidate.is_global
        ):
            return False
    return True


def host_allowed(host, allow_loopback=False):
    name = host.lower().rstrip(".")
    if not name or name.endswith(BLOCKED_NAMES) or name == "localhost":
        return False
    try:
        return ip_allowed(ipaddress.ip_address(name), allow_loopback)
    except ValueError:
        pass
    # ponytail: DNS is resolved here, then again by the browser, so DNS rebinding
    # between the two lookups is a known residual gap; the route check must hold per request.
    try:
        infos = socket.getaddrinfo(name, None)
    except OSError:
        return False
    return bool(infos) and all(
        ip_allowed(ipaddress.ip_address(str(info[4][0]).split("%")[0]), allow_loopback)
        for info in infos
    )


def url_allowed(url, allow_loopback=False):
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return parts.scheme in SCHEMES and host_allowed(parts.hostname or "", allow_loopback)
