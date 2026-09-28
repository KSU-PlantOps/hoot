"""One shared I2C bus for every device on it.

Each driver used to construct its own ``busio.I2C(board.SCL, board.SDA)`` and
call ``deinit()`` on close. That is wrong twice over:

* The bus is a single physical resource. Four objects wrapping the same
  ``/dev/i2c-1`` is at best redundant, and ``deinit()`` from one driver tears the
  bus out from under the other three -- so stopping the service, or a single
  sensor failing and closing itself, could take the healthy sensors with it.
* It hides the real dependency. Everything on this pod shares one bus, one set
  of pull-ups, and one clock; the code should say so.

So the bus is opened once, reference-counted, and only closed when the last
user releases it.

**On bus speed:** ``busio.I2C`` accepts a ``frequency=`` argument and Blinka on
Linux *ignores it*, emitting ``RuntimeWarning: I2C frequency is not settable in
python, ignoring!``. The kernel takes the bus speed from the device tree, so the
only thing that actually sets it is::

    dtparam=i2c_arm=on,i2c_arm_baudrate=50000

in ``/boot/firmware/config.txt`` (see ``deploy/install.sh``). This module
therefore never passes ``frequency=`` -- promising a speed the call cannot
deliver would be worse than not offering the knob at all.
"""

from __future__ import annotations

import logging
import threading

log = logging.getLogger(__name__)

_lock = threading.Lock()
_bus = None
_refcount = 0


def acquire():
    """Return the shared I2C bus, opening it on first use."""
    global _bus, _refcount
    with _lock:
        if _bus is None:
            try:
                import board
                import busio
            except ImportError as exc:  # pragma: no cover - hardware only
                from .base import SensorError
                raise SensorError(
                    "I2C access needs adafruit-blinka (not available here: "
                    f"{exc}). Use driver 'simulator' for off-hardware work."
                ) from exc
            # No frequency= here on purpose -- see the module docstring.
            _bus = busio.I2C(board.SCL, board.SDA)
            log.info("opened shared I2C bus")
        _refcount += 1
        return _bus


def release() -> None:
    """Drop one reference; close the bus when the last user lets go."""
    global _bus, _refcount
    with _lock:
        if _refcount == 0:
            return
        _refcount -= 1
        if _refcount == 0 and _bus is not None:
            try:
                _bus.deinit()
            except Exception:  # pragma: no cover - best effort on teardown
                log.debug("I2C deinit failed", exc_info=True)
            _bus = None
            log.info("closed shared I2C bus")


def warn_if_frequency_configured(options: dict, driver_key: str) -> None:
    """Tell the operator that ``i2c_frequency`` does nothing, rather than
    letting them believe they slowed the bus down for a long cable."""
    if "i2c_frequency" in options:
        log.warning(
            "sensor '%s': the 'i2c_frequency' option is ignored -- Linux takes "
            "the I2C bus speed from the device tree, not from Python. Set "
            "dtparam=i2c_arm=on,i2c_arm_baudrate=%s in /boot/firmware/config.txt "
            "and reboot instead.",
            driver_key, options["i2c_frequency"],
        )
