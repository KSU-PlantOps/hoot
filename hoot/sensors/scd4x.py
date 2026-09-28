"""Sensirion SCD40/SCD41 photoacoustic NDIR CO2 sensor.

Publishes CO2 only. The chip also reports temperature and humidity, but it
self-heats by several degrees, so those are exposed as *diagnostic* channels:
useful as a health signal, never as a space measurement.
"""

from __future__ import annotations

from . import i2cbus
from .base import (
    CO2,
    ChannelSpec,
    Sample,
    SensorDriver,
    SensorError,
)

#: Channel names for the SCD4x's own T/RH, kept distinct from the published
#: SHT45 channels so nothing downstream can confuse the two.
SCD_TEMPERATURE = "scd_temperature"
SCD_HUMIDITY = "scd_humidity"


class SCD4xDriver(SensorDriver):
    key = "scd4x"
    display_name = "Sensirion SCD-41 (true NDIR CO2)"

    @property
    def channels(self) -> list[ChannelSpec]:
        return [
            # 400 ppm is roughly clean outdoor air and the sensor's own
            # calibration floor; below that means a bad read, not fresh air.
            ChannelSpec(CO2, "ppm", valid_min=300.0, valid_max=40_000.0),
            ChannelSpec(SCD_TEMPERATURE, "degC", diagnostic=True,
                        valid_min=-10.0, valid_max=60.0),
            ChannelSpec(SCD_HUMIDITY, "percentRH", diagnostic=True,
                        valid_min=0.0, valid_max=100.0),
        ]

    def open(self) -> None:
        if self._open:
            return
        try:
            import adafruit_scd4x  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover
            raise SensorError(
                "SCD4x driver needs adafruit-circuitpython-scd4x "
                f"(not available here: {exc}). Use driver 'simulator' instead."
            ) from exc

        i2cbus.warn_if_frequency_configured(self.options, self.key)
        self._i2c = i2cbus.acquire()
        try:
            self._configure(adafruit_scd4x.SCD4X(self._i2c))
        except Exception:
            # Give the bus reference back: read() retries open() every cycle.
            self._dev = None
            self.close()
            raise
        self._open = True

    def _configure(self, dev) -> None:
        self._dev = dev

        # Altitude compensation materially affects NDIR accuracy: the reading
        # is pressure-dependent. Set altitude_m to the site's elevation.
        altitude_m = self.options.get("altitude_m")
        if altitude_m is not None:
            self._dev.altitude = int(altitude_m)

        # A permanent offset the chip subtracts from its own temperature. We
        # do not publish its temperature, so this stays 0 by default rather
        # than baking a fudge factor into the hardware.
        self._dev.temperature_offset = float(self.options.get("temperature_offset", 0.0))

        # Automatic self-calibration assumes the sensor sees 400 ppm outdoor
        # air regularly. That is true for a probe carried between sites and
        # false for one sealed in an occupied room for weeks, so it is a
        # config decision rather than a default.
        asc = self.options.get("automatic_self_calibration")
        if asc is not None:
            self._dev.self_calibration_enabled = bool(asc)

        self._dev.start_periodic_measurement()

    def close(self) -> None:
        dev = getattr(self, "_dev", None)
        if dev is not None:
            try:
                dev.stop_periodic_measurement()
            except Exception:  # pragma: no cover
                pass
        if getattr(self, "_i2c", None) is not None:
            i2cbus.release()
            self._i2c = None
        self._open = False

    def force_recalibration(self, reference_ppm: float) -> None:
        """Field calibration: sit the head in known-concentration air (outdoors,
        ~420 ppm) until stable, then anchor the sensor to that value."""
        if not self._open:
            self.open()
        self._dev.stop_periodic_measurement()
        try:
            self._dev.force_calibration(int(reference_ppm))
        finally:
            self._dev.start_periodic_measurement()

    def read(self) -> Sample:
        if not self._open:
            self.open()
        sample = Sample()

        # The SCD41 samples on its own 5 s cadence. If HOOT polls faster than
        # that, data_ready is False and we simply have nothing new this cycle
        # -- that is not an error, so report no values and no errors and let
        # the caller hold the previous value.
        try:
            if not self._dev.data_ready:
                return sample
            readings = (
                (CO2, self._dev.CO2),
                (SCD_TEMPERATURE, self._dev.temperature),
                (SCD_HUMIDITY, self._dev.relative_humidity),
            )
        except Exception as exc:
            sample.errors[CO2] = str(exc)
            return sample

        for name, value in readings:
            spec = self.channel(name)
            try:
                assert spec is not None
                spec.validate(float(value))
            except SensorError as exc:
                sample.errors[name] = str(exc)
            else:
                sample.values[name] = float(value)
        return sample
