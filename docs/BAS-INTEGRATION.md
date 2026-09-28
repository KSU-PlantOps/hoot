# Connecting HOOT to a BAS

HOOT is a plain BACnet/IP device. Anything that speaks BACnet/IP can read it with no
driver and no gateway. This page covers the parts that actually trip people up.

---

## Before you touch the BAS

```bash
hoot selftest
```

Confirm the bound address line:

```
OK   bound 10.10.0.100 on 10.10.0.0/23 (broadcast 10.10.1.255)
```

The **broadcast address must be right for the pod's subnet.** Ninety percent of "the BAS
can't find it" turns out to be a wrong prefix length here, because Who-Is and I-Am are
directed broadcasts. `address: auto` reads the real netmask from the interface; if you set
the address by hand, the prefix is mandatory and HOOT refuses a bare IP.

---

## Same subnet as the BAS

Nothing to configure. Run a Who-Is / device discovery and HOOT answers with its device
instance, name, and object list.

## Different subnet from the BAS

Broadcasts do not cross routers, so the pod will answer a unicast read but never appear in
a discovery scan. Register it as a foreign device with your BBMD:

```yaml
bacnet:
  bbmd_address: "10.20.0.5:47808"
  bbmd_ttl_seconds: 900
```

If BBMD registration fails, HOOT logs it and keeps running — it still serves unicast reads
and its own subnet perfectly well. Check `journalctl -u hoot | grep BBMD`.

---

## Device instance numbering

The single most important field. It must be unique across the **entire** BACnet
internetwork — not just the subnet.

`hoot init` derives a default from the Pi's CPU serial (`100000 + serial % 4000000`) so two
units out of the box do not collide. That is a safety net, not a numbering plan. Get real
numbers from whoever owns your BACnet standard, and record them.

A suggested convention for a fleet:

```
5990xx    HOOT portable probes    (599001, 599002, …)
```

---

## Tridium Niagara

1. **Drivers** → `BacnetNetwork`. If you don't have one, add it from the palette
   (`bacnet` → `BacnetNetwork`).
2. Under `Local Device` → `Network` → `IpPort`, confirm the adapter and that UDP 47808 is
   in use.
3. `Bacnet Comm` → `Network` → **Discover Devices**, or add the device manually by its
   instance number if it is across a BBMD.
4. Discover objects on the device and drag `SpaceTemp`, `SpaceHumidity`, `SpaceCO2` into
   your points folder as **Numeric Points** (read-only).
5. **Set the facets.** Niagara will not infer °F from the BACnet units property. Set
   `units=°F`, `precision=1` on the temperature point, `units=%RH` on humidity, and
   `units=ppm` on CO₂. Mismatched facets here is the usual cause of "the BAS shows a
   different number than the web UI".
6. Add a `NumericInterval` or `NumericCov` extension for trending.

### Verify the fault path

Unplug HOOT's probe lead. Within a couple of sample intervals the Niagara point should go
to a fault/stale status rather than holding its last value. If it holds the value, the
point's `Fault Cause` mapping is not wired through — fix it now. A dead sensor that still
displays a plausible number is the failure mode this whole design exists to prevent.

---

## Johnson Controls Metasys

Add as a **Third Party BACnet Integration** under the target FEC/NAE. Map the analog inputs
as AI objects. Metasys reads BACnet units natively, so units usually come through without
manual facets — but confirm rather than assume.

## Automated Logic (WebCTRL)

Add via **BACnet Device Manager** → auto-discovery, then create microblocks bound to
`AI:1`–`AI:4`. WebCTRL respects `covIncrement`, so tune it in HOOT's config rather than
polling hard: `cov_increment: 0.1` on a °F point is a good starting value.

## Delta Controls / Siemens Desigo / Schneider EcoStruxure

Standard BACnet/IP discovery. No special handling. All read the object list, units, and
reliability properties correctly.

---

## Object reference

| Object | Default name | Units | Notes |
|---|---|---|---|
| `analog-input,1` | `SpaceTemp` | °F or °C | The measurement. SHT45. |
| `analog-input,2` | `SpaceHumidity` | % RH | SHT45. |
| `analog-input,3` | `SpaceCO2` | ppm | SCD41, true NDIR. |
| `analog-input,4` | `ProbeHeadTemp` | °F or °C | **Diagnostic only** — SCD41 self-heated. |
| `analog-input,5` | `RefTemp` | °F or °C | Optional — TMP119 cross-check, if you add the point (see `config.example.yaml`). |

`ProbeHeadTemp` is deliberately *not* a space measurement. Trend it anyway: it should sit a
few degrees above `SpaceTemp`, and a gap that widens over weeks means the probe head is
dirty or airflow-blocked. Do not put it on an operator graphic — it will get misread as a
second space temperature.

### Properties HOOT maintains

| Property | Behaviour |
|---|---|
| `presentValue` | Calibrated value in configured units |
| `reliability` | `no-fault-detected`, `communication-failure`, `no-sensor`, `unreliable-other` |
| `statusFlags` | Fault bit on sensor failure; in-alarm bit when a configured limit is exceeded |
| `eventState` | `normal` / `high-limit` / `low-limit` |
| `covIncrement` | From config, per point |
| `outOfService` | Always `false` today; the fault/reliability path covers an unplugged probe |

---

## Alarm limits

Set them in HOOT (`high_limit` / `low_limit`) when you want the *probe* to assert the alarm
condition in `statusFlags` and `eventState`. Set them in the BAS when you want the BAS's own
alarm routing, escalation, and acknowledgement.

Most sites should do it in the BAS. HOOT's limits are useful for a portable unit doing a
standalone investigation, where there is no BAS alarm class to route to.
