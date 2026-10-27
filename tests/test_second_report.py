"""The same engine must work on a different report by swapping only the English rules file."""
from pathlib import Path

import pandas as pd
import pytest

from generate_support_data import build

from anomaly_agent import compiler, engine, loader
from anomaly_agent.summarizer import summarize_day

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def support():
    raw, truth = build(11)
    df, dq = loader.prepare(raw)
    return df, truth, dq


@pytest.fixture(scope="module")
def support_rules(support):
    df, _, _ = support
    res = compiler.compile_rules((ROOT / "rules" / "support_rules.txt").read_text(), list(df.columns), mode="templates")
    assert not res.errors, res.errors
    return res.rules


def test_support_data_loads_clean(support):
    df, truth, dq = support
    assert not dq.has_errors and not dq.issues
    assert list(df.columns) == [
        "tickets_opened", "tickets_resolved", "avg_response_time_hrs",
        "satisfaction_rate", "escalations", "backlog",
    ]
    assert len(truth) == 5


def test_every_support_rule_compiles(support_rules):
    assert len(support_rules) == 12


def test_planted_support_anomalies_are_caught(support, support_rules):
    df, truth, _ = support
    found = engine.evaluate(df, support_rules)
    flagged = {d.strftime("%Y-%m-%d") for d in found["date"]}
    missed = {t["date"] for t in truth} - flagged
    assert not missed, f"missed planted anomalies: {sorted(missed)}"


def test_support_summary_gets_support_explanation(support, support_rules):
    df, truth, _ = support
    found = engine.evaluate(df, support_rules)
    surge = next(t["date"] for t in truth if t["name"] == "ticket_surge")
    day = found[found["date"] == pd.Timestamp(surge)]
    s = summarize_day(day, list(df.columns))
    assert s.severity == "critical"
    assert any("Ticket volume and response time both rose" in e for e in s.explanations)


def test_sales_rules_do_not_silently_apply_to_support_columns(support):
    """Rules naming metrics the report doesn't have must fail loudly, not run on nothing."""
    df, _, _ = support
    res = compiler.compile_rules((ROOT / "rules" / "sales_rules.txt").read_text(), list(df.columns), mode="templates")
    assert res.rules == []
    assert res.errors
