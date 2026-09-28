"""Per-channel field calibration.

Applied to canonical (SI) values before anything is published or logged, so a
calibration is a property of the sensor head and survives a change of display
units.

Two forms, both stored the same way:

* **Single-point offset** -- the common case. Sit HOOT next to a reference
  instrument, let both settle, record the difference.
* **Two-point** -- solve gain and offset from readings at two known values
  (e.g. an ice bath and a warm reference). More work, better across a range.

    corrected = raw * gain + offset
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class Calibration:
    """A linear correction for one channel, in canonical units."""

    offset: float = 0.0
    gain: float = 1.0
    #: Free text: who calibrated it, against what, when. Written by the web UI.
    note: str = ""

    def apply(self, raw: float) -> float:
        return raw * self.gain + self.offset

    @property
    def is_identity(self) -> bool:
        return self.gain == 1.0 and self.offset == 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "Calibration":
        if not data:
            return cls()
        return cls(
            offset=float(data.get("offset", 0.0)),
            gain=float(data.get("gain", 1.0)),
            note=str(data.get("note", "")),
        )

    @classmethod
    def from_single_point(cls, raw: float, reference: float, note: str = "") -> "Calibration":
        """Offset-only correction so ``raw`` reads as ``reference``."""
        return cls(offset=reference - raw, gain=1.0, note=note)

    @classmethod
    def from_two_point(
        cls,
        raw_low: float,
        reference_low: float,
        raw_high: float,
        reference_high: float,
        note: str = "",
    ) -> "Calibration":
        """Solve gain and offset from two (raw, reference) pairs."""
        span = raw_high - raw_low
        if abs(span) < 1e-9:
            raise ValueError(
                "two-point calibration needs two distinct raw readings; "
                f"got {raw_low} and {raw_high}"
            )
        gain = (reference_high - reference_low) / span
        offset = reference_low - raw_low * gain
        return cls(offset=offset, gain=gain, note=note)

    def describe(self, unit: str = "") -> str:
        if self.is_identity:
            return "uncalibrated (raw)"
        parts = []
        if self.gain != 1.0:
            parts.append(f"gain x{self.gain:.5g}")
        if self.offset:
            parts.append(f"offset {self.offset:+.4g}{unit}")
        return ", ".join(parts)
