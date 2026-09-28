import csv
import io
import json
import logging
import zipfile

import pytest
import yaml
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


# --- logs, events, full config, support bundle -----------------------------

async def test_logs_endpoint_returns_recent_service_log(client):
    logging.getLogger("hoot.test").warning("probe lead unplugged")
    body = client.get("/api/logs?level=WARNING").json()
    assert any("probe lead unplugged" in e["line"] for e in body["lines"])
    assert all(e["levelno"] >= logging.WARNING for e in body["lines"])


async def test_log_download_is_an_attachment_with_a_header(client):
    logging.getLogger("hoot.test").warning("for the download")
    r = client.get("/api/logs/download")
    assert r.status_code == 200
    assert "attachment" in r.headers["content-disposition"]
    assert r.headers["content-disposition"].endswith('.log"')
    assert r.text.startswith("# HOOT ")
    assert "for the download" in r.text


async def test_events_are_listed_and_exported_as_quoted_csv(client):
    client.service.store.log_event("warning", "test", 'disagree, by "0.6" degC')
    events = client.get("/api/events").json()["events"]
    assert any(e["source"] == "service" and "starting" in e["message"] for e in events)

    r = client.get("/api/events.csv")
    assert r.status_code == 200
    rows = list(csv.reader(io.StringIO(r.text)))
    assert rows[0] == ["timestamp_iso", "timestamp_epoch", "level", "source", "message"]
    assert ["warning", "test", 'disagree, by "0.6" degC'] in [row[2:] for row in rows[1:]]


async def test_config_yaml_download_round_trips(client):
    r = client.get("/api/config.yaml")
    assert r.status_code == 200
    assert "attachment" in r.headers["content-disposition"]
    assert yaml.safe_load(r.text) == client.service.config.to_dict()


async def test_config_yaml_dry_run_validates_without_saving(client):
    cfg = client.service.config.to_dict()
    cfg["points"][0]["description"] = "changed in dry run"
    r = client.post("/api/config.yaml?dry_run=true", content=yaml.safe_dump(cfg),
                    headers={"Content-Type": "application/yaml"})
    assert r.status_code == 200
    assert r.json()["saved"] is False and r.json()["valid"] is True
    assert client.service.config.points[0].description != "changed in dry run"
    assert not client.service.config.path.exists()


async def test_config_yaml_upload_saves_and_applies(client):
    cfg = client.service.config.to_dict()
    cfg["points"][0]["high_limit"] = 90.0
    r = client.post("/api/config.yaml", content=yaml.safe_dump(cfg),
                    headers={"Content-Type": "text/yaml"})
    assert r.status_code == 200
    assert r.json()["saved"] is True
    assert client.service.config.points[0].high_limit == 90.0
    assert yaml.safe_load(client.service.config.path.read_text())["points"][0]["high_limit"] == 90.0


async def test_config_yaml_upload_rejects_bad_input(client):
    headers = {"Content-Type": "application/yaml"}
    assert client.post("/api/config.yaml", content="device: {name: 'x", headers=headers).status_code == 400
    assert client.post("/api/config.yaml", content="- just a list", headers=headers).status_code == 400
    r = client.post("/api/config.yaml", content="device: nonsense", headers=headers)
    assert r.status_code == 400
    assert "invalid configuration" in r.json()["detail"]


async def test_config_yaml_upload_refuses_form_content_types(client):
    """CSRF: a cross-site HTML form can POST text/plain with no preflight, so
    only YAML content types -- which always need a preflight -- are accepted."""
    body = yaml.safe_dump(client.service.config.to_dict())
    for ctype in ("text/plain", "application/x-www-form-urlencoded", "multipart/form-data"):
        r = client.post("/api/config.yaml", content=body, headers={"Content-Type": ctype})
        assert r.status_code == 415, ctype


async def test_restart_refuses_a_bodiless_form_post(client):
    r = client.post("/api/restart", headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert r.status_code == 415


async def test_support_bundle_contains_config_status_logs_and_events(client):
    r = client.get("/api/support-bundle.zip")
    assert r.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    assert set(zf.namelist()) == {"config.yaml", "status.json", "service.log", "events.csv"}
    assert json.loads(zf.read("status.json"))["device"]["name"] == client.service.config.device.name


async def test_new_endpoints_require_auth_when_enabled(sim_config, paused_service, monkeypatch):
    monkeypatch.setenv("HOOT_WEB_PASSWORD", "s3cret")
    sim_config.web.auth_enabled = True
    svc = await paused_service(sim_config)
    try:
        with TestClient(create_app(svc)) as c:
            for path in ("/api/logs", "/api/logs/download", "/api/events", "/api/events.csv",
                         "/api/config.yaml", "/api/support-bundle.zip"):
                assert c.get(path).status_code == 401, path
    finally:
        await svc.stop()
