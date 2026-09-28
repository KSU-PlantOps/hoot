import pytest

from hoot.calibration import Calibration


def test_identity_by_default():
    cal = Calibration()
    assert cal.is_identity
    assert cal.apply(21.5) == 21.5
    assert cal.describe() == "uncalibrated (raw)"


def test_single_point_makes_raw_read_as_reference():
    cal = Calibration.from_single_point(raw=22.9, reference=22.2)
    assert cal.apply(22.9) == pytest.approx(22.2)
    assert cal.gain == 1.0
    assert not cal.is_identity


def test_two_point_solves_gain_and_offset():
    cal = Calibration.from_two_point(0.4, 0.0, 50.6, 50.0)
    assert cal.apply(0.4) == pytest.approx(0.0, abs=1e-9)
    assert cal.apply(50.6) == pytest.approx(50.0, abs=1e-9)
    # and interpolates sensibly in between
    assert cal.apply(25.5) == pytest.approx(25.0, abs=0.05)


def test_two_point_rejects_identical_raw_readings():
    with pytest.raises(ValueError, match="two distinct raw readings"):
        Calibration.from_two_point(20.0, 20.0, 20.0, 25.0)


def test_round_trips_through_dict():
    cal = Calibration.from_single_point(10.0, 11.0, note="bench")
    restored = Calibration.from_dict(cal.to_dict())
    assert restored.offset == pytest.approx(cal.offset)
    assert restored.note == "bench"


def test_from_dict_handles_missing_and_none():
    assert Calibration.from_dict(None).is_identity
    assert Calibration.from_dict({}).is_identity
