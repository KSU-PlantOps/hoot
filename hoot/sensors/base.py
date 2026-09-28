"""
Sensor driver interface.

Every driver speaks the same canonical units, no matter what the chip reports:

    temperature -> degrees Celsius
    humidity    -> percent relative humidity
    co2         -> parts per million
    pressure    -> hectopascals

Conversion to display/BACnet units (e.g. degF) happens once, at publish time, in
``hoot.units``. Keeping the drivers in SI means calibration offsets, stored
history, and cross-sensor comparisons are all in one coordinate system, and a
config change from degC to degF never silently corrupts logged data.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

# Canonical channel names. Drivers advertise a subset of these.
TEMPERATURE = "temperature"
HUMIDITY = "humidity"
CO2 = "co2"
PRESSURE = "pressure"

CANONICAL_UNITS = {
    TEMPERATURE: "degC",
    HUMIDITY: "percentRH",
    CO2: "ppm",
    PRESSURE: "hPa",
}


class SensorError(Exception):
    """A read failed. Carries the reason through to BACnet reliability."""


@dataclass
class ChannelSpec:
    """What a driver can produce on one channel."""

    name: str
    unit: str
    #: Diagnostic channels are read and logged but never published as the
    #: primary source for their quantity. The SCD41's own temperature is the
    #: motivating case: it self-heats, so it is useful for health monitoring
    #: and useless as a space temperature.
    diagnostic: bool = False
    #: Plausible range. Readings outside this are treated as a sensor fault
    #: rather than published, which is what stops a wiring fault from
    #: propagating a garbage setpoint into the BAS.
    valid_min: float = float("-inf")
    valid_max: float = float("inf")

    def validate(self, value: float) -> None:
        if not (self.valid_min <= value <= self.valid_max):
            raise SensorError(
                f"{self.name}={value:.3f}{self.unit} outside plausible range "
                f"[{self.valid_min}, {self.valid_max}]"
            )


@dataclass
class Sample:
    """One synchronous read across all of a driver's channels."""

    values: dict[str, float] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    #: Per-channel error strings for channels that failed this cycle.
    errors: dict[str, str] = field(default_factory=dict)

    def ok(self, channel: str) -> bool:
        return channel in self.values and channel not in self.errors


class SensorDriver(ABC):
    """Base class for a physical (or simulated) sensor."""

    #: Short identifier used in config files: ``driver: sht4x``
    key: str = "base"
    #: Human-readable name for the UI.
    display_name: str = "Sensor"
    #: Set on drivers whose readings must not be trusted as a reference.
    #: The web UI and BACnet description surface this loudly.
    uncalibrated: bool = False
    #: Why it is uncalibrated, shown to the user.
    uncalibrated_reason: str = ""

    def __init__(self, **options: Any) -> None:
        self.options = options
        self._open = False

    @property
    @abstractmethod
    def channels(self) -> list[ChannelSpec]:
        """Channels this driver provides."""

    def open(self) -> None:
        """Acquire the bus/device. Safe to call repeatedly."""
        self._open = True

    def close(self) -> None:
        self._open = False

    @abstractmethod
    def read(self) -> Sample:
        """Take one reading. Must not raise for a single bad channel -- record
        it in ``Sample.errors`` instead so healthy channels still publish."""

    def channel(self, name: str) -> ChannelSpec | None:
        for spec in self.channels:
            if spec.name == name:
                return spec
        return None

    def __enter__(self) -> "SensorDriver":
        self.open()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def __repr__(self) -> str:
        chans = ",".join(c.name for c in self.channels)
        return f"<{type(self).__name__} {chans}>"
