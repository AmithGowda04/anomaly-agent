"""Hybrid rule compiler with a confirmation step.

mode='auto'      templates first (deterministic, free); LLM only for lines the templates
                 cannot parse, if an API key is available.
mode='llm'       every line goes to the LLM.
mode='templates' no LLM at all.

Compiled rules are saved with a hash of (rule text + column names). Running requires
`confirmed: true`, set only after a human has read the plain-English readback."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import llm
from .rule_parser import parse_rules, SUPPORTED_HELP
from .schema import Rule, describe, normalize_name


@dataclass
class CompileResult:
    rules: list[Rule] = field(default_factory=list)
    errors: list[tuple[int, str, str]] = field(default_factory=list)  # (line, text, message)
    used_llm: bool = False

    def readback(self) -> list[dict]:
        return [
            {"source": r.source, "your_words": r.text, "interpreted_as": describe(r)}
            for r in self.rules
        ]


def source_hash(text: str, columns: list[str]) -> str:
    body = text.strip() + "\n#cols:" + ",".join(sorted(normalize_name(c) for c in columns))
    return hashlib.sha256(body.encode()).hexdigest()[:16]


def compile_rules(
    text: str,
    columns: list[str],
    mode: str = "auto",
    api_key: Optional[str] = None,
    client=None,
) -> CompileResult:
    cols = [normalize_name(c) for c in columns]
    if mode not in ("auto", "llm", "templates"):
        raise ValueError("mode must be auto, llm or templates")
    result = CompileResult()

    numbered = [(i, raw.split("#", 1)[0].strip()) for i, raw in enumerate(text.splitlines(), 1)]
    numbered = [(i, t) for i, t in numbered if t]

    if mode == "llm":
        pending = numbered
        parsed_rules: list[Rule] = []
    else:
        parsed_rules, pending = [], []
        for i, line in numbered:
            pr = parse_rules(line, cols)
            if pr.rules:
                parsed_rules.extend(pr.rules)
            else:
                pending.append((i, line))
    result.rules.extend(parsed_rules)

    if pending and mode != "templates" and (client is not None or llm.available(api_key)):
        try:
            rules, problems = llm.compile_with_llm(pending, cols, api_key=api_key, client=client)
            result.used_llm = True
            result.rules.extend(rules)
            done = {r.text for r in rules}
            bad = {n: msg for n, msg in problems}
            for i, line in pending:
                if line in done:
                    continue
                result.errors.append((i, line, bad.get(i, "The model could not express this rule in the supported schema.")))
        except Exception as e:  # network/auth/etc: fall back to errors, never crash the run
            for i, line in pending:
                result.errors.append((i, line, f"LLM compile failed ({type(e).__name__}); not understood by templates either."))
    else:
        for i, line in pending:
            hint = "" if mode != "templates" else ""
            pr = parse_rules(line, cols)
            msg = pr.errors[0][2] if pr.errors else "Sentence not understood.\n" + SUPPORTED_HELP
            result.errors.append((i, line, msg + hint))
    return result


def save(result: CompileResult, path: Path, text: str, columns: list[str], confirmed: bool = False) -> None:
    payload = {
        "source_hash": source_hash(text, columns),
        "confirmed": confirmed,
        "rules": [r.model_dump() for r in result.rules],
    }
    Path(path).write_text(json.dumps(payload, indent=2))


def load(path: Path) -> tuple[list[Rule], dict]:
    data = json.loads(Path(path).read_text())
    rules = [Rule.model_validate(r) for r in data["rules"]]
    return rules, {"confirmed": data.get("confirmed", False), "source_hash": data.get("source_hash", "")}
