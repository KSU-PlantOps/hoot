# Calibration, Testing & Tuning

This is the part that turns a Raspberry Pi with a sensor on it into an instrument you can
quote in a work order.

---

## 0. Bench acceptance (before a unit ever leaves the shop)

Run this on every unit, every time, and keep the output.

```bash
sudo -u hoot /opt/hoot/.venv/bin/python -m hoot selftest
```

Expect `PASS`, plus:

```
=== BACnet ===
  OK   bound 10.10.0.100 on 10.10.0.0/23 (broadcast 10.10.1.255)
```

Check the broadcast address is right for the subnet the pod is on. If it isn't, discovery
will fail later in a way that looks like a network problem and isn't.

Then confirm the bus:

```bash
i2cdetect -y 1        # expect 3c (OLED), 44 (SHT45), 48 (TMP119, if fitted), 62 (SCD41)
```

### The 1 m I²C soak

The probe lead is the most likely long-term failure point, so stress it before deployment,
not after.

```bash
hoot read --driver sht4x -n 200 -i 1 | grep -c FAULT
```

**Accept 0 faults in 200 reads.** Any faults at all mean the bus is marginal — fit the
LTC4311 terminator, drop `i2c_arm_baudrate` to 25000, and re-run. A bus that is marginal on
the bench will be intermittent in a mechanical room next to a VFD, and intermittent I²C
produces the single most annoying class of field problem.

While it runs, flex and tug the lead. It should not produce a single fault.

---

## 1. Do these actually need calibrating?

Mostly **no**, and it is worth being precise about why, because the intuition from working with
cheap sensors does not apply here.

### Temperature and humidity: no

Every SHT4x is **individually calibrated at the factory** against traceable transfer standards
and carries a unique serial number. From the SHT4x datasheet:

| Spec | Value |
|---|---|
| Long-term drift, temperature | **< 0.03 °C / year** |
| Long-term drift, humidity | **< 0.2 % RH / year** |
| SHT45 typical accuracy | ±0.1 °C, ±1.0 % RH |

Over a five-year service life that is about **0.15 °C** of accumulated drift. Now compare that
against what you would calibrate it with:

| Reference | Accuracy | vs. an SHT45 |
|---|---|---|
| Fluke 971 | ±0.5 °C | **5× worse** |
| Vaisala HM40 | ±0.2 °C | 2× worse |
| NIST-traceable lab hygrometer | ±0.1 °C | comparable |

Calibrating a ±0.1 °C sensor against a ±0.5 °C meter does not improve it — it **transfers the
meter's larger uncertainty onto the probe**. You would be making it worse and feeling good
about it.

So the routine is a **verification**, not a calibration: once a year, sit the units side by side
(or beside whatever reference you have) and confirm they agree. You are looking for a *damaged*
or *contaminated* head — a fouled PTFE membrane, a cracked housing, water ingress — not for a
few hundredths of a degree of drift. Log the delta; do not correct it unless something is
obviously wrong.

The offset machinery (web UI **Calibration** tab, or `POST /api/calibrate`) exists for that case, and for sites whose standards
require a documented as-found/as-left record. It is not part of normal operation.

### CO₂: effectively no, and free when it is

Sensirion lists the SCD4x maintenance interval as **maintenance free** when automatic
self-calibration is enabled. Typical additional drift after *five years* with ASC is
±(5 ppm + 0.5 % of reading).

The catch is that ASC needs to see roughly 400 ppm air **weekly** to re-anchor. That decides the
setting for you:

| Use pattern | ASC |
|---|---|
| Carried between sites, outdoors between jobs | `true` — genuinely maintenance free |
| Parked indoors for weeks at a time | `false`, then re-anchor manually (below) |

Manual re-anchoring costs nothing: stand outside, away from traffic and your own breath, let it
run five minutes, and force a recalibration to the current outdoor baseline (~420 ppm, rising a
couple of ppm a year — look it up rather than reusing 400). Stop the service first
(`sudo systemctl stop hoot`) so nothing else is talking to the sensor, and run this as the
`hoot` user from `/opt/hoot/.venv/bin/python`:

