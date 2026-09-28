"""Configuration: load, validate, save.

A gitignored ``config.yaml`` sits beside a committed ``config.example.yaml``;
secrets live in environment variables, never in the file.

The config is also written at runtime by the web UI, so saving is a
first-class operation: it round-trips through the same dataclasses
that validate it, and writes atomically so a power cut mid-save cannot leave a
unit unbootable.
"""

from __future__ import annotations

import os
import re
import secrets
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .calibration import Calibration
from .sensors import DRIVERS

#: BACnet device instance numbers are 22-bit; 4194303 is reserved.
MAX_DEVICE_INSTANCE = 4_194_302

#: ASHRAE assigns vendor IDs. 999 is the reserved "test" ID used by BACpypes
#: samples -- fine on a lab bench, wrong on a production internetwork where two
#: vendors sharing an ID makes device management ambiguous. HOOT ships with it
#: and warns until the site sets its own.
TEST_VENDOR_ID = 999

VALID_TEMPERATURE_UNITS = {"degF", "degC"}


class ConfigError(Exception):
    """Configuration is malformed or internally inconsistent."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ConfigError(message)


@dataclass
class DeviceConfig:
    name: str = "HOOT-01"
    location: str = ""
    description: str = "HOOT portable environmental probe"
    #: Free-text asset tag, surfaced in the UI and BACnet description.
    asset_tag: str = ""

    def validate(self) -> None:
        _require(bool(self.name.strip()), "device.name must not be empty")
        # BACnet object names are CharacterString; keep them portable across
        # workstation software that dislikes exotic characters in point names.
        _require(
            re.fullmatch(r"[A-Za-z0-9 _\-.]{1,63}", self.name) is not None,
            "device.name must be 1-63 chars of letters, digits, space, _ - or .",
        )


@dataclass
class BACnetConfig:
    enabled: bool = True
    #: Must be unique across the whole BACnet internetwork.
    device_instance: int = 599001
    #: "auto" picks the primary interface and its real netmask, which is what
    #: BACnet broadcast (Who-Is/I-Am) requires to work correctly.
    address: str = "auto"
    port: int = 47808
    vendor_identifier: int = TEST_VENDOR_ID
    vendor_name: str = "HOOT"
    model_name: str = "HOOT Environmental Probe"
    #: BBMD address for foreign-device registration, when the pod sits on a
    #: different subnet from the BAS and broadcasts will not cross the router.
    bbmd_address: str = ""
    bbmd_ttl_seconds: int = 900
    #: Advertised segmentation / APDU behaviour.
    max_apdu_length: int = 1024
    segmentation: str = "segmentedBoth"

    def validate(self) -> None:
        _require(
            0 <= self.device_instance <= MAX_DEVICE_INSTANCE,
            f"bacnet.device_instance must be 0..{MAX_DEVICE_INSTANCE}",
        )
        _require(1 <= self.port <= 65535, "bacnet.port must be 1..65535")
        _require(
            self.address == "auto" or "/" in self.address,
            "bacnet.address must be 'auto' or CIDR like '10.1.2.3/24' -- the "
            "prefix length is required so broadcasts reach the right subnet",
        )
        _require(self.bbmd_ttl_seconds > 0, "bacnet.bbmd_ttl_seconds must be positive")

    @property
    def uses_test_vendor_id(self) -> bool:
        return self.vendor_identifier == TEST_VENDOR_ID


@dataclass
class PointConfig:
    """One sensor channel published as one BACnet analog input."""

    channel: str
    name: str
    instance: int
    enabled: bool = True
    description: str = ""
    #: Minimum change before a COV notification is issued, in published units.
    cov_increment: float = 0.1
    calibration: Calibration = field(default_factory=Calibration)
    #: Optional alarm limits, in published units. None disables that limit.
    high_limit: float | None = None
    low_limit: float | None = None

    def validate(self) -> None:
        _require(bool(self.channel), "point.channel must be set")
        _require(
            re.fullmatch(r"[A-Za-z0-9 _\-.]{1,63}", self.name) is not None,
            f"point name {self.name!r} must be 1-63 chars of letters, digits, space, _ - or .",
        )
        _require(0 <= self.instance <= MAX_DEVICE_INSTANCE,
                 f"point {self.name!r}: instance out of range")
        _require(self.cov_increment >= 0, f"point {self.name!r}: cov_increment must be >= 0")
        if self.high_limit is not None and self.low_limit is not None:
            _require(
                self.low_limit < self.high_limit,
                f"point {self.name!r}: low_limit must be below high_limit",
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "name": self.name,
            "instance": self.instance,
            "enabled": self.enabled,
            "description": self.description,
            "cov_increment": self.cov_increment,
            "calibration": self.calibration.to_dict(),
            "high_limit": self.high_limit,
            "low_limit": self.low_limit,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PointConfig":
        try:
            return cls(
                channel=data["channel"],
                name=data["name"],
                instance=int(data["instance"]),
                enabled=bool(data.get("enabled", True)),
                description=str(data.get("description", "")),
                cov_increment=float(data.get("cov_increment", 0.1)),
                calibration=Calibration.from_dict(data.get("calibration")),
                high_limit=_opt_float(data.get("high_limit")),
                low_limit=_opt_float(data.get("low_limit")),
            )
        except KeyError as exc:
            raise ConfigError(f"point is missing required key {exc}") from None


def _opt_float(value: Any) -> float | None:
    return None if value is None or value == "" else float(value)


@dataclass
class SensorConfig:
    driver: str
    options: dict[str, Any] = field(default_factory=dict)
    #: Publishing an uncalibrated driver (Sense HAT, simulator) as a primary
    #: point requires opting in, so nobody trends a 30 degF error by accident.
    allow_uncalibrated: bool = False

    def validate(self) -> None:
        _require(
            self.driver in DRIVERS,
            f"unknown sensor driver {self.driver!r}; known: {', '.join(sorted(DRIVERS))}",
        )


@dataclass
class SamplingConfig:
    """How often sensors are read. This also sets the BACnet update rate."""

    interval_seconds: float = 5.0

    def validate(self) -> None:
        _require(
            self.interval_seconds >= 1.0,
            "sampling.interval_seconds must be >= 1.0 (the SCD41 only produces "
            "a new reading every 5 s; faster polling just burns CPU)",
        )


@dataclass
class LoggingConfig:
    enabled: bool = True
    #: Trend interval. Independent of the sample rate on purpose: the BAS wants
    #: live values every few seconds, but a 90-day local trend at 5 s would be
    #: 1.5M rows per channel for no analytical benefit.
    interval_seconds: float = 60.0
    retention_days: int = 90
    database: str = "data/hoot.db"
    #: Log the min/max/mean seen between trend samples rather than a single
    #: instantaneous value, so a 60 s trend cannot miss a 10 s excursion.
    aggregate: bool = True

    def validate(self) -> None:
        _require(self.interval_seconds >= 1.0, "logging.interval_seconds must be >= 1.0")
        _require(self.retention_days >= 0, "logging.retention_days must be >= 0")
        _require(bool(self.database), "logging.database must be set")


@dataclass
class WebConfig:
    enabled: bool = True
    host: str = "0.0.0.0"
    port: int = 8080
    #: When set, the UI requires this password. Read from the env var named in
    #: ``password_env`` -- never stored in the config file.
    auth_enabled: bool = False
    username: str = "hoot"
    password_env: str = "HOOT_WEB_PASSWORD"

    def validate(self) -> None:
        _require(1 <= self.port <= 65535, "web.port must be 1..65535")
        if self.auth_enabled:
            _require(
                bool(os.environ.get(self.password_env)),
                f"web.auth_enabled is true but ${self.password_env} is not set",
            )

    @property
    def password(self) -> str | None:
        return os.environ.get(self.password_env)


@dataclass
class DisplayConfig:
    enabled: bool = True
    driver: str = "ssd1306"
    width: int = 128
    height: int = 64
    address: int = 0x3C
    #: Seconds per page before the display advances.
    rotate_seconds: float = 5.0
    #: Reserved for OLED burn-in protection; not implemented yet, so any
    #: value is accepted and ignored. Kept so existing configs still load.
    dim_after_seconds: float = 0.0

    def validate(self) -> None:
        _require(self.rotate_seconds > 0, "display.rotate_seconds must be positive")


@dataclass
class UnitsConfig:
    temperature: str = "degF"

    def validate(self) -> None:
        _require(
            self.temperature in VALID_TEMPERATURE_UNITS,
            f"units.temperature must be one of {sorted(VALID_TEMPERATURE_UNITS)}",
        )


@dataclass
class Config:
    device: DeviceConfig = field(default_factory=DeviceConfig)
    bacnet: BACnetConfig = field(default_factory=BACnetConfig)
    units: UnitsConfig = field(default_factory=UnitsConfig)
    sensors: list[SensorConfig] = field(default_factory=list)
    points: list[PointConfig] = field(default_factory=list)
    sampling: SamplingConfig = field(default_factory=SamplingConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    web: WebConfig = field(default_factory=WebConfig)
    display: DisplayConfig = field(default_factory=DisplayConfig)
    #: Where this was loaded from, for saving back.
    path: Path | None = None

    # ---- validation -------------------------------------------------------

    def validate(self) -> None:
        for section in (
            self.device, self.bacnet, self.units, self.sampling,
            self.logging, self.web, self.display,
        ):
            section.validate()
        for sensor in self.sensors:
            sensor.validate()
        for point in self.points:
            point.validate()

        _require(bool(self.sensors), "at least one sensor must be configured")

        instances = [p.instance for p in self.points]
        dupes = {i for i in instances if instances.count(i) > 1}
        _require(not dupes, f"duplicate BACnet point instances: {sorted(dupes)}")

        names = [p.name.lower() for p in self.points]
        dupe_names = {n for n in names if names.count(n) > 1}
        _require(not dupe_names, f"duplicate point names: {sorted(dupe_names)}")

        _require(
            self.logging.interval_seconds >= self.sampling.interval_seconds,
            "logging.interval_seconds must be >= sampling.interval_seconds -- "
            "you cannot trend faster than you sample",
        )

    def warnings(self) -> list[str]:
        """Non-fatal problems worth showing in the UI and the startup log."""
        out: list[str] = []
        if self.bacnet.enabled and self.bacnet.uses_test_vendor_id:
            out.append(
                f"BACnet vendor_identifier is {TEST_VENDOR_ID} (the BACpypes test ID). "
                "Fine for a bench, but set your own ASHRAE-assigned ID before "
                "putting units on a production internetwork."
            )
        for sensor in self.sensors:
            cls = DRIVERS.get(sensor.driver)
            if cls and cls.uncalibrated and not sensor.allow_uncalibrated:
                out.append(
                    f"sensor '{sensor.driver}' is flagged uncalibrated "
                    f"({cls.uncalibrated_reason}) and allow_uncalibrated is false, "
                    "so its channels will not be published to BACnet."
                )
        if self.web.enabled and not self.web.auth_enabled:
            out.append(
                "Web UI has no authentication. Acceptable on an isolated BAS "
                "VLAN; set web.auth_enabled and $HOOT_WEB_PASSWORD otherwise."
            )
        if not self.points:
            out.append("No points configured -- this unit will not publish anything.")
        return out

    # ---- serialisation ----------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "device": _shallow(self.device),
            "bacnet": _shallow(self.bacnet),
            "units": _shallow(self.units),
            "sensors": [
                {"driver": s.driver, "options": s.options,
                 "allow_uncalibrated": s.allow_uncalibrated}
                for s in self.sensors
            ],
            "points": [p.to_dict() for p in self.points],
            "sampling": _shallow(self.sampling),
            "logging": _shallow(self.logging),
            "web": _shallow(self.web),
            "display": _shallow(self.display),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], path: Path | None = None) -> "Config":
        data = data or {}
        cfg = cls(
            device=_build(DeviceConfig, data.get("device")),
            bacnet=_build(BACnetConfig, data.get("bacnet")),
            units=_build(UnitsConfig, data.get("units")),
            sensors=[
                SensorConfig(
                    driver=s["driver"],
                    options=dict(s.get("options") or {}),
                    allow_uncalibrated=bool(s.get("allow_uncalibrated", False)),
                )
                for s in (data.get("sensors") or [])
            ],
            points=[PointConfig.from_dict(p) for p in (data.get("points") or [])],
            sampling=_build(SamplingConfig, data.get("sampling")),
            logging=_build(LoggingConfig, data.get("logging")),
            web=_build(WebConfig, data.get("web")),
            display=_build(DisplayConfig, data.get("display")),
            path=path,
        )
        return cfg

    def save(self, path: Path | None = None, backup: bool = True) -> Path:
        """Validate, then write atomically.

        The web UI writes this file on a device that may lose PoE at any moment,
        so the new config lands in a temp file on the same filesystem and is
        renamed over the original -- a rename is atomic, a partial write is not.
        """
        self.validate()
        target = Path(path or self.path or "config.yaml")
        target.parent.mkdir(parents=True, exist_ok=True)

        if backup and target.exists():
            shutil.copy2(target, target.with_suffix(target.suffix + ".bak"))

        tmp = target.with_name(f".{target.name}.{secrets.token_hex(4)}.tmp")
        try:
            with tmp.open("w", encoding="utf-8") as fh:
                yaml.safe_dump(self.to_dict(), fh, sort_keys=False, allow_unicode=True)
                fh.flush()
                os.fsync(fh.fileno())
            tmp.replace(target)
        finally:
            tmp.unlink(missing_ok=True)

        self.path = target
        return target


def _shallow(obj: Any) -> dict[str, Any]:
    """dataclass -> plain dict, one level deep (no nested dataclasses here)."""
    return {k: v for k, v in obj.__dict__.items() if not k.startswith("_")}


def _build(cls: type, data: dict[str, Any] | None) -> Any:
    """Instantiate a config dataclass, ignoring unknown keys but reporting them."""
    data = data or {}
    known = {f for f in cls.__dataclass_fields__}
    unknown = set(data) - known
    if unknown:
        raise ConfigError(
            f"{cls.__name__}: unknown option(s) {sorted(unknown)}; "
            f"valid options are {sorted(known)}"
        )
    return cls(**data)


def load_config(path: str | Path | None = None) -> Config:
    """Load config from ``path``, ``$HOOT_CONFIG``, or ./config.yaml."""
    resolved = Path(path or os.environ.get("HOOT_CONFIG") or "config.yaml")
    if not resolved.exists():
        raise ConfigError(
            f"config file not found: {resolved}\n"
            "Copy config.example.yaml to config.yaml and edit it, or run "
            "'hoot init' to generate one."
        )
    try:
        with resolved.open(encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        raise ConfigError(f"{resolved}: invalid YAML: {exc}") from exc

    cfg = Config.from_dict(raw, path=resolved)
    cfg.validate()
    return cfg


def default_device_instance() -> int:
    """Derive a stable, likely-unique BACnet device instance from the Pi serial.

    Two HOOTs with the same instance number will fight on the internetwork in
    ways that are miserable to diagnose, so the default is derived from hardware
    rather than being the same number on every unit out of the box. It is still
    only a default -- site standards should assign real numbers.
    """
    serial = ""
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("Serial"):
                    serial = line.split(":", 1)[1].strip()
                    break
    except OSError:
        pass
    if not serial:
        return 599001
    # Keep it inside the valid 22-bit range, offset away from 0 to avoid the
    # low numbers sites often reserve for controllers.
    return 100_000 + (int(serial[-8:], 16) % 4_000_000)
