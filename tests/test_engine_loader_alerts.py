import io
from datetime import date

import numpy as np
import pandas as pd

from anomaly_agent import alerts, engine, loader
from anomaly_agent.rule_parser import parse_line
from anomaly_agent.summarizer import summarize_day
from conftest import COLS


def rule(line, cols=("x",)):
    return parse_line(line, list(cols))


# ---------------- engine ----------------
def test_mad_flags_the_spike_and_little_else(flat_noise):
    f = engine.evaluate(flat_noise, [rule("Warn if x is unusually high")])
    assert list(f["date"].dt.day_of_year) == [flat_noise.index[45].day_of_year]
    assert f.iloc[0]["direction"] == "up"


def test_baseline_excludes_the_day_being_scored(flat_noise):
    sc = engine.score_series(flat_noise["x"], 14, "mad")
    # if today leaked into its own baseline the median would move toward 160
    assert abs(sc["baseline"].iloc[45] - 100) < 3
    assert sc["score"].iloc[45] > 10


def test_spike_does_not_poison_following_baselines(flat_noise):
    sc = engine.score_series(flat_noise["x"], 14, "mad")
    # median/MAD are robust: the day after the spike still has a sane baseline
    assert abs(sc["baseline"].iloc[46] - 100) < 3


def test_flat_series_yields_no_score_instead_of_infinity():
    idx = pd.date_range("2026-01-01", periods=40, freq="D")
    df = pd.DataFrame({"x": np.full(40, 5.0)}, index=idx)
    sc = engine.score_series(df["x"], 14, "mad")
    assert sc["score"].isna().all()
    assert engine.evaluate(df, [rule("Warn if x is unusually high")]).empty


def test_min_change_guard_ignores_statistically_odd_but_tiny_moves():
    idx = pd.date_range("2026-01-01", periods=40, freq="D")
    base = np.tile([100.0, 100.1, 99.9, 100.0], 10)
    base[30] = 101.0  # huge in sigma terms, 1% in business terms
    df = pd.DataFrame({"x": base}, index=idx)
    assert engine.evaluate(df, [rule("Warn if x is unusually high")]).empty


def test_pct_rule_direction_matters(flat_noise):
    assert engine.evaluate(flat_noise, [rule("Alert if x drops more than 20% below its 14-day average")]).empty
    up = engine.evaluate(flat_noise, [rule("Alert if x rises more than 20% above its 14-day average")])
    assert len(up) == 1


def test_static_percent_literal_resolves_against_column_scale():
    idx = pd.date_range("2026-01-01", periods=5, freq="D")
    frac = pd.DataFrame({"conv": [0.026, 0.025, 0.004, 0.026, 0.027]}, index=idx)   # stored as 0.026
    whole = pd.DataFrame({"conv": [2.6, 2.5, 0.4, 2.6, 2.7]}, index=idx)            # stored as 2.6
    r = parse_line("Warn if conv is below 1%", ["conv"])
    assert len(engine.evaluate(frac, [r])) == 1
    assert len(engine.evaluate(whole, [r])) == 1


def test_pattern_requires_both_legs_same_day():
    rng = np.random.default_rng(1)
    idx = pd.date_range("2026-01-01", periods=60, freq="D")
    a = 100 + rng.normal(0, 2, 60)
    b = 50 + rng.normal(0, 1, 60)
    a[40] = 150           # a up alone
    a[50], b[50] = 150, 20  # a up AND b down
    df = pd.DataFrame({"a": a, "b": b}, index=idx)
    f = engine.evaluate(df, [parse_line("Alert if a rises and b falls", ["a", "b"])])
    assert list(f["date"].dt.strftime("%m-%d")) == [idx[50].strftime("%m-%d")]


def test_missing_column_detected():
    r = rule("Warn if x is unusually high")
    assert engine.missing_columns([r], ["y"]) == ["x"]


def test_planted_anomalies_are_caught_end_to_end(sales):
    from anomaly_agent.compiler import compile_rules

    df, truth = sales
    text = open("rules/sales_rules.txt").read()
    res = compile_rules(text, list(df.columns), mode="templates")
    assert not res.errors
    flagged = {d.strftime("%Y-%m-%d") for d in engine.evaluate(df, res.rules)["date"]}
    assert {t["date"] for t in truth} <= flagged


# ---------------- loader ----------------
def test_loader_cleans_messy_input():
    raw = pd.DataFrame({
        "Day": ["2026-01-01", "01/02/2026", "2026-01-04", "2026-01-04", "garbage"],
        "Rev (INR)": ["1,200", "₹1,300", "1400", "1500", "1600"],
        "Notes": ["a", "b", "c", "d", "e"],
    })
    df, dq = loader.prepare(raw, column_map={"Rev (INR)": "revenue"})
    assert "revenue" in df.columns and "notes" not in df.columns
    codes = {i.code for i in dq.issues}
    assert {"bad_dates", "duplicate_dates", "dropped_column"} <= codes
    assert df["revenue"].iloc[0] == 1200.0


