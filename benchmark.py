"""Benchmark a rule file across many simulated datasets (different noise seeds).

One dataset can flatter a detector. This reports recall on the planted anomalies and
the false-alarm rate on normal days across many seeds.

  python benchmark.py rules/sales_rules.txt --seeds 100 120
"""
from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "data"))

from generate_data import build as build_sales  # noqa: E402
from generate_support_data import build as build_support  # noqa: E402

from anomaly_agent import compiler, engine, loader  # noqa: E402

BUILDERS = {"sales": build_sales, "support": build_support}


def score_seed(rules_text: str, seed: int, dataset: str = "sales") -> dict:
    raw, truth = BUILDERS[dataset](seed)
    df, _ = loader.prepare(raw)
    res = compiler.compile_rules(rules_text, list(df.columns), mode="templates")
    if res.errors:
        raise SystemExit(f"rules failed to compile: {res.errors}")
    found = engine.evaluate(df, res.rules)
    flagged = {d.strftime("%Y-%m-%d") for d in found["date"]} if not found.empty else set()
    planted = {t["date"] for t in truth}
    warmup = max(r.window for r in res.rules)
    crit = (
        {d.strftime("%Y-%m-%d") for d in found.loc[found["severity"] == "critical", "date"]}
        if not found.empty else set()
    )
    return {
        "false_critical_days": len(crit - planted),
        "caught": len(planted & flagged),
        "planted": len(planted),
        "false_days": len(flagged - planted),
        "normal_days": len(df) - warmup - len(planted),
        "missed": sorted(planted - flagged),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("rules")
    ap.add_argument("--seeds", nargs=2, type=int, default=[0, 20], metavar=("START", "END"))
    ap.add_argument("--json", default=None, help="write the summary numbers to this path")
    ap.add_argument("--dataset", choices=sorted(BUILDERS), default="sales",
                    help="which synthetic report the rules are scored against")
    a = ap.parse_args()
    text = Path(a.rules).read_text()
    rows = [score_seed(text, s, a.dataset) for s in range(a.seeds[0], a.seeds[1])]
    caught = sum(r["caught"] for r in rows)
    planted = sum(r["planted"] for r in rows)
    fdays = [r["false_days"] for r in rows]
    normal = rows[0]["normal_days"]
    missed: dict[str, int] = {}
    for r in rows:
        for m in r["missed"]:
            missed[m] = missed.get(m, 0) + 1
    print(f"{Path(a.rules).name}  [{a.dataset}]  seeds {a.seeds[0]}..{a.seeds[1] - 1}  ({len(rows)} datasets)")
    print(f"  recall: {caught}/{planted} planted anomalies caught ({caught / planted:.0%})")
    print(f"  false-alarm days per dataset: mean {statistics.mean(fdays):.1f}, "
          f"median {statistics.median(fdays):.0f}, max {max(fdays)}  (of {normal} normal days -> "
          f"{statistics.mean(fdays) / normal:.1%} of normal days)")
    fcrit = [r["false_critical_days"] for r in rows]
    print(f"  of which CRITICAL (would page someone): mean {statistics.mean(fcrit):.2f} per dataset")
    if a.json:
        import json
        Path(a.json).parent.mkdir(parents=True, exist_ok=True)
        Path(a.json).write_text(json.dumps({
            "rules_file": Path(a.rules).name, "seeds": f"{a.seeds[0]}-{a.seeds[1] - 1}", "datasets": len(rows),
            "planted_total": planted, "caught_total": caught, "recall": caught / planted,
            "false_days_mean": statistics.mean(fdays), "false_days_max": max(fdays),
            "normal_days": normal, "false_rate": statistics.mean(fdays) / normal,
            "false_critical_mean": statistics.mean(fcrit),
        }, indent=2))
    if missed:
        print(f"  most-missed dates: {dict(sorted(missed.items(), key=lambda kv: -kv[1])[:3])}")


if __name__ == "__main__":
    main()
