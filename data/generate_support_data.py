"""Generate a SECOND synthetic report: a customer-support desk, with planted anomalies.

Purpose: show the agent works on a different report (different columns, different
meaning of "bad") just by swapping the plain-English rules file. The data is SYNTHETIC.

Run:  python data/generate_support_data.py
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).parent
SEED = 11
DAYS = 180
START = "2026-04-01"

# (day index, short name, description)
PLANTED = [
    (55, "ticket_surge", "Tickets opened 2.2x and response time 1.9x: a release or outage floods the queue"),
    (90, "slow_response", "Response time 2.5x and satisfaction -16%: staffing gap"),
    (118, "escalation_spike", "Escalations 4x: a new issue type the first line cannot handle"),
    (145, "resolution_stall", "Tickets resolved -55% and backlog 1.6x: agents or tooling offline"),
    (170, "satisfaction_drop", "Satisfaction -22% with normal response times: a bad policy or release"),
]


def build(seed: int = SEED) -> tuple[pd.DataFrame, list[dict]]:
    rng = np.random.default_rng(seed)
    dates = pd.date_range(START, periods=DAYS, freq="D")
    t = np.arange(DAYS)

    weekly = np.where(dates.dayofweek.to_numpy() >= 5, 0.85, 1.0)  # quieter weekends
    growth = 1 + 0.0005 * t

    opened = 420 * growth * weekly * rng.normal(1, 0.07, DAYS)
    resolved = 410 * growth * weekly * rng.normal(1, 0.06, DAYS)
    response = 2.0 * rng.normal(1, 0.08, DAYS)  # hours
    satisfaction = 0.88 * rng.normal(1, 0.025, DAYS)
    escalations = rng.poisson(12, DAYS).astype(float)
    backlog = 300 * rng.normal(1, 0.08, DAYS)

    opened[55] *= 2.2
    response[55] *= 1.9
    response[90] *= 2.5
    satisfaction[90] *= 0.84
    escalations[118] *= 4.0
    resolved[145] *= 0.45
    backlog[145] *= 1.6
    satisfaction[170] *= 0.78

    df = pd.DataFrame(
        {
            "Date": dates,
            "Tickets Opened": opened.round(0),
            "Tickets Resolved": resolved.round(0),
            "Avg Response Time Hrs": response.round(2),
            "Satisfaction Rate": satisfaction.round(4),
            "Escalations": escalations,
            "Backlog": backlog.round(0),
        }
    )
    truth = [
        {"date": dates[i].strftime("%Y-%m-%d"), "name": name, "description": desc}
        for i, name, desc in PLANTED
    ]
    return df, truth


def main() -> None:
    df, truth = build()
    out = HERE / "support_metrics.xlsx"
    df.to_excel(out, index=False, sheet_name="Daily Support")
    (HERE / "support_ground_truth.json").write_text(json.dumps(truth, indent=2))
    print(f"wrote {out} ({len(df)} rows) and support_ground_truth.json ({len(truth)} planted anomalies)")


if __name__ == "__main__":
    main()