```python
from hoot.sensors import build_driver
d = build_driver("scd4x", {}); d.open()
d.force_recalibration(420)
```

### When you *would* want traceable documentation

If a reading is going into a formal report, a dispute, or a compliance submission, you need
documented traceability per sensor. The cheap route is **not** to buy a reference meter — it is
to buy the sensor that already has the paperwork: Sensirion sells the SHT4x with a **per-sensor
ISO 17025:2017 calibration certificate and 3-point temperature calibration**. One certified head
gives stronger evidence than a shop meter and costs a fraction of one.

## 2. Single-point temperature calibration

The everyday procedure.

1. Put the HOOT probe head and the reference **in the same air**, touching if possible,
   away from sunlight, supply diffusers, and your own body heat.
2. Wait **15 minutes.** This is not optional. Both instruments have thermal mass and the
   head has just been in your hand. Watch the web UI trend flatten out before proceeding.
3. Read the reference.
4. Web UI → **Calibration** → enter the reference value → **Apply**.

Or:

```bash
curl -X POST http://pod:8080/api/calibrate \
  -H 'Content-Type: application/json' \
  -d '{"channel":"temperature","reference":71.8,"note":"vs Vaisala HM40 #4412, cert 2026-03"}'
```

The offset is stored in **canonical °C** regardless of your display units, so switching the
unit later does not invalidate it. Verify:

```bash
hoot points        # description now carries the offset
```

### Acceptance

After calibrating, the published value should sit within **±0.2 °C / ±0.4 °F** of the
reference for 30 minutes. If it wanders more than that, you have an airflow problem (one
instrument is in a draft) or a self-heating problem (the head is against something warm),
not a calibration problem.

---

## 3. Two-point calibration (gain + offset)

Worth doing if you use a unit across a wide range — a chilled-water room at 55 °F and a
boiler room at 95 °F will not both be right from one offset.

1. **Low point:** an ice bath. Crushed ice, topped with just enough water to make a slurry,
   stirred. That is 0.0 °C to within about 0.05 °C. Bag the probe head in thin plastic and
   immerse it; wait 10 minutes.
2. **High point:** a stable warm environment measured by the reference — a water bath at
   ~40 °C works well.
3. Record raw and reference at each, then:

```bash
curl -X POST http://pod:8080/api/calibrate -H 'Content-Type: application/json' -d '{
  "channel":"temperature",
  "raw_low": 0.4,  "reference_low": 0.0,
  "raw_high": 40.6, "reference_high": 40.0,
  "note":"two-point ice bath + water bath 2026-09-08"
}'
```

> **Do not put the SCD41 in an ice bath or a water bath.** Condensation will ruin it.
> Two-point the SHT45 only; the head is designed so the SHT45 can be isolated.

---

## 4. Humidity

RH is harder than temperature and drifts more.

- **Field check:** side-by-side against the reference, same 15-minute settle. Accept
  ±3 % RH before calibrating; below that you are inside both instruments' uncertainty and
  "correcting" it just adds error.
- **Salt-cell check (proper):** saturated salt slurries in a sealed jar give known RH at a
  known temperature:
  - **Lithium chloride** → 11.3 % RH
  - **Magnesium chloride** → 32.8 % RH
  - **Sodium chloride** → 75.3 % RH
  Let the head equilibrate in the sealed jar for **at least 4 hours** (overnight is better).
  These are temperature-dependent — record the temperature with the reading.

The SHT45 is specified at ±1 % RH, so if a salt cell says you are off by 5 %, suspect the
cell (contamination, insufficient equilibration, temperature drift) before the sensor.

---

## 5. CO₂

The SCD41 is factory calibrated, and its most common field error is **drift toward reading
low** if automatic self-calibration (ASC) is enabled somewhere it never sees fresh air.

### The ASC decision

ASC assumes the sensor sees ~400 ppm outdoor air regularly, and slowly re-anchors its
baseline to the lowest value it has seen over ~week-long windows.

