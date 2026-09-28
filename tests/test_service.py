
import pytest

from hoot.config import PointConfig, SensorConfig
from hoot.units import convert

pytestmark = pytest.mark.asyncio


async def _run_cycles(service, n=3):
    for _ in range(n):
        await service._sample_once()


@pytest.fixture
async def service(sim_config, paused_service):
    svc = await paused_service(sim_config)
    yield svc
    await svc.stop()


async def test_readings_are_published_in_configured_units(service):
    await _run_cycles(service)
    temp = service.latest["temperature"]
    assert temp["unit"] == "degF"
    # simulator sits near 22 degC -> low 70s degF
    assert 60.0 < temp["value"] < 85.0
    assert temp["fault"] is None


async def test_calibration_is_applied_in_canonical_units(service):
    service.config.points[0].calibration.offset = 1.0  # +1.0 degC
    await _run_cycles(service, 2)
    reading = service.latest["temperature"]
    expected = convert(reading["raw"] + 1.0, "degC", "degF")
    assert reading["value"] == pytest.approx(expected)


async def test_sensor_failure_produces_a_fault_not_a_stale_value(sim_config, paused_service):
    sim_config.sensors = [SensorConfig(driver="simulator",
                                       options={"seed": 1, "fail_rate": 1.0},
                                       allow_uncalibrated=True)]
    svc = await paused_service(sim_config)
    try:
        await _run_cycles(svc, 2)
        reading = svc.latest["temperature"]
        assert reading["value"] is None
        assert "simulated I2C read failure" in reading["fault"]
    finally:
        await svc.stop()


async def test_health_counts_cycles_and_errors(service):
    await _run_cycles(service, 4)
    assert service.health.cycles == 4
    assert service.health.last_sample_at is not None


async def test_trend_rows_are_written(service):
    service.config.logging.interval_seconds = 0.0  # trend every cycle
    await _run_cycles(service, 3)
    rows = service.store.history("temperature")
    assert len(rows) >= 2
    assert all(r["unit"] == "degF" for r in rows)


async def test_uncalibrated_driver_is_not_published_without_opt_in(sim_config, paused_service):
    sim_config.sensors[0].allow_uncalibrated = False
    svc = await paused_service(sim_config)
    try:
        await _run_cycles(svc, 2)
        binding = svc.bindings["temperature"]
        assert binding.publishable is False
        assert "Synthetic data" in binding.reason
        # still measured and trended -- just not presented as truth
        assert svc.latest["temperature"]["value"] is not None
    finally:
        await svc.stop()


async def test_first_configured_sensor_wins_a_shared_channel(sim_config, paused_service):
    """Config order is the precedence declaration."""
    sim_config.sensors = [
        SensorConfig(driver="simulator", options={"seed": 1}, allow_uncalibrated=True),
        SensorConfig(driver="sensehat", options={}, allow_uncalibrated=True),
    ]
    svc = await paused_service(sim_config)
    try:
        assert type(svc.bindings["temperature"].driver).__name__ == "SimulatorDriver"
    finally:
        await svc.stop()


async def test_point_with_no_supplying_sensor_is_faulted_at_startup(sim_config, paused_service):
    sim_config.bacnet.enabled = True
    sim_config.points.append(
        PointConfig(channel="pressure", name="SpacePressure", instance=9))
    svc = await paused_service(sim_config)
    try:
        state = svc.bacnet.points.get("pressure")
        assert state is not None
        assert state.fault is not None
        assert "no configured sensor" in state.fault
    finally:
        await svc.stop()


async def test_status_snapshot_has_everything_the_ui_needs(service):
    await _run_cycles(service, 2)
    status = service.status()
    for key in ("device", "bacnet", "readings", "bindings", "health", "warnings"):
        assert key in status
    assert status["health"]["cycles"] == 2


