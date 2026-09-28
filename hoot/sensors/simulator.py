"""Synthetic sensor for development, CI, demos, and BAS-side integration tests.

Generates plausible building data -- a diurnal temperature swing, humidity that
moves inversely to it, and CO2 that tracks an occupancy schedule -- so the whole
stack (BACnet objects, logging, web UI, alarms) can be exercised end to end with
no hardware attached.

That matters beyond convenience: it means the BAS integrator can point a
workstation at a HOOT instance and validate their trend logs and graphics before
a single unit is mounted.
"""

from __future__ import annotations

import math
import random
import time

from .base import (
    CO2,
    HUMIDITY,
    TEMPERATURE,
    ChannelSpec,
    Sample,
    SensorDriver,
)
from .tmp11x import REFERENCE_TEMPERATURE


class SimulatorDriver(SensorDriver):
    key = "simulator"
    display_name = "Simulator (synthetic data)"
    uncalibrated = True
    uncalibrated_reason = (
        "Synthetic data. This unit is not measuring anything physical."
    )

    def __init__(self, **options) -> None:
        super().__init__(**options)
        self._base_temp_c = float(options.get("base_temp_c", 22.2))     # ~72 F
        self._swing_c = float(options.get("swing_c", 1.5))
        self._base_rh = float(options.get("base_rh", 45.0))
        self._outdoor_co2 = float(options.get("outdoor_co2", 420.0))
        self._occupied_co2 = float(options.get("occupied_co2", 950.0))
        self._noise = float(options.get("noise", 0.05))
        self._fail_rate = float(options.get("fail_rate", 0.0))
        # Emit a second, independent temperature channel so the SHT45/TMP119
        # cross-check can be exercised without hardware. ``reference_offset_c``
        # injects a deliberate disagreement for testing the alarm path.
        self._emit_reference = bool(options.get("emit_reference", False))
        self._reference_offset = float(options.get("reference_offset_c", 0.0))
        seed = options.get("seed")
        self._rng = random.Random(seed)
        # Lets tests drive the clock instead of waiting on wall time.
        self._clock = options.get("clock", time.time)

    @property
    def channels(self) -> list[ChannelSpec]:
        specs = [
            ChannelSpec(TEMPERATURE, "degC", valid_min=-40.0, valid_max=85.0),
            ChannelSpec(HUMIDITY, "percentRH", valid_min=0.0, valid_max=100.0),
            ChannelSpec(CO2, "ppm", valid_min=300.0, valid_max=40_000.0),
        ]
        if self._emit_reference:
            specs.append(ChannelSpec(REFERENCE_TEMPERATURE, "degC",
                                     valid_min=-55.0, valid_max=150.0))
        return specs

    def _occupancy(self, hour: float) -> float:
        """0..1 occupancy curve: ramps up at 7, peaks midday, gone by 18."""
        if hour < 6.5 or hour > 18.5:
            return 0.0
        # A smooth hump across the occupied band.
        return max(0.0, math.sin((hour - 6.5) / 12.0 * math.pi))

    def read(self) -> Sample:
        sample = Sample()
        now = self._clock()

        if self._fail_rate and self._rng.random() < self._fail_rate:
            msg = "simulated I2C read failure"
            for spec in self.channels:
                sample.errors[spec.name] = msg
            return sample

        hour = (now % 86_400) / 3600.0
        occ = self._occupancy(hour)

        # Temperature: slow diurnal swing plus a little sensor noise.
        temp = (
            self._base_temp_c
            + self._swing_c * math.sin((hour - 9.0) / 24.0 * 2 * math.pi)
            + self._rng.gauss(0.0, self._noise)
        )
        # Humidity moves opposite temperature in a conditioned space.
        rh = self._base_rh - (temp - self._base_temp_c) * 2.0 + self._rng.gauss(0.0, self._noise * 4)
        rh = min(100.0, max(0.0, rh))
        # CO2 follows occupancy with a lag toward the occupied ceiling.
        co2 = self._outdoor_co2 + (self._occupied_co2 - self._outdoor_co2) * occ
        co2 += self._rng.gauss(0.0, self._noise * 40)
        co2 = max(300.0, co2)

        sample.values = {TEMPERATURE: temp, HUMIDITY: rh, CO2: co2}
        if self._emit_reference:
            sample.values[REFERENCE_TEMPERATURE] = (
                temp + self._reference_offset + self._rng.gauss(0.0, self._noise * 0.4)
            )
        sample.timestamp = now
        return sample
