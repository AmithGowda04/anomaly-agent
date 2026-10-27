"""Command line interface.

  python -m anomaly_agent compile  --data data/business_metrics.xlsx --rules rules/sales_rules.txt
  python -m anomaly_agent explain  --rules rules/sales_rules.compiled.json
  python -m anomaly_agent run      --data data/business_metrics.xlsx --rules rules/sales_rules.compiled.json
  python -m anomaly_agent evaluate --data data/business_metrics.xlsx --rules rules/sales_rules.compiled.json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from . import compiler, loader
from .engine import evaluate as engine_evaluate, firing_rates
from .pipeline import run as pipeline_run
from .schema import describe


def _load_data(args):
    raw = loader.read_any(args.data, name=str(args.data))
    df, dq = loader.prepare(raw, date_column=getattr(args, "date_column", None))
    if getattr(args, "max_age_days", None) is not None:
        loader.check_freshness(dq, df, date.today(), args.max_age_days)
    return df, dq


def _print_dq(dq) -> bool:
    for line in dq.lines():
        print("  ", line)
    return dq.has_errors


def cmd_compile(args) -> int:
    df, dq = _load_data(args)
    if _print_dq(dq) and df.empty:
        print("Cannot compile: the data has errors (see above).")
        return 2
    text = Path(args.rules).read_text()
    res = compiler.compile_rules(text, list(df.columns), mode=args.mode)
    print(f"Compiled {len(res.rules)} rule(s)" + (" (LLM used for some lines)" if res.used_llm else "") + ":\n")
    for i, rb in enumerate(res.readback(), 1):
        print(f"{i:>2}. [{rb['source']}] {rb['your_words']}\n      -> {rb['interpreted_as']}")
    for ln, line, msg in res.errors:
        print(f"\nLine {ln} NOT compiled: {line}\n  {msg}")
    if res.rules:
        scored = max(len(df) - max(r.window for r in res.rules), 1)
        print(f"\nBacktest on this file ({scored} scored days): how often each rule would have fired")
        for r, n, rate in firing_rates(engine_evaluate(df, res.rules), res.rules, scored):
            note = "  <- fires often; consider loosening" if rate > 0.05 else ""
            print(f"  {n:>3} days ({rate:5.1%})  {r.text}{note}")
    out = Path(args.rules).with_suffix(".compiled.json")
    if res.errors and not args.allow_partial:
        print("\nFix the lines above (or pass --allow-partial). Nothing saved.")
        return 1
    confirmed = bool(args.yes)
    if not confirmed and sys.stdin.isatty():
        confirmed = input("\nIs this what you meant? Type 'yes' to confirm: ").strip().lower() == "yes"
    compiler.save(res, out, text, list(df.columns), confirmed=confirmed)
    print(f"\nSaved {out} (confirmed={confirmed}).")
    if not confirmed:
        print("Not confirmed: 'run' will refuse to use it until you re-run with --yes after reading the readback.")
    return 0


def cmd_explain(args) -> int:
    rules, meta = compiler.load(Path(args.rules))
    print(f"confirmed={meta['confirmed']}  rules={len(rules)}\n")
    for i, r in enumerate(rules, 1):
        print(f"{i:>2}. [{r.source}] {r.text}\n      -> {describe(r)}")
    return 0


def _load_confirmed(args, df):
    rules, meta = compiler.load(Path(args.rules))
    if not meta["confirmed"]:
        print("These compiled rules have not been confirmed. Run 'compile ... --yes' after reading the readback.")
        return None
    src = Path(args.rules).with_suffix("").with_suffix(".txt")
    if src.exists() and compiler.source_hash(src.read_text(), list(df.columns)) != meta["source_hash"]:
        print(f"{src.name} changed (or the columns changed) since it was compiled. Re-run 'compile'.")
        return None
    return rules


def cmd_run(args) -> int:
    df, dq = _load_data(args)
    if _print_dq(dq) and df.empty:
        return 2
    rules = _load_confirmed(args, df)
    if rules is None:
        return 1
    res = pipeline_run(df, rules, report_name=args.name, replay=args.replay, out_dir=Path(args.out),
                       cooldown_days=args.cooldown, send=args.send, polish=args.polish)
    when = "all days (replay)" if args.replay else str(df.index.max().date())
    print(f"Evaluated {when}: {len(res.alerted)} alert row(s), {len(res.suppressed)} suppressed by cooldown.")
    for s in res.summaries:
        print("\n" + s.text())
    for d in res.deliveries:
        print("  ", d)
    if not res.summaries:
        print("No anomalies to report.")
    return 0


def cmd_evaluate(args) -> int:
    """Measure detection against data/ground_truth.json (planted anomalies)."""
    df, dq = _load_data(args)
    rules = _load_confirmed(args, df)
    if rules is None:
        return 1
    truth = json.loads(Path(args.truth).read_text())
    planted = {t["date"] for t in truth}
    found = engine_evaluate(df, rules)
    flagged = {d.strftime("%Y-%m-%d") for d in found["date"]} if not found.empty else set()
    hit = planted & flagged
    false = sorted(flagged - planted)
    scored_days = len(df) - max(r.window for r in rules)
    print(f"Planted anomalies: {len(planted)}  caught: {len(hit)}  missed: {sorted(planted - flagged)}")
    print(f"Flagged days not planted (false alarms): {len(false)} of {scored_days - len(planted)} normal days -> {false}")
    result = {
        "planted": len(planted), "caught": len(hit), "missed": sorted(planted - flagged),
        "false_alarm_days": false, "normal_days": scored_days - len(planted),
        "caught_dates": sorted(hit),
    }
    out = Path(args.out) / "evaluation.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2))
    return 0


def main(argv=None) -> int:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass
    p = argparse.ArgumentParser(prog="anomaly_agent")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp, rules_help):
        sp.add_argument("--data", required=True)
        sp.add_argument("--rules", required=True, help=rules_help)
        sp.add_argument("--date-column", default=None)

    c = sub.add_parser("compile", help="plain-English rules -> validated rules + readback")
    common(c, "text file with one English rule per line")
    c.add_argument("--mode", choices=["auto", "llm", "templates"], default="auto")
    c.add_argument("--yes", action="store_true", help="confirm without prompting (after reading the readback)")
    c.add_argument("--allow-partial", action="store_true")
    c.set_defaults(fn=cmd_compile)

    e = sub.add_parser("explain", help="show what a compiled rule file will do")
    e.add_argument("--rules", required=True)
    e.set_defaults(fn=cmd_explain)

    r = sub.add_parser("run", help="detect, summarise, alert")
    common(r, "compiled rules .json")
    r.add_argument("--name", default="Sales metrics")
    r.add_argument("--replay", action="store_true", help="evaluate every day in order (demo / backtest)")
    r.add_argument("--cooldown", type=int, default=3)
    r.add_argument("--send", action="store_true", help="really send via SMTP (default: write .eml files)")
    r.add_argument("--polish", action="store_true", help="LLM re-wording of the summary (needs API key)")
    r.add_argument("--max-age-days", type=int, default=None)
    r.add_argument("--out", default="outputs")
    r.set_defaults(fn=cmd_run)

    v = sub.add_parser("evaluate", help="score detection against planted anomalies")
    common(v, "compiled rules .json")
    v.add_argument("--truth", default="data/ground_truth.json")
    v.add_argument("--out", default="outputs")
    v.set_defaults(fn=cmd_evaluate)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