async def test_scd41_between_reads_holds_value_rather_than_faulting(sim_config, paused_service):
    """A driver returning no data (SCD41's 5 s cadence) is not an error."""
    svc = await paused_service(sim_config)
    try:
        await _run_cycles(svc, 1)
        before = svc.latest["temperature"]["value"]

        driver = svc.drivers[0]
        original = driver.read
        driver.read = lambda: __import__("hoot.sensors", fromlist=["Sample"]).Sample()
        await svc._sample_once()
        driver.read = original

        assert svc.latest["temperature"]["value"] == before
        assert svc.latest["temperature"]["fault"] is None
    finally:
        await svc.stop()


async def test_agreement_is_quiet_with_a_single_temperature_sensor(service):
    """Nothing to cross-check against, so no false alarm."""
    await _run_cycles(service, 2)
    assert service.health.temperature_agreement is None
    assert service.health.agreement_alarm is False


async def test_agreeing_sensors_report_a_small_delta(sim_config, paused_service):
    sim_config.sensors = [SensorConfig(driver="simulator",
                                       options={"seed": 4, "emit_reference": True},
                                       allow_uncalibrated=True)]
    svc = await paused_service(sim_config)
    try:
        await _run_cycles(svc, 2)
        assert svc.health.temperature_agreement is not None
        assert svc.health.temperature_agreement < 0.5
        assert svc.health.agreement_alarm is False
    finally:
        await svc.stop()


async def test_diverging_sensors_raise_the_agreement_alarm(sim_config, paused_service):
    """A single sensor cannot detect its own drift; two disagreeing can."""
    sim_config.sensors = [SensorConfig(driver="simulator",
                                       options={"seed": 4, "emit_reference": True,
                                                "reference_offset_c": 3.0, "noise": 0.0},
                                       allow_uncalibrated=True)]
    svc = await paused_service(sim_config)
    try:
        await _run_cycles(svc, 2)
        assert svc.health.temperature_agreement == pytest.approx(3.0, abs=0.1)
        assert svc.health.agreement_alarm is True
        assert svc.status()["health"]["agreement_alarm"] is True
    finally:
        await svc.stop()


async def test_sensor_that_raises_non_sensor_error_on_open_does_not_stop_the_pod(
        sim_config, paused_service, monkeypatch):
    """Regression: only SensorError was caught at startup, but the Adafruit
    libraries report a missing chip as ValueError -- so an unplugged probe head
    crashed the service instead of publishing a fault."""
    from hoot.sensors import DRIVERS, ChannelSpec, SensorDriver

    class Unplugged(SensorDriver):
        key = "unplugged"

        @property
        def channels(self):
            return [ChannelSpec("temperature", "degC")]

        def open(self):
            raise ValueError("No I2C device at address: 0x44")

        def read(self):
            self.open()

    monkeypatch.setitem(DRIVERS, "unplugged", Unplugged)
    sim_config.sensors = [SensorConfig(driver="unplugged")]
    svc = await paused_service(sim_config)
    try:
        await _run_cycles(svc, 1)
        assert "No I2C device" in svc.latest["temperature"]["fault"]
    finally:
        await svc.stop()


async def test_sampling_loop_picks_up_a_new_interval_without_restart(service, monkeypatch):
    """Regression: _run() read the interval once, so a change saved from the
    web UI was reported as applied but never took effect."""
    import asyncio

    import hoot.service as service_mod

    slept: list[float] = []

    async def fake_sleep(seconds):
        slept.append(seconds)
        if len(slept) == 1:
            service.config.sampling.interval_seconds = 30.0
        else:
            raise asyncio.CancelledError

    monkeypatch.setattr(service_mod.asyncio, "sleep", fake_sleep)
    service.config.sampling.interval_seconds = 1.0
    with pytest.raises(asyncio.CancelledError):
        await service._run()
    assert slept[0] <= 1.0
    assert slept[1] > 20.0


async def test_temperature_disagreement_is_surfaced_as_a_warning(sim_config, paused_service):
    sim_config.sensors = [SensorConfig(driver="simulator",
                                       options={"seed": 4, "emit_reference": True,
                                                "reference_offset_c": 3.0, "noise": 0.0},
                                       allow_uncalibrated=True)]
    svc = await paused_service(sim_config)
    try:
        await _run_cycles(svc, 1)
        assert any("disagree" in w for w in svc.status()["warnings"])
    finally:
        await svc.stop()