| Use pattern | ASC setting |
|---|---|
| Probe carried between sites, outdoors between jobs | `automatic_self_calibration: true` |
| Left in one occupied room for weeks | **`false`** — it would "learn" that stale air is fresh |

`hoot init` and `config.example.yaml` set **ASC off**, because "leave it in a room for two weeks" is the more common
job and the failure mode of ASC-on-in-a-sealed-room is silent and wrong in the direction
that matters.

### Manual reference calibration

Take the unit outdoors, away from traffic, loading docks, and your own breath. Let it run
**at least 5 minutes** on a clear day, then anchor it:

```python
from hoot.sensors import build_driver
d = build_driver("scd4x", {}); d.open()
d.force_recalibration(420)     # current global outdoor baseline, ppm
```

Outdoor CO₂ is currently around **420–425 ppm** and rises a couple of ppm each year — check
a current figure rather than reusing 400.

### Sanity checks that need no equipment

- An empty, well-ventilated room should read within ~50 ppm of outdoor.
- Breathe on the head: it should climb into the thousands within 30 s and recover in a
  couple of minutes. Slow recovery means the head is not vented enough.
- The **ProbeHeadTemp** diagnostic point should sit a few degrees above SpaceTemp. If that
  gap *widens* over weeks, the head is dirty or blocked — that is what the diagnostic point
  is for.

---

## 6. Verifying against the BAS

Calibrating the probe is half the job; proving the BAS sees the same number is the other
half.

1. Discover the device from your workstation (Who-Is / device instance).
2. Confirm every configured object appears with the right name, units, and description.
3. Compare `present-value` in the BAS against the HOOT web UI **at the same moment**. They
   should be identical — HOOT publishes the calibrated value, so any difference is a BAS-side
   unit conversion or scaling error, not a probe error.
4. **Test the fault path.** Unplug the probe lead and confirm the point goes unreliable in
   the BAS within a couple of sample intervals rather than freezing at its last value.
   If your BAS shows a stale value instead of a fault, fix the BAS-side point configuration
   now — that behaviour is exactly what makes dead sensors dangerous.
5. Plug it back in and confirm recovery without a restart.

Step 4 is the one people skip and the one that matters most.

---

## 7. Tuning the sample and trend intervals

Defaults are 5 s sampling / 60 s trending. Reasons to change them:

| Situation | Sampling | Trending | Why |
|---|---|---|---|
| Default comfort monitoring | 5 s | 60 s | Balanced; SCD41's own cadence is 5 s |
| Chasing a fast control loop / hunting valve | 5 s | 5 s | You need the excursions, accept the row count |
| Long unattended deployment | 10 s | 300 s | Fewer SD writes, months of runtime |
| Commissioning / functional testing | 5 s | 10 s | Fine detail while someone is watching |

Going below 5 s sampling gains nothing: the SCD41 only produces a new reading every 5 s,
and HOOT correctly holds the previous value rather than faulting in between.

Because the local trend records **min/max/mean between writes**, a slow trend interval still
captures short excursions. You lose the exact shape, not the event.

---

## 8. Ongoing checks

| Interval | Check |
|---|---|
| Every deployment | `hoot selftest`; confirm BACnet address and broadcast |
| Annually | Side-by-side agreement check between units; log the delta, don't correct it |
| Annually (ASC off only) | Re-anchor CO₂ outdoors |
| Whenever the head is opened, fouled, or the lead is replaced | Verify, and correct only if it fails |

Logging the delta without correcting it is what tells you whether a unit is *stable*. A probe
that appears to need a new offset every year has a hardware problem — a fouled membrane, water
ingress, a failing lead — that an offset would only hide.

---

## Recording results

The calibration `note` field travels into the BACnet object description and the local event
log. Use it:

```
"vs Vaisala HM40 s/n 4412, cert 2026-03-14, 15min settle, <your initials>"
```

Export the record for the file:

```bash
hoot export --hours 168 -o HOOT-01_acceptance_$(date +%Y%m%d).csv
```
