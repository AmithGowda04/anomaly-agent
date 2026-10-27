"""Generate a synthetic daily business-metrics workbook with known, planted anomalies.

The data is SYNTHETIC. It exists so the agent can be tested against ground truth:
every planted anomaly is recorded in ground_truth.json, so detection recall and
false-alarm counts can be measured instead of eyeballed.

Run:  python data/generate_data.py
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).parent
SEED = 7
DAYS = 180
START = "2026-04-01"

# (day index, short name, human description). Effects are applied below.
PLANTED = [
    (60, "traffic_quality_shift", "Traffic +80% but conversion -40%: low-intent traffic floods in"),
    (95, "refund_surge", "Refunds jump 4x with no change in orders"),
    (120, "traffic_collapse", "Traffic falls 60%: acquisition channel outage"),
    (150, "ad_spend_spike", "Cost jumps 2.2x with revenue unchanged"),
    (172, "checkout_failure", "Conversion falls 55%: checkout bug, traffic unaffected"),
]


def build(seed: int = SEED) -> tuple[pd.DataFrame, list[dict]]:
    rng = np.random.default_rng(seed)
    dates = pd.date_range(START, periods=DAYS, freq="D")
    t = np.arange(DAYS)

    weekday = dates.dayofweek.to_numpy()
    weekly = np.where(weekday >= 5, 0.90, 1.0)  # mild weekend dip
    growth = 1 + 0.0008 * t  # slow organic growth

    traffic = 12000 * growth * weekly * rng.normal(1, 0.05, DAYS)
    conversion = 0.026 * rng.normal(1, 0.06, DAYS)
    aov = 1500 * rng.normal(1, 0.04, DAYS)  # average order value
    refund_rate = 0.025 * rng.normal(1, 0.15, DAYS)
    cost_base = 150_000 * growth * rng.normal(1, 0.05, DAYS)

    # --- planted anomalies (multipliers on the underlying drivers) ---
    traffic[60] *= 1.8
    conversion[60] *= 0.60
    refund_rate[95] *= 4.0
    traffic[120] *= 0.40
    cost_base[150] *= 2.2
    conversion[172] *= 0.45

    orders = traffic * conversion
    revenue = orders * aov
    refunds = orders * refund_rate

    df = pd.DataFrame(
        {
            "Date": dates,
            "Revenue": revenue.round(0),
            "Orders": orders.round(0),
            "Traffic": traffic.round(0),
            "Conversion Rate": conversion.round(5),
            "Cost": cost_base.round(0),
            "Refunds": refunds.round(0),
        }
    )

    truth = [
        {
            "date": dates[i].strftime("%Y-%m-%d"),
            "name": name,
            "description": desc,
        }
        for i, name, desc in PLANTED
    ]
    return df, truth


def main() -> None:
    df, truth = build()
    out = HERE / "business_metrics.xlsx"
    df.to_excel(out, index=False, sheet_name="Daily Metrics")
    (HERE / "ground_truth.json").write_text(json.dumps(truth, indent=2))
    print(f"wrote {out} ({len(df)} rows) and ground_truth.json ({len(truth)} planted anomalies)")


if __name__ == "__main__":
    main()
