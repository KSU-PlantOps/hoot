import time

import pytest

from hoot.store import Accumulator, Store


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "t.db", retention_days=1) as s:
        yield s


def test_accumulator_reports_mean_min_max():
    acc = Accumulator("degF")
    for v in (70.0, 72.0, 71.0):
        acc.add(v)
    mean, lo, hi, n, fault = acc.summarise()
    assert mean == pytest.approx(71.0)
    assert (lo, hi, n, fault) == (70.0, 72.0, 3, None)


def test_accumulator_captures_excursion_between_trend_writes():
    """The reason aggregation exists: a 60 s trend must not miss a 5 s spike."""
    acc = Accumulator("ppm")
    for v in (420, 421, 1850, 419):
        acc.add(v)
    mean, lo, hi, _n, _f = acc.summarise()
    assert hi == 1850
    assert mean < 800  # the mean alone would hide it; max does not


def test_accumulator_all_faults_yields_no_value():
    acc = Accumulator("degF")
    acc.add_fault("I2C timeout")
    mean, lo, hi, n, fault = acc.summarise()
    assert mean is None and lo is None and hi is None
    assert fault == "I2C timeout"


def test_accumulator_reset_clears_everything():
    acc = Accumulator("degF")
    acc.add(1.0)
    acc.add_fault("x")
    acc.reset()
    assert acc.empty


def test_write_and_read_back(store):
    store.write_batch([("temperature", "degF", 71.0, 70.0, 72.0, 3, None)])
    rows = store.history("temperature")
    assert len(rows) == 1
    assert rows[0]["value"] == pytest.approx(71.0)
    assert rows[0]["min"] == pytest.approx(70.0)


def test_history_returns_chronological_order(store):
    now = time.time()
    for i in range(5):
        store.write_batch([("t", "degF", float(i), None, None, 1, None)],
                          timestamp=now + i)
    values = [r["value"] for r in store.history("t")]
    assert values == [0.0, 1.0, 2.0, 3.0, 4.0]


def test_faults_are_recorded_with_null_value(store):
    store.write_batch([("co2", "ppm", None, None, None, 0, "I2C read failed")])
    row = store.history("co2")[0]
    assert row["value"] is None
    assert row["fault"] == "I2C read failed"


def test_purge_removes_only_rows_past_retention(store):
    now = time.time()
    store.write_batch([("t", "degF", 1.0, None, None, 1, None)], timestamp=now - 3 * 86400)
    store.write_batch([("t", "degF", 2.0, None, None, 1, None)], timestamp=now)
    assert store.purge(force=True) == 1
    remaining = [r["value"] for r in store.history("t")]
    assert remaining == [2.0]


def test_purge_is_rate_limited_unless_forced(store):
    store.purge(force=True)
    assert store.purge() == 0  # too soon after the last one


def test_retention_zero_disables_purging(tmp_path):
    with Store(tmp_path / "keep.db", retention_days=0) as s:
        s.write_batch([("t", "degF", 1.0, None, None, 1, None)],
                      timestamp=time.time() - 10_000 * 86400)
        assert s.purge(force=True) == 0
        assert len(s.history("t")) == 1


def test_csv_export_has_header_and_rows(store):
    store.write_batch([("temperature", "degF", 71.5, 71.0, 72.0, 2, None)])
    lines = list(store.export_csv())
    assert lines[0].startswith("timestamp_iso,timestamp_epoch,channel")
    assert "temperature" in lines[1]
    assert "71.5000" in lines[1]


def test_csv_export_escapes_commas_in_fault_text(store):
    store.write_batch([("t", "degF", None, None, None, 0, "bad, very bad")])
    row = list(store.export_csv())[1]
    assert row.count(",") == 8  # exactly the column separators, none injected
    assert "bad; very bad" in row


def test_stats_reports_rows_and_channels(store):
    store.write_batch([("a", "degF", 1.0, None, None, 1, None),
                       ("b", "ppm", 2.0, None, None, 1, None)])
    stats = store.stats()
    assert stats["rows"] == 2
    assert stats["channels"] == ["a", "b"]


def test_history_buckets_long_windows_across_the_whole_range(store):
    """Regression: history() returned the newest ``limit`` rows, so a 30-day
    chart at a 60 s trend interval only showed the last day and a half."""
    start = time.time() - 10_000
    for i in range(1000):
        store.write_batch([("t", "degF", float(i), float(i) - 1, float(i) + 1, 1, None)],
                          timestamp=start + i * 10)
    rows = store.history("t", since=start - 1, limit=100, bucket_seconds=100)
    assert len(rows) <= 101  # one extra for a window that straddles a bucket edge
    assert rows[0]["ts"] < start + 200            # reaches back to the start
    assert rows[-1]["ts"] > start + 9_700         # and forward to the end
    assert rows[0]["min"] == pytest.approx(-1.0)  # min of mins survives bucketing
    assert rows[-1]["max"] == pytest.approx(1000.0)


def test_csv_export_formats_numbers_and_blanks(store):
    store.write_batch([("t", "degF", 71.5, None, None, 1, "I2C, timeout")], timestamp=0.0)
    lines = list(store.export_csv())
    assert lines[1] == "1970-01-01T00:00:00Z,0.000,t,71.5000,,,1,degF,I2C; timeout\n"
