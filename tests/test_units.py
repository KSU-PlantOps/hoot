import pytest

from hoot.units import convert, delta_convert, display_unit


def test_absolute_temperature_conversion():
    assert convert(0.0, "degC", "degF") == pytest.approx(32.0)
    assert convert(100.0, "degC", "degF") == pytest.approx(212.0)
    assert convert(72.0, "degF", "degC") == pytest.approx(22.2222, abs=1e-4)


def test_identity_conversion_is_exact():
    assert convert(21.7, "degC", "degC") == 21.7


def test_delta_conversion_does_not_add_the_freezing_offset():
    """The bug this guards against: treating a calibration offset as an
    absolute reading turns a 0.5 degC correction into a 32.9 degF one."""
    assert delta_convert(1.0, "degC", "degF") == pytest.approx(1.8)
    assert delta_convert(0.5, "degC", "degF") == pytest.approx(0.9)
    assert delta_convert(0.0, "degC", "degF") == 0.0
    # and specifically NOT the absolute conversion
    assert delta_convert(0.5, "degC", "degF") != convert(0.5, "degC", "degF")


def test_delta_round_trips():
    assert delta_convert(delta_convert(2.5, "degC", "degF"), "degF", "degC") == pytest.approx(2.5)


def test_unsupported_conversion_raises():
    with pytest.raises(ValueError, match="no conversion"):
        convert(1.0, "ppm", "degF")


def test_display_unit_respects_site_preference():
    assert display_unit("temperature", "degF") == "degF"
    assert display_unit("temperature", "degC") == "degC"
    assert display_unit("scd_temperature", "degC") == "degC"
    assert display_unit("humidity", "degF") == "percentRH"
    assert display_unit("co2", "degF") == "ppm"


def test_display_unit_humidity_channels_by_suffix():
    assert display_unit("scd_humidity", "degF") == "percentRH"


def test_unknown_channel_keeps_its_driver_unit():
    """Regression: an unrecognised channel fell through to degF, so a new
    driver's channel (say, VOC index) was published as a temperature and the
    conversion from its real unit raised on every cycle."""
    assert display_unit("voc_index", "degF", "noUnits") == "noUnits"
    assert display_unit("pm25", "degF", "ugm3") == "ugm3"
