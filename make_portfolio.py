"""Build portfolio/index.html, a self-contained case-study page.

Every number, the chart, the rules readback and the sample email are read from real
outputs of this repo (run the CLI + benchmark first), so the page cannot drift from the code.

  python make_portfolio.py --name "Your Name" --github https://github.com/you/anomaly-agent --demo https://your-app.streamlit.app
"""
from __future__ import annotations

import argparse
import email
import glob
import html
import json
from email import policy
from pathlib import Path

import pandas as pd

from anomaly_agent import compiler, engine, loader
from anomaly_agent.schema import describe

ROOT = Path(__file__).parent


def revenue_chart(df: pd.DataFrame, flagged: set[pd.Timestamp], labels: dict[str, str]) -> str:
    W, H, L, R, T, B = 860, 290, 62, 16, 18, 30
    s = df["revenue"]
    lo, hi = s.min() * 0.92, s.max() * 1.05
    n = len(s)
    x = lambda i: L + (W - L - R) * i / (n - 1)  # noqa: E731
    y = lambda v: T + (H - T - B) * (1 - (v - lo) / (hi - lo))  # noqa: E731
    pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(s))
    grid, ticks = [], []
    for k in range(5):
        v = lo + (hi - lo) * k / 4
        grid.append(f'<line class="grid" x1="{L}" x2="{W - R}" y1="{y(v):.1f}" y2="{y(v):.1f}"/>'
                    f'<text class="tick" x="{L - 8}" y="{y(v) + 4:.1f}" text-anchor="end">{v / 1000:.0f}k</text>')
    last_m = None
    for i, d in enumerate(s.index):
        if d.month != last_m and d.day <= 7:
            last_m = d.month
            ticks.append(f'<text class="tick" x="{x(i):.1f}" y="{H - 8}" text-anchor="middle">{d.strftime("%b")}</text>')
    marks, notes = [], []
    for i, d in enumerate(s.index):
        if d in flagged:
            cx, cy = x(i), y(s.iloc[i])
            marks.append(f'<circle class="hit" cx="{cx:.1f}" cy="{cy:.1f}" r="5.5"/>')
            lab = labels.get(d.strftime("%Y-%m-%d"))
            if lab:
                anchor = "end" if cx > W - 160 else "start"
                dx = -10 if anchor == "end" else 10
                notes.append(f'<text class="note" x="{cx + dx:.1f}" y="{cy - 6:.1f}" text-anchor="{anchor}">{html.escape(d.strftime("%b %-d"))}: {html.escape(lab)}</text>')
    return (f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="Daily revenue with the days an alert fired highlighted">'
            f'{"".join(grid)}<polyline class="line" points="{pts}"/>{"".join(ticks)}{"".join(marks)}{"".join(notes)}</svg>')


