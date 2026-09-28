"""Sensor driver registry.

Drivers are looked up by their ``key`` so config files stay declarative:

    sensors:
      - driver: sht4x
      - driver: scd4x
        options: {altitude_m: 335}
"""

from __future__ import annotations

from typing import Any

from .base import (
    CANONICAL_UNITS,
    CO2,
    HUMIDITY,
    PRESSURE,
    TEMPERATURE,
    ChannelSpec,
    Sample,
    SensorDriver,
    SensorError,
)
from .scd4x import SCD_HUMIDITY, SCD_TEMPERATURE, SCD4xDriver
from .sensehat import CPU_TEMPERATURE, SenseHatDriver
from .sht4x import SHT4xDriver
from .tmp11x import REFERENCE_TEMPERATURE, TMP11xDriver
from .simulator import SimulatorDriver

DRIVERS: dict[str, type[SensorDriver]] = {
    cls.key: cls
    for cls in (SHT4xDriver, SCD4xDriver, TMP11xDriver, SenseHatDriver, SimulatorDriver)
}

__all__ = [
    "CANONICAL_UNITS", "CO2", "CPU_TEMPERATURE", "DRIVERS", "HUMIDITY",
    "PRESSURE", "REFERENCE_TEMPERATURE", "SCD_HUMIDITY", "SCD_TEMPERATURE", "TEMPERATURE",
    "ChannelSpec", "Sample", "SensorDriver", "SensorError", "build_driver",
    "SHT4xDriver", "SCD4xDriver", "TMP11xDriver", "SenseHatDriver", "SimulatorDriver",
]


def build_driver(key: str, options: dict[str, Any] | None = None) -> SensorDriver:
    """Instantiate a driver by config key."""
    try:
        cls = DRIVERS[key]
    except KeyError:
        known = ", ".join(sorted(DRIVERS))
        raise SensorError(f"unknown sensor driver {key!r}; known drivers: {known}") from None
    return cls(**(options or {}))
