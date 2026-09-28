import logging

from hoot.logbuffer import RingBufferHandler


def _logger(handler, name="hoot.test"):
    log = logging.getLogger(name)
    log.setLevel(logging.DEBUG)
    log.addHandler(handler)
    log.propagate = False
    return log


def test_keeps_formatted_lines_in_order():
    buf = RingBufferHandler(capacity=10)
    log = _logger(buf, "hoot.test.order")
    log.info("first")
    log.warning("second")
    lines = [e["line"] for e in buf.entries()]
    assert lines[0].endswith("hoot.test.order: first")
    assert "WARNING" in lines[1] and lines[1].endswith("second")


def test_drops_oldest_beyond_capacity():
    buf = RingBufferHandler(capacity=3)
    log = _logger(buf, "hoot.test.cap")
    for i in range(5):
        log.info("n=%d", i)
    assert [e["line"][-3:] for e in buf.entries()] == ["n=2", "n=3", "n=4"]


def test_filters_by_level_and_limit():
    buf = RingBufferHandler(capacity=10)
    log = _logger(buf, "hoot.test.level")
    log.debug("d")
    log.info("i")
    log.error("e1")
    log.error("e2")
    assert len(buf.entries(logging.WARNING)) == 2
    assert [e["line"][-2:] for e in buf.entries(limit=1)] == ["e2"]


def test_text_includes_tracebacks():
    buf = RingBufferHandler(capacity=10)
    log = _logger(buf, "hoot.test.exc")
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        log.exception("failed")
    assert "RuntimeError: boom" in buf.text()
