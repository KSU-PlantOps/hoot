"""Texas Instruments TMP117 / TMP119 high-precision temperature sensor.

Temperature only, and deliberately so. It does not replace the SHT45 -- it sits
beside it as a second, independent temperature reading.

**Why a second temperature sensor is worth $15.** A single sensor cannot tell
you that it has drifted, been contaminated, or come loose in its head; it just
keeps reporting confidently. Two sensors built on different physical principles
by different manufacturers cannot drift in the same direction by the same amount
for the same reason. When they disagree by more than their combined uncertainty,
something is genuinely wrong, and HOOT can say so on its own rather than waiting
for a technician to notice at the next annual check.

That is what makes "no routine calibration" a defensible position rather than an
optimistic one: the verification is continuous and automatic.

Accuracy, from the TI datasheets:

    TMP119   +-0.03 degC typ, +-0.08 degC max    0 .. 45 degC
    TMP117   +-0.1 degC max                     -20 .. 50 degC
    SHT45    +-0.1 degC typ (max is a curve)

The TMP119's *worst case* is better than the SHT45's *typical*, which matters
for a probe whose job is to settle an argument.

Both parts share TI's register map, so one driver covers them.
"""

from __future__ import annotations

import logging

from . import i2cbus
from .base import ChannelSpec, Sample, SensorDriver, SensorError

log = logging.getLogger(__name__)

#: Channel name kept distinct from the SHT45's ``temperature`` so both are
#: recorded, comparable, and independently publishable.
REFERENCE_TEMPERATURE = "reference_temperature"

_REG_TEMP_RESULT = 0x00
_REG_CONFIG = 0x01
_REG_DEVICE_ID = 0x0F

#: Both parts report 7.8125 m degC per LSB (16-bit, two's complement).
_LSB_DEG_C = 0.0078125

#: Whole-register values from register 0x0F, NOT masked.
#:
#: This was wrong on the first pass and is worth recording. The TMP119 does not
#: return 0x0119 -- it returns **0x2117**, and the TMP117 returns 0x0117. The
#: original code masked with 0x0FFF, which collapses 0x2117 to 0x117 and so
#: silently reported every TMP119 as a "TMP117". It never raised, it just
#: quietly mislabelled the part. Do not reintroduce the mask: the high nibble is
#: the only thing that distinguishes these two devices.
_DEVICE_IDS = {0x0117: "TMP117", 0x2117: "TMP119"}


class TMP11xDriver(SensorDriver):
    key = "tmp11x"
    display_name = "TI TMP119 / TMP117 precision temperature"

    #: Default I2C address with ADD0 tied low. 0x49-0x4B are the alternatives,
    #: which matters only if you fit two of them.
    DEFAULT_ADDRESS = 0x48

    @property
    def channels(self) -> list[ChannelSpec]:
        return [
            ChannelSpec(REFERENCE_TEMPERATURE, "degC", valid_min=-55.0, valid_max=150.0),
        ]

    def open(self) -> None:
        if self._open:
            return
        try:
            from adafruit_bus_device.i2c_device import I2CDevice  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover - hardware only
            raise SensorError(
                "TMP11x driver needs adafruit-circuitpython-busdevice "
                f"(not available here: {exc}). Use driver 'simulator' off-hardware."
            ) from exc

        i2cbus.warn_if_frequency_configured(self.options, self.key)
        self._address = int(self.options.get("address", self.DEFAULT_ADDRESS))
        self._i2c = i2cbus.acquire()
        try:
            self._dev = I2CDevice(self._i2c, self._address)
            device_id = self._read_register(_REG_DEVICE_ID)
        except Exception:
            # Give the bus reference back: read() retries open() every cycle.
            self.close()
            raise

        self.part = _DEVICE_IDS.get(device_id)
        if self.part is None:
            # Don't hard-fail on an unrecognised revision -- the register map is
            # stable across this family, so read it and say what we saw.
            self.part = f"unknown (device id 0x{device_id:04X})"
        log.info("TMP11x at 0x%02X identified as %s", self._address, self.part)
        self._open = True

    def close(self) -> None:
        if getattr(self, "_i2c", None) is not None:
            i2cbus.release()
            self._i2c = None
        self._open = False

    def _read_register(self, register: int) -> int:
        buf = bytearray(2)
        with self._dev as dev:
            dev.write_then_readinto(bytes([register]), buf)
        return (buf[0] << 8) | buf[1]

    def read(self) -> Sample:
        if not self._open:
            self.open()
        sample = Sample()
        try:
            raw = self._read_register(_REG_TEMP_RESULT)
        except Exception as exc:
            sample.errors[REFERENCE_TEMPERATURE] = str(exc)
            return sample

        if raw & 0x8000:          # two's complement
            raw -= 1 << 16
        value = raw * _LSB_DEG_C

        spec = self.channel(REFERENCE_TEMPERATURE)
        try:
            assert spec is not None
            spec.validate(value)
        except SensorError as exc:
            sample.errors[REFERENCE_TEMPERATURE] = str(exc)
        else:
            sample.values[REFERENCE_TEMPERATURE] = value
        return sample
