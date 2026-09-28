"""128x64 SSD1306 OLED status screen.

Deliberately the least important component in HOOT: it renders in its own task,
every failure is caught, and the service treats its absence as a non-event. A
probe that stops measuring because a $10 screen came unplugged would be a bad
trade.

What it shows is chosen for someone standing in a mechanical room holding the
unit: first the IP address (so they can reach the web UI), then the live
readings, then health. Everything else is on the web page.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from .units import SYMBOLS

if TYPE_CHECKING:
    from .config import Config
    from .service import HootService

log = logging.getLogger(__name__)


class StatusDisplay:
    def __init__(self, config: "Config", service: "HootService") -> None:
        self.config = config
        self.service = service
        self._device: Any = None
        self._image: Any = None
        self._draw: Any = None
        self._font: Any = None
        self._font_big: Any = None
        self._task: asyncio.Task | None = None
        self._page = 0
        self._stopping = asyncio.Event()
        self._failures = 0
        self._holds_bus = False

    # ---- lifecycle --------------------------------------------------------

    async def start(self) -> None:
        try:
            await asyncio.to_thread(self._init_hardware)
        except Exception:
            # No screen fitted is the common case; hand the bus back so it can
            # close cleanly when the sensors are done with it.
            self._release_bus()
            raise
        self._task = asyncio.create_task(self._run(), name="hoot-display")

    def _init_hardware(self) -> None:
        import adafruit_ssd1306  # noqa: PLC0415
        from PIL import Image, ImageDraw, ImageFont  # noqa: PLC0415

        from .sensors import i2cbus  # noqa: PLC0415

        # Same physical bus as the sensors -- acquire, never construct.
        i2c = i2cbus.acquire()
        self._holds_bus = True
        self._device = adafruit_ssd1306.SSD1306_I2C(
            self.config.display.width,
            self.config.display.height,
            i2c,
            addr=self.config.display.address,
        )
        self._device.fill(0)
        self._device.show()

        self._image = Image.new("1", (self.config.display.width, self.config.display.height))
        self._draw = ImageDraw.Draw(self._image)
        try:
            self._font = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 10)
            self._font_big = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 20)
        except OSError:
            self._font = self._font_big = ImageFont.load_default()

    async def stop(self) -> None:
        self._stopping.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._device is not None:
            try:
                self._device.fill(0)
                self._device.show()
            except Exception:
                log.debug("display clear failed", exc_info=True)
        self._release_bus()

    def _release_bus(self) -> None:
        if self._holds_bus:
            from .sensors import i2cbus
            i2cbus.release()
            self._holds_bus = False

    # ---- render loop ------------------------------------------------------

    async def _run(self) -> None:
        while not self._stopping.is_set():
            try:
                await asyncio.to_thread(self._render, self.service.status())
                self._failures = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._failures += 1
                if self._failures == 1:
                    log.warning("display render failed: %s", exc)
                # Back off rather than hammering a dead bus every 5 s: a
                # wedged I2C transaction would otherwise steal a worker
                # thread from sensor reads.
                if self._failures > 5:
                    log.error("display failing repeatedly; stopping renderer")
                    return
            self._page += 1
            await asyncio.sleep(self.config.display.rotate_seconds)

    def _render(self, status: dict[str, Any]) -> None:
        if self._device is None:
            return
        pages = self._pages(status)
        lines = pages[self._page % len(pages)]

        w, h = self.config.display.width, self.config.display.height
        self._draw.rectangle((0, 0, w, h), outline=0, fill=0)

        title, body, big = lines
        self._draw.text((0, 0), title, font=self._font, fill=255)
        self._draw.line((0, 12, w, 12), fill=255)
        if big:
            self._draw.text((0, 16), big, font=self._font_big, fill=255)
            y = 40
        else:
            y = 16
        for line in body:
            self._draw.text((0, y), line, font=self._font, fill=255)
            y += 11
            if y > h - 10:
                break

        self._device.image(self._image)
        self._device.show()

    def _pages(self, status: dict[str, Any]) -> list[tuple[str, list[str], str]]:
        """-> list of (title, body_lines, big_line)"""
        device = status["device"]
        bacnet = status["bacnet"]
        readings = status["readings"]
        health = status["health"]

        addr = (bacnet.get("address") or "no address").split(":")[0].split("/")[0]

        pages: list[tuple[str, list[str], str]] = [
            (
                device["name"],
                [
                    f"http://{addr}:{self.config.web.port}",
                    f"BACnet id {bacnet['device_instance']}",
                    device["location"][:21] or "no location set",
                ],
                "",
            )
        ]

        temp = readings.get("temperature")
        rh = readings.get("humidity")
        if temp or rh:
            big = ""
            body = []
            if temp and temp.get("value") is not None:
                big = f"{temp['value']:.1f}{SYMBOLS.get(temp['unit'], '')}"
            elif temp:
                big = "TEMP FAULT"
            if rh and rh.get("value") is not None:
                body.append(f"RH  {rh['value']:.1f} %")
            elif rh:
                body.append("RH  FAULT")
            pages.append(("Temperature / RH", body, big))

        co2 = readings.get("co2")
        if co2:
            if co2.get("value") is not None:
                value = co2["value"]
                # Rules of thumb a tech can act on, not a compliance claim.
                verdict = ("good" if value < 800 else
                           "elevated" if value < 1100 else
                           "high - check OA")
                pages.append(("CO2", [verdict], f"{value:.0f} ppm"))
            else:
                pages.append(("CO2", ["sensor fault"], "----"))

        faults = [
            f"{ch}: {r['fault'][:16]}"
            for ch, r in readings.items() if r.get("fault")
        ]
        pages.append((
            "Health",
            [
                f"up {_duration(health['uptime_seconds'])}",
                f"cycles {health['cycles']}  err {health['read_errors']}",
                f"trend rows {health['trend_rows']}",
            ] + faults[:1],
            "",
        ))
        return pages


def _duration(seconds: float) -> str:
    seconds = int(seconds)
    days, rem = divmod(seconds, 86_400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"
