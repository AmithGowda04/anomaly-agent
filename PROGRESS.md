# PROGRESS (resume file)

Read this first in any new session. Update it at the end of every stage.

Last verified: 2026-10-27. `pytest` -> 59 passed. `python -m anomaly_agent --help` works.

## Decisions made
- Detector default: median/MAD (robust score), 28-day baseline excluding the current day.
- Rules: plain English. Template parser first, Claude (Anthropic) only for lines templates can't parse, then schema validation, readback and human confirm (Option C).
- Only sanitised column names and rule text are sent to the API, never cell values.
- Streamlit demo with optional bring-your-own API key. Email is dry-run by default.

## Done and verified (code exists, tests pass)
- [x] Synthetic data + ground truth: data/generate_data.py, data/business_metrics.xlsx, data/ground_truth.json
- [x] Loader + data-quality checks: anomaly_agent/loader.py
- [x] Rule schema (pydantic) + template parser: schema.py, rule_parser.py
- [x] Claude compiler with forced tool call + validation: llm.py, compiler.py
- [x] Detection engine + pattern rules: engine.py
- [x] Summariser with hedged explanations: summarizer.py
- [x] Alerts, cooldown dedupe, alert log, .eml / SMTP: alerts.py, pipeline.py
- [x] CLI: compile, explain, run, evaluate (cli.py)
- [x] Benchmark: benchmark.py -> outputs/benchmark.json (100/100 caught, 1.6% false-alarm days, synthetic data)
- [x] Streamlit app: app.py
- [x] README, portfolio page generator (make_portfolio.py -> portfolio/index.html), CI tests workflow
- [x] Second report (support desk): data/generate_support_data.py, rules/support_rules.txt, benchmark.py --dataset support, 5 support lines in summarizer HYPOTHESES, sample selector in app.py, tests/test_second_report.py. Results: 12/12 rules compiled first try; dev seeds 0-19 100/100; held-out seeds 200-219 99/100 (one miss, seed 200, low random draw), 1.8% false-alarm days. Rules were not tuned after scoring.

## Not done / open
- [ ] Live Claude path tested against the real API (only tested with a fake client; needs a real key)
- [x] Scheduled run template: .github/workflows/daily_run.yml (YAML validated and the command run locally; the workflow itself has NOT been executed on GitHub). Log does not persist between CI runs.
- [ ] Deploy the Streamlit demo (Streamlit Community Cloud) and put the link in README + portfolio page
- [ ] Publish portfolio page to the user's site (needs user's hosting choice)
- [ ] Per-weekday baseline (seasonality) - listed as a known limitation

## Incident log
- 2026-10-27: Claude overwrote requirements.txt, .gitignore, .env.example without reading them first. Rebuilt from what the code imports and reads (pydantic, anthropic, streamlit, pandas, numpy, openpyxl; env vars ANTHROPIC_API_KEY, ANOMALY_AGENT_MODEL, SMTP_*, ALERT_*). Originals were not backed up, so compare against any copy the owner has. A stray pyproject.toml and src/ folder were also added and removed.

## Publish status (2026-10-27)
- PUSHED: https://github.com/AmithGowda04/anomaly-agent (branch main, commit 94f67db). Remote HEAD verified equal to local.
- DEPLOYED (owner-confirmed URL, not verified by Claude): https://anomaly-agent-aj8iynmb5c93kp2swzxnuc.streamlit.app/ . Demo link added to README and portfolio page.
- Done: `python make_portfolio.py --name "Amith Gowda S" --github https://github.com/AmithGowda04/anomaly-agent --demo <streamlit url>`, add the demo link to README, commit and push.
- Portfolio site: https://amithgowda.netlify.app (source repo likely AmithGowda04/amithgowdaportfolio, TypeScript, dark theme #0B0D12). Not yet edited; needs the owner's project-card format.
- GitHub Actions workflows (tests.yml, daily_run.yml) have not run yet; check the Actions tab after the push.

## Portfolio site (2026-10-27)
- Demo verified working by the owner. Card added to AmithGowda04/amithgowdaportfolio, src/components/Projects.tsx, second position (beside Aadhar Seeding), on branch `add-anomaly-agent-project` (commit 4919834). `main` untouched, so the live site (https://amithgowda.netlify.app) does not show it yet.
- Next: owner approves, then merge the branch into main (Netlify redeploys). Optional: StatsBand says "8+ Projects Delivered".
- Last grid row has one card and two empty cells (9 cards, 3 columns); cosmetic.
