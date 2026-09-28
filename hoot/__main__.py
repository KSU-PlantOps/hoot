"""HOOT command line.

    hoot run          start the service (this is what systemd runs)
    hoot init         write a starter config.yaml for this unit
    hoot read         one-shot sensor read, no BACnet, no logging
    hoot selftest     check I2C, sensors, config, and BACnet bind
    hoot export       dump the local trend to CSV
    hoot points       print the BACnet object list a BAS will discover
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import signal
import sys
import time
from pathlib import Path

from . import __version__
from .config import (
    Config,
    ConfigError,
    PointConfig,
    SensorConfig,
    default_device_instance,
    load_config,
)
from .sensors import DRIVERS, build_driver
from .units import SYMBOLS, convert, display_unit

log = logging.getLogger("hoot")


def _setup_logging(verbose: bool, log_file: str | None = None) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file))
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
    )
    # bacpypes3 is extremely chatty at DEBUG.
    logging.getLogger("bacpypes3").setLevel(logging.WARNING)


# ---- commands -------------------------------------------------------------


async def cmd_run(args: argparse.Namespace) -> int:
    from .service import HootService

    cfg = load_config(args.config)
    service = HootService(cfg)
    await service.start()

    server = None
    if cfg.web.enabled:
        import uvicorn
        from .web.app import create_app

        app = create_app(service)
        server = uvicorn.Server(uvicorn.Config(
            app, host=cfg.web.host, port=cfg.web.port,
            log_level="warning", access_log=False,
        ))
        log.info("web UI on http://%s:%d", cfg.web.host, cfg.web.port)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    try:
        if server is not None:
            server_task = asyncio.create_task(server.serve())
            await stop.wait()
            server.should_exit = True
            await server_task
        else:
            await stop.wait()
    finally:
        await service.stop()
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    target = Path(args.config or "config.yaml")
    if target.exists() and not args.force:
        print(f"{target} already exists; use --force to overwrite", file=sys.stderr)
        return 1

    cfg = Config()
    cfg.device.name = args.name
    cfg.bacnet.device_instance = args.instance or default_device_instance()
    cfg.sensors = [
        SensorConfig(driver="sht4x", options={}),
        SensorConfig(driver="tmp11x", options={"address": 0x48}),
        # ASC off explicitly: the chip ships with it on, and it silently drifts
        # low in a room that never sees outdoor air (docs/CALIBRATION.md).
        SensorConfig(driver="scd4x", options={"altitude_m": args.altitude,
                                              "automatic_self_calibration": False}),
    ] if not args.simulate else [
        SensorConfig(driver="simulator", options={}, allow_uncalibrated=True),
    ]
    cfg.points = [
        PointConfig(channel="temperature", name="SpaceTemp", instance=1,
                    description="Space dry-bulb temperature", cov_increment=0.1),
        PointConfig(channel="humidity", name="SpaceHumidity", instance=2,
                    description="Space relative humidity", cov_increment=0.5),
        PointConfig(channel="co2", name="SpaceCO2", instance=3,
                    description="Space CO2 concentration", cov_increment=10.0),
    ]
    if not args.simulate:
        cfg.points.append(PointConfig(
            channel="scd_temperature", name="ProbeHeadTemp", instance=4,
            description="DIAGNOSTIC ONLY - SCD41 internal temp, self-heated",
            cov_increment=0.5))
    cfg.save(target, backup=False)
    print(f"wrote {target}")
    print(f"  device name     : {cfg.device.name}")
    print(f"  BACnet instance : {cfg.bacnet.device_instance}")
    print(f"  sensors         : {', '.join(s.driver for s in cfg.sensors)}")
    if not args.simulate and not args.altitude:
        print("  ! SCD4x altitude is 0 m (sea level). CO2 readings are pressure-"
              "dependent; set sensors[].options.altitude_m for your site.")
    for warning in cfg.warnings():
        print(f"  ! {warning}")
    return 0


def cmd_read(args: argparse.Namespace) -> int:
    """One-shot read. Deliberately does not touch BACnet or the database, so it
    is safe to run against a unit that is in service."""
    drivers = []
    if args.driver:
        drivers = [build_driver(args.driver, {})]
    else:
        cfg = load_config(args.config)
        drivers = [build_driver(s.driver, s.options) for s in cfg.sensors]

    unit_pref = getattr(args, "units", "degF")
    rc = 0
    for driver in drivers:
        print(f"\n{driver.display_name}")
        if driver.uncalibrated:
            print(f"  ** {driver.uncalibrated_reason}")
        try:
            driver.open()
        except Exception as exc:
            print(f"  ERROR: {exc}")
            rc = 1
            continue

        for attempt in range(args.samples):
            if attempt:
                time.sleep(args.interval)
            sample = driver.read()
            if not sample.values and not sample.errors:
                print("  (no new data this cycle)")
                continue
            for channel, value in sorted(sample.values.items()):
                spec = driver.channel(channel)
                src = spec.unit if spec else None
                out_unit = display_unit(channel, unit_pref, src)
                shown = convert(value, src, out_unit) if src and src != out_unit else value
                tag = " [diagnostic]" if spec and spec.diagnostic else ""
                print(f"  {channel:18s} {shown:9.3f} {SYMBOLS.get(out_unit, out_unit)}{tag}")
            for channel, err in sorted(sample.errors.items()):
                print(f"  {channel:18s} FAULT: {err}")
                rc = 1
        driver.close()
    return rc


def cmd_points(args: argparse.Namespace) -> int:
    """Print the BACnet object list, for handing to whoever integrates the BAS."""
    cfg = load_config(args.config)
    print(f"BACnet device {cfg.bacnet.device_instance}  \"{cfg.device.name}\"")
    print(f"  vendor {cfg.bacnet.vendor_identifier} ({cfg.bacnet.vendor_name})")
    print(f"  address {cfg.bacnet.address}:{cfg.bacnet.port}")
    print(f"  location {cfg.device.location or '-'}")
    print()
    print(f"  {'OBJECT':<16}{'NAME':<20}{'UNITS':<26}{'COV':<8}DESCRIPTION")
    print("  " + "-" * 100)
    from .units import BACNET_UNITS
    for point in cfg.points:
        if not point.enabled:
            continue
        unit = display_unit(point.channel, cfg.units.temperature)
        print(f"  {'analog-input,' + str(point.instance):<16}{point.name:<20}"
              f"{BACNET_UNITS.get(unit, unit):<26}{point.cov_increment:<8}{point.description}")
    return 0


async def cmd_selftest(args: argparse.Namespace) -> int:
    """Pre-flight: config, sensors, BACnet bind. Run this before mounting a unit."""
    failures = 0

    print("=== config ===")
    try:
        cfg = load_config(args.config)
        print(f"  OK   loaded {cfg.path}")
        for warning in cfg.warnings():
            print(f"  WARN {warning}")
    except ConfigError as exc:
        print(f"  FAIL {exc}")
        return 1

    print("\n=== sensors ===")
    provided: set[str] = set()
    for spec in cfg.sensors:
        try:
            driver = build_driver(spec.driver, spec.options)
            driver.open()
            sample = driver.read()
            if sample.errors and not sample.values:
                print(f"  FAIL {spec.driver}: {next(iter(sample.errors.values()))}")
                failures += 1
            else:
                names = ", ".join(sorted(sample.values)) or "(no data yet)"
                print(f"  OK   {spec.driver}: {names}")
                provided |= set(c.name for c in driver.channels)
            driver.close()
        except Exception as exc:
            print(f"  FAIL {spec.driver}: {exc}")
            failures += 1

    print("\n=== points ===")
    for point in cfg.points:
        if not point.enabled:
            print(f"  SKIP {point.name} (disabled)")
        elif point.channel in provided:
            print(f"  OK   AI:{point.instance} {point.name} <- {point.channel}")
        else:
            print(f"  FAIL AI:{point.instance} {point.name}: no sensor provides "
                  f"'{point.channel}'")
            failures += 1

    print("\n=== BACnet ===")
    if not cfg.bacnet.enabled:
        print("  SKIP disabled in config")
    else:
        from .bacnet import BACnetServer
        from .netaddr import describe
        try:
            server = BACnetServer(cfg)
            await server.start()
            print(f"  OK   bound {describe(server.bound_address or '')}")
            print(f"  OK   device instance {cfg.bacnet.device_instance}, "
                  f"{len(server.points)} object(s)")
            await server.stop()
        except Exception as exc:
            print(f"  FAIL {exc}")
            failures += 1

    print("\n=== storage ===")
    if cfg.logging.enabled:
        from .store import Store
        try:
            with Store(cfg.logging.database, cfg.logging.retention_days) as store:
                stats = store.stats()
                print(f"  OK   {cfg.logging.database}: {stats['rows']} rows, "
                      f"{stats['bytes'] / 1024:.1f} KiB")
        except Exception as exc:
            print(f"  FAIL {exc}")
            failures += 1
    else:
        print("  SKIP logging disabled")

    print(f"\n{'PASS' if not failures else f'{failures} FAILURE(S)'}")
    return 1 if failures else 0


def cmd_export(args: argparse.Namespace) -> int:
    from .store import Store

    cfg = load_config(args.config)
    since = time.time() - args.hours * 3600
    channels = [c.strip() for c in args.channels.split(",") if c.strip()] or None
    out = open(args.output, "w", encoding="utf-8") if args.output else sys.stdout
    try:
        with Store(cfg.logging.database, cfg.logging.retention_days) as store:
            for line in store.export_csv(channels, since, None):
                out.write(line)
    finally:
        if args.output:
            out.close()
            print(f"wrote {args.output}", file=sys.stderr)
    return 0


# ---- argument parsing -----------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hoot",
        description="HOOT - Humidity & Operational Observation Terminal",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--version", action="version", version=f"hoot {__version__}")
    parser.add_argument("-c", "--config", help="config file (default: ./config.yaml or $HOOT_CONFIG)")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--log-file", help="also write logs here")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("run", help="start the service")

    p_init = sub.add_parser("init", help="write a starter config")
    p_init.add_argument("--name", default="HOOT-01", help="device name")
    p_init.add_argument("--instance", type=int, help="BACnet device instance")
    p_init.add_argument("--altitude", type=int, default=0,
                        help="site altitude in metres, for SCD4x CO2 pressure "
                             "compensation (default 0 = sea level; set this)")
    p_init.add_argument("--simulate", action="store_true",
                        help="configure the simulator instead of real sensors")
    p_init.add_argument("--force", action="store_true", help="overwrite existing config")

    p_read = sub.add_parser("read", help="one-shot sensor read")
    p_read.add_argument("--driver", choices=sorted(DRIVERS),
                        help="read this driver directly, ignoring config")
    p_read.add_argument("-n", "--samples", type=int, default=1)
    p_read.add_argument("-i", "--interval", type=float, default=5.0)
    p_read.add_argument("--units", default="degF", choices=["degF", "degC"])

    sub.add_parser("points", help="print the BACnet object list")
    sub.add_parser("selftest", help="pre-flight check")

    p_exp = sub.add_parser("export", help="export trend data as CSV")
    p_exp.add_argument("--hours", type=float, default=24.0)
    p_exp.add_argument("--channels", default="", help="comma-separated, default all")
    p_exp.add_argument("-o", "--output", help="output file (default: stdout)")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose, args.log_file)
    try:
        if args.command == "run":
            return asyncio.run(cmd_run(args))
        if args.command == "selftest":
            return asyncio.run(cmd_selftest(args))
        return {
            "init": cmd_init,
            "read": cmd_read,
            "points": cmd_points,
            "export": cmd_export,
        }[args.command](args)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
