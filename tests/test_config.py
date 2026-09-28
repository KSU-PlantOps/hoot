import pytest
import yaml

from hoot.config import (
    ConfigError,
    DeviceConfig,
    SensorConfig,
    _build,
    load_config,
)


def test_rejects_duplicate_point_instances(sim_config):
    sim_config.points[1].instance = sim_config.points[0].instance
    with pytest.raises(ConfigError, match="duplicate BACnet point instances"):
        sim_config.validate()


def test_rejects_duplicate_point_names(sim_config):
    sim_config.points[1].name = sim_config.points[0].name
    with pytest.raises(ConfigError, match="duplicate point names"):
        sim_config.validate()


def test_rejects_trending_faster_than_sampling(sim_config):
    sim_config.sampling.interval_seconds = 10.0
    sim_config.logging.interval_seconds = 5.0
    with pytest.raises(ConfigError, match="cannot trend faster than you sample"):
        sim_config.validate()


def test_rejects_unknown_driver(sim_config):
    sim_config.sensors = [SensorConfig(driver="nonesuch")]
    with pytest.raises(ConfigError, match="unknown sensor driver"):
        sim_config.validate()


def test_rejects_out_of_range_device_instance(sim_config):
    sim_config.bacnet.device_instance = 9_999_999
    with pytest.raises(ConfigError, match="device_instance"):
        sim_config.validate()


def test_rejects_address_without_prefix_length(sim_config):
    sim_config.bacnet.address = "10.1.2.3"
    with pytest.raises(ConfigError, match="prefix length is required"):
        sim_config.validate()


def test_rejects_inverted_alarm_limits(sim_config):
    sim_config.points[0].low_limit = 90.0
    sim_config.points[0].high_limit = 50.0
    with pytest.raises(ConfigError, match="low_limit must be below high_limit"):
        sim_config.validate()


def test_typo_in_config_key_is_reported_not_ignored():
    """A silently-ignored typo means a setting the operator believes is applied
    but is not -- worse than a startup failure."""
    with pytest.raises(ConfigError, match="unknown option"):
        _build(DeviceConfig, {"nmae": "typo"})


def test_save_and_reload_round_trips(sim_config, tmp_path):
    target = tmp_path / "roundtrip.yaml"
    sim_config.points[0].calibration.offset = 0.75
    sim_config.points[0].calibration.note = "bench"
    sim_config.save(target, backup=False)

    reloaded = load_config(target)
    assert reloaded.device.name == sim_config.device.name
    assert reloaded.points[0].calibration.offset == pytest.approx(0.75)
    assert reloaded.points[0].calibration.note == "bench"
    assert [s.driver for s in reloaded.sensors] == ["simulator"]


def test_save_is_atomic_and_leaves_no_temp_files(sim_config, tmp_path):
    target = tmp_path / "atomic.yaml"
    sim_config.save(target, backup=False)
    leftovers = [p.name for p in tmp_path.iterdir() if ".tmp" in p.name]
    assert leftovers == []


def test_save_writes_backup_of_previous(sim_config, tmp_path):
    target = tmp_path / "cfg.yaml"
    sim_config.save(target, backup=False)
    sim_config.device.location = "changed"
    sim_config.save(target, backup=True)
    backup = target.with_suffix(".yaml.bak")
    assert backup.exists()
    assert yaml.safe_load(backup.read_text())["device"]["location"] != "changed"


def test_invalid_config_is_never_written(sim_config, tmp_path):
    target = tmp_path / "never.yaml"
    sim_config.points[1].instance = sim_config.points[0].instance
    with pytest.raises(ConfigError):
        sim_config.save(target, backup=False)
    assert not target.exists()


def test_warns_about_test_vendor_id(sim_config):
    sim_config.bacnet.enabled = True
    assert any("vendor_identifier" in w for w in sim_config.warnings())


def test_warns_when_uncalibrated_driver_not_opted_in(sim_config):
    sim_config.sensors[0].allow_uncalibrated = False
    assert any("uncalibrated" in w for w in sim_config.warnings())


def test_missing_config_file_gives_actionable_message(tmp_path):
    with pytest.raises(ConfigError, match="hoot init"):
        load_config(tmp_path / "absent.yaml")


def test_malformed_yaml_is_reported(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("device: {name: 'unterminated\n")
    with pytest.raises(ConfigError, match="invalid YAML"):
        load_config(bad)
