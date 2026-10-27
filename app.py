"""Streamlit demo: upload a report, write rules in English, confirm the interpretation,
see anomalies, summaries and the alert email.

  streamlit run app.py
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from anomaly_agent import alerts, compiler, engine, llm, loader, pipeline
from anomaly_agent.rule_parser import SUPPORTED_HELP

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

ROOT = Path(__file__).parent
SAMPLES = {  # source label -> (data file, English rules file); every label starts with "Sample"
    "Sample: sales metrics (synthetic)": (ROOT / "data" / "business_metrics.xlsx", ROOT / "rules" / "sales_rules.txt"),
    "Sample: support desk (synthetic)": (ROOT / "data" / "support_metrics.xlsx", ROOT / "rules" / "support_rules.txt"),
}
MAX_UPLOAD_MB, MAX_ROWS = 5, 20_000

BLUE, ORANGE, GREY = "#2a78d6", "#eb6834", "#8a8984"  # categorical slots 1, 2 + neutral

st.set_page_config(page_title="Anomaly Agent", page_icon=None, layout="wide")
st.title("Anomaly Agent")
st.caption("Write monitoring rules in plain English. Review how they were interpreted. Get alerts on unusual moves.")

# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.header("Data")
    src = st.radio("Source", [*SAMPLES, "Upload Excel/CSV"], label_visibility="collapsed")
    upload = None
    if src.startswith("Upload"):
        upload = st.file_uploader("Excel or CSV", type=["xlsx", "xls", "csv"])
        st.warning("Don't upload confidential data to a shared demo. Files are processed in memory "
                   "and not stored by this app, but the host may log traffic.")
    st.header("Rule compiler")
    mode = st.selectbox(
        "Mode", ["auto", "templates", "llm"],
        help="auto: built-in sentence patterns first, Claude only for lines they can't parse. "
             "templates: no AI. llm: Claude for every line.",
    )
    api_key = st.text_input("Anthropic API key (optional)", type="password",
                            help="Used only in this session. Only column names and your rule sentences are sent, "
                                 "never spreadsheet values.")
    polish = st.checkbox("Also show an AI-written summary", disabled=not api_key)
    if mode != "templates" and not api_key:
        st.info("No API key: sentences outside the supported patterns will be reported as errors.")

# ---------------------------------------------------------------- 1. data
st.subheader("1. Data")
try:
    if upload is not None:
        if upload.size > MAX_UPLOAD_MB * 1024 * 1024:
            st.error(f"File is over {MAX_UPLOAD_MB} MB."); st.stop()
        raw = loader.read_any(upload, name=upload.name)
    elif src.startswith("Sample"):
        raw = loader.read_any(SAMPLES[src][0], name=SAMPLES[src][0].name)
    else:
        st.info("Upload a file to continue."); st.stop()
except Exception as e:  # unreadable file
    st.error(f"Could not read the file: {e}"); st.stop()
if len(raw) > MAX_ROWS:
    st.error(f"Too many rows ({len(raw):,}); this demo handles up to {MAX_ROWS:,}."); st.stop()

df, dq = loader.prepare(raw)
if df.empty or dq.has_errors:
    for line in dq.lines():
        st.error(line)
    st.stop()
c1, c2 = st.columns([3, 2])
with c1:
    st.dataframe(df.reset_index().head(8), width="stretch", hide_index=True)
    st.caption(f"{len(df):,} rows, {df.index.min().date()} to {df.index.max().date()}. Metric names: "
               + ", ".join(f"`{c}`" for c in df.columns))
with c2:
    st.markdown("**Data-quality checks**")
    if dq.issues:
        for i in dq.issues:
            (st.warning if i.level == "warning" else st.info)(i.message)
    else:
        st.success("No problems found.")

# ---------------------------------------------------------------- 2. rules
st.subheader("2. Rules, in plain English")
default_rules = SAMPLES[src][1].read_text() if src.startswith("Sample") else \
    "Alert if " + df.columns[0].replace("_", " ") + " drops more than 30% below its 28-day average.\n"
text = st.text_area("One rule per line. Start with Alert or Warn.", value=default_rules, height=250)
with st.expander("Supported sentence patterns"):
    st.code(SUPPORTED_HELP)

if st.button("Compile rules", type="primary"):
    with st.spinner("Compiling..."):
        res = compiler.compile_rules(text, list(df.columns), mode=mode, api_key=api_key or None)
    st.session_state["compiled"] = (res, compiler.source_hash(text, list(df.columns)))
    st.session_state["confirmed"] = False

compiled = st.session_state.get("compiled")
if not compiled:
    st.stop()
res, h = compiled
if h != compiler.source_hash(text, list(df.columns)):
    st.warning("Rules or data changed since compiling. Compile again.")
    st.stop()

# ---------------------------------------------------------------- 3. readback + confirm
st.subheader("3. Check the interpretation")
if res.used_llm:
    st.info("Some lines were interpreted by Claude. Read them carefully before confirming.")
for ln, line, msg in res.errors:
    st.error(f"Line {ln} was not compiled: `{line}`\n\n{msg}")
if not res.rules:
    st.stop()

found_all = engine.evaluate(df, res.rules)
scored = max(len(df) - max(r.window for r in res.rules), 1)
rates = {r.key: (n, rate) for r, n, rate in engine.firing_rates(found_all, res.rules, scored)}
table = pd.DataFrame([{
    "Your words": rb["your_words"],
    "Interpreted as": rb["interpreted_as"],
    "By": "Claude" if rb["source"] == "llm" else "pattern",
    "Would have fired (history)": f"{rates[r.key][0]} days ({rates[r.key][1]:.1%})"
                                  + ("  too sensitive?" if rates[r.key][1] > 0.05 else ""),
} for rb, r in zip(res.readback(), res.rules)])
st.dataframe(table, width="stretch", hide_index=True)
st.caption("The 'Interpreted as' column is generated from the validated rule, not from model text. "
           "A rule that would have fired on more than 5% of past days is usually too sensitive.")
confirmed = st.checkbox("This is what I meant. Use these rules.", key="confirmed")
if not confirmed:
    st.stop()

# ---------------------------------------------------------------- 4. run
st.subheader("4. Results")
a, b = st.columns(2)
scope = a.radio("Evaluate", ["Latest day", "Whole history (replay)"], horizontal=True)
cooldown = b.slider("Cooldown (days) before repeating the same alert", 1, 14, 3)

with tempfile.TemporaryDirectory() as tmp:
    out = pipeline.run(df, res.rules, report_name="Demo report", replay=scope.startswith("Whole"),
                       out_dir=Path(tmp), cooldown_days=cooldown, use_log=False, write_emails=False)

m1, m2, m3 = st.columns(3)
m1.metric("Alert days", len(out.summaries))
m2.metric("Signals", len(out.alerted))
m3.metric("Suppressed by cooldown", len(out.suppressed))

if not out.summaries:
    st.success(f"No anomalies on {df.index.max().date()} under these rules." if not scope.startswith("Whole")
               else "No anomalies found in the history under these rules.")
    st.stop()

flagged_metrics = sorted({m for v in out.alerted["metric"] for m in v.split("+")})
metric = st.selectbox("Chart a metric", flagged_metrics + [c for c in df.columns if c not in flagged_metrics])
rule_windows = [r.window for r in res.rules if metric in r.metrics]
window = max(rule_windows) if rule_windows else 28
sc = engine.score_series(df[metric], window, "mad").reset_index()
sc["flag"] = sc["date"].isin(
    out.alerted[out.alerted["metric"].apply(lambda v: metric in v.split("+"))]["date"])
long = pd.concat([
    sc.assign(series="Value")[["date", "value", "series"]],
    sc.assign(series=f"{window}-day median baseline").rename(columns={"baseline": "value_b"})
      .assign(value=lambda d: d["value_b"])[["date", "value", "series"]],
])
line = alt.Chart(long.dropna()).mark_line(strokeWidth=2).encode(
    x=alt.X("date:T", title=None), y=alt.Y("value:Q", title=metric.replace("_", " "), scale=alt.Scale(zero=False)),
    color=alt.Color("series:N", scale=alt.Scale(domain=["Value", f"{window}-day median baseline"], range=[BLUE, GREY]),
                    legend=alt.Legend(orient="top", title=None)),
    strokeDash=alt.StrokeDash("series:N", scale=alt.Scale(domain=["Value", f"{window}-day median baseline"],
                                                           range=[[1, 0], [5, 4]]), legend=None),
)
pts = alt.Chart(sc[sc["flag"]]).mark_point(size=90, filled=True, color=ORANGE, opacity=1, stroke="white", strokeWidth=2).encode(
    x="date:T", y="value:Q", tooltip=[alt.Tooltip("date:T"), alt.Tooltip("value:Q", format=",.2f"),
                                      alt.Tooltip("baseline:Q", format=",.2f", title="baseline"),
                                      alt.Tooltip("change_pct:Q", format="+.1f", title="% vs baseline")])
st.altair_chart((line + pts).properties(height=300), width="stretch")
st.caption("Orange points are days an alert fired for this metric. Dashed grey is the rolling median baseline.")
with st.expander("Table view of the chart"):
    st.dataframe(sc.drop(columns=["flag"]), width="stretch", hide_index=True)

st.markdown("**Alert summaries**")
for s in out.summaries[-10:][::-1]:
    with st.expander(s.headline, expanded=(s is out.summaries[-1])):
        st.markdown("\n".join(f"- {x}" for x in s.bullets))
        if s.explanations:
            st.markdown("*Possible explanations (hypotheses to check, not confirmed causes):*")
            st.markdown("\n".join(f"- {x}" for x in s.explanations))
        if polish and api_key:
            try:
                st.markdown("**AI-written version:** " + llm.polish_summary(s.text(), api_key=api_key))
            except Exception as e:
                st.caption(f"AI summary unavailable ({type(e).__name__}); showing the standard summary.")
        st.markdown("**Email that would be sent**")
        msg = alerts.build_email(s, "Demo report")
        st.code(f"Subject: {msg['Subject']}\n\n{msg.get_content()}", language="text")
if len(out.summaries) > 10:
    st.caption(f"Showing the 10 most recent of {len(out.summaries)} alert days.")
