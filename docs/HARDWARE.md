# HOOT — Hardware Plan & Bill of Materials

**HOOT** — *Humidity & Operational Observation Terminal*

A portable, PoE-powered temperature/humidity/CO₂ probe that presents itself to any
BAS as a standard **BACnet/IP** device, logs locally, and is configured from a web UI.

---

## The one design rule that matters

> **The sensing element never lives inside the box with the Pi.**

A Raspberry Pi 5 dissipates several watts. Any sensor mounted on top of it — a Sense HAT,
a Waveshare Sense HAT B, a BME280 breakout on the GPIO — sits inside that thermal plume.
Measured error on a Sense HAT is **~17 °C / 30 °F high at idle**, and worse under load.

The common workaround (`t_real = t - (t_cpu - t) / factor`) is a curve fit to one unit, in
one enclosure, at one CPU load, in one airflow condition. Change any of those and it drifts.
It cannot hold a calibration, which is exactly what a reference probe has to do.

So HOOT puts the SHT45 and SCD41 in a **small vented head on a 1 m cable**. This costs about
$10 more than a HAT and buys three things:

1. **Real accuracy** — ±0.1 °C instead of ±17 °C.
2. **Better ergonomics** — dangle the head at breathing height, clip it to a diffuser,
   drop it in a return, or tape it next to the space sensor you're auditing while the
   Pi sits on the floor or hangs off the ceiling grid.
3. **A stable calibration** — the head can be bench-verified against a reference and the
   offset stored per unit (see `calibration:` in the config).

This is also why the PoE HAT's fan is *fine*: it exhausts heat away from a sensor that
isn't in the box anyway.

---

## Bill of Materials — one HOOT unit

Prices are approximate USD retail as of September 2026; verify at your distributor.
Adafruit, DigiKey, and PiShop.us all stock these parts and generally accept purchase orders.

### Core

| # | Item | Qty | ~Each | Notes |
|---|------|-----|-------|-------|
| 1 | Raspberry Pi 5 — **2 GB** | 1 | $65 | 2 GB is sufficient for this workload. The 4 GB board is **$110** and rising — see the price note below. |
| 2 | **Waveshare PoE HAT (F)** | 1 | $28.95 | **Get this one, not the official HAT** — see below |
| 3 | microSD 32 GB high-endurance | 1 | $12 | High-endurance matters; this logs continuously |
| 4 | Vented ABS enclosure | 1 | $10 | Must clear the PoE HAT; needs a gland for the probe lead |

### Sensor head (external, on the cable)

