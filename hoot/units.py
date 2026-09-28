"""Unit conversion, applied once at the publish boundary.

Drivers always report SI (see ``hoot.sensors.base``). Everything downstream --
BACnet present-values, the web UI, CSV export -- converts here, so there is
exactly one place where a degC/degF mistake can happen.
"""

from __future__ import annotations

from .sensors import CO2, HUMIDITY, PRESSURE, TEMPERATURE

#: BACnet EngineeringUnits enumeration names, as bacpypes3 spells them.
BACNET_UNITS = {
    "degC": "degreesCelsius",
    "degF": "degreesFahrenheit",
    "percentRH": "percentRelativeHumidity",
    "ppm": "partsPerMillion",
    "hPa": "hectopascals",
    "inHg": "inchesOfMercury",
}

#: Short symbols for the UI and the OLED.
SYMBOLS = {
    "degC": "°C",
    "degF": "°F",
    "percentRH": "%RH",
    "ppm": "ppm",
    "hPa": "hPa",
    "inHg": "inHg",
}


def c_to_f(celsius: float) -> float:
    return celsius * 9.0 / 5.0 + 32.0


def f_to_c(fahrenheit: float) -> float:
    return (fahrenheit - 32.0) * 5.0 / 9.0


def convert(value: float, from_unit: str, to_unit: str) -> float:
    """Convert between supported units. Identity conversions are free."""
    if from_unit == to_unit:
        return value
    pair = (from_unit, to_unit)
    if pair == ("degC", "degF"):
        return c_to_f(value)
    if pair == ("degF", "degC"):
        return f_to_c(value)
    if pair == ("hPa", "inHg"):
        return value * 0.029529983071445
    if pair == ("inHg", "hPa"):
        return value / 0.029529983071445
    raise ValueError(f"no conversion from {from_unit!r} to {to_unit!r}")


def delta_convert(delta: float, from_unit: str, to_unit: str) -> float:
    """Convert a *difference*, not an absolute reading.

    Offsets and COV increments are intervals: 1 degC of span is 1.8 degF, not
    33.8 degF. Using ``convert`` on a calibration offset is a classic and
    quiet bug, so intervals get their own function.
    """
    if from_unit == to_unit:
        return delta
    pair = (from_unit, to_unit)
    if pair == ("degC", "degF"):
        return delta * 9.0 / 5.0
    if pair == ("degF", "degC"):
        return delta * 5.0 / 9.0
    return convert(delta, from_unit, to_unit)


def display_unit(
    channel: str, temperature_unit: str = "degF", canonical: str | None = None
) -> str:
    """The unit a channel is published in, given the site's temperature preference.

    Channels this module does not recognise are published in ``canonical`` --
    the unit their driver reports -- so a new driver's channel passes through
    unconverted instead of being mislabelled as a temperature.
    """
    if channel.endswith(TEMPERATURE):
        return temperature_unit
    if channel.endswith(HUMIDITY):
        return "percentRH"
    known = {CO2: "ppm", PRESSURE: "hPa"}.get(channel)
    return known or canonical or "noUnits"
