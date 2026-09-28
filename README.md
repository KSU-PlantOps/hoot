# HOOT

**H**umidity & **O**perational **O**bservation **T**erminal

A portable, PoE-powered temperature / humidity / CO₂ probe that presents itself to **any**
BAS as a standard **BACnet/IP** device, keeps its own local trend, and is configured from a
web page.

Point it at Niagara, Metasys, Delta, ALC, Desigo, EcoStruxure — anything that speaks
BACnet/IP. No driver, no gateway, no vendor cooperation, no cloud.

```
   ┌──────────────────────────┐
   │  Raspberry Pi 5 + PoE    │───── one Ethernet drop: power + data
   │  ┌────────────────────┐  │
   │  │ OLED: IP, readings │  │
   │  └────────────────────┘  │
   └───────────┬──────────────┘
               │ 1 m cable
        ┌──────▼───────┐
        │ VENTED HEAD  │  SHT45  ±0.1 °C / ±1 % RH
        │              │  SCD41  true NDIR CO₂
        └──────────────┘
```

---

## Why the sensor is on a cable

This is the design decision everything else follows from.

A Raspberry Pi dissipates several watts. Any sensor mounted *on* it — a Sense HAT, a
Waveshare Sense HAT B, a breakout on the GPIO — sits inside that thermal plume. The
documented error on a Sense HAT is **around +17 °C / +30 °F at idle**, and it climbs under
load.

The usual workaround is to subtract a fraction of the CPU temperature:

```python
t_corrected = t - (t_cpu - t) / factor    # don't
```

That is a one-point curve fit to a single unit, in a single enclosure, at a single CPU
load, in a single airflow condition. Change any of them and it drifts. A reference probe
that cannot hold a calibration is not a reference probe — and a probe that reads 30 °F
high while *looking* healthy is worse than no probe at all, because someone will act on it.

So HOOT puts the sensing element in a small vented head on a 1 m lead. It costs about $10
more, it measures correctly, it holds a calibration, and it is a better field tool: you can
hang the head at breathing height, clip it to a diffuser, drop it in a return, or tape it
beside the space sensor you are auditing while the Pi sits on the floor.

HOOT still *supports* a Sense HAT — but the driver is flagged `uncalibrated`, and the
service refuses to publish it to BACnet unless you explicitly set `allow_uncalibrated: true`.
The warning follows the reading into the web UI and the BACnet object description.

---

## Quick start

### On a Raspberry Pi

```bash
git clone https://github.com/KSU-PlantOps/hoot.git
cd hoot
sudo ./deploy/install.sh
```

Then open `http://<pod-ip>:8080`.

The installer creates a service user, builds a virtualenv, enables I²C at 50 kHz (the
right speed for a 1 m probe lead), generates a config with a device instance derived from
the Pi's serial number, and installs a systemd unit.

### On a laptop, with no hardware

Every part of HOOT runs against a built-in simulator, so you can develop, demo, and — most
usefully — **let your BAS integrator validate their trends and graphics before a single
unit is mounted.**

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m hoot init --simulate
.venv/bin/python -m hoot run
```

The simulator produces a diurnal temperature swing, humidity that moves inversely to it,
and CO₂ that follows an occupancy curve.

---

## What your BAS sees

```
$ hoot points

BACnet device 599001  "HOOT-01"
  vendor 999 (HOOT)
  address auto:47808

  OBJECT          NAME                UNITS                     COV     DESCRIPTION
  ----------------------------------------------------------------------------------
  analog-input,1  SpaceTemp           degreesFahrenheit         0.1     Space dry-bulb temperature
  analog-input,2  SpaceHumidity       percentRelativeHumidity   0.5     Space relative humidity
  analog-input,3  SpaceCO2            partsPerMillion           10.0    Space CO2 concentration
  analog-input,4  ProbeHeadTemp       degreesFahrenheit         0.5     DIAGNOSTIC ONLY - SCD41 internal temp
```

Readings are **analog-input** objects, not analog-value: AI means "a physical sensor
reading", and most BAS tools treat it read-only by default. Nothing should be commanding a
thermometer.

### Faults are real faults

When a sensor read fails, HOOT does **not** hold the last good value. It sets
`reliability` (`communication-failure`, `no-sensor`, …) and raises the fault bit in
`statusFlags`. Your workstation renders the point as unreliable instead of trending a
plausible-looking lie.

This matters more than it sounds. A stale value that looks healthy is how a dead sensor
ends up justifying a control decision for three weeks.

---

## Configuration

Everything lives in `config.yaml` (see [`config.example.yaml`](config.example.yaml) for the
fully-commented version). The web UI reads and writes the same file.

```yaml
device:
  name: "HOOT-01"
  location: "Bldg 12 / Rm 214"

