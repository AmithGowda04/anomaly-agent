"""Deterministic template parser: plain-English lines -> validated Rule objects.

Supported sentences (metric names are matched against the real column names):

  Alert if revenue drops more than 20% below its 14-day average.
  Alert if cost rises more than 3 standard deviations.
  Warn if refunds are unusually high.
  Warn if conversion rate is below 1%.
  Alert if revenue rises and conversion rate falls.
  Warn if orders are unusually low over the last 28 days.   (optional baseline window)

Start with 'Alert' (critical) or 'Warn' (warning). Lines starting with # are comments.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from pydantic import ValidationError

from .schema import Leg, Rule, normalize_name

UP = r"rises?|increases?|goes up|spikes?|jumps?|climbs?|grows?|surges?|up"
DOWN = r"drops?|falls?|decreases?|declines?|goes down|dips?|collapses?|plunges?|down"
EITHER = r"changes?|moves?|deviates?|swings?"

SUPPORTED_HELP = """Supported sentence patterns:
  Alert|Warn if <metric> drops|rises more than <N>% below|above its <W>-day average|median
  Alert|Warn if <metric> drops|rises more than <N> standard deviations
  Alert|Warn if <metric> is unusually high|low
  Alert|Warn if <metric> is below|above <value>[%]
  Alert|Warn if <metric> rises|falls and <metric> rises|falls
  (deviation / pattern / static sentences may end with: over the last <N> days|weeks)"""


@dataclass
class ParseResult:
    rules: list[Rule]
    errors: list[tuple[int, str, str]]  # (line number, line text, message)


def _dir(word: str) -> str:
    w = word.strip().lower()
    if re.fullmatch(UP, w):
        return "up"
    if re.fullmatch(DOWN, w):
        return "down"
    return "either"


def _metric_alt(columns: list[str]) -> str:
    # longest first so 'conversion_rate' wins over 'conversion'
    names = sorted(columns, key=len, reverse=True)
    return "|".join(re.escape(n).replace("_", r"[ _]") for n in names)


def _lead(text: str) -> tuple[str, str] | None:
    m = re.match(
        r"^(alert|critical|warn|warning|notify)(?: me)?\s*(?:if|when|whenever|:)?\s+(.*)$",
        text, flags=re.I,
    )
    if not m:
        return None
    word = m.group(1).lower()
    return ("critical" if word in ("alert", "critical") else "warning"), m.group(2)


def parse_line(line: str, columns: list[str]) -> Rule:
    text = line.strip().rstrip(".").strip()
    lead = _lead(text)
    if lead is None:
        raise ValueError("Line must start with 'Alert if' or 'Warn if'.\n" + SUPPORTED_HELP)
    severity, cond = lead
    cond = cond.strip()
    M = f"(?P<m>{_metric_alt(columns)})"
    M1 = f"(?P<m1>{_metric_alt(columns)})"
    M2 = f"(?P<m2>{_metric_alt(columns)})"
    flags = re.I

    # optional baseline-window phrase: "... over the last 28 days" / "... in the past 4 weeks"
    window_override: int | None = None
    mw = re.search(r"\s+(?:over|in|within|during) the (?:last|past) (\d+)[- ]?(days?|weeks?)$", cond, flags)
    if mw:
        window_override = int(mw.group(1)) * (7 if mw.group(2).lower().startswith("week") else 1)
        cond = cond[: mw.start()].strip()

    def finish(**kw) -> Rule:
        if window_override is not None and "window" not in kw:
            kw["window"] = window_override
        return Rule(severity=severity, text=line.strip(), source="template", **kw)

    # pattern: A rises and B falls
    m = re.fullmatch(rf"{M1} (?P<d1>{UP}|{DOWN}) and {M2} (?P<d2>{UP}|{DOWN})", cond, flags)
    if m:
        return finish(kind="pattern", legs=[
            Leg(metric=normalize_name(m["m1"]), direction=_dir(m["d1"])),
            Leg(metric=normalize_name(m["m2"]), direction=_dir(m["d2"])),
        ])

    # pct vs baseline
    m = re.fullmatch(
        rf"{M} (?P<d>{UP}|{DOWN}|{EITHER}) (?:by )?(?:more than|over|at least|above|beyond) "
        rf"(?P<n>\d+(?:\.\d+)?) ?% (?:below|above|from|versus|vs\.?|compared (?:to|with)|away from) "
        rf"(?:its|the|their) (?:(?P<w>\d+)[- ]?(?P<u>days?|weeks?)[- ]?)?(?:rolling |moving )?"
        rf"(?P<s>average|mean|median|baseline)",
        cond, flags,
    )
    if m:
        window = 14
        if m["w"]:
            window = int(m["w"]) * (7 if m["u"].lower().startswith("week") else 1)
        stat = "median" if (m["s"] or "").lower() == "median" else "mean"
        return finish(kind="pct_vs_baseline", metric=normalize_name(m["m"]), direction=_dir(m["d"]),
                      threshold=float(m["n"]), window=window, baseline_stat=stat)

    # deviation by N standard deviations
    m = re.fullmatch(
        rf"{M} (?P<d>{UP}|{DOWN}|{EITHER}) (?:to |by )?(?:more than |above |over |beyond )"
        rf"(?P<n>\d+(?:\.\d+)?) (?:standard deviations?|std devs?|sigmas?|sds?)"
        rf"(?: (?:above|below|from) (?:its |the )?(?:normal|average|mean|baseline))?",
        cond, flags,
    )
    if m:
        return finish(kind="deviation", metric=normalize_name(m["m"]), direction=_dir(m["d"]),
                      method="zscore", threshold=float(m["n"]))

    # unusually high / low
    m = re.fullmatch(rf"{M} (?:is|are) (?:unusually|abnormally|unexpectedly) (?P<d>high|low|large|small)", cond, flags)
    if m:
        d = "up" if m["d"].lower() in ("high", "large") else "down"
        return finish(kind="deviation", metric=normalize_name(m["m"]), direction=d,
                      method="mad", threshold=3.5)
    m = re.fullmatch(rf"{M} (?P<d>{EITHER}|{UP}|{DOWN}) (?:unusually|abnormally|unexpectedly)", cond, flags)
    if m:
        return finish(kind="deviation", metric=normalize_name(m["m"]), direction=_dir(m["d"]),
                      method="mad", threshold=3.5)

    # static bound
    m = re.fullmatch(
        rf"{M} (?:is |goes |falls |drops )?(?P<b>below|under|above|over|exceeds) "
        rf"(?P<v>-?\d[\d,]*(?:\.\d+)?)(?P<p> ?%)?",
        cond, flags,
    )
    if m:
        bound = "below" if m["b"].lower() in ("below", "under") else "above"
        return finish(kind="static", metric=normalize_name(m["m"]), bound=bound,
                      threshold=float(m["v"].replace(",", "")), percent_literal=bool(m["p"]))

    raise ValueError("Sentence not understood.\n" + SUPPORTED_HELP)


def parse_rules(text: str, columns: list[str]) -> ParseResult:
    rules: list[Rule] = []
    errors: list[tuple[int, str, str]] = []
    cols = [normalize_name(c) for c in columns]
    for i, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        try:
            rules.append(parse_line(line, cols))
        except ValidationError as e:
            errors.append((i, line, "Invalid rule: " + "; ".join(x["msg"] for x in e.errors())))
        except ValueError as e:
            errors.append((i, line, str(e)))
    return ParseResult(rules, errors)
