import pytest
from fastapi.testclient import TestClient

from hoot.web.app import create_app


@pytest.fixture
async def client(sim_config, paused_service):
    sim_config.web.enabled = True
    svc = await paused_service(sim_config)
    await svc._sample_once()
    with TestClient(create_app(svc)) as c:
        c.service = svc
        yield c
    await svc.stop()


async def test_dashboard_renders(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "HOOT" in r.text
    assert client.service.config.device.name in r.text


async def test_status_endpoint_shape(client):
    body = client.get("/api/status").json()
    assert set(body) >= {"device", "bacnet", "readings", "bindings", "health", "warnings"}
    assert "temperature" in body["readings"]


async def test_health_endpoint_is_ok_when_sampling(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


async def test_health_reports_503_when_sampling_has_stalled(client):
    client.service.health.last_sample_at = 0.0  # ancient
    r = client.get("/api/health")
    assert r.status_code == 503
    assert r.json()["ok"] is False


async def test_history_returns_points(client):
    client.service.config.logging.interval_seconds = 0.0
    await client.service._sample_once()
    body = client.get("/api/history?channel=temperature&hours=1").json()
    assert body["channel"] == "temperature"
    assert len(body["points"]) >= 1


async def test_csv_export_streams_a_header(client):
    client.service.config.logging.interval_seconds = 0.0
    await client.service._sample_once()
    r = client.get("/api/export.csv?hours=1")
    assert r.status_code == 200
    assert r.text.splitlines()[0].startswith("timestamp_iso")
    assert "attachment" in r.headers["content-disposition"]


async def test_single_point_calibration_via_api(client):
    reading = client.service.latest["temperature"]
    target = reading["value"] + 2.0
    r = client.post("/api/calibrate",
                    json={"channel": "temperature", "reference": target, "note": "t"})
    assert r.status_code == 200
    # 2 degF of published offset is stored as 2/1.8 degC canonically
    assert r.json()["calibration"]["offset"] == pytest.approx(2.0 / 1.8, abs=1e-3)


async def test_calibration_rejects_unknown_channel(client):
    r = client.post("/api/calibrate", json={"channel": "nope", "reference": 1.0})
    assert r.status_code == 404


async def test_calibration_can_be_cleared(client):
    client.service.config.points[0].calibration.offset = 5.0
    r = client.post("/api/calibrate/clear", json={"channel": "temperature"})
    assert r.status_code == 200
    assert client.service.config.points[0].calibration.is_identity


async def test_invalid_config_is_rejected_with_a_reason(client):
    bad = client.service.config.to_dict()
    bad["points"][1]["instance"] = bad["points"][0]["instance"]
    r = client.post("/api/config", json=bad)
    assert r.status_code == 400
    assert "duplicate" in r.json()["detail"].lower()


async def test_hot_config_change_applies_without_restart(client):
    cfg = client.service.config.to_dict()
    cfg["sampling"]["interval_seconds"] = 7.0
    cfg["logging"]["interval_seconds"] = 60.0   # must stay >= sampling
    body = client.post("/api/config", json=cfg).json()
    assert body["saved"] is True
    assert body["restart_required"] is False
    assert "sampling interval" in body["hot_applied"]
    assert client.service.config.sampling.interval_seconds == 7.0


async def test_bacnet_identity_change_is_flagged_restart_required(client):
    cfg = client.service.config.to_dict()
    cfg["bacnet"]["device_instance"] = 1234
    body = client.post("/api/config", json=cfg).json()
    assert body["restart_required"] is True


async def test_auth_blocks_when_enabled(sim_config, paused_service, monkeypatch):
    monkeypatch.setenv("HOOT_WEB_PASSWORD", "s3cret")
    sim_config.web.auth_enabled = True
    sim_config.web.username = "hoot"
    svc = await paused_service(sim_config)
    try:
        with TestClient(create_app(svc)) as c:
            assert c.get("/api/status").status_code == 401
            assert c.get("/api/status", auth=("hoot", "wrong")).status_code == 401
            assert c.get("/api/status", auth=("hoot", "s3cret")).status_code == 200
            # liveness probe stays open for monitoring systems: it may report
            # 503 before the first sample, but must never demand credentials
            assert c.get("/api/health").status_code != 401
    finally:
        await svc.stop()


async def test_trend_faster_than_sampling_is_rejected(client):
    """Guards the rule that you cannot trend faster than you sample."""
    cfg = client.service.config.to_dict()
    cfg["sampling"]["interval_seconds"] = 10.0
    cfg["logging"]["interval_seconds"] = 2.0
    r = client.post("/api/config", json=cfg)
    assert r.status_code == 400
    assert "cannot trend faster than you sample" in r.json()["detail"]


async def test_device_name_change_is_flagged_restart_required(client):
    """The name is the BACnet device objectName, fixed at startup."""
    cfg = client.service.config.to_dict()
    cfg["device"]["name"] = "HOOT-99"
    body = client.post("/api/config", json=cfg).json()
    assert body["restart_required"] is True


async def test_cov_increment_change_is_applied_live(client):
    cfg = client.service.config.to_dict()
    cfg["points"][0]["cov_increment"] = 0.25
    body = client.post("/api/config", json=cfg).json()
    assert body["restart_required"] is False
    assert any("COV" in item for item in body["hot_applied"])
    assert client.service.config.points[0].cov_increment == 0.25


async def test_calibration_does_not_create_a_database_when_logging_is_off(
        sim_config, paused_service):
    sim_config.web.enabled = True
    sim_config.logging.enabled = False
    svc = await paused_service(sim_config)
    try:
        await svc._sample_once()
        with TestClient(create_app(svc)) as c:
            target = svc.latest["temperature"]["value"] + 1.0
            r = c.post("/api/calibrate", json={"channel": "temperature", "reference": target})
            assert r.status_code == 200
        from pathlib import Path
        assert not Path(sim_config.logging.database).exists()
    finally:
        await svc.stop()


async def test_openapi_reports_the_software_version(client):
    from hoot import __version__
    assert client.get("/openapi.json").json()["info"]["version"] == __version__