| # | Item | Qty | ~Each | Notes |
|---|------|-----|-------|-------|
| 5 | Adafruit **SHT45** (#5665) | 1 | $12.50 | ±0.1 °C / ±1 % RH typical. The PTFE-filtered variant (#6174, $13.50) is preferable but **out of stock as of 8 Sep 2026** — check before ordering. |
| 5b | Adafruit **TMP119** (#6482) | 1 | $14.95 | **±0.08 °C _maximum_** over 0–45 °C — tighter than the SHT45's *typical*. Temperature only; it does not replace the SHT45, it cross-checks it. See below. |
| 6 | Adafruit **SCD-41** true CO₂ — STEMMA QT (#5190) | 1 | $49.95 | Photoacoustic NDIR. Real CO₂, not an eCO₂ guess. |
| 7 | STEMMA QT cable, 1 m + 50 mm jumper | 1 | $6 | Carries I²C to the head |
| 8 | Adafruit **LTC4311** I²C active terminator (#4756) | 1 | $5.95 | Insurance for the 1 m run — see *I²C over distance* |
| 9 | Small vented enclosure for the head | 1 | $12 | Must be **vented**; a sealed head lags badly on RH |

### Display & misc

| # | Item | Qty | ~Each | Notes |
|---|------|-----|-------|-------|
| 10 | 0.96 in 128×64 OLED, STEMMA QT (#326) | 1 | $17.50 | Shows IP, live readings, BACnet ID, logging state. The 1.3 in (#938) is $19.95. |
| 11 | Cable gland, strain relief, standoffs, fixings | — | $10 | Field durability — the probe lead *will* get tugged |

**Per-unit total ≈ $245** (verified retail, 8 September 2026, excl. tax and shipping).

A temp/RH-only build, dropping the SCD-41, the TMP119 and the OLED, is **≈ $162**.
Dropping just the TMP119 saves $15 and loses the self-check.

> **Price note — read before ordering.** Raspberry Pi prices rose sharply through 2026 on
> AI-driven memory shortages: the Pi 5 4 GB roughly doubled (about $60 → $110) and the 16 GB
> went from $120 to $305. This BOM deliberately specifies the **2 GB** board, which is both
> sufficient here and far less exposed to memory pricing. **Re-quote before every order.**

### Shared / per-site (buy once)

**Nothing.** This is unusual and worth stating plainly:

- **No PoE injectors** — provided your drops are PoE-capable switch ports. The Waveshare
  HAT is 802.3af/at, and negotiates down cleanly on an 802.3bt (PoE++) port — a bt source
  simply offers more budget than the HAT asks for. One drop per pod, no power
  infrastructure. If a location has no PoE, add an 802.3at injector (~$20).
- **No reference instrument.** See [CALIBRATION.md](CALIBRATION.md) — the SHT45 is
  individually factory calibrated and drifts less than the field meters you would check it
  against, so there is no routine calibration to fund.

---

## Why the Waveshare PoE HAT (F), not the official one

This is the part that would have caused a re-order, so it's worth being explicit:

- The **original Raspberry Pi PoE+ HAT does not fit a Pi 5** at all — different board layout.
- The **official Pi 5 PoE+ HAT** is a compact L-shaped board designed to fit the official
  case. It has **no fan** (it assumes the case fan), and Raspberry Pi has not documented
  GPIO passthrough — the question was asked repeatedly on the announcement post and left
  unanswered.
- The **Waveshare PoE HAT (F)** explicitly advertises a **"GPIO stackable header, allows
  connecting other HATs"**, includes an **active cooling fan + metal heatsink**, and provides
  **5 V 4.5 A plus a separate 12 V 2 A rail**.

HOOT needs the I²C pins for both the sensor head and the OLED. A HAT that occupies the GPIO
without passthrough turns a $28 part into a blocked build, so the stackable header is the
deciding feature.

> **Verify on arrival:** before assembling all units, fit one HAT and confirm
> `i2cdetect -y 1` still sees a device on the stacked header. Order one unit's worth first.

---

## I²C over distance

I²C was designed for traces on a board, not 1 m of cable. Cable capacitance slows the
open-drain pull-ups and corrupts edges.

Mitigations, in the order HOOT applies them:

1. **Run the bus at 100 kHz or slower.** Set in `/boot/firmware/config.txt`:
   ```
   dtparam=i2c_arm=on,i2c_arm_baudrate=50000
   ```
   50 kHz is comfortable at 1 m and is plenty — we sample every few seconds, not every
   few microseconds.
2. **Fit the LTC4311 active terminator** at the Pi end of the long cable. It watches SDA/SCL
   and injects current to sharpen the rising edges.
3. **Keep the probe lead away from VFD and motor feeders.** In a mechanical room this matters
   more than cable length. Route it like a signal cable, because it is one.

HOOT's driver layer treats I²C errors as a **sensor fault**, not a crash: the BACnet object
goes to `reliability = communication-failure` and raises the fault bit in `statusFlags`, so
the BAS shows the point as unreliable rather than silently holding a stale value. That
behavior is what makes a marginal bus *visible* instead of dangerous.

---

## Sensor choices, and what we rejected

| Sensor | Temp acc. | RH acc. | Verdict |
|--------|-----------|---------|---------|
| **SHT45** | ±0.1 °C typ | ±1 % | **Chosen** as the primary T/RH sensor. |
| **TMP119** | ±0.08 °C **max** | — | **Chosen** as the reference/cross-check. Temperature only. |
| HDC3022 | ±0.1 °C typ | **±0.5 %** | Better than the SHT45 and cheaper, with an integrated PTFE filter — but out of stock. |
| SHT43 | see datasheet | ±1.8 % | ISO 17025 certified, 3-point calibration, <0.01 °C/y drift — but *worse* RH. Traceability, not accuracy. |
| SEN66 | ±0.45 °C | ±4.5 % | Adds PM2.5/VOC/NOx, but four times worse on RH. An IAQ node, not a probe. |
| SHT41 | ±0.2 °C | ±1.8 % | Fine fallback; ~$4 cheaper. Same driver. |
| BME280 | ±1.0 °C | ±3 % | **Rejected.** ±1 °C is the same size as the error you're hunting. |
| DHT22 | ±0.5 °C | ±2–5 % | Rejected. Slow, no I²C, poor long-term stability. |
| Sense HAT (SHTC3) | ±0.2 °C *chip* | ±2 % *chip* | **Rejected as primary.** Good chip, fatal mounting. |

**On CO₂:** the SCD41 is photoacoustic NDIR — it measures CO₂ directly. Avoid "eCO₂" parts
(CCS811, SGP30); those infer CO₂ from VOCs and are not defensible for a ventilation argument.

A newer **Adafruit STCC4 + SHT41 combo (#6478)** puts CO₂ and T/RH on one smaller board for
less money. It's a good option for a compact head if you build more units, but the STCC4 is
thermal-conductivity based rather than NDIR, so for a probe meant to *settle* arguments about
ventilation, the SCD41 is the stronger instrument. (There is no STCC4 driver yet; the SHT41
half works with the existing `sht4x` driver.)

**Note:** the SCD41 reports its own temperature and humidity. HOOT reads them but treats them
as **diagnostic only** — the SCD41 self-heats, so its T/RH runs warm. The SHT45 is always the
published T/RH. Publish the SCD41's temperature as the `ProbeHeadTemp` diagnostic point and
trend it next to `SpaceTemp`: a widening gap is an early warning that the head is dirty or
airflow is blocked.

---

## Assembly notes

```
   ┌─────────────────────────┐
   │  Pi 5 + PoE HAT (F)     │──── Ethernet (power + data, one drop)
   │  ┌───────────────────┐  │
   │  │ OLED 128×64       │  │     short STEMMA QT
   │  └───────────────────┘  │
   │        │ I²C bus        │
   │   [LTC4311]             │
   └────────┼────────────────┘
            │ 1 m STEMMA QT cable, through a cable gland
            ▼
      ┌───────────────┐
      │ VENTED HEAD   │   SHT45   (temp / RH — published)
      │               │   TMP119  (reference temp — cross-check)
      │               │   SCD41   (CO₂ — published; its T/RH = diagnostic)
      └───────────────┘
```

I²C addresses — all distinct, no conflicts, one bus:

| Device | Address |
|--------|---------|
| SSD1306 OLED | `0x3C` |
| SHT45 | `0x44` |
| TMP119 | `0x48` |
| SCD41 | `0x62` |

### Pi OS setup

```bash
sudo raspi-config nonint do_i2c 0          # enable I²C
echo 'dtparam=i2c_arm=on,i2c_arm_baudrate=50000' | sudo tee -a /boot/firmware/config.txt
sudo reboot
i2cdetect -y 1                              # expect 3c, 44, 48, 62
```

---

## Why two temperature sensors

The TMP119 is the one addition that is not about measuring more things — it is about being able
to *trust* the measurement without a calibration programme.

A single sensor cannot detect its own drift. If the SHT45 is knocked, contaminated, or slowly
degrades, it keeps reporting confidently and nothing in the system knows. Two sensors built on
different physical principles, by different manufacturers, cannot drift in the same direction by
the same amount for the same reason. When they disagree by more than their combined uncertainty
(HOOT's default limit is 0.5 °C), the pod says so on its own: a warning in the log and the
event table, and a banner plus a "Temperature cross-check" row in the web UI. It does not
fault the BACnet point, because with two sensors disagreeing it cannot tell which is wrong.

| | Accuracy | Note |
|---|---|---|
| SHT45 | ±0.1 °C **typical** | max is a curve; ~±0.2 °C at room temperature |
| TMP119 | ±0.08 °C **maximum**, 0–45 °C | ±0.03 °C typical; 0.0078 °C resolution |

The TMP119's *worst case* is better than the SHT45's *typical* case, which is the right way round
for a probe whose job is settling arguments. It also has TI's hardware support for NIST
traceability.

This is what makes "no routine calibration" (see [CALIBRATION.md](CALIBRATION.md)) a defensible
position rather than an optimistic one: verification is continuous and automatic instead of
annual and manual.

Enable it with:

```yaml
sensors:
  - driver: tmp11x
    options: {address: 0x48}
```

Drop it and you save $15 and lose the self-check; everything else still works.

## Parts worth watching

Two parts would improve the build and were out of stock when this was specified (8 Sep 2026):

| Part | Price | Why |
|---|---|---|
| **TI HDC3022** (#5989) | $8.95 | RH ±0.5 % — **twice** the SHT45's accuracy — with an *integrated* PTFE filter, and $3.55 cheaper. If it restocks, it becomes the better primary sensor. |
| **Sensirion SCD-43** (#6512) | $49.95 | ±(30 ppm + 3 %) across 400–5000 ppm vs the SCD-41's ±(50 ppm + 2.5 %) — better accuracy at **the same price**. Same SCD4x command set, so `hoot/sensors/scd4x.py` drives it unchanged: a pure drop-in swap. |

## Ordering strategy

**Order one unit first.** Build it, confirm the HAT's GPIO passthrough works and the 1 m I²C
run is clean, then order the rest. The two things most likely to bite are exactly those, and
both are cheap to verify and expensive to discover across ten units.
