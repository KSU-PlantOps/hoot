"""BACnet object construction and fault semantics.

These exercise the object/state logic without binding a socket, so they run
anywhere. The socket path is covered by ``hoot selftest`` on the real unit.
"""

import pytest

from hoot.bacnet import BACnetServer

# bacpypes3's local objects bind to the running event loop when constructed,
# which is always the case in production (BACnetServer.start is async). These
# tests therefore run on a loop too.
pytestmark = pytest.mark.asyncio


@pytest.fixture
def server(sim_config):
    sim_config.bacnet.enabled = True
    return BACnetServer(sim_config)


def _point(server, channel="temperature", **kw):
    cfg = next(p for p in server.config.points if p.channel == channel)
    for key, value in kw.items():
        setattr(cfg, key, value)
    state = server._build_point(cfg)
    server.points[channel] = state
    return state


async def test_new_point_starts_faulted_not_at_zero(server):
    """A point that has never been read must not present 0.0 as a measurement --
    0 degF is plausible-looking and would trend and alarm."""
    state = _point(server)
    assert str(state.obj.reliability) == "no-sensor"
    assert state.value is None
    assert not state.healthy


async def test_publish_clears_the_fault(server):
    state = _point(server)
    server.publish("temperature", 71.5)
    assert state.value == 71.5
    assert state.fault is None
    assert state.healthy
    assert str(state.obj.reliability) == "no-fault-detected"
    assert float(state.obj.presentValue) == pytest.approx(71.5)


async def test_fault_sets_reliability_and_does_not_fake_a_value(server):
    state = _point(server)
    server.publish("temperature", 71.5)
    server.fault("temperature", "I2C timeout")
    assert state.fault == "I2C timeout"
    assert str(state.obj.reliability) == "communication-failure"
    assert not state.healthy


async def test_units_follow_the_site_temperature_preference(sim_config):
    sim_config.units.temperature = "degC"
    server = BACnetServer(sim_config)
    state = _point(server)
    assert "celsius" in str(state.obj.units).lower()

    sim_config.units.temperature = "degF"
    server2 = BACnetServer(sim_config)
    state2 = _point(server2)
    assert "fahrenheit" in str(state2.obj.units).lower()


async def test_humidity_and_co2_get_their_own_units(server):
    rh = _point(server, "humidity")
    co2 = _point(server, "co2")
    assert "humidity" in str(rh.obj.units).lower()
    assert "million" in str(co2.obj.units).lower()


async def test_high_limit_raises_the_alarm_flag(server):
    state = _point(server, high_limit=80.0)
    server.publish("temperature", 85.0)
    assert state.in_alarm
    assert str(state.obj.eventState) == "high-limit"


async def test_low_limit_raises_the_alarm_flag(server):
    state = _point(server, low_limit=60.0)
    server.publish("temperature", 55.0)
    assert state.in_alarm


async def test_value_inside_limits_is_normal(server):
    state = _point(server, low_limit=60.0, high_limit=80.0)
    server.publish("temperature", 71.0)
    assert not state.in_alarm
    assert str(state.obj.eventState) == "normal"


async def test_alarm_clears_when_value_returns_to_range(server):
    state = _point(server, high_limit=80.0)
    server.publish("temperature", 85.0)
    assert state.in_alarm
    server.publish("temperature", 70.0)
    assert not state.in_alarm


async def test_calibration_note_appears_in_the_description(server):
    cfg = next(p for p in server.config.points if p.channel == "temperature")
    cfg.calibration.offset = 0.5
    state = server._build_point(cfg)
    assert "offset" in str(state.obj.description)


async def test_publishing_an_unknown_channel_is_a_no_op(server):
    server.publish("nonexistent", 1.0)   # must not raise
    server.fault("nonexistent", "x")


async def test_snapshot_shape_matches_what_the_ui_reads(server):
    _point(server)
    server.publish("temperature", 71.0)
    snap = server.snapshot()["temperature"]
    for key in ("name", "instance", "value", "unit", "fault",
                "in_alarm", "updates", "healthy", "calibration"):
        assert key in snap
    assert snap["updates"] == 1


