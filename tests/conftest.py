import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hoot.config import Config, PointConfig, SensorConfig  # noqa: E402


@pytest.fixture
def sim_config(tmp_path):
    """A fully valid simulator-backed config that touches nothing real."""
    cfg = Config()
    cfg.bacnet.enabled = False
    cfg.web.enabled = False
    cfg.display.enabled = False
    cfg.sampling.interval_seconds = 1.0
    cfg.logging.interval_seconds = 1.0
    cfg.logging.database = str(tmp_path / "hoot.db")
    cfg.path = tmp_path / "config.yaml"
    cfg.sensors = [SensorConfig(driver="simulator", options={"seed": 42},
                                allow_uncalibrated=True)]
    cfg.points = [
        PointConfig(channel="temperature", name="SpaceTemp", instance=1),
        PointConfig(channel="humidity", name="SpaceHumidity", instance=2),
        PointConfig(channel="co2", name="SpaceCO2", instance=3),
    ]
    cfg.validate()
    return cfg


@pytest.fixture
def paused_service():
    """Start a service but suspend its autonomous sampling loop.

    Tests drive ``_sample_once()`` explicitly so counters and values are
    deterministic; leaving the real loop running makes every assertion about
    cycle counts a race.
    """
    import asyncio
    import contextlib

    from hoot.service import HootService

    async def _start(cfg):
        svc = HootService(cfg)
        await svc.start()
        if svc._task is not None:
            svc._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await svc._task
            svc._task = None
        # Reset counters the loop may have incremented before we cancelled it.
        svc.health.cycles = 0
        svc.health.read_errors = 0
        return svc

    return _start
