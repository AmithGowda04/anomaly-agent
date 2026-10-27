"""Detection engine.

Baselines are computed from the PREVIOUS `window` rows only (shifted by one), so an
anomaly never contaminates its own baseline. Robust scoring uses median/MAD:
    modified_z = 0.6745 * (x - median) / MAD
(the Iglewicz-Hoaglin convention; 3.5 is the usual cutoff).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .schema import Rule

FINDING_COLS = [
    "date", "metric", "rule_key", "rule_text", "kind", "severity",
    "direction", "value", "baseline", "change_pct", "score",
]


def _min_periods(window: int) -> int:
    return max(3, int(np.ceil(window * 0.7)))


def _mad(x: np.ndarray) -> float:
    x = x[~np.isnan(x)]
    return float(np.median(np.abs(x - np.median(x)))) if len(x) else np.nan


def score_series(s: pd.Series, window: int, method: str = "mad") -> pd.DataFrame:
    """Per-day baseline and score against the previous `window` observations."""
    prev = s.shift(1)
    mp = _min_periods(window)
    roll = prev.rolling(window, min_periods=mp)
    if method == "mad":
        base = roll.median()
        mad = roll.apply(_mad, raw=True)
        mad = mad.where(mad > 0)  # flat window -> no score (use a pct/static rule)
        score = 0.6745 * (s - base) / mad
    else:
        base = roll.mean()
        std = roll.std(ddof=1)
        std = std.where(std > 0)
        score = (s - base) / std
    change = (s - base) / base.abs().where(base.abs() > 0) * 100
    return pd.DataFrame({"value": s, "baseline": base, "score": score, "change_pct": change})


def baseline_for_pct(s: pd.Series, window: int, stat: str) -> pd.Series:
    roll = s.shift(1).rolling(window, min_periods=_min_periods(window))
    return roll.mean() if stat == "mean" else roll.median()


def _dir_ok(direction: str, signed: pd.Series) -> pd.Series:
    if direction == "up":
        return signed > 0
    if direction == "down":
        return signed < 0
    return signed != 0


def _static_limit(rule: Rule, s: pd.Series) -> float:
    """'below 1%' on a column stored as 0.026 means 0.01; stored as 2.6 means 1."""
    limit = float(rule.threshold)
    if rule.percent_literal:
        scale = float(s.abs().max()) if s.notna().any() else 0.0
        if scale <= 1.5:
            limit /= 100.0
    return limit


def _rows(date, metric, rule: Rule, direction, value, baseline, change, score) -> dict:
    return dict(
        date=date, metric=metric, rule_key=rule.key, rule_text=rule.text,
        kind=rule.kind, severity=rule.severity, direction=direction,
        value=value, baseline=baseline, change_pct=change, score=score,
    )


def _eval_rule(df: pd.DataFrame, rule: Rule) -> list[dict]:
    out: list[dict] = []
    if rule.kind == "pct_vs_baseline":
        s = df[rule.metric]
        base = baseline_for_pct(s, rule.window, rule.baseline_stat)
        change = (s - base) / base.abs().where(base.abs() > 0) * 100
        hit = (change.abs() > rule.threshold) & _dir_ok(rule.direction, change)
        for d in change.index[hit.fillna(False)]:
            out.append(_rows(d, rule.metric, rule, "up" if change[d] > 0 else "down",
                             s[d], base[d], change[d], np.nan))
    elif rule.kind == "deviation":
        sc = score_series(df[rule.metric], rule.window, rule.method)
        big = sc["change_pct"].abs() >= rule.min_change_pct
        hit = (sc["score"].abs() >= rule.threshold) & _dir_ok(rule.direction, sc["score"]) & big
        for d in sc.index[hit.fillna(False)]:
            r = sc.loc[d]
            out.append(_rows(d, rule.metric, rule, "up" if r["score"] > 0 else "down",
                             r["value"], r["baseline"], r["change_pct"], r["score"]))
    elif rule.kind == "static":
        s = df[rule.metric]
        limit = _static_limit(rule, s)
        hit = (s < limit) if rule.bound == "below" else (s > limit)
        for d in s.index[hit.fillna(False)]:
            out.append(_rows(d, rule.metric, rule, "down" if rule.bound == "below" else "up",
                             s[d], limit, np.nan, np.nan))
    elif rule.kind == "pattern":
        flags = []
        scored = {}
        for leg in rule.legs or []:
            sc = score_series(df[leg.metric], rule.window, "mad")
            scored[leg.metric] = sc
            ok = (sc["score"].abs() >= rule.leg_threshold) & _dir_ok(leg.direction, sc["score"])
            ok &= sc["change_pct"].abs() >= rule.min_change_pct
            flags.append(ok.fillna(False))
        both = flags[0]
        for f in flags[1:]:
            both = both & f
        name = "+".join(leg.metric for leg in rule.legs or [])
        for d in both.index[both]:
            lead = (rule.legs or [])[0].metric
            r = scored[lead].loc[d]
            out.append(_rows(d, name, rule, "pattern", r["value"], r["baseline"], r["change_pct"], r["score"]))
    return out


def evaluate(df: pd.DataFrame, rules: list[Rule]) -> pd.DataFrame:
    """Run every rule over every day. Returns one row per (day, rule) breach."""
    rows: list[dict] = []
    for rule in rules:
        rows.extend(_eval_rule(df, rule))
    if not rows:
        return pd.DataFrame(columns=FINDING_COLS)
    out = pd.DataFrame(rows, columns=FINDING_COLS)
    out["date"] = pd.to_datetime(out["date"])
    return out.sort_values(["date", "severity", "metric"]).reset_index(drop=True)


def firing_rates(findings: pd.DataFrame, rules: list[Rule], scored_days: int) -> list[tuple[Rule, int, float]]:
    """Backtest helper: how many days each rule would have fired in this history.

    A rule that fires on >5% of days is usually too sensitive for that metric."""
    out = []
    for r in rules:
        n = int((findings["rule_key"] == r.key).sum()) if not findings.empty else 0
        out.append((r, n, n / scored_days if scored_days else 0.0))
    return out


def missing_columns(rules: list[Rule], columns) -> list[str]:
    cols = set(columns)
    return sorted({m for r in rules for m in r.metrics if m not in cols})
