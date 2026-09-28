"""BACnet/IP server: presents HOOT's readings as standard analog input objects.

Any BAS that speaks BACnet/IP -- Niagara, Metasys, Delta, ALC, Desigo,
EcoStruxure -- discovers this with a Who-Is and reads it with no driver, no
gateway, and no vendor cooperation. That is the whole point of choosing BACnet
over MQTT or a REST push.

Design notes:

* Readings are published as **analog-input** objects, not analog-value. AI is
  semantically "a physical sensor reading" and most BAS tools treat it as
  read-only by default, which is correct here -- nothing should be commanding a
  thermometer.

* A failed sensor does **not** hold its last good value. It sets
  ``reliability`` and raises the fault bit in ``statusFlags``. A stale value
  that looks healthy is far more dangerous in a BAS than an obvious fault: it
  is how a dead sensor ends up justifying a control decision for three weeks.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from typing import Any

from bacpypes3.app import Application
from bacpypes3.basetypes import EngineeringUnits, Reliability, StatusFlags
from bacpypes3.local.analog import AnalogInputObject
from bacpypes3.local.device import DeviceObject
from bacpypes3.local.networkport import NetworkPortObject
from bacpypes3.primitivedata import ObjectIdentifier

from .config import Config, PointConfig
from .netaddr import resolve as resolve_address
from .units import BACNET_UNITS

log = logging.getLogger(__name__)

#: BACnetStatusFlags bit order, per ASHRAE 135 and bacpypes3:
#:     0 = in-alarm, 1 = fault, 2 = overridden, 3 = out-of-service
#:
#: Note the order: **overridden comes before out-of-service**. An earlier
#: version of this file had them the other way round, so set_out_of_service()
#: actually raised the *overridden* flag -- telling the operator the point had
#: been manually forced rather than that it was not being maintained. Those
#: mean different things on a workstation. Verified against bacpypes3:
#: StatusFlags([0,0,0,1]) renders as "out-of-service".
_IN_ALARM, _FAULT, _OVERRIDDEN, _OUT_OF_SERVICE = 0, 1, 2, 3

_FLAGS_OK = [0, 0, 0, 0]
_FLAGS_FAULT = [0, 1, 0, 0]
_FLAGS_ALARM = [1, 0, 0, 0]
_FLAGS_OUT_OF_SERVICE = [0, 0, 0, 1]


@dataclass
class PointState:
    """Live state of one published point."""

    config: PointConfig
    obj: AnalogInputObject
    unit: str
    value: float | None = None
    fault: str | None = None
    in_alarm: bool = False
    updates: int = 0

    @property
    def healthy(self) -> bool:
        return self.fault is None and self.value is not None


class BACnetServer:
    """Owns the bacpypes3 application and the analog input objects."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.app: Application | None = None
        self.points: dict[str, PointState] = {}
        self.bound_address: str | None = None
        self._started = False

    # ---- lifecycle --------------------------------------------------------

    async def start(self) -> None:
        if self._started:
            return
        cfg = self.config
        self.bound_address = resolve_address(cfg.bacnet.address, cfg.bacnet.port)

        device = DeviceObject(
            objectIdentifier=ObjectIdentifier(f"device,{cfg.bacnet.device_instance}"),
            objectName=cfg.device.name,
            vendorIdentifier=cfg.bacnet.vendor_identifier,
            vendorName=cfg.bacnet.vendor_name,
            modelName=cfg.bacnet.model_name,
            description=self._device_description(),
            location=cfg.device.location or "unspecified",
            systemStatus="operational",
            maxApduLengthAccepted=cfg.bacnet.max_apdu_length,
            segmentationSupported=cfg.bacnet.segmentation,
            applicationSoftwareVersion=_version(),
        )

        network_port = NetworkPortObject(
            self.bound_address,
            objectIdentifier=ObjectIdentifier("network-port,1"),
            objectName="HOOT-NetworkPort",
        )

        objects: list[Any] = [device, network_port]
        for point in cfg.points:
            if not point.enabled:
                continue
            state = self._build_point(point)
            self.points[point.channel] = state
            objects.append(state.obj)

        self.app = Application.from_object_list(objects)

        if cfg.bacnet.bbmd_address:
            await self._register_foreign_device()

        self._started = True
        log.info(
            "BACnet device %s '%s' listening on %s with %d point(s)",
            cfg.bacnet.device_instance, cfg.device.name,
            self.bound_address, len(self.points),
        )

    async def stop(self) -> None:
        if self.app is not None:
            with _quiet_endpoint_cancellation():
                self.app.close()
                # bacpypes3 creates its datagram endpoints in background tasks
                # whose done-callbacks fire *after* close(). Yielding here lets
                # those callbacks run while the filter above is still installed,
                # so a clean shutdown does not print CancelledError tracebacks
                # on every systemd stop and every web-UI restart.
                for _ in range(3):
                    await asyncio.sleep(0)
            self.app = None
        self._started = False
        log.info("BACnet server stopped")

    async def _register_foreign_device(self) -> None:
        """Register with a BBMD so this pod is reachable from another subnet.

        Without this, a pod on a different subnet from the BAS answers unicast
        reads but never appears in a discovery scan, because Who-Is broadcasts
        do not cross the router.
        """
        cfg = self.config.bacnet
        try:
            assert self.app is not None
            await self.app.register_foreign_device(  # type: ignore[attr-defined]
                cfg.bbmd_address, cfg.bbmd_ttl_seconds
            )
            log.info("registered as foreign device with BBMD %s (ttl %ds)",
                     cfg.bbmd_address, cfg.bbmd_ttl_seconds)
        except Exception:
            # A BBMD failure must not take the pod down: it still serves
            # unicast reads and the local subnet perfectly well.
            log.exception("BBMD registration with %s failed; continuing without it",
                          cfg.bbmd_address)

    # ---- object construction ---------------------------------------------

    def _device_description(self) -> str:
        parts = [self.config.device.description]
        if self.config.device.asset_tag:
            parts.append(f"asset {self.config.device.asset_tag}")
        if self.config.device.location:
            parts.append(self.config.device.location)
        return " | ".join(p for p in parts if p)[:255]

    def _build_point(self, point: PointConfig) -> PointState:
        from .units import display_unit

        unit = display_unit(point.channel, self.config.units.temperature)
        bacnet_unit = BACNET_UNITS.get(unit, "noUnits")

        description = point.description or f"HOOT {point.channel}"
        if not point.calibration.is_identity:
            description += f" [{point.calibration.describe()}]"

        obj = AnalogInputObject(
            objectIdentifier=ObjectIdentifier(f"analog-input,{point.instance}"),
            objectName=point.name,
            description=description[:255],
            units=getattr(EngineeringUnits, bacnet_unit),
            # Start faulted (reliability no-sensor). A point that has never
            # been read must not present 0.0 as if it were a measurement --
            # 0 degF is a plausible-looking number that would trend and alarm.
            presentValue=0.0,
            statusFlags=StatusFlags(_FLAGS_FAULT),
            reliability=Reliability("no-sensor"),
            eventState="normal",
            outOfService=False,
            covIncrement=point.cov_increment,
        )
        return PointState(config=point, obj=obj, unit=unit)

    # ---- publishing -------------------------------------------------------

    def publish(self, channel: str, value: float) -> None:
        """Push a good reading, in published units, to its BACnet object."""
        state = self.points.get(channel)
        if state is None:
            return
        state.value = value
        state.fault = None
        state.updates += 1
        state.obj.presentValue = value
        state.obj.reliability = Reliability("no-fault-detected")

        event_state = self._check_limits(state, value)
        state.in_alarm = event_state != "normal"
        # Preserve out-of-service: it is a property of the point, not of this
        # reading, so a fresh sample must not silently clear it.
        flags = list(_FLAGS_ALARM if state.in_alarm else _FLAGS_OK)
        flags[_OUT_OF_SERVICE] = 1 if bool(state.obj.outOfService) else 0
        state.obj.statusFlags = StatusFlags(flags)
        state.obj.eventState = event_state

    def fault(self, channel: str, reason: str,
              reliability: str = "communication-failure") -> None:
        """Mark a point unreliable. Deliberately does not clear presentValue --
        BACnet's contract is that a faulted point's value is not to be trusted,
        and workstations render it that way."""
        state = self.points.get(channel)
        if state is None:
            return
        if state.fault != reason:
            log.warning("point %s faulted: %s", state.config.name, reason)
        state.fault = reason
        state.obj.reliability = Reliability(reliability)
        flags = list(_FLAGS_FAULT)
        flags[_OUT_OF_SERVICE] = 1 if bool(state.obj.outOfService) else 0
        state.obj.statusFlags = StatusFlags(flags)

    def _check_limits(self, state: PointState, value: float) -> str:
        """-> "high-limit", "low-limit" or "normal".

        Returns the specific BACnet event state rather than a bare boolean.
        The earlier version returned True/False and the caller mapped that to
        "high-limit" unconditionally, so a space that was too *cold* raised a
        HIGH limit alarm on the operator workstation -- pointing whoever
        answered the alarm in exactly the wrong direction.
        """
        point = state.config
        if point.high_limit is not None and value > point.high_limit:
            return "high-limit"
        if point.low_limit is not None and value < point.low_limit:
            return "low-limit"
        return "normal"

    def set_out_of_service(self, channel: str, out_of_service: bool) -> None:
        """Flag a point as not being maintained -- e.g. its sensor is unplugged
        for calibration. The BAS then knows to ignore it rather than alarm."""
        state = self.points.get(channel)
        if state is None:
            return
        state.obj.outOfService = out_of_service
        # Keep statusFlags coherent in BOTH directions. The earlier version only
        # set the flag and never cleared it, so a point returned to service kept
        # advertising out-of-service until something else happened to rewrite
        # the flags.
        flags = list(_FLAGS_ALARM if state.in_alarm else _FLAGS_OK)
        if state.fault is not None:
            flags = list(_FLAGS_FAULT)
        flags[_OUT_OF_SERVICE] = 1 if out_of_service else 0
        state.obj.statusFlags = StatusFlags(flags)

    # ---- introspection ----------------------------------------------------

    def snapshot(self) -> dict[str, dict[str, Any]]:
        """Current state of every point, for the web UI and the OLED."""
        return {
            channel: {
                "name": s.config.name,
                "instance": s.config.instance,
                "value": s.value,
                "unit": s.unit,
                "fault": s.fault,
                "in_alarm": s.in_alarm,
                "updates": s.updates,
                "healthy": s.healthy,
                "calibration": s.config.calibration.describe(),
            }
            for channel, s in self.points.items()
        }


@contextlib.contextmanager
def _quiet_endpoint_cancellation():
    """Suppress the CancelledError tracebacks bacpypes3 emits during teardown.

    Closing the application cancels the pending ``create_datagram_endpoint``
    tasks. Their done-callbacks then reach asyncio's default exception handler,
    which logs a full traceback for what is a completely normal shutdown. Only
    CancelledError from that specific path is swallowed; every other loop
    exception still surfaces.
    """
    loop = asyncio.get_event_loop()
    previous = loop.get_exception_handler()

    def handler(loop_, context):
        exc = context.get("exception")
        if isinstance(exc, asyncio.CancelledError):
            source = repr(context.get("handle", ""))
            if "transport_protocol" in source or "DatagramServer" in source:
                log.debug("ignored endpoint cancellation during shutdown: %s", source)
                return
        if previous is not None:
            previous(loop_, context)
        else:
            loop_.default_exception_handler(context)

    loop.set_exception_handler(handler)
    try:
        yield
    finally:
        loop.set_exception_handler(previous)


def _version() -> str:
    from . import __version__
    return __version__
