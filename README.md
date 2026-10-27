# Anomaly Agent

**Live demo:** https://anomaly-agent-aj8iynmb5c93kp2swzxnuc.streamlit.app/ (synthetic sample data; don't upload confidential files)

Watches a business spreadsheet, notices when a metric moves outside its normal range, explains the move in plain English, and sends an alert. The monitoring rules are also written in plain English, so non-programmers can change them.

```
Alert if revenue drops more than 30% below its 28-day average.
Warn if refunds are unusually high over the last 28 days.
Alert if traffic rises and conversion rate falls over the last 28 days.
```

**Everything below was measured on synthetic data (see [Limitations](#limitations)).** On 20 freshly simulated datasets the shipped rules caught 100 of 100 planted anomalies and raised a false alarm on 1.6% of normal days (0.05 false *critical* days per dataset).

## How it works

```
 English rules ──► compile ──► readback ──► you confirm ──► compiled rules (JSON)
                      │
   pattern templates first (deterministic, free)
   Claude only for lines the templates can't parse

 Excel/CSV ──► load + data-quality checks ──► detect ──► dedupe ──► summarise ──► email + log
```

1. **Compile.** Each sentence becomes a validated rule. Built-in sentence patterns handle the common cases offline. If an `ANTHROPIC_API_KEY` is set, Claude interprets sentences the patterns can't, through a forced tool call whose output is validated by the same schema.
2. **Readback and confirm.** The tool prints what each rule will actually do (generated from the validated rule, not from model text) plus a backtest of how often it would have fired on your history. Nothing runs until you confirm.
3. **Detect.** Each metric is compared to a baseline built from the *previous* N days only, so an anomaly never contaminates its own baseline.
4. **Dedupe.** The same signal won't re-alert inside a cooldown window; re-running is idempotent.
5. **Summarise and alert.** Findings become a short business summary with hedged explanations, written as an `.eml` (dry run) or sent over SMTP.

### How an anomaly is defined

| Rule type | Meaning |
|---|---|
| `unusually high/low` | Robust score `0.6745 * (x - median) / MAD` over the previous N days beyond 3.5, **and** a move of at least 5% (so a tiny wiggle on a very stable metric is ignored) |
| `more than N standard deviations` | Classic z-score against the previous N days' mean and standard deviation |
| `more than P% below/above its N-day average` | Percentage distance from the rolling mean or median |
| `below/above <value>` | Fixed limit. `1%` works whether the column stores `0.026` or `2.6` |
| `A rises and B falls` | Both moves, each beyond a robust score of 2.5, on the same day |

Median/MAD is the default because a past spike barely moves it. Flat windows (MAD of zero) produce no score instead of an infinite one.

## Run it

```bash
pip install -r requirements.txt
python data/generate_data.py                     # synthetic data with 5 planted anomalies

python -m anomaly_agent compile  --data data/business_metrics.xlsx --rules rules/sales_rules.txt --mode templates
python -m anomaly_agent run      --data data/business_metrics.xlsx --rules rules/sales_rules.compiled.json --replay
python -m anomaly_agent evaluate --data data/business_metrics.xlsx --rules rules/sales_rules.compiled.json
python benchmark.py rules/sales_rules.txt --seeds 200 220

# second report (customer-support desk): same engine, different columns, different rules file
python data/generate_support_data.py
python -m anomaly_agent compile  --data data/support_metrics.xlsx --rules rules/support_rules.txt --mode templates
python -m anomaly_agent evaluate --data data/support_metrics.xlsx --rules rules/support_rules.compiled.json --truth data/support_ground_truth.json
python benchmark.py rules/support_rules.txt --dataset support --seeds 200 220

streamlit run app.py                             # interactive demo
pytest                                           # 59 tests
```

`compile` asks you to confirm the readback (`--yes` skips the prompt once you've read it). `run` refuses unconfirmed rules, and refuses rules that are stale because the rule file or the columns changed.

Without `--replay`, `run` evaluates only the latest day, which is what you would schedule daily. Emails are written to `outputs/emails/` unless `--send` is given with SMTP settings.

### Changing the rules for another report

Edit the text file, recompile, read the readback, confirm. Metric names are matched against the real column headers (`Conversion Rate` becomes `conversion_rate`). Supported sentence patterns:

```
Alert|Warn if <metric> drops|rises more than <N>% below|above its <W>-day average|median
Alert|Warn if <metric> drops|rises more than <N> standard deviations
Alert|Warn if <metric> is unusually high|low
Alert|Warn if <metric> is below|above <value>[%]
Alert|Warn if <metric> rises|falls and <metric> rises|falls
   (add "over the last <N> days|weeks" to set the baseline window)
```

For anything else, set `ANTHROPIC_API_KEY` (see `.env.example`) and run with `--mode auto`.

## Results

Run on synthetic data where the truth is known. Five anomalies are planted in a 180-day series: a traffic-quality shift, a refund surge, a traffic collapse, an ad-spend spike, a checkout failure.

| Check | Result |
|---|---|
| Planted anomalies caught, 20 fresh datasets (seeds 200-219) | **100 / 100** |
| False-alarm days, same datasets | mean **2.4** per dataset (1.6% of 147 normal days), max 6 |
| False *critical* days | 0.05 per dataset (1 across all 20) |
| The committed sample file | 5 / 5 caught, 3 false alarm days |

How the rules got there, because the first draft was worse:

- The first draft used 14-day baselines and one classic z-score rule. On dev seeds 0-19 it fired on 3.0% of normal days. Moving to 28-day baselines and median/MAD scoring cut that to 1.4% with recall unchanged.
- A 13-16% cost move was paging people as *critical*. Cost is now tiered: a statistical outlier is a Warn; only a 50%+ jump is an Alert.
- The checkout failure produced a weak explanation because nothing watched conversion. Adding a conversion rule fixed the explanation and raised false alarms slightly, a trade I accepted.
- Final numbers come from seeds 200-219, which were not used for any of those choices.

### Second report: customer-support desk

To check the tool isn't fitted to one spreadsheet, there is a second synthetic report with different metrics (tickets opened/resolved, response time, satisfaction, escalations, backlog) and a different meaning of "bad". Only `rules/support_rules.txt` changes; the engine, parser and alerts are untouched. The one code addition is five support-desk lines in the summariser's explanation rulebook.

| Check | Result |
|---|---|
| Rules compiled on the first attempt | 12 / 12 |
| Dev seeds 0-19 (rules were not adjusted after this run) | 100 / 100 caught, 1.8% of normal days flagged |
| Held-out seeds 200-219 | **99 / 100** caught, mean 2.6 false-alarm days per dataset (1.8% of 147 normal days), 0.05 false critical days per dataset |
| The committed sample file | 5 / 5 caught, 2 false-alarm days |

The one miss (seed 200) was the escalation spike: the random count that day was only 5, so the planted 4x came to 20 against a baseline of 12, which falls below the 3.5 robust-score cutoff. I left the rules as they were rather than tune against the held-out seeds. Same caveat as above: this is synthetic data with a mild weekend dip and no holidays or promotions.

The sales rules, pointed at the support columns, fail to compile with clear errors instead of silently running on nothing (covered by a test).

## Design decisions

- **Templates first, Claude second.** Deterministic parsing is free, offline and reproducible; the model handles only what it can't. Claude output goes through the same validation as everything else, then through a human confirmation.
- **Only names and sentences leave the machine.** Column names are reduced to `[a-z0-9_]` (max 40 chars) and rule lines are capped before any API call; cell values are never sent. This is a prompt-injection guard as well as a privacy one.
- **Bad data is a data problem.** Duplicate dates, gaps, unreadable dates, non-numeric columns, stale files are reported explicitly rather than turned into fake anomalies.
- **Explanations are hypotheses.** They come from a small explicit rulebook, are worded as things to check, and are omitted when the report doesn't contain the relevant metrics.

## Limitations

- **All results are on synthetic data I generated.** Real data has seasonality, holidays, promotions and outliers this generator does not. Expect more false alarms on real data and tune with the backtest shown at compile time.
- **The planted anomalies are on the same dates in every simulated dataset;** only the noise changes between seeds. The benchmark shows robustness to noise, not to every anomaly shape or position.
- **The live Claude path has not been exercised against the real API.** It is tested with a fake client, which proves the validation and guard logic, not the model's interpretation quality. Try it with your own key and check the readback.
- **No seasonality handling.** A 28-day rolling window adapts to level shifts but will flag a predictable weekend or month-end pattern if it is large. A per-weekday baseline is the natural next step.
- **Pattern rules are hand-written;** the agent does not discover relationships between metrics.
- **Scheduling is a template only.** `.github/workflows/daily_run.yml` runs the command daily in dry-run mode on the static sample data. It is not wired to a real report, and the alert log does not persist between runs there, so de-duplication across days needs a cache or a stored log.
- The Streamlit demo processes uploads in memory but is not a place for confidential data.

## Layout

```
anomaly_agent/   schema, loader, rule_parser, llm, compiler, engine, summarizer, alerts, pipeline, cli
app.py           Streamlit demo
rules/           example English rules (sales, support desk)
data/            synthetic data generators (two reports) + ground truth
tests/           59 tests (parser, schema, detectors, loader, dedupe, mocked LLM, CLI, app)
benchmark.py     multi-seed recall / false-alarm benchmark
```