bacnet:
  device_instance: 599001    # MUST be unique on your internetwork
  address: "auto"            # detects the interface AND its real netmask
  port: 47808

units:
  temperature: "degF"

sensors:
  - driver: sht4x
  - driver: scd4x
    options: {altitude_m: 300}   # your site's elevation; NDIR is pressure-dependent

points:
  - {channel: temperature, name: SpaceTemp,     instance: 1, cov_increment: 0.1}
  - {channel: humidity,    name: SpaceHumidity, instance: 2, cov_increment: 0.5}
  - {channel: co2,         name: SpaceCO2,      instance: 3, cov_increment: 10.0}

sampling:  {interval_seconds: 5.0}    # also the BACnet update rate
logging:   {interval_seconds: 60.0, retention_days: 90}
```

Two things worth knowing:

- **`address: auto` detects the netmask, not just the IP.** BACnet's Who-Is/I-Am are
  directed broadcasts. A wrong prefix length gives you a device that answers unicast reads
  but never appears in a discovery scan — the most common and most confusing BACnet/IP
  misconfiguration there is. If you set the address manually, the prefix is mandatory and
  HOOT will refuse a bare IP.
- **Sampling and trending are separate on purpose.** The BAS wants live values every few
  seconds; a 90-day local trend at 5 s intervals would be ~1.5 M rows per channel for no
  analytical benefit. The local trend records min/max/mean between samples, so a 60 s trend
  still cannot miss a 10 s excursion.

### Different subnet from the BAS?

Set `bacnet.bbmd_address` to your BBMD and HOOT registers as a foreign device. Without it,
broadcasts don't cross the router and the pod stays invisible to discovery.

---

## Calibration

HOOT is meant to *settle arguments*, so it has to be traceable to something.

From the web UI's **Calibration** tab: sit the probe head next to a reference instrument,
let both settle 10–15 minutes, and enter what the reference reads. Or from the API:

```bash
curl -X POST http://pod:8080/api/calibrate \
  -H 'Content-Type: application/json' \
  -d '{"channel":"temperature","reference":71.8,"note":"vs Fluke 971, 2026-09-08"}'
```

Offsets are stored in **canonical SI units**, so switching the display between °F and °C
never invalidates a calibration. Two-point calibration (gain + offset) is supported for
work across a wider range. See [docs/CALIBRATION.md](docs/CALIBRATION.md).

---

## CLI

```
hoot run          start the service (what systemd runs)
hoot init         write a starter config for this unit
hoot read         one-shot sensor read — safe on an in-service unit
hoot selftest     pre-flight: config, sensors, points, BACnet bind, storage
hoot points       print the BACnet object list, to hand to your integrator
hoot export       dump the local trend to CSV
```

`hoot selftest` is the one to run before mounting a unit:

```
=== BACnet ===
  OK   bound 10.10.0.100 on 10.10.0.0/23 (broadcast 10.10.1.255)
  OK   device instance 599001, 4 object(s)
```

---

## Hardware

Full bill of materials, part rationale, and assembly notes:
**[docs/HARDWARE.md](docs/HARDWARE.md)**. Roughly $245/unit at 2026 retail prices.

The one part choice worth repeating here: use the **Waveshare PoE HAT (F)**, not the
official Raspberry Pi PoE+ HAT. The original PoE+ HAT does not fit a Pi 5 at all, and the
official Pi 5 version has no fan and no documented GPIO passthrough. The Waveshare board
explicitly provides a **GPIO stacking header**, which HOOT needs for the sensor head and
the OLED.

---

## Development

```bash
pip install -r requirements.txt -e ".[dev]"
pytest                       # no hardware required
hoot init --simulate && hoot run
```

Adding a sensor means implementing one class:

```python
class MyDriver(SensorDriver):
    key = "mysensor"

    @property
    def channels(self): ...
    def read(self) -> Sample: ...
```

Report values in canonical SI (°C, %RH, ppm, hPa) and return per-channel errors in
`Sample.errors` rather than raising — a single bad channel must not take the healthy ones
down with it. Register it in `hoot/sensors/__init__.py`.

Continuous integration runs the suite on Python 3.11–3.13 (Raspberry Pi OS Bookworm ships
3.11, so do not use syntax newer than that). See [CONTRIBUTING.md](CONTRIBUTING.md).

---

## License

MIT — see [LICENSE](LICENSE).
