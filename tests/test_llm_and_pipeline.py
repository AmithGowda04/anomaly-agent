"""LLM path tested with a fake client: no network, no API key, deterministic.

NOTE: these tests prove the validation/guard logic. They do not prove the live model's
interpretation quality; that needs a real ANTHROPIC_API_KEY (see README)."""
import json
from pathlib import Path
from types import SimpleNamespace

from anomaly_agent import compiler, llm, pipeline
from anomaly_agent.cli import main as cli_main
from conftest import COLS


class FakeClient:
    def __init__(self, rules_payload):
        self.calls = []
        self.messages = self
        self._payload = rules_payload

    def create(self, **kw):
        self.calls.append(kw)
        block = SimpleNamespace(type="tool_use", name="submit_rules", input={"rules": self._payload})
        return SimpleNamespace(content=[block])


def test_llm_rules_are_validated_and_marked_source_llm():
    client = FakeClient([{
        "kind": "pct_vs_baseline", "severity": "critical", "metric": "revenue",
        "direction": "down", "threshold": 40, "window": 14, "source_line": 1,
    }])
    rules, problems = llm.compile_with_llm([(1, "tell me if sales collapse")], COLS, client=client)
    assert len(rules) == 1 and rules[0].source == "llm" and not problems
    assert rules[0].text == "tell me if sales collapse"


def test_llm_output_with_unknown_column_is_rejected():
    client = FakeClient([{"kind": "static", "severity": "warning", "metric": "salary", "bound": "above",
                          "threshold": 1, "source_line": 1}])
    rules, problems = llm.compile_with_llm([(1, "x")], COLS, client=client)
    assert not rules and "unknown column" in problems[0][1]


def test_llm_output_with_insane_threshold_is_rejected():
    client = FakeClient([{"kind": "deviation", "severity": "warning", "metric": "revenue",
                          "threshold": 0.0001, "source_line": 1}])
    rules, problems = llm.compile_with_llm([(1, "x")], COLS, client=client)
    assert not rules and problems


def test_prompt_contains_only_names_and_rule_text_never_cell_values():
    client = FakeClient([])
    llm.compile_with_llm([(1, "watch revenue")], COLS, client=client)
    sent = json.dumps(client.calls[0]["messages"]) + client.calls[0]["system"]
    assert "revenue" in sent and "watch revenue" in sent
    assert client.calls[0]["tool_choice"]["name"] == "submit_rules"


def test_long_lines_and_too_many_rules_are_capped():
    client = FakeClient([])
    llm.compile_with_llm([(1, "a" * 5000)], COLS, client=client)
    assert "a" * 301 not in client.calls[0]["messages"][0]["content"]
    try:
        llm.compile_with_llm([(i, "x") for i in range(60)], COLS, client=client)
        raised = False
    except ValueError:
        raised = True
    assert raised


def test_auto_mode_sends_only_lines_templates_cannot_parse():
    client = FakeClient([{"kind": "pct_vs_baseline", "severity": "warning", "metric": "orders",
                          "direction": "down", "threshold": 30, "source_line": 2}])
    text = "Alert if revenue drops more than 20% below its 14-day average\nwarn me when orders tank hard"
    res = compiler.compile_rules(text, COLS, mode="auto", client=client)
    assert len(res.rules) == 2 and not res.errors and res.used_llm
    prompt = client.calls[0]["messages"][0]["content"]
    assert "orders tank" in prompt and "20%" not in prompt  # parsed line was not re-sent


def test_without_key_or_client_unparsed_lines_become_errors(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    res = compiler.compile_rules("warn me when orders tank hard", COLS, mode="auto")
    assert not res.rules and res.errors and not res.used_llm


def test_llm_failure_degrades_to_errors_not_crash():
    class Boom:
        messages = None

        def __init__(self):
            self.messages = self

        def create(self, **kw):
            raise TimeoutError("network down")

    res = compiler.compile_rules("warn me when orders tank hard", COLS, mode="auto", client=Boom())
    assert not res.rules and "LLM compile failed" in res.errors[0][2]


def test_hash_changes_with_rules_or_columns():
    a = compiler.source_hash("x", ["a"])
    assert a != compiler.source_hash("y", ["a"]) and a != compiler.source_hash("x", ["b"])


# ---------------- pipeline + CLI ----------------
def test_pipeline_replay_alerts_on_planted_days_and_writes_artifacts(sales, tmp_path):
    df, truth = sales
    res = compiler.compile_rules(Path("rules/sales_rules.txt").read_text(), list(df.columns), mode="templates")
    out = pipeline.run(df, res.rules, replay=True, out_dir=tmp_path)
    alerted = {d.strftime("%Y-%m-%d") for d in out.alerted["date"]}
    assert {t["date"] for t in truth} <= alerted
    assert (tmp_path / "alerts_log.csv").exists() and list((tmp_path / "emails").glob("*.eml"))
    again = pipeline.run(df, res.rules, replay=True, out_dir=tmp_path)
    assert again.alerted.empty  # re-running does not re-alert


def test_pipeline_rejects_rules_for_missing_columns(sales, tmp_path):
    df, _ = sales
    res = compiler.compile_rules("Warn if refunds are unusually high", ["refunds"], mode="templates")
    try:
        pipeline.run(df.drop(columns=["refunds"]), res.rules, out_dir=tmp_path)
        raised = False
    except ValueError as e:
        raised = "not in the data" in str(e)
    assert raised


def test_cli_refuses_unconfirmed_rules(tmp_path, capsys):
    rules = tmp_path / "r.txt"
    rules.write_text("Warn if refunds are unusually high over the last 28 days\n")
    data = "data/business_metrics.xlsx"
    assert cli_main(["compile", "--data", data, "--rules", str(rules), "--mode", "templates"]) == 0
    capsys.readouterr()
    code = cli_main(["run", "--data", data, "--rules", str(rules.with_suffix(".compiled.json")),
                     "--out", str(tmp_path / "o")])
    assert code == 1 and "not been confirmed" in capsys.readouterr().out


def test_cli_detects_stale_compiled_rules(tmp_path, capsys):
    rules = tmp_path / "r.txt"
    rules.write_text("Warn if refunds are unusually high over the last 28 days\n")
    data = "data/business_metrics.xlsx"
    cli_main(["compile", "--data", data, "--rules", str(rules), "--mode", "templates", "--yes"])
    rules.write_text("Warn if refunds are unusually high over the last 14 days\n")  # edited after compile
    capsys.readouterr()
    code = cli_main(["run", "--data", data, "--rules", str(rules.with_suffix(".compiled.json")),
                     "--out", str(tmp_path / "o")])
    assert code == 1 and "changed" in capsys.readouterr().out
