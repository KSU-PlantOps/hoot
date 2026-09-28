# Contributing to HOOT

Thanks for helping. HOOT is a field instrument, so the bar is "would I trust this reading
in a mechanical room at 2 a.m.?" — correctness and honest failure modes come before features.

## Development setup

No hardware is needed. Everything runs against the built-in simulator.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -e ".[dev]"
.venv/bin/ruff check hoot tests
.venv/bin/pytest
```

To see the whole stack running (BACnet, trend, web UI on :8080):

```bash
.venv/bin/python -m hoot init --simulate
.venv/bin/python -m hoot run
```

## Ground rules

- **Python 3.11 compatible.** Raspberry Pi OS Bookworm ships 3.11. CI tests 3.11–3.13 and
  ruff targets `py311`; don't use newer syntax (for example, reusing the outer quote
  character inside an f-string is a `SyntaxError` on 3.11).
- **Faults, not stale values.** A sensor that can't be read must show up as a BACnet
  reliability fault. Never hold the last good value.
- **Drivers report canonical SI units** (°C, %RH, ppm, hPa). Conversion happens once, in
  `hoot/units.py`.
- **One bad channel must not take down healthy ones.** Put per-channel errors in
  `Sample.errors` instead of raising from `read()`.
- **Add a test** for every bug fix. The regression tests in `tests/` explain the bug they
  guard against; please keep doing that.

## Adding a sensor driver

1. Subclass `hoot.sensors.base.SensorDriver`, set `key` and `display_name`, and implement
   `channels` and `read()`.
2. Acquire the I²C bus with `hoot.sensors.i2cbus.acquire()` and release it in `close()`.
   If `open()` fails after acquiring the bus, release it before re-raising.
3. Register it in `hoot/sensors/__init__.py`.
4. If a board-mounted or otherwise untrustworthy sensor, set `uncalibrated = True` with a
   reason. HOOT then refuses to publish it unless the config opts in.

## Pull requests

Keep PRs focused, describe how you tested (bench hardware or simulator), and update the docs
if behaviour changes. By contributing you agree your work is licensed under the project's
MIT license.
