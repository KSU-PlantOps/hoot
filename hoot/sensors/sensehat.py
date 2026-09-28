"""Raspberry Pi Sense HAT (v1 HTS221 / v2 SHTC3).

Supported so an existing Sense HAT is not wasted, and because its LED matrix and
IMU are genuinely useful. **Its temperature and humidity are not trustworthy as a
space measurement** and HOOT says so everywhere they appear.

The chip is fine. The mounting is not: it sits a few millimetres above a SoC
dissipating several watts, inside the same still air. Measured error is commonly
around +17 degC / +30 degF at idle and grows under load. The usual correction --

    t_corrected = t - (t_cpu - t) / factor

-- is a one-point curve fit to a single unit in a single enclosure at a single
CPU load. Change the load, the airflow, or the orientation and it drifts, which
is precisely what a reference probe must not do.

So: this driver is flagged ``uncalibrated``. The service refuses to publish it as
a primary BACnet point unless the config explicitly sets
``allow_uncalibrated: true``, and the BACnet description and web UI carry the
warning with it.
"""

from __future__ import annotations

from .base import (
    HUMIDITY,
    TEMPERATURE,
    ChannelSpec,
    Sample,
    SensorDriver,
    SensorError,
)

CPU_TEMPERATURE = "cpu_temperature"


class SenseHatDriver(SensorDriver):
    key = "sensehat"
    display_name = "Raspberry Pi Sense HAT"
    uncalibrated = True
    uncalibrated_reason = (
        "Board-mounted sensor inside the Pi's thermal plume. Typically reads "
        "~17 degC / 30 degF high at idle and drifts with CPU load. Use an "
        "SHT45 on a cable for any measurement you intend to act on."
    )

    @property
    def channels(self) -> list[ChannelSpec]:
        return [
            ChannelSpec(TEMPERATURE, "degC", valid_min=-40.0, valid_max=85.0),
            ChannelSpec(HUMIDITY, "percentRH", valid_min=0.0, valid_max=100.0),
            # Exposed deliberately: seeing the CPU temperature next to the
            # "space" temperature makes the self-heating problem legible
            # instead of mysterious.
            ChannelSpec(CPU_TEMPERATURE, "degC", diagnostic=True,
                        valid_min=-10.0, valid_max=110.0),
        ]

    def open(self) -> None:
        if self._open:
            return
        try:
            from sense_hat import SenseHat  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover
            raise SensorError(
                "Sense HAT driver needs the sense-hat package "
                f"(not available here: {exc})."
            ) from exc
        self._dev = SenseHat()
        self._dev.low_light = True
        self._open = True

    @staticmethod
    def _cpu_temperature() -> float | None:
        try:
            with open("/sys/class/thermal/thermal_zone0/temp") as fh:
                return int(fh.read().strip()) / 1000.0
        except (OSError, ValueError):  # pragma: no cover
            return None

    def read(self) -> Sample:
        if not self._open:
            self.open()
        sample = Sample()
        try:
            temp = self._dev.get_temperature()
            rh = self._dev.get_humidity()
        except Exception as exc:
            sample.errors[TEMPERATURE] = str(exc)
            sample.errors[HUMIDITY] = str(exc)
            return sample

        for name, value in ((TEMPERATURE, temp), (HUMIDITY, rh)):
            spec = self.channel(name)
            try:
                assert spec is not None
                spec.validate(float(value))
            except SensorError as exc:
                sample.errors[name] = str(exc)
            else:
                sample.values[name] = float(value)

        cpu = self._cpu_temperature()
        if cpu is not None:
            sample.values[CPU_TEMPERATURE] = cpu
        return sample

    def show_message(self, text: str) -> None:
        """Scroll text across the 8x8 matrix -- the one thing this HAT is
        unambiguously good at."""
        if self._open:
            self._dev.show_message(text, scroll_speed=0.06)
