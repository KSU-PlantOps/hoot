import pytest

from hoot.netaddr import AddressError, describe, resolve


def test_explicit_cidr_gets_the_configured_port():
    assert resolve("10.1.2.3/24", 47808) == "10.1.2.3/24:47808"


def test_explicit_port_in_the_address_is_kept():
    """Regression: resolve() split on ':' and returned only the host part, so a
    port written into bacnet.address was silently replaced by 47808."""
    assert resolve("10.1.2.3/24:47809", 47808) == "10.1.2.3/24:47809"


def test_bare_ip_without_prefix_is_refused():
    with pytest.raises(AddressError, match="prefix length"):
        resolve("10.1.2.3", 47808)


def test_describe_reports_the_broadcast_address():
    assert describe("10.10.0.100/23:47808") == (
        "10.10.0.100 on 10.10.0.0/23 (broadcast 10.10.1.255)"
    )
