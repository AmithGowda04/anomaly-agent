"""End-to-end run: findings -> dedupe -> summaries -> emails -> log."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pandas as pd

from . import alerts, engine, llm
from .schema import Rule
from .summarizer import DaySummary, summarize_day


@dataclass
class RunResult:
    findings: pd.DataFrame
    alerted: pd.DataFrame
    suppressed: pd.DataFrame
    summaries: list[DaySummary] = field(default_factory=list)
    deliveries: list[str] = field(default_factory=list)


def run(
    df: pd.DataFrame,
    rules: list[Rule],
    report_name: str = "report",
    replay: bool = False,
    out_dir: Path = Path("outputs"),
    cooldown_days: int = 3,
    send: bool = False,
    use_log: bool = True,
    polish: bool = False,
    api_key: Optional[str] = None,
    to: str = "team@example.com",
    write_emails: bool = True,
) -> RunResult:
    missing = engine.missing_columns(rules, df.columns)
    if missing:
        raise ValueError(f"Rules reference columns that are not in the data: {missing}. Columns: {list(df.columns)}")

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    allf = engine.evaluate(df, rules)
    if not replay and not allf.empty:
        allf = allf[allf["date"] == df.index.max()]

    log_path = out_dir / "alerts_log.csv"
    log = alerts.read_log(log_path) if use_log else pd.DataFrame(columns=alerts.LOG_COLS)
    to_alert, suppressed = alerts.filter_new(allf, log, cooldown_days)

    result = RunResult(findings=allf, alerted=to_alert, suppressed=suppressed)
    metrics = list(df.columns)
    for date, day in to_alert.groupby("date"):
        s = summarize_day(day, metrics)
        polished = None
        if polish and llm.available(api_key):
            try:
                polished = llm.polish_summary(s.text(), api_key=api_key)
            except Exception:
                polished = None  # deterministic summary still goes out
        msg = alerts.build_email(s, report_name, to=to, polished=polished)
        if write_emails or send:
            result.deliveries.append(alerts.deliver(msg, out_dir / "emails", send=send))
        result.summaries.append(s)
    if use_log and not to_alert.empty:
        alerts.append_log(log_path, to_alert, sent=send)
    return result
