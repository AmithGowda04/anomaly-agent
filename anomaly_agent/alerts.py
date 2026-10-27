"""Email building/sending, alert log, and de-duplication.

De-duplication works on DATA dates (not wall-clock time), so replaying history gives
the same result as running daily:
  * exact repeat (same day/metric/rule/direction) is always suppressed;
  * same metric+rule+direction within `cooldown_days` of the last alert is suppressed."""
from __future__ import annotations

import csv
import os
import smtplib
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Optional

import pandas as pd

from .summarizer import DaySummary

LOG_COLS = ["logged_at", "data_date", "metric", "rule_key", "severity", "direction", "value", "baseline", "sent"]


def read_log(path: Path) -> pd.DataFrame:
    p = Path(path)
    if not p.exists() or p.stat().st_size == 0:
        return pd.DataFrame(columns=LOG_COLS)
    df = pd.read_csv(p)
    df["data_date"] = pd.to_datetime(df["data_date"])
    return df


def filter_new(findings: pd.DataFrame, log: pd.DataFrame, cooldown_days: int = 3) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split findings into (to_alert, suppressed)."""
    if findings.empty:
        return findings, findings
    findings = findings.reset_index(drop=True)  # positional labels: callers may pass concatenated frames
    seen: dict[tuple, list[pd.Timestamp]] = {}
    for _, p in log.iterrows():
        seen.setdefault((p["metric"], p["rule_key"], p["direction"]), []).append(pd.Timestamp(p["data_date"]))
    window = max(cooldown_days, 1)
    keep, drop = [], []
    for idx, r in findings.sort_values("date").iterrows():
        key = (r["metric"], r["rule_key"], r["direction"])
        day = pd.Timestamp(r["date"])
        suppress = any(0 <= (day - prev).days < window for prev in seen.get(key, []))
        if suppress:
            drop.append(idx)
        else:
            keep.append(idx)
            seen.setdefault(key, []).append(day)
    return findings.loc[keep], findings.loc[drop]


def append_log(path: Path, findings: pd.DataFrame, sent: bool) -> None:
    p = Path(path)
    new = not p.exists() or p.stat().st_size == 0
    with p.open("a", newline="") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(LOG_COLS)
        now = datetime.now().isoformat(timespec="seconds")
        for _, r in findings.iterrows():
            w.writerow([now, pd.Timestamp(r["date"]).date(), r["metric"], r["rule_key"], r["severity"],
                        r["direction"], r["value"], r["baseline"], sent])


def build_email(summary: DaySummary, report_name: str, to: str = "team@example.com",
                sender: str = "anomaly-agent@example.com", polished: Optional[str] = None) -> EmailMessage:
    tag = summary.severity.upper()
    msg = EmailMessage()
    msg["Subject"] = f"[{tag}] {report_name}: {len(summary.bullets)} anomaly signal(s) on {summary.date.date()}"
    msg["From"] = sender
    msg["To"] = to
    body = [f"{summary.headline}", ""]
    if polished:
        body += [polished, "", "Details:"]
    body += [f"- {b}" for b in summary.bullets]
    if summary.explanations:
        body += ["", "Possible explanations (hypotheses to check, not confirmed causes):"]
        body += [f"- {e}" for e in summary.explanations]
    body += ["", "Why you are getting this: these values moved outside their recent normal range "
                 "according to the rules configured for this report.",
             "Repeat alerts for the same signal are suppressed during the cooldown window."]
    msg.set_content("\n".join(body))
    return msg


def deliver(msg: EmailMessage, out_dir: Path, send: bool = False) -> str:
    """Send via SMTP when send=True and SMTP_* env vars exist; otherwise write an .eml file."""
    if send:
        host = os.environ.get("SMTP_HOST")
        if not host:
            raise RuntimeError("SMTP_HOST is not set; refusing to pretend an email was sent.")
        port = int(os.environ.get("SMTP_PORT", "587"))
        user, pw = os.environ.get("SMTP_USER"), os.environ.get("SMTP_PASSWORD")
        msg.replace_header("To", os.environ.get("ALERT_TO", msg["To"]))
        msg.replace_header("From", os.environ.get("ALERT_FROM", msg["From"]))
        with smtplib.SMTP(host, port, timeout=30) as s:
            s.starttls()
            if user and pw:
                s.login(user, pw)
            s.send_message(msg)
        return f"sent to {msg['To']}"
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    safe = "".join(c if c.isalnum() else "_" for c in msg["Subject"])[:80]
    path = out_dir / f"{safe}.eml"
    path.write_bytes(bytes(msg))
    return f"dry-run: wrote {path.name}"