async def test_out_of_service_is_settable(server):
    state = _point(server)
    server.set_out_of_service("temperature", True)
    assert bool(state.obj.outOfService) is True


async def test_device_description_includes_asset_and_location(sim_config):
    sim_config.device.asset_tag = "ASSET-12345"
    sim_config.device.location = "Bldg 12 / Rm 214"
    server = BACnetServer(sim_config)
    desc = server._device_description()
    assert "ASSET-12345" in desc and "Bldg 12" in desc


async def test_device_description_is_within_bacnet_string_limits(sim_config):
    sim_config.device.description = "x" * 400
    sim_config.device.location = "y" * 400
    server = BACnetServer(sim_config)
    assert len(server._device_description()) <= 255


# --- regressions from the 2026-09-09 audit ---------------------------------

async def test_low_excursion_reports_low_limit_not_high(server):
    """Regression: _check_limits returned a bare bool and the caller mapped it
    to 'high-limit' unconditionally, so a space that was too COLD raised a HIGH
    limit alarm and sent the responding tech the wrong way."""
    state = _point(server, low_limit=60.0, high_limit=80.0)
    server.publish("temperature", 55.0)
    assert state.in_alarm
    assert str(state.obj.eventState) == "low-limit"


async def test_high_excursion_still_reports_high_limit(server):
    state = _point(server, low_limit=60.0, high_limit=80.0)
    server.publish("temperature", 85.0)
    assert str(state.obj.eventState) == "high-limit"


async def test_out_of_service_flag_clears_when_returned_to_service(server):
    """Regression: set_out_of_service only ever SET the flag, so a point put
    back in service kept advertising out-of-service."""
    _point(server)
    server.set_out_of_service("temperature", True)
    assert list(server.points["temperature"].obj.statusFlags)[3] == 1
    server.set_out_of_service("temperature", False)
    assert list(server.points["temperature"].obj.statusFlags)[3] == 0


async def test_publishing_does_not_clear_out_of_service(server):
    """Regression: publish() overwrote statusFlags wholesale, silently dropping
    the out-of-service bit on the next sample."""
    state = _point(server)
    server.set_out_of_service("temperature", True)
    server.publish("temperature", 71.0)
    assert list(state.obj.statusFlags)[3] == 1
    assert bool(state.obj.outOfService) is True


async def test_fault_preserves_out_of_service(server):
    state = _point(server)
    server.set_out_of_service("temperature", True)
    server.fault("temperature", "unplugged for calibration")
    flags = list(state.obj.statusFlags)
    assert flags[1] == 1   # fault
    assert flags[3] == 1   # still out of service


async def test_out_of_service_uses_the_correct_status_flag_bit(server):
    """Regression: ASHRAE 135 orders StatusFlags [in-alarm, fault, overridden,
    out-of-service]. An earlier constant had bits 2 and 3 swapped, so marking a
    point out of service actually flagged it OVERRIDDEN -- which tells an
    operator someone force-wrote the point, not that it is unmaintained."""
    from bacpypes3.basetypes import StatusFlags
    assert str(StatusFlags([0, 0, 0, 1])) == "out-of-service"
    assert str(StatusFlags([0, 0, 1, 0])) == "overridden"

    state = _point(server)
    server.set_out_of_service("temperature", True)
    flags = list(state.obj.statusFlags)
    assert flags[3] == 1, "out-of-service bit not set"
    assert flags[2] == 0, "overridden bit wrongly set"


async def test_snapshot_states_the_calibration_offset_unit(sim_config):
    """The offset is stored in degC; the UI shows it beside a degF value."""
    sim_config.bacnet.enabled = True
    server = BACnetServer(sim_config)
    state = _point(server)
    state.config.calibration.offset = 0.07
    assert server.snapshot()["temperature"]["calibration"] == "offset +0.07 °C"
