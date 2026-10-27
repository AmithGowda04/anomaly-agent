"""Smoke test the Streamlit app headlessly (no browser, no network)."""
from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parent.parent / "app.py")


def _button(at, label):
    return next(b for b in at.button if b.label == label)


def test_app_full_flow_on_sample_data():
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception
    _button(at, "Compile rules").click().run()
    assert not at.exception
    assert not at.error  # all sample rules compile
    at.checkbox(key="confirmed").check().run()
    assert not at.exception
    # replay shows the planted anomalies
    next(r for r in at.radio if r.label == "Evaluate").set_value("Whole history (replay)").run()
    assert not at.exception
    alert_days = int(at.metric[0].value)
    assert alert_days >= 5


def test_app_support_sample_uses_its_own_rules_and_data():
    at = AppTest.from_file(APP, default_timeout=60).run()
    next(r for r in at.radio if r.label == "Source").set_value("Sample: support desk (synthetic)").run()
    assert not at.exception
    assert "Tickets Opened" in at.text_area[0].value or "tickets opened" in at.text_area[0].value
    _button(at, "Compile rules").click().run()
    assert not at.exception and not at.error
    at.checkbox(key="confirmed").check().run()
    next(r for r in at.radio if r.label == "Evaluate").set_value("Whole history (replay)").run()
    assert not at.exception
    assert int(at.metric[0].value) >= 5


def test_app_blocks_until_confirmed():
    at = AppTest.from_file(APP, default_timeout=60).run()
    _button(at, "Compile rules").click().run()
    assert len(at.metric) == 0  # no results before confirmation


def test_app_reports_unparseable_rule_clearly():
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.text_area[0].set_value("tell me if sales explode").run()
    _button(at, "Compile rules").click().run()
    assert any("not compiled" in e.value for e in at.error)
