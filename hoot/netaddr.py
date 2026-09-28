"""Figure out which interface address to bind BACnet to.

BACnet/IP needs the *prefix length*, not just the address: Who-Is and I-Am are
directed broadcasts, and a wrong netmask produces a device that answers unicast
reads but never shows up in a discovery scan -- the single most common and most
confusing BACnet/IP misconfiguration.
"""

from __future__ import annotations

import ipaddress
import socket


class AddressError(Exception):
    pass


def outbound_ip() -> str:
    """The local address the kernel would use to reach the outside world.

    Uses an unconnected UDP socket, so no packet is actually sent -- this only
    asks the routing table which source address applies.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.0.2.1", 53))  # TEST-NET-1, guaranteed unrouted
        return sock.getsockname()[0]
    except OSError as exc:  # pragma: no cover - no network at all
        raise AddressError(f"cannot determine outbound IP: {exc}") from exc
    finally:
        sock.close()


def prefix_for(ip: str) -> int:
    """Prefix length of the interface holding ``ip``."""
    try:
        import psutil
    except ImportError as exc:  # pragma: no cover
        raise AddressError(
            "psutil is required to detect the interface netmask; install it or "
            "set bacnet.address explicitly as CIDR"
        ) from exc

    for _name, addrs in psutil.net_if_addrs().items():
        for addr in addrs:
            if addr.family == socket.AF_INET and addr.address == ip and addr.netmask:
                return ipaddress.IPv4Network(f"0.0.0.0/{addr.netmask}").prefixlen
    raise AddressError(f"no interface found holding address {ip}")


def resolve(address: str, port: int) -> str:
    """Turn a config address into the ``a.b.c.d/prefix:port`` bacpypes3 wants.

    ``"auto"``          -> detected interface address and real netmask
    ``"10.1.2.3/24"``   -> used as given, port appended
    ``"10.1.2.3/24:47809"`` -> used as given, including its port
    ``"10.1.2.3"``      -> rejected; the prefix is not optional (see module docstring)
    """
    if address and address != "auto":
        if "/" not in address:
            raise AddressError(
                f"bacnet.address {address!r} has no prefix length. BACnet/IP "
                "needs it to compute the broadcast address -- write it as "
                f"'{address}/24' (or whatever your subnet actually is)."
            )
        # An explicit ":port" in the address wins over bacnet.port.
        return address if ":" in address else f"{address}:{port}"

    ip = outbound_ip()
    prefix = prefix_for(ip)
    return f"{ip}/{prefix}:{port}"


def describe(address: str) -> str:
    """Human-readable summary of what a resolved address implies."""
    host = address.split(":", 1)[0]
    try:
        iface = ipaddress.ip_interface(host)
    except ValueError:
        return address
    return (
        f"{iface.ip} on {iface.network} "
        f"(broadcast {iface.network.broadcast_address})"
    )
