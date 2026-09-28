import pytest

from hoot.sensors import DRIVERS, build_driver
from hoot.sensors.base import ChannelSpec, SensorError


def test_all_drivers_are_registered_under_their_key():
    for key, cls in DRIVERS.items():
        assert cls.key == key


def test_unknown_driver_lists_the_valid_ones():
    with pytest.raises(SensorError, match="known drivers"):
        build_driver("nope")


def test_simulator_produces_all_three_channels():
    driver = build_driver("simulator", {"seed": 5})
    driver.open()
    sample = driver.read()
    assert set(sample.values) == {"temperature", "humidity", "co2"}
    assert not sample.errors


def test_simulator_is_deterministic_with_a_seed():
    a = build_driver("simulator", {"seed": 9, "clock": lambda: 1_000_000.0})
    b = build_driver("simulator", {"seed": 9, "clock": lambda: 1_000_000.0})
    a.open(); b.open()
    assert a.read().values == b.read().values


def test_simulator_failure_injection_marks_every_channel():
    driver = build_driver("simulator", {"seed": 1, "fail_rate": 1.0})
    driver.open()
    sample = driver.read()
    assert not sample.values
    assert set(sample.errors) == {"temperature", "humidity", "co2"}


def test_simulator_co2_tracks_occupancy():
    at = lambda h: build_driver(  # noqa: E731
        "simulator", {"seed": 2, "noise": 0.0, "clock": lambda: h * 3600.0})
    night, midday = at(3), at(12)
    night.open(); midday.open()
    assert midday.read().values["co2"] > night.read().values["co2"] + 300


def test_channel_spec_rejects_implausible_values():
    spec = ChannelSpec("temperature", "degC", valid_min=-40, valid_max=85)
    spec.validate(21.0)
    with pytest.raises(SensorError, match="outside plausible range"):
        spec.validate(500.0)


def test_sensehat_is_flagged_uncalibrated_with_a_reason():
    """The Sense HAT's chip is fine; its mounting is not. The driver must say so."""
    cls = DRIVERS["sensehat"]
    assert cls.uncalibrated
    assert "thermal plume" in cls.uncalibrated_reason


def test_scd4x_marks_its_own_temperature_diagnostic_only():
    driver = DRIVERS["scd4x"]()
    by_name = {c.name: c for c in driver.channels}
    assert by_name["co2"].diagnostic is False
    assert by_name["scd_temperature"].diagnostic is True
    assert by_name["scd_humidity"].diagnostic is True


def test_hardware_drivers_fail_with_guidance_when_libs_missing():
    for key in ("sht4x", "scd4x"):
        with pytest.raises(SensorError, match="simulator"):
            build_driver(key).open()


def test_tmp11x_provides_only_a_reference_temperature_channel():
    """It supplements the SHT45; it must not shadow the primary channel."""
    driver = DRIVERS["tmp11x"]()
    names = [c.name for c in driver.channels]
    assert names == ["reference_temperature"]
    assert "temperature" not in names


def test_tmp11x_needs_hardware_libs_and_says_so():
    from hoot.sensors.base import SensorError
    with pytest.raises(SensorError, match="simulator"):
        build_driver("tmp11x").open()


def test_simulator_can_emit_a_reference_temperature():
    driver = build_driver("simulator", {"seed": 3, "emit_reference": True})
    driver.open()
    values = driver.read().values
    assert "reference_temperature" in values
    # tracks the primary closely when no offset is injected
    assert abs(values["temperature"] - values["reference_temperature"]) < 0.5


def test_simulator_reference_offset_creates_a_disagreement():
    driver = build_driver("simulator", {"seed": 3, "emit_reference": True,
                                        "reference_offset_c": 3.0, "noise": 0.0})
    driver.open()
    values = driver.read().values
    assert abs(values["temperature"] - values["reference_temperature"]) == pytest.approx(3.0, abs=0.1)


# --- regressions from the 2026-09-09 audit ---------------------------------

def test_tmp119_device_id_is_not_masked_into_a_tmp117():
    """Regression: the driver masked register 0x0F with 0x0FFF. A real TMP119
    returns 0x2117, which the mask collapsed to 0x117 -- so every TMP119 was
    silently reported as a TMP117."""
    from hoot.sensors.tmp11x import _DEVICE_IDS
    assert _DEVICE_IDS[0x2117] == "TMP119"
    assert _DEVICE_IDS[0x0117] == "TMP117"
    # the two must stay distinguishable
    assert _DEVICE_IDS[0x2117] != _DEVICE_IDS[0x0117]
    assert 0x0119 not in _DEVICE_IDS   # the value that was wrong


def test_i2c_bus_is_reference_counted():
    """Regression: every driver built its own busio.I2C and deinit()ed it on
    close, so stopping one sensor tore the bus out from under the others."""
    from hoot.sensors import i2cbus
    calls = {"open": 0, "close": 0}

    class FakeBus:
        def deinit(self): calls["close"] += 1

    def fake_open():
        calls["open"] += 1
        return FakeBus()

    # drive the refcount directly, standing in for busio
    i2cbus._bus, i2cbus._refcount = None, 0
    try:
        i2cbus._bus = fake_open(); i2cbus._refcount = 1     # first acquire
        i2cbus._refcount += 1                                # second acquire
        i2cbus.release()
        assert i2cbus._bus is not None, "bus closed while still in use"
        assert calls["close"] == 0
        i2cbus.release()
        assert i2cbus._bus is None
        assert calls["close"] == 1
    finally:
        i2cbus._bus, i2cbus._refcount = None, 0


def test_i2c_frequency_option_warns_that_it_does_nothing(caplog):
    """Blinka ignores frequency= on Linux; the operator must be told rather
    than believing they slowed the bus for a long cable."""
    import logging
    from hoot.sensors import i2cbus
    with caplog.at_level(logging.WARNING):
        i2cbus.warn_if_frequency_configured({"i2c_frequency": 10000}, "sht4x")
    assert "ignored" in caplog.text
    assert "config.txt" in caplog.text
