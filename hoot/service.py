"""The sampling loop: sensors -> calibration -> BACnet + trend + display.

One asyncio task owns the cadence. Sensor reads are synchronous I2C calls, so
they run in a worker thread to keep the BACnet stack responsive -- a blocked
event loop means missed Who-Is replies, and a pod that vanishes from discovery
scans is a pod nobody trusts.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from . import __version__
from .bacnet import BACnetServer
from .config import Config
from .sensors import (
    REFERENCE_TEMPERATURE,
    TEMPERATURE,
    Sample,
    SensorDriver,
    build_driver,
)
from .store import Accumulator, Store
from .units import convert, display_unit

log = logging.getLogger(__name__)


@dataclass
class ChannelBinding:
    """Which driver supplies a channel, and how it is published."""

    channel: str
    driver: SensorDriver
    canonical_unit: str
    published_unit: str
    #: False when the supplying driver is flagged uncalibrated and the config
    #: has not opted in. The channel is still read and trended -- just not
    #: presented to the BAS as if it were a measurement.
    publishable: bool = True
    reason: str = ""


@dataclass
class Health:
    """Rolling health, surfaced in the web UI and on the OLED."""

    started_at: float = field(default_factory=time.time)
    cycles: int = 0
    read_errors: int = 0
    last_sample_at: float | None = None
    last_trend_at: float | None = None
    trend_rows: int = 0
    last_error: str = ""
    #: |SHT45 - TMP119| in published units, when both are fitted. A single
    #: sensor cannot detect its own drift; two built on different physical
    #: principles disagreeing is an unambiguous fault signal.
    temperature_agreement: float | None = None
    agreement_alarm: bool = False

    @property
    def uptime_seconds(self) -> float:
        return time.time() - self.started_at


class HootService:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.bacnet = BACnetServer(config)
        self.store = Store(config.logging.database, config.logging.retention_days)
        self.drivers: list[SensorDriver] = []
        self.bindings: dict[str, ChannelBinding] = {}
        self.accumulators: dict[str, Accumulator] = {}
        self.latest: dict[str, dict[str, Any]] = {}
        self.health = Health()
        self.display = None
        self._task: asyncio.Task | None = None
        self._stopping = asyncio.Event()
        self._last_trend = 0.0

    # ---- setup ------------------------------------------------------------

    def _build_drivers(self) -> None:
        for spec in self.config.sensors:
            driver = build_driver(spec.driver, spec.options)
            try:
                driver.open()
            except Exception as exc:
                # A missing sensor must not stop the pod: the other channels
                # still have work to do, and the fault is visible over BACnet.
                # Catch broadly -- an absent chip surfaces from the Adafruit
                # libraries as ValueError/OSError, not SensorError. read()
                # retries open() every cycle, so a head plugged in later
                # recovers without a restart.
                log.error("sensor %s failed to open: %s", spec.driver, exc)
                self.health.last_error = str(exc)
            self.drivers.append(driver)
            self._bind_channels(driver, spec.allow_uncalibrated)

    def _bind_channels(self, driver: SensorDriver, allow_uncalibrated: bool) -> None:
        """Map each channel to the driver that supplies it.

        First driver in config order wins, so the config file is the precedence
        declaration. An uncalibrated driver never silently displaces a good one.
        """
        for spec in driver.channels:
            existing = self.bindings.get(spec.name)
            if existing is not None:
                if existing.publishable:
                    log.info(
                        "channel %s already supplied by %s; ignoring %s",
                        spec.name, type(existing.driver).__name__, type(driver).__name__,
                    )
                    continue
                log.info("channel %s: %s replaces unpublishable %s",
                         spec.name, type(driver).__name__, type(existing.driver).__name__)

            # Drivers report in canonical units by contract, so the channel
            # spec's own unit *is* the canonical unit.
            published = display_unit(spec.name, self.config.units.temperature, spec.unit)
            publishable = not (driver.uncalibrated and not allow_uncalibrated)
            self.bindings[spec.name] = ChannelBinding(
                channel=spec.name,
                driver=driver,
                canonical_unit=spec.unit,
                published_unit=published,
                publishable=publishable,
                reason="" if publishable else driver.uncalibrated_reason,
            )
            self.accumulators[spec.name] = Accumulator(published)

    async def start(self) -> None:
        self._build_drivers()

        if self.config.logging.enabled:
            self.store.open()
            self.store.log_event("info", "service", f"HOOT starting: {self.config.device.name}")

        if self.config.bacnet.enabled:
            await self.bacnet.start()
            self._flag_unpublishable_points()

        if self.config.display.enabled:
            await self._start_display()

        for warning in self.config.warnings():
            log.warning("%s", warning)

        self._last_trend = time.time()
        self._task = asyncio.create_task(self._run(), name="hoot-sampling")
        log.info("HOOT service started (sampling every %.1fs, trending every %.1fs)",
                 self.config.sampling.interval_seconds,
                 self.config.logging.interval_seconds)

    def _flag_unpublishable_points(self) -> None:
        """Any configured point whose channel nothing publishable supplies is
        faulted immediately, rather than presenting 0.0 until someone notices."""
        for point in self.config.points:
            if not point.enabled:
                continue
            binding = self.bindings.get(point.channel)
            if binding is None:
                self.bacnet.fault(
                    point.channel,
                    f"no configured sensor provides channel '{point.channel}'",
                    "no-sensor",
                )
            elif not binding.publishable:
                self.bacnet.fault(
                    point.channel,
                    f"source sensor is uncalibrated: {binding.reason}",
                    "unreliable-other",
                )

    async def _start_display(self) -> None:
        try:
            from .display import StatusDisplay
            self.display = StatusDisplay(self.config, self)
            await self.display.start()
        except Exception as exc:
            # The screen is a nicety. It must never prevent the pod from
            # measuring and serving data.
            log.warning("display unavailable (%s); continuing without it", exc)
            self.display = None

    async def stop(self) -> None:
        self._stopping.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self.display is not None:
            await self.display.stop()
        await self.bacnet.stop()
        for driver in self.drivers:
            try:
                driver.close()
            except Exception:
                log.debug("driver close failed", exc_info=True)
        if self.config.logging.enabled:
            self.store.log_event("info", "service", "HOOT stopping")
            self.store.close()
        log.info("HOOT service stopped")

    # ---- main loop --------------------------------------------------------

    async def _run(self) -> None:
        while not self._stopping.is_set():
            cycle_start = time.perf_counter()
            try:
                await self._sample_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.health.last_error = str(exc)
                log.exception("sampling cycle failed")

            # Drift-free cadence: sleep the remainder of the interval, not a
            # flat interval, so a slow read does not push every later sample.
            # Read the interval each cycle so a change from the web UI applies
            # without a restart.
            elapsed = time.perf_counter() - cycle_start
            await asyncio.sleep(max(0.0, self.config.sampling.interval_seconds - elapsed))

    async def _sample_once(self) -> None:
        samples = await asyncio.gather(
            *(asyncio.to_thread(self._safe_read, d) for d in self.drivers)
        )
        now = time.time()
        self.health.cycles += 1
        self.health.last_sample_at = now

        for driver, sample in zip(self.drivers, samples, strict=True):
            self._process_sample(driver, sample, now)

        self._check_agreement()

        if self.config.logging.enabled:
            if (now - self._last_trend) >= self.config.logging.interval_seconds:
                self._write_trend(now)
                self._last_trend = now
            self.store.purge()

    #: Combined uncertainty of an SHT45 (+-0.1 degC typ) and a TMP119
    #: (+-0.08 degC max), plus headroom for the two sitting a few millimetres
    #: apart in moving air. Beyond this, the disagreement is not measurement
    #: noise -- one of them is wrong.
    AGREEMENT_LIMIT_C = 0.5

    def _check_agreement(self) -> None:
        """Cross-check the primary and reference temperature sensors.

        Only meaningful when a TMP11x is fitted alongside the SHT45; with one
        sensor there is nothing to compare and the check stays quiet.
        """
        primary = self.latest.get(TEMPERATURE)
        reference = self.latest.get(REFERENCE_TEMPERATURE)
        if not primary or not reference:
            self.health.temperature_agreement = None
            self.health.agreement_alarm = False
            return
        if primary.get("raw") is None or reference.get("raw") is None:
            return

        # Compare in canonical degC so the limit does not change with display units.
        delta = abs(primary["raw"] - reference["raw"])
        self.health.temperature_agreement = delta

        limit = self.AGREEMENT_LIMIT_C
        alarm = delta > limit
        if alarm and not self.health.agreement_alarm:
            log.warning(
                "temperature sensors disagree by %.3f degC (limit %.2f) -- "
                "one of the two heads is faulty, dirty, or loose",
                delta, limit,
            )
            if self.config.logging.enabled:
                self.store.log_event("warning", "agreement",
                                     f"temperature disagreement {delta:.3f} degC")
        self.health.agreement_alarm = alarm

    def _safe_read(self, driver: SensorDriver) -> Sample:
        try:
            return driver.read()
        except Exception as exc:
            self.health.read_errors += 1
            sample = Sample()
            for spec in driver.channels:
                sample.errors[spec.name] = str(exc)
            return sample

    def _process_sample(self, driver: SensorDriver, sample: Sample, now: float) -> None:
        # Walk channels in the driver's declared order (not a set): this order
        # becomes the order of readings in the status API and the web UI cards.
        for channel in (s.name for s in driver.channels):
            binding = self.bindings.get(channel)
            if binding is None or binding.driver is not driver:
                continue

            if channel in sample.errors:
                reason = sample.errors[channel]
                self.health.read_errors += 1
                self.latest[channel] = {
                    "value": None, "unit": binding.published_unit,
                    "fault": reason, "ts": now,
                }
                self.accumulators[channel].add_fault(reason)
                if binding.publishable:
                    self.bacnet.fault(channel, reason)
                continue

            if channel not in sample.values:
                # Nothing new this cycle (e.g. SCD41 between its 5 s reads).
                # Not an error -- hold the previous value and say nothing.
                continue

            raw = sample.values[channel]
            calibrated = self._calibrate(channel, raw)
            published = convert(calibrated, binding.canonical_unit, binding.published_unit)

            self.latest[channel] = {
                "value": published,
                "raw": raw,
                "unit": binding.published_unit,
                "fault": None,
                "ts": now,
                "publishable": binding.publishable,
                "reason": binding.reason,
            }
            self.accumulators[channel].add(published)
            if binding.publishable:
                self.bacnet.publish(channel, published)

    def _calibrate(self, channel: str, raw: float) -> float:
        """Apply the point's calibration, in canonical units."""
        for point in self.config.points:
            if point.channel == channel:
                return point.calibration.apply(raw)
        return raw

    def _write_trend(self, now: float) -> None:
        rows = []
        for channel, acc in self.accumulators.items():
            if acc.empty:
                continue
            if self.config.logging.aggregate:
                mean, lo, hi, n, fault = acc.summarise()
            else:
                latest = self.latest.get(channel, {})
                mean = latest.get("value")
                lo = hi = mean
                n = 1
                fault = latest.get("fault")
            rows.append((channel, acc.unit, mean, lo, hi, n, fault))
            acc.reset()
        if rows:
            written = self.store.write_batch(rows, timestamp=now)
            self.health.trend_rows += written
            self.health.last_trend_at = now

    # ---- introspection ----------------------------------------------------

    def status(self) -> dict[str, Any]:
        """Everything the web UI and OLED need, in one call."""
        return {
            "device": {
                "name": self.config.device.name,
                "location": self.config.device.location,
                "asset_tag": self.config.device.asset_tag,
                "version": __version__,
            },
            "bacnet": {
                "enabled": self.config.bacnet.enabled,
                "device_instance": self.config.bacnet.device_instance,
                "address": self.bacnet.bound_address,
                "points": self.bacnet.snapshot(),
            },
            "readings": self.latest,
            "bindings": {
                ch: {
                    "driver": type(b.driver).__name__,
                    "publishable": b.publishable,
                    "reason": b.reason,
                    "unit": b.published_unit,
                }
                for ch, b in self.bindings.items()
            },
            "health": {
                "uptime_seconds": self.health.uptime_seconds,
                "cycles": self.health.cycles,
                "read_errors": self.health.read_errors,
                "last_sample_at": self.health.last_sample_at,
                "last_trend_at": self.health.last_trend_at,
                "trend_rows": self.health.trend_rows,
                "last_error": self.health.last_error,
                "temperature_agreement": self.health.temperature_agreement,
                "agreement_alarm": self.health.agreement_alarm,
            },
            "warnings": self.config.warnings() + self._runtime_warnings(),
        }

    def _runtime_warnings(self) -> list[str]:
        out: list[str] = []
        if self.health.agreement_alarm and self.health.temperature_agreement is not None:
            out.append(
                f"Temperature sensors disagree by {self.health.temperature_agreement:.2f} "
                f"degC (limit {self.AGREEMENT_LIMIT_C} degC). One probe head is faulty, "
                "dirty, or loose -- verify against a reference before trusting SpaceTemp."
            )
        return out