def test_loader_reports_gaps_and_missing_values():
    idx = pd.date_range("2026-01-01", periods=10, freq="D").delete([3, 4])
    raw = pd.DataFrame({"date": idx, "v": [1, 2, 3, np.nan, 5, 6, 7, 8]})
    df, dq = loader.prepare(raw)
    codes = {i.code for i in dq.issues}
    assert "date_gaps" in codes and "missing_values" in codes


def test_loader_errors_without_date_or_numbers():
    _, dq = loader.prepare(pd.DataFrame({"a": ["x", "y"], "b": ["p", "q"]}))
    assert dq.has_errors


def test_freshness_warning():
    df, dq = loader.prepare(pd.DataFrame({"date": pd.date_range("2026-01-01", periods=5), "v": range(5)}))
    loader.check_freshness(dq, df, date(2026, 3, 1), max_age_days=3)
    assert any(i.code == "stale_data" for i in dq.issues)


def test_read_any_csv_and_excel_roundtrip(tmp_path):
    src = pd.DataFrame({"date": pd.date_range("2026-01-01", periods=5), "v": range(5)})
    src.to_csv(tmp_path / "a.csv", index=False)
    src.to_excel(tmp_path / "a.xlsx", index=False)
    assert len(loader.read_any(tmp_path / "a.csv", name="a.csv")) == 5
    assert len(loader.read_any(tmp_path / "a.xlsx", name="a.xlsx")) == 5


# ---------------- alerts / dedupe ----------------
def _findings(dates, metric="revenue", key="k", direction="down", sev="critical"):
    return pd.DataFrame({
        "date": pd.to_datetime(dates), "metric": metric, "rule_key": key, "rule_text": "t", "kind": "deviation",
        "severity": sev, "direction": direction, "value": 1.0, "baseline": 2.0, "change_pct": -50.0, "score": -5.0,
    })


def test_cooldown_suppresses_repeats_then_realerts():
    f = _findings(["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-05"])
    keep, drop = alerts.filter_new(f, alerts.read_log("/nonexistent.csv"), cooldown_days=3)
    assert list(keep["date"].dt.strftime("%d")) == ["01", "05"]
    assert list(drop["date"].dt.strftime("%d")) == ["02", "03"]


def test_different_direction_or_metric_not_suppressed():
    f = pd.concat([_findings(["2026-01-01"]), _findings(["2026-01-01"], metric="orders"),
                   _findings(["2026-01-01"], direction="up")])
    keep, drop = alerts.filter_new(f, alerts.read_log("/nonexistent.csv"), 3)
    assert len(keep) == 3 and drop.empty


def test_rerun_is_idempotent_via_log(tmp_path):
    f = _findings(["2026-01-01"])
    log = tmp_path / "log.csv"
    k1, _ = alerts.filter_new(f, alerts.read_log(log), 3)
    alerts.append_log(log, k1, sent=False)
    k2, d2 = alerts.filter_new(f, alerts.read_log(log), 3)
    assert len(k1) == 1 and k2.empty and len(d2) == 1


def test_dry_run_writes_eml_and_send_without_smtp_refuses(tmp_path, monkeypatch):
    s = summarize_day(_findings(["2026-01-01"]), COLS)
    msg = alerts.build_email(s, "Sales")
    assert "[CRITICAL]" in msg["Subject"]
    out = alerts.deliver(msg, tmp_path, send=False)
    assert out.startswith("dry-run") and list(tmp_path.glob("*.eml"))
    monkeypatch.delenv("SMTP_HOST", raising=False)
    try:
        alerts.deliver(msg, tmp_path, send=True)
        raised = False
    except RuntimeError:
        raised = True
    assert raised  # never pretend an email was sent


# ---------------- summarizer ----------------
def test_pattern_legs_feed_explanations():
    f = _findings(["2026-01-01"], metric="traffic+conversion_rate", direction="pattern",
                  key="pattern|traffic:up+conversion_rate:down|w28|mad|mean|None")
    f["kind"] = "pattern"
    s = summarize_day(f, COLS)
    assert any("visitor mix" in e or "Traffic rose while conversion fell" in e for e in s.explanations)
    assert any("traffic rose while conversion rate fell" in b for b in s.bullets)


def test_no_explanations_invented_for_unknown_metrics():
    f = _findings(["2026-01-01"], metric="widgets")
    s = summarize_day(f, ["widgets", "gadgets"])
    assert s.explanations == []
