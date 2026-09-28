"""Build a demo HOOT unit with ~26 h of simulated history, for README screenshots.

Run from an empty scratch directory; pass the repo root. Needs $HOOT_WEB_PASSWORD.
"""
import sys, time
sys.path.insert(0, sys.argv[1])
from hoot.config import Config, PointConfig, SensorConfig
from hoot.sensors import build_driver
from hoot.store import Accumulator, Store
from hoot.units import convert

cfg = Config()
cfg.device.name = "HOOT-07"
cfg.device.location = "Bldg 12 / Rm 214"
cfg.device.asset_tag = "A-10442"
cfg.bacnet.device_instance = 599007
cfg.bacnet.port = 47999
cfg.bacnet.vendor_identifier = 1255
cfg.web.port = 18081
cfg.web.auth_enabled = True
cfg.display.enabled = False
cfg.sensors = [SensorConfig(driver="simulator", options={"emit_reference": True, "seed": 7},
                            allow_uncalibrated=True)]
cfg.points = [
    PointConfig(channel="temperature", name="SpaceTemp", instance=1,
                description="Space dry-bulb temperature", cov_increment=0.1),
    PointConfig(channel="humidity", name="SpaceHumidity", instance=2,
                description="Space relative humidity", cov_increment=0.5),
    PointConfig(channel="co2", name="SpaceCO2", instance=3,
                description="Space CO2 concentration", cov_increment=10.0, high_limit=1100.0),
    PointConfig(channel="reference_temperature", name="RefTemp", instance=5,
                description="Reference temperature (TMP119)", cov_increment=0.1),
]
cfg.points[0].calibration.offset = 0.07
cfg.points[0].calibration.note = "vs Vaisala HM40, 2026-09-24"
cfg.save("config.yaml", backup=False)

now = time.time()
start = now - 26 * 3600
clock = [start]
drv = build_driver("simulator", {"emit_reference": True, "seed": 7, "clock": lambda: clock[0]})
units = {"temperature": "degF", "reference_temperature": "degF", "humidity": "percentRH", "co2": "ppm"}
with Store("data/hoot.db", 90) as st:
    t = start
    while t < now - 60:
        accs = {ch: Accumulator(u) for ch, u in units.items()}
        for k in range(12):
            clock[0] = t + k * 5
            for ch, v in drv.read().values.items():
                if ch == "temperature":
                    v += 0.07
                accs[ch].add(convert(v, "degC", units[ch]) if units[ch] == "degF" else v)
        rows = [(ch, a.unit, *a.summarise()[:3], a.summarise()[3], None) for ch, a in accs.items()]
        rows = [(ch, u, m, lo, hi, n, f) for ch, u, m, lo, hi, n, f in rows]
        st.write_batch(rows, timestamp=t + 60)
        t += 60
    for dt, lvl, src, msg in [
        (-26 * 3600, "info", "service", "HOOT starting: HOOT-07"),
        (-49 * 3600 / 2, "info", "calibration",
         "temperature: offset +0.07degC (vs Vaisala HM40, 2026-09-24)"),
        (-9 * 3600, "warning", "service", "point SpaceCO2 in high-limit: 1142 ppm"),
        (-3 * 3600, "info", "service", "HOOT stopping"),
        (-3 * 3600 + 20, "info", "service", "HOOT starting: HOOT-07"),
    ]:
        st.conn.execute("INSERT INTO events (ts, level, source, message) VALUES (?,?,?,?)",
                        (now + dt, lvl, src, msg))
    st.conn.commit()
    print(st.stats()["rows"], "rows")
