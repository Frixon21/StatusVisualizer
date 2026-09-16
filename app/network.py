from __future__ import annotations

import ipaddress


def canonical_probe_address(value: object) -> str | None:
    """Return a safe single-host address without invoking DNS."""
    text = str(value or "").strip()
    if "%" in text:
        return None
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        return None
    if (
        address.is_loopback
        or address.is_multicast
        or address.is_unspecified
        or address.is_reserved
        or not (address.is_private or address.is_link_local)
    ):
        return None
    if isinstance(address, ipaddress.IPv4Address) and int(address) & 0xFF in {0, 255}:
        return None
    return address.compressed
