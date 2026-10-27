"""Structured rule schema. Every rule, whether parsed from a template or compiled
by an LLM, must pass through this validation before it can run."""
from __future__ import annotations

import re
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

Direction = Literal["up", "down", "either"]
Severity = Literal["warning", "critical"]

MAX_NAME_LEN = 40


def normalize_name(name: object) -> str:
    """'Conversion Rate' -> 'conversion_rate'. Restricts names to [a-z0-9_].

    This doubles as a prompt-injection guard: column names are untrusted input
    and are only ever sent to an LLM in this restricted form."""
    s = re.sub(r"[^0-9a-zA-Z]+", "_", str(name).strip().lower()).strip("_")
    return s[:MAX_NAME_LEN] or "col"


class Leg(BaseModel):
    metric: str
    direction: Literal["up", "down"]


class Rule(BaseModel):
    kind: Literal["pct_vs_baseline", "deviation", "static", "pattern"]
    severity: Severity = "warning"
    metric: Optional[str] = None
    direction: Direction = "either"
    # deviation: robust (median/MAD) or classic (mean/std) score
    method: Literal["mad", "zscore"] = "mad"
    # pct_vs_baseline: which baseline the percentage is measured against
    baseline_stat: Literal["mean", "median"] = "mean"
    # pct_vs_baseline: percent; deviation: score cutoff; static: the value
    threshold: Optional[float] = None
    window: int = Field(14, ge=3, le=120)
    # deviation guard: ignore moves smaller than this % of baseline
    min_change_pct: float = Field(5.0, ge=0, le=100)
    # static
    bound: Optional[Literal["above", "below"]] = None
    percent_literal: bool = False  # the user wrote "1%"; resolve against column scale
    # pattern
    legs: Optional[list[Leg]] = None
    leg_threshold: float = Field(2.5, gt=0, le=20)
    text: str = ""
    source: Literal["template", "llm"] = "template"

    @model_validator(mode="after")
    def _check(self) -> "Rule":
        k = self.kind
        if k == "pct_vs_baseline":
            if not self.metric:
                raise ValueError("pct_vs_baseline needs a metric")
            if self.threshold is None or not (0 < self.threshold <= 1000):
                raise ValueError("pct_vs_baseline threshold must be in (0, 1000]")
        elif k == "deviation":
            if not self.metric:
                raise ValueError("deviation needs a metric")
            if self.threshold is None or not (1 <= self.threshold <= 20):
                raise ValueError("deviation threshold must be between 1 and 20")
        elif k == "static":
            if not self.metric or self.bound is None or self.threshold is None:
                raise ValueError("static needs metric, bound and threshold")
            if not (-1e12 < self.threshold < 1e12):
                raise ValueError("static threshold out of range")
        elif k == "pattern":
            if not self.legs or not (2 <= len(self.legs) <= 4):
                raise ValueError("pattern needs 2-4 legs")
            if len({leg.metric for leg in self.legs}) != len(self.legs):
                raise ValueError("pattern legs must use distinct metrics")
        return self

    @property
    def key(self) -> str:
        if self.kind == "pattern":
            body = "+".join(f"{leg.metric}:{leg.direction}" for leg in (self.legs or []))
        else:
            body = f"{self.metric}:{self.direction}:{self.threshold}"
        return f"{self.kind}|{body}|w{self.window}|{self.method}|{self.baseline_stat}|{self.bound}"

    @property
    def metrics(self) -> list[str]:
        if self.kind == "pattern":
            return [leg.metric for leg in (self.legs or [])]
        return [self.metric] if self.metric else []


def describe(rule: Rule) -> str:
    """Deterministic plain-English readback of a structured rule.

    Always generated from the validated structure, never from LLM text, so the
    confirmation step shows what will actually run."""
    sev = "ALERT" if rule.severity == "critical" else "WARN"
    dword = {"up": "rises", "down": "falls", "either": "moves"}
    if rule.kind == "pct_vs_baseline":
        d = rule.direction
        rel = {"up": "above", "down": "below", "either": "away from"}[d]
        return (
            f"{sev}: when {rule.metric} is more than {rule.threshold:g}% "
            f"{rel} its {rule.window}-day {rule.baseline_stat}."
        )
    if rule.kind == "deviation":
        d = rule.direction
        if rule.method == "mad":
            how = f"robust score (median/MAD) beyond {rule.threshold:g}"
        else:
            how = f"more than {rule.threshold:g} standard deviations from the {rule.window}-day mean"
        side = {"up": "upward", "down": "downward", "either": "in either direction"}[d]
        return (
            f"{sev}: when {rule.metric} is unusual {side} - {how} over a {rule.window}-day "
            f"window (ignoring moves under {rule.min_change_pct:g}%)."
        )
    if rule.kind == "static":
        pct = "%" if rule.percent_literal else ""
        return f"{sev}: when {rule.metric} is {rule.bound} {rule.threshold:g}{pct}."
    legs = " AND ".join(f"{leg.metric} goes {leg.direction}" for leg in (rule.legs or []))
    return (
        f"{sev}: when these happen on the same day: {legs} "
        f"(each beyond a robust score of {rule.leg_threshold:g})."
    )
