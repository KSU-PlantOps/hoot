"""Web UI and JSON API.

Serves a single dashboard page plus a small API. Kept intentionally plain --
no build step, no npm, no CDN. A field tool has to work on a laptop plugged
into an isolated BAS VLAN with no internet, so every asset is served locally.

Config changes fall into two classes:

* **Hot** -- calibration, alarm limits, display and logging settings. Applied
  without interrupting measurement.
* **Restart-required** -- anything that changes BACnet identity or object
  structure (device instance, address, point instances, sensor drivers).
  Rebuilding the BACnet stack briefly drops the device off the internetwork,
  so it is never done implicitly; the UI says so and the user confirms.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import secrets
import time
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates

import yaml

from .. import __version__, logbuffer
from ..calibration import Calibration
from ..config import Config, ConfigError, _shallow

if TYPE_CHECKING:
    from ..service import HootService

log = logging.getLogger(__name__)

TEMPLATES = Path(__file__).parent / "templates"

#: Content types accepted for a YAML config upload. None of these is a
#: CORS-safelisted type, so a cross-site page cannot submit one without a
#: preflight, and this app answers no preflights -- that is the CSRF defence
#: for an endpoint that takes a raw body rather than JSON.
YAML_TYPES = {"application/yaml", "application/x-yaml", "text/yaml", "text/x-yaml"}

LOG_LEVELS = {"DEBUG": logging.DEBUG, "INFO": logging.INFO,
              "WARNING": logging.WARNING, "ERROR": logging.ERROR}

#: Config keys that need a service restart to take effect (mirrors
#: ``_restart_required``). ``web.host`` and ``web.port`` additionally need the
#: whole process restarted, since the HTTP server itself is bound to them.
RESTART_KEYS = {
    "bacnet.*", "device.*", "web.*", "sensors", "units.temperature",
    "points.channel", "points.instance", "points.enabled", "points.name",
    "logging.enabled", "logging.database", "display.enabled",
}


def create_app(service: "HootService") -> FastAPI:
    app = FastAPI(
        title="HOOT",
        description="Humidity & Operational Observation Terminal",
        version=__version__,
        docs_url="/api/docs",
        redoc_url=None,
    )
    templates = Jinja2Templates(directory=str(TEMPLATES))
    logbuffer.install()
    # Strong references to fire-and-forget tasks; asyncio keeps only weak ones,
    # so an unreferenced restart task could be garbage-collected mid-flight.
    background: set[asyncio.Task] = set()
    security = HTTPBasic(auto_error=False)

    def require_auth(credentials: HTTPBasicCredentials | None = Depends(security)) -> None:
        web = service.config.web
        if not web.auth_enabled:
            return
        expected = web.password
        if credentials is None or expected is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="authentication required",
                headers={"WWW-Authenticate": "Basic"},
            )
        # compare_digest on both fields, always, so response time does not
        # leak whether the username happened to be right.
        ok_user = secrets.compare_digest(credentials.username, web.username)
        ok_pass = secrets.compare_digest(credentials.password, expected)
        if not (ok_user and ok_pass):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid credentials",
                headers={"WWW-Authenticate": "Basic"},
            )

    # ---- pages ------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request, _: None = Depends(require_auth)):
        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context={"status": service.status(), "config": service.config},
        )

    # ---- live data --------------------------------------------------------

    @app.get("/api/status")
    async def api_status(_: None = Depends(require_auth)):
        return service.status()

    @app.get("/api/history")
    async def api_history(
        channel: str,
        hours: float = Query(6.0, gt=0, le=24 * 365),
        limit: int = Query(2000, gt=0, le=50_000),
        _: None = Depends(require_auth),
    ):
        if not service.config.logging.enabled:
            raise HTTPException(400, "local logging is disabled on this unit")
        since = time.time() - hours * 3600
        # Downsample long windows so the chart spans the whole range rather
        # than just the most recent ``limit`` rows.
        bucket = hours * 3600 / limit
        rows = await asyncio.to_thread(
            service.store.history, channel, since, None, limit,
            bucket if bucket > service.config.logging.interval_seconds else 0.0,
        )
        return {"channel": channel, "hours": hours, "points": rows}

    @app.get("/api/channels")
    async def api_channels(_: None = Depends(require_auth)):
        return {
            "live": sorted(service.bindings),
            "logged": await asyncio.to_thread(service.store.channels),
        }

    @app.get("/api/export.csv")
    async def api_export(
        hours: float = Query(24.0, gt=0, le=24 * 365),
        channels: str = "",
        _: None = Depends(require_auth),
    ):
        if not service.config.logging.enabled:
            raise HTTPException(400, "local logging is disabled on this unit")
        wanted = [c.strip() for c in channels.split(",") if c.strip()] or None
        since = time.time() - hours * 3600
        name = service.config.device.name.replace(" ", "_")
        stamp = time.strftime("%Y%m%d-%H%M%S")

        def generate():
            # Streamed so a 90-day export never has to fit in the Pi's RAM.
            yield from service.store.export_csv(wanted, since, None)

        return StreamingResponse(
            generate(),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{name}_{stamp}.csv"'},
        )

    # ---- configuration ----------------------------------------------------

    @app.get("/api/config")
    async def api_get_config(_: None = Depends(require_auth)):
        return {
            "config": service.config.to_dict(),
            "warnings": service.config.warnings(),
            "path": str(service.config.path) if service.config.path else None,
            "restart_keys": sorted(RESTART_KEYS),
        }

    def replace_config(payload: Any, dry_run: bool = False) -> dict[str, Any]:
        """Validate, then (unless ``dry_run``) save and apply what can be applied
        live. Never persists a config that would fail to load on next boot."""
        if not isinstance(payload, dict):
            raise HTTPException(400, "invalid configuration: expected a mapping of sections")
        try:
            new = Config.from_dict(payload, path=service.config.path)
            new.validate()
        except (ConfigError, ValueError, KeyError, TypeError, AttributeError) as exc:
            raise HTTPException(400, f"invalid configuration: {exc}") from exc

        needs_restart = _restart_required(service.config, new)
        if dry_run:
            return {"saved": False, "valid": True, "restart_required": needs_restart,
                    "hot_applied": [], "warnings": new.warnings()}
        try:
            new.save()
        except OSError as exc:
            raise HTTPException(500, f"could not write config: {exc}") from exc

        applied = _apply_hot(service, new)
        return {
            "saved": True,
            "valid": True,
            "restart_required": needs_restart,
            "hot_applied": applied,
            "warnings": new.warnings(),
        }

    @app.post("/api/config")
    async def api_set_config(payload: dict[str, Any], _: None = Depends(require_auth)):
        """Replace the config from JSON (what the Settings form sends)."""
        return replace_config(payload)

    @app.get("/api/config.yaml")
    async def api_get_config_yaml(_: None = Depends(require_auth)):
        """The full config as YAML -- the same shape as config.yaml on disk.
        Contains no secrets: passwords live in environment variables."""
        return Response(
            _config_yaml(service.config),
            media_type="application/yaml",
            headers={"Content-Disposition":
                     f'attachment; filename="{_filename(service, "config", "yaml")}"'},
        )

    @app.post("/api/config.yaml")
    async def api_set_config_yaml(
        request: Request,
        dry_run: bool = False,
        _: None = Depends(require_auth),
    ):
        """Replace the whole config from YAML. ``?dry_run=true`` validates only.

        This is the escape hatch for everything the Settings form does not
        cover: sensors, points, web, display, logging.
        """
        content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
        if content_type not in YAML_TYPES:
            raise HTTPException(415, f"send the config as one of: {', '.join(sorted(YAML_TYPES))}")
        body = await request.body()
        try:
            payload = yaml.safe_load(body.decode("utf-8"))
        except (UnicodeDecodeError, yaml.YAMLError) as exc:
            raise HTTPException(400, f"invalid YAML: {exc}") from exc
        return replace_config(payload, dry_run=dry_run)

    @app.post("/api/calibrate")
    async def api_calibrate(payload: dict[str, Any], _: None = Depends(require_auth)):
        """Single- or two-point calibration for one channel.

        Single point:  {"channel": "temperature", "reference": 71.8}
        Two point:     {"channel": "temperature", "raw_low": .., "reference_low": ..,
                        "raw_high": .., "reference_high": ..}

        ``reference`` is in the unit the point is *published* in; the offset is
        stored in canonical units, so switching degF/degC later keeps it valid.
        """
        from ..units import convert

        channel = payload.get("channel")
        point = next((p for p in service.config.points if p.channel == channel), None)
        if point is None:
            raise HTTPException(404, f"no configured point for channel {channel!r}")
        binding = service.bindings.get(channel)
        if binding is None:
            raise HTTPException(400, f"channel {channel!r} has no sensor bound")

        note = payload.get("note", "")
        try:
            if "reference" in payload:
                latest = service.latest.get(channel) or {}
                raw = latest.get("raw")
                if raw is None:
                    raise HTTPException(409, "no current reading to calibrate against")
                reference_pub = float(payload["reference"])
                reference_canonical = convert(
                    reference_pub, binding.published_unit, binding.canonical_unit
                )
                point.calibration = Calibration.from_single_point(
                    raw, reference_canonical, note or f"single-point {time.strftime('%Y-%m-%d')}"
                )
            else:
                def to_canon(v: Any) -> float:
                    return convert(float(v), binding.published_unit, binding.canonical_unit)

                point.calibration = Calibration.from_two_point(
                    float(payload["raw_low"]), to_canon(payload["reference_low"]),
                    float(payload["raw_high"]), to_canon(payload["reference_high"]),
                    note or f"two-point {time.strftime('%Y-%m-%d')}",
                )
        except KeyError as exc:
            raise HTTPException(400, f"missing field {exc}") from None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

        _save(service.config)
        if service.config.logging.enabled:
            service.store.log_event(
                "info", "calibration",
                f"{channel}: {point.calibration.describe(' ' + binding.canonical_unit)} ({note})",
            )
        return {
            "channel": channel,
            "calibration": point.calibration.to_dict(),
            "describe": point.calibration.describe(" " + binding.canonical_unit),
        }

    @app.post("/api/calibrate/clear")
    async def api_calibrate_clear(payload: dict[str, Any], _: None = Depends(require_auth)):
        channel = payload.get("channel")
        point = next((p for p in service.config.points if p.channel == channel), None)
        if point is None:
            raise HTTPException(404, f"no configured point for channel {channel!r}")
        point.calibration = Calibration()
        _save(service.config)
        return {"channel": channel, "calibration": point.calibration.to_dict()}

    @app.post("/api/restart")
    async def api_restart(request: Request, _: None = Depends(require_auth)):
        """Rebuild the service so structural config changes take effect.

        Briefly removes the device from the BACnet internetwork, so this is an
        explicit user action rather than something a config save does silently.
        """
        # Require a JSON request (the UI sends one). A bodiless POST could be
        # fired by a plain HTML form on any site the operator visits.
        if not request.headers.get("content-type", "").startswith("application/json"):
            raise HTTPException(415, "send Content-Type: application/json")
        task = asyncio.create_task(_restart(service))
        background.add(task)
        task.add_done_callback(background.discard)
        return {"restarting": True}

    # ---- logs & diagnostics -----------------------------------------------

    @app.get("/api/logs")
    async def api_logs(
        level: str = Query("INFO", pattern="^(DEBUG|INFO|WARNING|ERROR)$"),
        limit: int = Query(500, gt=0, le=logbuffer.CAPACITY),
        _: None = Depends(require_auth),
    ):
        """Recent service log lines, oldest first. In memory: since last start."""
        entries = logbuffer.BUFFER.entries(LOG_LEVELS[level], limit)
        return {"level": level, "capacity": logbuffer.CAPACITY, "lines": entries}

    @app.get("/api/logs/download")
    async def api_logs_download(
        level: str = Query("DEBUG", pattern="^(DEBUG|INFO|WARNING|ERROR)$"),
        _: None = Depends(require_auth),
    ):
        return Response(
            _log_text(service, LOG_LEVELS[level]),
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition":
                     f'attachment; filename="{_filename(service, "service", "log")}"'},
        )

    @app.get("/api/events")
    async def api_events(
        limit: int = Query(100, gt=0, le=5000),
        _: None = Depends(require_auth),
    ):
        """The persistent event log: startups, calibrations, sensor warnings."""
        if not service.config.logging.enabled:
            return {"events": [], "logging_enabled": False}
        rows = await asyncio.to_thread(service.store.events, None, limit)
        return {"events": rows, "logging_enabled": True}

    @app.get("/api/events.csv")
    async def api_events_csv(
        hours: float = Query(24 * 90, gt=0, le=24 * 3650),
        _: None = Depends(require_auth),
    ):
        if not service.config.logging.enabled:
            raise HTTPException(400, "local logging is disabled on this unit")
        since = time.time() - hours * 3600
        return StreamingResponse(
            service.store.export_events_csv(since),
            media_type="text/csv",
            headers={"Content-Disposition":
                     f'attachment; filename="{_filename(service, "events", "csv")}"'},
        )

    @app.get("/api/support-bundle.zip")
    async def api_support_bundle(_: None = Depends(require_auth)):
        """Config, status, service log and event log in one file -- the thing
        to attach to an email when asking someone else to look at a unit."""
        data = await asyncio.to_thread(_support_bundle, service)
        return Response(
            data,
            media_type="application/zip",
            headers={"Content-Disposition":
                     f'attachment; filename="{_filename(service, "support", "zip")}"'},
        )

    @app.get("/api/health")
    async def api_health():
        """Unauthenticated liveness probe for monitoring systems."""
        health = service.health
        stale_after = service.config.sampling.interval_seconds * 4
        last = health.last_sample_at
        ok = last is not None and (time.time() - last) < stale_after
        faults = [ch for ch, r in service.latest.items() if r.get("fault")]
        return JSONResponse(
            status_code=200 if ok else 503,
            content={
                "ok": ok,
                "device": service.config.device.name,
                "uptime_seconds": round(health.uptime_seconds, 1),
                "cycles": health.cycles,
                "read_errors": health.read_errors,
                "faulted_channels": faults,
                "seconds_since_sample": None if last is None else round(time.time() - last, 2),
            },
        )

    return app


# ---- helpers --------------------------------------------------------------


def _filename(service: "HootService", kind: str, ext: str) -> str:
    name = "".join(c if c.isalnum() or c in "-_." else "_" for c in service.config.device.name)
    return f"{name}_{kind}_{time.strftime('%Y%m%d-%H%M%S')}.{ext}"


def _config_yaml(config: Config) -> str:
    return yaml.safe_dump(config.to_dict(), sort_keys=False, allow_unicode=True)


def _log_text(service: "HootService", min_level: int = logging.NOTSET) -> str:
    header = (
        f"# HOOT {__version__} service log -- {service.config.device.name}\n"
        f"# captured {time.strftime('%Y-%m-%d %H:%M:%S %Z')}; the last "
        f"{logbuffer.CAPACITY} lines at most, since the service last started.\n"
        "# Older history: the events log, or `journalctl -u hoot` on the unit.\n"
    )
    return header + logbuffer.BUFFER.text(min_level)


def _support_bundle(service: "HootService") -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("config.yaml", _config_yaml(service.config))
        zf.writestr("status.json", json.dumps(service.status(), indent=2, default=str))
        zf.writestr("service.log", _log_text(service))
        if service.config.logging.enabled:
            zf.writestr("events.csv", "".join(service.store.export_events_csv()))
    return out.getvalue()


def _save(config: Config) -> None:
    try:
        config.save()
    except (ConfigError, OSError) as exc:
        raise HTTPException(500, f"could not write config: {exc}") from exc


def _restart_required(old: Config, new: Config) -> bool:
    # Every BACnet setting, and the device identity fields that feed the BACnet
    # device object (name, location, description), are baked in at startup.
    if (_shallow(old.bacnet) != _shallow(new.bacnet)
            or _shallow(old.device) != _shallow(new.device)
            or _shallow(old.web) != _shallow(new.web)
            or old.units.temperature != new.units.temperature
            or old.logging.enabled != new.logging.enabled
            or old.logging.database != new.logging.database
            or old.display.enabled != new.display.enabled):
        return True
    if [(s.driver, s.options) for s in old.sensors] != [(s.driver, s.options) for s in new.sensors]:
        return True
    old_points = {(p.channel, p.instance, p.name, p.enabled) for p in old.points}
    new_points = {(p.channel, p.instance, p.name, p.enabled) for p in new.points}
    return old_points != new_points


def _apply_hot(service: "HootService", new: Config) -> list[str]:
    """Apply the changes that do not need a restart. Returns what changed."""
    applied: list[str] = []
    old = service.config

    if old.sampling.interval_seconds != new.sampling.interval_seconds:
        old.sampling.interval_seconds = new.sampling.interval_seconds
        applied.append("sampling interval")
    if old.logging.interval_seconds != new.logging.interval_seconds:
        old.logging.interval_seconds = new.logging.interval_seconds
        applied.append("trend interval")
    if old.logging.retention_days != new.logging.retention_days:
        old.logging.retention_days = new.logging.retention_days
        service.store.retention_days = new.logging.retention_days
        applied.append("retention")
    if old.display.rotate_seconds != new.display.rotate_seconds:
        old.display.rotate_seconds = new.display.rotate_seconds
        applied.append("display rotation")

    # Calibration and alarm limits are per-point and always hot.
    by_channel = {p.channel: p for p in new.points}
    for point in old.points:
        incoming = by_channel.get(point.channel)
        if incoming is None:
            continue
        if point.calibration.to_dict() != incoming.calibration.to_dict():
            point.calibration = incoming.calibration
            applied.append(f"{point.name} calibration")
        if (point.high_limit, point.low_limit) != (incoming.high_limit, incoming.low_limit):
            point.high_limit, point.low_limit = incoming.high_limit, incoming.low_limit
            applied.append(f"{point.name} limits")
        if point.cov_increment != incoming.cov_increment:
            point.cov_increment = incoming.cov_increment
            state = service.bacnet.points.get(point.channel)
            if state is not None:
                state.obj.covIncrement = incoming.cov_increment
            applied.append(f"{point.name} COV increment")
        if point.description != incoming.description:
            point.description = incoming.description
            applied.append(f"{point.name} description")

    return applied


async def _restart(service: "HootService") -> None:
    """Stop and rebuild the service in place."""
    from ..config import load_config
    from ..service import HootService as _Service

    await asyncio.sleep(0.2)  # let the HTTP response flush first
    log.info("restarting service on request")
    try:
        await service.stop()
        fresh = load_config(service.config.path) if service.config.path else service.config
        rebuilt = _Service(fresh)
        service.__dict__.update(rebuilt.__dict__)
        await service.start()
        log.info("service restarted")
    except Exception:
        log.exception("restart failed")
