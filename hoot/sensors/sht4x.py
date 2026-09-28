"""Sensirion SHT40/SHT41/SHT45 temperature + humidity over I2C.

This is HOOT's reference sensor. On an SHT45 it is good for +/-0.1 degC and
+/-1 %RH, which is why it -- and never a board-mounted sensor -- is the
published source for space temperature and humidity.
"""

from __future__ import annotations

from . import i2cbus
from .base import (
    HUMIDITY,
    TEMPERATURE,
    ChannelSpec,
    Sample,
    SensorDriver,
    SensorError,
)


class SHT4xDriver(SensorDriver):
    key = "sht4x"
    display_name = "Sensirion SHT4x (SHT45/41/40)"

    #: Sensirion's "high precision, no heater" mode. The on-chip heater is for
    #: driving off condensation after a dew event; leaving it on would bias
    #: temperature high, so it stays off unless explicitly enabled.
    DEFAULT_MODE = "NOHEAT_HIGHPRECISION"

    @property
    def channels(self) -> list[ChannelSpec]:
        return [
            # Datasheet operating range is -40..125 degC; anything outside a
            # generous building range means the bus is lying to us, not that
            # the mechanical room is at 200 degrees.
            ChannelSpec(TEMPERATURE, "degC", valid_min=-40.0, valid_max=85.0),
            ChannelSpec(HUMIDITY, "percentRH", valid_min=0.0, valid_max=100.0),
        ]

    def open(self) -> None:
        if self._open:
            return
        try:
            import adafruit_sht4x  # noqa: PLC0415  (hardware-only import)
        except ImportError as exc:  # pragma: no cover - depends on platform
            raise SensorError(
                "SHT4x driver needs adafruit-circuitpython-sht4x "
                f"(not available here: {exc}). Use driver 'simulator' for "
                "off-hardware development."
            ) from exc

        i2cbus.warn_if_frequency_configured(self.options, self.key)
        self._i2c = i2cbus.acquire()
        try:
            self._dev = adafruit_sht4x.SHT4x(self._i2c)
            mode = self.options.get("mode", self.DEFAULT_MODE)
            try:
                self._dev.mode = getattr(adafruit_sht4x.Mode, mode)
            except AttributeError as exc:
                raise SensorError(f"unknown SHT4x mode {mode!r}") from exc
        except Exception:
            # Give the bus reference back: read() retries open() every cycle,
            # and a leaked reference per attempt keeps the bus open forever.
            self.close()
            raise
        self._open = True

    def close(self) -> None:
        if getattr(self, "_i2c", None) is not None:
            i2cbus.release()
            self._i2c = None
        self._open = False

    @property
    def serial_number(self) -> int | None:
        """Unique chip serial -- handy for tying a calibration record to the
        physical sensor head rather than to the Pi it happens to be plugged into."""
        dev = getattr(self, "_dev", None)
        return getattr(dev, "serial_number", None) if dev else None

    def read(self) -> Sample:
        if not self._open:
            self.open()
        sample = Sample()
        try:
            temperature, humidity = self._dev.measurements
        except Exception as exc:
            # One I2C hiccup fails the whole chip, not one channel -- the
            # SHT4x returns both values in a single transaction.
            sample.errors[TEMPERATURE] = str(exc)
            sample.errors[HUMIDITY] = str(exc)
            return sample

        for name, value in ((TEMPERATURE, temperature), (HUMIDITY, humidity)):
            spec = self.channel(name)
            try:
                assert spec is not None
                spec.validate(value)
            except SensorError as exc:
                sample.errors[name] = str(exc)
            else:
                sample.values[name] = float(value)
        return sample