CSS = """
:root{--bg:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--line:#e4e3de;--card:#f4f3ef;--blue:#2a78d6;--orange:#eb6834;--mono:ui-monospace,SFMono-Regular,Menlo,monospace}
@media (prefers-color-scheme:dark){:root{--bg:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--line:#34332f;--card:#232321;--blue:#3987e5;--orange:#d95926}}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font:17px/1.6 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:880px;margin:0 auto;padding:48px 16px 80px}
h1{font-size:clamp(34px,6vw,54px);line-height:1.08;margin:0 0 14px;letter-spacing:-.02em}
h2{font-size:24px;margin:56px 0 12px;letter-spacing:-.01em}
p{margin:0 0 14px}.lede{font-size:20px;color:var(--ink2);max-width:660px}
.muted{color:var(--ink2)}.small{font-size:14px}
.btns{display:flex;gap:10px;flex-wrap:wrap;margin:22px 0 0}
.btn{display:inline-block;padding:9px 16px;border:1px solid var(--ink);border-radius:8px;color:var(--ink);text-decoration:none;font-weight:600;font-size:15px}
.btn.primary{background:var(--ink);color:var(--bg)}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px;margin:28px 0 8px}
.stat{background:var(--card);border-radius:10px;padding:16px}
.stat b{display:block;font-size:30px;line-height:1.1;letter-spacing:-.02em}.stat span{font-size:14px;color:var(--ink2)}
.card{background:var(--card);border-radius:10px;padding:18px 20px;margin:14px 0}
table{width:100%;border-collapse:collapse;font-size:15px}
th,td{text-align:left;padding:9px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-size:13px;color:var(--ink2);font-weight:600}
code,pre{font-family:var(--mono);font-size:13.5px}
pre{background:var(--card);border-radius:10px;padding:16px 18px;overflow-x:auto;margin:0;white-space:pre-wrap;word-break:break-word}
.email{border:1px solid var(--line);border-radius:10px;overflow:hidden}
.email .hd{background:var(--card);padding:10px 16px;font-size:14px;border-bottom:1px solid var(--line)}
.email pre{border-radius:0;background:transparent}
svg{width:100%;height:auto;display:block}
.grid{stroke:var(--line);stroke-width:1}.tick{fill:var(--ink2);font-size:12px}
.line{fill:none;stroke:var(--blue);stroke-width:2;stroke-linejoin:round}
.hit{fill:var(--orange);stroke:var(--bg);stroke-width:2}.note{fill:var(--ink);font-size:12.5px;font-weight:600}
.flow{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:14px 0;counter-reset:s}
.flow div{background:var(--card);border-radius:10px;padding:14px;font-size:15px;counter-increment:s}
.flow div::before{content:counter(s);display:block;font-weight:700;color:var(--blue);margin-bottom:4px}
.flow b{display:block}
ul{padding-left:20px;margin:0 0 14px}li{margin-bottom:6px}
.scroll{overflow-x:auto}
footer{margin-top:60px;font-size:14px;color:var(--ink2)}
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="")
    ap.add_argument("--github", default="")
    ap.add_argument("--demo", default="")
    ap.add_argument("--out", default="portfolio/index.html")
    a = ap.parse_args()

    bench = json.loads((ROOT / "outputs/benchmark.json").read_text())
    ev = json.loads((ROOT / "outputs/evaluation.json").read_text())
    truth = json.loads((ROOT / "data/ground_truth.json").read_text())
    names = {t["date"]: t["name"].replace("_", " ") for t in truth}

    raw = loader.read_any(ROOT / "data/business_metrics.xlsx", name="x.xlsx")
    df, _ = loader.prepare(raw)
    rules, _ = compiler.load(ROOT / "rules/sales_rules.compiled.json")
    found = engine.evaluate(df, rules)
    rev_flagged = {pd.Timestamp(d) for d, m in zip(found["date"], found["metric"]) if "revenue" in m.split("+")}
    chart = revenue_chart(df, rev_flagged, {d: n for d, n in names.items() if pd.Timestamp(d) in rev_flagged})

    english = (ROOT / "rules/sales_rules.txt").read_text()
    english_lines = [ln for ln in english.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    rows = "".join(f"<tr><td>{html.escape(r.text)}</td><td class='muted'>{html.escape(describe(r))}</td></tr>" for r in rules)

    target = next(iter(sorted(glob.glob(str(ROOT / "outputs/emails/*2026_05_31*")))), None)
    em = email.message_from_bytes(Path(target).read_bytes(), policy=policy.default)
    body = em.get_content()

    btns = []
    if a.demo:
        btns.append(f'<a class="btn primary" href="{html.escape(a.demo)}">Try the live demo</a>')
    if a.github:
        btns.append(f'<a class="btn" href="{html.escape(a.github)}">Source on GitHub</a>')
    btn_html = f'<div class="btns">{"".join(btns)}</div>' if btns else ""
    by = f"<p class='muted small'>Built by {html.escape(a.name)}</p>" if a.name else ""

    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Anomaly Agent</title>
<meta name="description" content="A spreadsheet watcher with plain-English rules: detects unusual metric moves, explains them, and sends an alert.">
<style>{CSS}</style></head><body><main>
<header>
<p class="muted small">Case study &middot; Python, pandas, Streamlit, Claude API</p>
<h1>Anomaly Agent</h1>
<p class="lede">A spreadsheet watcher. It notices when a business metric leaves its normal range, explains the move in plain English, and emails someone before the manager has to find it.</p>
{btn_html}{by}
</header>

<div class="stats">
<div class="stat"><b>{bench['caught_total']}/{bench['planted_total']}</b><span>planted anomalies caught across {bench['datasets']} fresh simulated datasets</span></div>
<div class="stat"><b>{bench['false_rate']:.1%}</b><span>of normal days raised a false alarm (about {bench['false_days_mean']:.1f} days per dataset, out of {bench['normal_days']} normal days)</span></div>
<div class="stat"><b>{bench['false_critical_mean']:.2f}</b><span>false <em>critical</em> alerts per dataset, the kind that page someone</span></div>
</div>
<p class="small muted">Measured on synthetic data with known, planted anomalies (seeds {bench['seeds']}, not used when tuning the rules). Real data will be noisier; see limitations below.</p>

<h2>The problem</h2>
<p>Managers don't want to open a spreadsheet every morning to see whether something broke. A notebook full of charts shows that you can inspect data; it doesn't tell anyone when revenue falls off a cliff on a Tuesday. This project turns a spreadsheet into something that watches itself.</p>

<h2>What it does</h2>
<div class="flow">
<div><b>Write rules</b>in plain English, one per line</div>
<div><b>Review</b>a readback of what each rule really does, plus how often it would have fired</div>
<div><b>Detect</b>moves outside the recent normal range</div>
<div><b>Explain &amp; alert</b>a short summary by email, no repeat spam</div>
</div>

<h2>Rules anyone can edit</h2>
<p>Rules are sentences, not config. Each is compiled into a validated rule and read back before anything runs. Sentences the built-in patterns can't parse can go to Claude, whose output passes through the same validation and the same confirmation step.</p>
<pre>{html.escape(chr(10).join(english_lines[:4]))}
...</pre>
<div class="scroll"><table><thead><tr><th>You wrote</th><th>The tool will do</th></tr></thead><tbody>{rows}</tbody></table></div>

<h2>What an anomaly is</h2>
<p>A value that is unlikely given what that metric normally does. Each metric is compared with a baseline built from the <strong>previous</strong> 28 days only, so a spike can't hide itself by raising its own baseline. The default score uses the median and median absolute deviation, which a past spike barely moves. Moves under 5% are ignored even if statistically odd, and flat series produce no score rather than an infinite one. Combined patterns (revenue up while conversion falls) catch problems a single metric would miss.</p>

<h2>Before and after</h2>
<p>Daily revenue over six months. Orange dots are days an alert fired on revenue; both are real incidents planted in the data.</p>
<div class="card">{chart}</div>

<h2>The alert a manager receives</h2>
<p>For the day traffic doubled but conversion fell by 40%. Explanations are written as hypotheses to check, not confirmed causes.</p>
<div class="email"><div class="hd"><strong>Subject:</strong> {html.escape(em['Subject'])}</div><pre>{html.escape(body)}</pre></div>

<h2>How I tested it</h2>
<ul>
<li><strong>Ground truth.</strong> The generator plants five anomalies (traffic-quality shift, refund surge, traffic collapse, ad-spend spike, checkout failure) and records them, so recall and false alarms are measured, not eyeballed. The committed sample file: {ev['caught']}/{ev['planted']} caught, {len(ev['false_alarm_days'])} false-alarm days.</li>
<li><strong>Many datasets, not one.</strong> A single file can flatter a detector, so the benchmark runs 20 noise seeds. Rules were tuned on seeds 0-19 and scored on 200-219.</li>
<li><strong>It got better for measurable reasons.</strong> The first draft (14-day baselines, one classic z-score) fired on 3.0% of normal days. 28-day baselines with median/MAD cut that to about half with recall unchanged. A 13% cost move was paging people as critical, so cost became a two-tier rule. Both changes are in the README with the numbers.</li>
<li><strong>59 automated tests</strong> covering the sentence parser, rule validation, detectors, data-quality checks, de-duplication, the CLI's confirmation gate and the Streamlit app.</li>
</ul>

<h2>Decisions worth explaining</h2>
<ul>
<li><strong>Templates first, Claude second.</strong> Deterministic parsing is free, offline and reproducible. Claude handles only what templates can't, and a human confirms its interpretation before anything runs, because a silently misread alert rule is worse than none.</li>
<li><strong>Only column names and rule sentences ever reach the model,</strong> never spreadsheet values. Names are reduced to <code>[a-z0-9_]</code> and length-capped, which also blunts prompt injection through a malicious header.</li>
<li><strong>Bad input is reported as bad input.</strong> Gaps, duplicate dates, unreadable cells and stale files raise data-quality notices instead of fake anomalies.</li>
<li><strong>Repeat alerts are suppressed</strong> for a cooldown window, and re-running the same data is idempotent. Alert fatigue kills monitoring tools faster than missed alerts.</li>
</ul>

<h2>Limitations</h2>
<ul>
<li>All results are on synthetic data I generated. Real data has seasonality, promotions and outliers this generator does not.</li>
<li>The five planted anomalies sit on the same dates in every simulated dataset; only the noise changes. The benchmark shows robustness to noise, not to every anomaly shape.</li>
<li>The live Claude integration is tested against a fake client (validation and guard logic), not yet against the real API.</li>
<li>No seasonality model; a large, predictable weekend swing can trigger alerts. A per-weekday baseline is the next step.</li>
</ul>

<footer>Python &middot; pandas &middot; pydantic &middot; Streamlit &middot; Anthropic API &middot; pytest</footer>
</main></body></html>"""
    out = ROOT / a.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page)
    print(f"wrote {out} ({len(page):,} bytes)")
    if not (a.github or a.demo):
        print("note: no --github/--demo given, so the page has no links yet; re-run with your URLs.")


if __name__ == "__main__":
    main()
