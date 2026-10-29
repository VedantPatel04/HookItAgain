import ipaddress
import socket
from urllib.parse import urlparse


# Blocked address check for destination write (docs/api-contracts.md outgoing SSRF).
# 1. Unwrap IPv4-mapped IPv6 (::ffff:127.0.0.1 still counts as 127.0.0.1).
# 2. Reject private, loopback, link-local, reserved, unspecified.
def _ip_blocked(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if addr.version == 6 and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped # ::ffff:x.x.x.x is v4 address
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_unspecified
    )


# SSRF at destination write. worker re-checks at delivery time.
# 1. Scheme must be https, host required.
# 2. If the literal host is an IP, reject blocked ranges (127.0.0.1, 10/8, 169.254/16, ::1, ...).
# 3. Resolve DNS. reject if any resolved address is blocked (DNS rebinding at write).
# 4. Return a 422 message, or None if the URL may be stored.
def https_destination_url_rejected(url: str) -> str | None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        return "Destination URL must be https with a host."

    host = parsed.hostname
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None # hostname, not a literal IP
    if literal is not None and _ip_blocked(literal):
        return "Destination URL host is not allowed."

    try:
        resolved = socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return "Destination URL host could not be resolved."

    for item in resolved:
        sockaddr = item[4]
        ip_text = sockaddr[0]
        if ip_text.startswith("::ffff:"):
            ip_text = ip_text.removeprefix("::ffff:")
        addr = ipaddress.ip_address(ip_text)
        if _ip_blocked(addr):
            return "Destination URL resolved to a blocked address."
    return None
