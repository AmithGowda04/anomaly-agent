"""Turn findings for one day into business language.

The summary is deterministic: every number comes from the findings table, and the
'possible explanations' come from a small, explicit rulebook. An LLM may re-word it
(llm.polish_summary) but is never the source of numbers or causes."""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

SEV_RANK = {"critical": 2, "warning": 1}

# (required moves, explanation). "flat" means: no finding for that metric today.
HYPOTHESES: list[tuple[dict[str, str], str]] = [
    ({"traffic": "up", "conversion": "down"},
     "Traffic rose while conversion fell: the visitor mix may have changed (new campaign, low-intent or bot traffic)."),
    ({"revenue": "up", "conversion": "down"},
     "Revenue rose while conversion fell: growth may be coming from volume rather than buyer quality."),
    ({"traffic": "down", "orders": "down"},
     "Fewer visitors are producing fewer orders: check acquisition channels, campaigns and tracking."),
    ({"traffic": "flat", "conversion": "down"},
     "Traffic is normal but fewer visitors are buying: check checkout, payments and site errors."),
    ({"refunds": "up"},
     "Refunds are above normal: check recent product quality, delivery or pricing issues."),
    ({"cost": "up", "revenue": "flat"},
     "Spend rose without a matching revenue change: check campaign budgets, bids and billing errors."),
    ({"revenue": "down"},
     "Revenue fell: compare traffic, conversion and order value to find which driver moved."),
    # support-desk reports (keys are matched as substrings of the metric names)
    ({"opened": "up", "response": "up"},
     "Ticket volume and response time both rose: the team may be overloaded, or a release or outage is generating tickets."),
    ({"response": "up", "satisfaction": "down"},
     "Slower responses coincided with lower satisfaction: check staffing and queue routing."),
    ({"resolved": "down", "backlog": "up"},
     "Fewer tickets are being closed while the backlog grows: check agent availability and tooling."),
    ({"satisfaction": "down", "response": "flat"},
     "Satisfaction fell without slower responses: check recent product or policy changes and answer quality."),
    ({"escalation": "up"},
     "Escalations are above normal: check for a new issue type or a gap in first-line training."),
]


def fmt(metric: str, v: float) -> str:
    if v is None or pd.isna(v):
        return "n/a"
    name = metric.lower()
    if abs(v) <= 1.5 and ("rate" in name or "ratio" in name or "pct" in name or "share" in name):
        return f"{v:.2%}"
    if abs(v) >= 100:
        return f"{v:,.0f}"
    return f"{v:,.2f}"


def _label(metric: str) -> str:
    return metric.replace("_", " ")


@dataclass
class DaySummary:
    date: pd.Timestamp
    severity: str
    headline: str
    bullets: list[str] = field(default_factory=list)
    explanations: list[str] = field(default_factory=list)

    def text(self) -> str:
        parts = [self.headline, ""]
        parts += [f"- {b}" for b in self.bullets]
        if self.explanations:
            parts += ["", "Possible explanations (hypotheses, not confirmed):"]
            parts += [f"- {e}" for e in self.explanations]
        return "\n".join(parts)


def _pattern_legs(rule_key: str) -> list[tuple[str, str]]:
    """'pattern|traffic:up+conversion_rate:down|w28|...' -> [('traffic','up'),('conversion_rate','down')]"""
    body = rule_key.split("|")[1]
    return [tuple(part.split(":")) for part in body.split("+")]  # type: ignore[misc]


def _moves(day: pd.DataFrame) -> dict[str, str]:
    moves: dict[str, str] = {}
    for _, r in day.iterrows():
        if r["kind"] == "pattern":
            for metric, direction in _pattern_legs(r["rule_key"]):
                moves.setdefault(metric, direction)
        else:
            moves[r["metric"]] = r["direction"]
    return moves


def _match(required: dict[str, str], moves: dict[str, str], all_metrics: set[str]) -> bool:
    for key, want in required.items():
        present = [m for m in all_metrics if key in m]
        if not present:
            return False  # this report has no such metric; skip hypothesis
        got = [moves[m] for m in present if m in moves]
        if want == "flat":
            if got:
                return False
        elif want not in got:
            return False
    return True


def summarize_day(day: pd.DataFrame, all_metrics: list[str]) -> DaySummary:
    day = day.copy()
    date = day["date"].iloc[0]
    sev = max(day["severity"], key=lambda s: SEV_RANK[s])
    bullets: list[str] = []

    single = day[day["kind"] != "pattern"]
    for metric, grp in single.groupby("metric", sort=False):
        top = grp.sort_values("severity", key=lambda s: s.map(SEV_RANK), ascending=False).iloc[0]
        verb = "rose" if top["direction"] == "up" else "fell"
        if pd.notna(top["change_pct"]) and pd.notna(top["baseline"]):
            bullets.append(
                f"{_label(metric).capitalize()} {verb} {abs(top['change_pct']):.1f}% versus its recent "
                f"baseline ({fmt(metric, top['value'])} vs {fmt(metric, top['baseline'])})."
            )
        elif pd.notna(top["baseline"]):
            side = "below" if top["direction"] == "down" else "above"
            bullets.append(
                f"{_label(metric).capitalize()} is {fmt(metric, top['value'])}, {side} the limit of "
                f"{fmt(metric, top['baseline'])}."
            )
        else:
            bullets.append(f"{_label(metric).capitalize()} {verb}: {fmt(metric, top['value'])}.")

    for _, p in day[day["kind"] == "pattern"].drop_duplicates("rule_key").iterrows():
        legs = " while ".join(f"{_label(m)} {'rose' if d == 'up' else 'fell'}" for m, d in _pattern_legs(p["rule_key"]))
        bullets.append(f"Combined pattern on the same day: {legs}.")

    exps: list[str] = []
    mset = set(all_metrics)
    mv = _moves(day)
    for required, text in HYPOTHESES:
        if _match(required, mv, mset):
            exps.append(text)
        if len(exps) == 2:
            break

    label = {"critical": "Critical", "warning": "Warning"}[sev]
    headline = f"{label}: {len(bullets)} anomaly signal(s) on {pd.Timestamp(date).date()}"
    return DaySummary(date=pd.Timestamp(date), severity=sev, headline=headline, bullets=bullets, explanations=exps)
