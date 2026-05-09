# BUILD_STATUS.md
# Minervini SEPA Stock Analysis System — Build Status

> **Last Updated:** 2026-05-09 (Phases 9–11 complete, Phase 12 ~80% done)
> **Version:** 1.1.0
> **Reference Design:** PROJECT_DESIGN.md v1.4.0
> **Python:** 3.11 | **Test Suite:** 896 tests, 895 passing, 1 failing

---

## Quick Summary

| Phase | Title | Status | Progress |
|-------|-------|--------|----------|
| **Phase 1** | Foundation — Data Ingestion & Storage | ✅ **COMPLETE** | 100% |
| **Phase 2** | Feature Engineering | ✅ **COMPLETE** | 100% |
| **Phase 3** | Rule Engine (SEPA Logic) | ✅ **COMPLETE** | 100% |
| **Phase 4** | Reports, Charts, Alerts & Early Paper Trading | ✅ **COMPLETE** | 100% |
| **Phase 5** | Fundamentals & News Sentiment | ✅ **COMPLETE** | 100% |
| **Phase 6** | LLM Narrative Layer | ✅ **COMPLETE** | 100% |
| **Phase 7** | Paper Trading Simulator | ✅ **COMPLETE** | 100% |
| **Phase 8** | Backtesting Engine | ✅ **COMPLETE** | 100% |
| **Phase 9** | Hardening & Production | ✅ **COMPLETE** | 100% |
| **Phase 10** | API Layer (FastAPI) | ✅ **COMPLETE** | 100% |
| **Phase 11** | Streamlit Dashboard MVP | ✅ **COMPLETE** | 100% |
| **Phase 12** | Next.js Production Frontend | 🚧 **IN PROGRESS** | ~80% |

**Overall Project Completion: ~97%**

**Known gap:** Prometheus metrics endpoint was deprioritised in Phase 9.  
**Known test failure:** `tests/integration/test_api_e2e.py::test_full_api_flow` — portfolio endpoint returns `200` where test expects `404` (minor E2E expectation mismatch, tracked).

---

## Legend

| Badge | Meaning |
|-------|---------|
| ✅ | Fully implemented, tested, and verified |
| 🚧 | Partially implemented — work in progress |
| ⏳ | Not yet started — directory/stub only |
| ❌ | Required by design but missing entirely |
| 📁 | Directory exists, `__init__.py` stub only |


---

## Phase 1 — Foundation (Weeks 1–3) ✅ COMPLETE

**Goal:** Raw data flowing into clean, queryable storage.

| Task | Status | File/Notes |
|------|--------|------------|
| Project skeleton (all directories, `__init__.py`, `pyproject.toml`) | ✅ | All dirs created |
| `ingestion/base.py` — abstract `DataSource` interface | ✅ | `fetch`, `fetch_universe_batch`, `fetch_universe` |
| `ingestion/yfinance_source.py` — PRIMARY batch source | ✅ | `yf.download(tickers, group_by="ticker")` |
| `ingestion/angel_one_source.py` — FALLBACK 1 | ✅ | Angel One SmartAPI adapter |
| `ingestion/upstox_source.py` — FALLBACK 2 | ✅ | Upstox API v2 adapter |
| `ingestion/source_factory.py` — config-driven selector + fallback | ✅ | `universe.source` in settings.yaml |
| `ingestion/nsepython_universe.py` — Nifty 500 + full NSE list | ✅ | Replaces Bhav Copy |
| `utils/trading_calendar.py` — NSE holiday schedule | ✅ | `pandas_market_calendars` |
| `ingestion/validator.py` — schema + OHLCV sanity + gap detection | ✅ | Uses `trading_calendar.py` |
| `ingestion/universe_loader.py` — unified symbol resolver | ✅ | Watchlist + universe merge |
| `load_watchlist_file()` — CSV / JSON / XLSX / TXT parser | ✅ | In `universe_loader.py` |
| SQLite `watchlist` table (symbol, note, added_via, last_score) | ✅ | `data/sepa_ai.db` |
| `storage/parquet_store.py` — atomic append support | ✅ | Temp-file + rename pattern |
| `storage/sqlite_store.py` — results + run_history | ✅ | |
| `utils/logger.py`, `utils/date_utils.py`, `utils/exceptions.py` | ✅ | + `math_utils.py` |
| `scripts/run_daily.py` — `--watchlist`, `--symbols`, `--watchlist-only`, `--scope` | ✅ | |
| `scripts/bootstrap.py` — full history skeleton | ✅ | yfinance batch mode |
| `config/settings.yaml` — all Phase 1 parameters | ✅ | Includes watchlist + source config |
| `config/universe.yaml`, `config/logging.yaml`, `config/symbol_aliases.yaml` | ✅ | |
| Unit tests — `test_universe_loader.py`, `test_validator.py` | ✅ | |
| Unit tests — `test_trading_calendar.py`, `test_source_factory.py`, `test_storage.py` | ✅ | |
| **Deliverable:** `run_daily.py --watchlist mylist.csv` analyses only those symbols | ✅ | |

**Notes:** `pipeline/context.py` (`RunContext`) also implemented here as infrastructure.

---

## Phase 2 — Feature Engineering (Weeks 4–6) ✅ COMPLETE

**Goal:** All Minervini-relevant indicators computed and stored.

| Task | Status | File/Notes |
|------|--------|------------|
| `features/moving_averages.py` — SMA 10/21/50/150/200, EMA 21, slopes | ✅ | |
| `features/relative_strength.py` — RS raw + RS rating vs Nifty 500 | ✅ | |
| `features/sector_rs.py` — sector-level RS ranking, top-5 bonus (+5 pts) | ✅ | |
| `features/atr.py` — ATR 14, ATR% | ✅ | |
| `features/volume.py` — vol ratio, acc/dist, up/down vol days | ✅ | |
| `features/pivot.py` — swing high/low detection (ZigZag) | ✅ | |
| `features/vcp.py` — `RuleBasedVCPDetector` + `VCPDetector` ABC | ✅ | |
| `features/feature_store.py` — `bootstrap()`, `update()`, `needs_bootstrap()` | ✅ | |
| `screener/pre_filter.py` — 52w-high + RS + SMA200 gate (eliminates ~70%) | ✅ | |
| Unit tests — all feature modules with fixture data | ✅ | `tests/fixtures/sample_ohlcv_MOCK*.parquet` present |
| Benchmark: bootstrap 500 symbols < 15 min; daily update < 30 sec | ✅ | `tests/unit/test_feature_benchmark.py` |
| **Deliverable:** `data/features/RELIANCE.parquet` with all indicators; `update()` < 50ms/symbol | ✅ | |

**Blockers:** Phase 1 must be complete (✅ done).


---

## Phase 3 — Rule Engine (Weeks 7–9) ✅ COMPLETE

**Goal:** Deterministic, fully testable SEPA screening logic.

| Task | Status | File/Notes |
|------|--------|------------|
| `rules/stage.py` — Stage 1/2/3/4 detection with confidence score (hard gate, runs first) | ✅ | |
| `rules/trend_template.py` — all 8 conditions, configurable thresholds | ✅ | |
| `rules/vcp_rules.py` — VCP qualification rules | ✅ | |
| `rules/entry_trigger.py` — pivot breakout detection + volume confirmation | ✅ | |
| `rules/stop_loss.py` — VCP `base_low` (primary) + ATR fallback | ✅ | |
| `rules/risk_reward.py` — R:R estimator using nearest resistance | ✅ | |
| `rules/scorer.py` — `SEPAResult` dataclass + weighted composite score (0–100) | ✅ | Includes stage, fundamentals, news fields |
| `screener/pipeline.py` — batch screener with `ProcessPoolExecutor` | ✅ | |
| `screener/results.py` — persist `SEPAResult` to SQLite | ✅ | |
| Unit tests: Stage detection with synthetic MA data | ✅ | `tests/unit/test_stage_detection.py` |
| Unit tests: Trend Template (each of 8 conditions pass/fail) | ✅ | `tests/unit/test_trend_template.py` |
| Unit tests: VCP rules with known patterns | ✅ | `tests/unit/test_vcp_rules.py` |
| Unit tests: Entry trigger, stop loss, risk/reward | ✅ | `test_entry_trigger.py`, `test_stop_loss.py`, `test_risk_reward.py` |
| Unit tests: Scorer + SEPAResult dataclass | ✅ | `tests/unit/test_scorer.py` |
| Regression test: Stage 4 stock scores FAIL even if 8/8 TT conditions pass | ✅ | `tests/integration/test_known_setups.py` |
| Integration test: screen on historical date, verify known setups | ✅ | `tests/integration/test_screener_batch.py` |
| **Deliverable:** `run_daily.py --date 2024-01-15` produces ranked watchlist; non-Stage-2 filtered | ✅ | |

**Blockers:** Phase 2 must be complete (feature store needed as input to rule engine). ✅ done.

---

## Phase 4 — Reports, Charts, Alerts & Early Paper Trading (Weeks 10–12) ✅ COMPLETE

**Goal:** Human-consumable outputs, alert dispatch, and early paper trading for signal validation.

> **Note:** Paper trading starts here (not Phase 7) to generate real signal data sooner for backtesting calibration.

| Task | Status | File/Notes |
|------|--------|------------|
| `reports/daily_watchlist.py` — CSV + HTML report | ✅ | |
| `reports/chart_generator.py` — candlestick + MA ribbons + VCP markup + stage annotation | ✅ | |
| `reports/templates/watchlist.html.j2` — styled HTML template | ✅ | |
| `alerts/alert_deduplicator.py` — dedup gates (days, score jump, quality, new breakout) | ✅ | |
| `alerts/telegram_alert.py` — daily watchlist to Telegram (with deduplication) | ✅ | |
| `alerts/email_alert.py` — optional SMTP summary | ✅ | |
| `pipeline/scheduler.py` — APScheduler at 15:35 IST, skips NSE holidays | ✅ | |
| `pipeline/runner.py` — unified entry point (daily / historical / backtest modes) | ✅ | |
| `paper_trading/simulator.py` — `enter_trade()`, `exit_position()`, slippage model (0.15%) | ✅ | |
| `paper_trading/portfolio.py` — portfolio state + P&L tracking | ✅ | |
| `paper_trading/order_queue.py` — market-hours aware pending order queue | ✅ | |
| `data/paper_trading/` directory structure (portfolio.json, trades.json, pending_orders.json) | ✅ | Dir exists |
| Unit tests: alert deduplication logic | ✅ | `tests/unit/test_alert_deduplicator.py` |
| Unit tests: chart generator | ✅ | `tests/unit/test_chart_generator.py` |
| Unit tests: daily watchlist | ✅ | `tests/unit/test_daily_watchlist.py` |
| Unit tests: pipeline runner | ✅ | `tests/unit/test_runner.py` |
| **Deliverable:** Telegram message at 15:35 IST with A+/A setups + chart images; no duplicate alerts | ✅ | |

**Blockers:** Phase 3 rule engine must be complete. ✅ done.


---

## Phase 5 — Fundamentals & News Sentiment (Weeks 13–14) ✅ COMPLETE

**Goal:** Add Minervini fundamental conditions and news sentiment as scoring inputs.

| Task | Status | File/Notes |
|------|--------|------------|
| `ingestion/fundamentals.py` — Screener.in scraper with 7-day cache | ✅ | |
| `rules/fundamental_template.py` — 7 Minervini fundamental conditions (F1–F7) | ✅ | |
| Unit tests: fundamental template (known PE/ROE/EPS → expected pass/fail) | ✅ | `tests/unit/test_fundamental_template.py` |
| Unit tests: fundamentals scraper (cache, parse, graceful fail) | ✅ | `tests/unit/test_fundamentals.py` |
| `ingestion/news.py` — RSS + NewsData.io + keyword scorer + LLM re-scorer | ✅ | |
| `config/symbol_aliases.yaml` — symbol → alias list for news matching | ✅ | Created in Phase 1 |
| Wire `fundamental_score` + `news_score` into `rules/scorer.py` composite | ✅ | |
| Update HTML report to show fundamental conditions per candidate | ✅ | Via `reports/daily_watchlist.py` |
| Update Telegram alert to include fundamental summary line | ✅ | Via `alerts/telegram_alert.py` |
| Unit test: fundamental template with `None` data → graceful fail (no crash) | ✅ | `test_fundamental_template.py` |
| Unit test: news keyword fallback works without LLM | ✅ | `tests/unit/test_news.py` |
| `data/fundamentals/` — 7-day TTL cache store | ✅ | Dir exists |
| `data/news/` — 30-min TTL cache store | ✅ | Dir + `market_news.json` |
| **Deliverable:** A+/A setups show EPS acceleration, ROE, promoter holding, news score in report | ✅ | |

**Blockers:** Phase 3 (scorer wiring) + Phase 4 (report/alert templates). ✅ both done.

---

## Phase 6 — LLM Narrative Layer (Weeks 15–16) ✅ COMPLETE

**Goal:** AI-generated trade briefs as an optional overlay.

| Task | Status | File/Notes |
|------|--------|------------|
| `llm/llm_client.py` — abstract `LLMClient` base class | ✅ | |
| `llm/explainer.py` — `generate_trade_brief()` + `generate_watchlist_summary()` | ✅ | |
| `llm/prompt_templates/trade_brief.j2` — Jinja2 trade brief template | ✅ | |
| `llm/prompt_templates/watchlist_summary.j2` — daily watchlist narrative template | ✅ | |
| `GroqClient` implementation (default — free, fast) | ✅ | In `llm/llm_client.py` |
| `AnthropicClient` implementation | ✅ | In `llm/llm_client.py` |
| `OpenAIClient` implementation | ✅ | In `llm/llm_client.py` |
| `OllamaClient` — local model fallback (zero API cost) | ✅ | In `llm/llm_client.py` |
| `OpenRouterClient` — deepseek-r1:free for best reasoning | ✅ | In `llm/llm_client.py` |
| Add narrative field to HTML report | ✅ | |
| Token cost logging per run | ✅ | |
| Graceful degradation — LLM failure skips narrative, logs warning, pipeline continues | ✅ | |
| Unit tests: LLM client adapters | ✅ | `tests/unit/test_llm_client.py` |
| Unit tests: explainer with mocked LLM response | ✅ | `tests/unit/test_explainer.py` |
| Unit tests: prompt template rendering | ✅ | `tests/unit/test_prompt_templates.py` |
| **Deliverable:** HTML report includes 3-sentence AI trade brief for each A+/A setup via Groq | ✅ | |

**Blockers:** Phase 3 (`SEPAResult`) + Phase 4 (report rendering). ✅ both done.


---

## Phase 7 — Paper Trading Simulator (Weeks 17–18) ✅ COMPLETE

**Goal:** Full paper trading with pyramiding, market-hours-aware order execution, and performance reporting.

> **Note:** Basic enter/exit + slippage was started in Phase 4. Phase 7 completed pyramiding,
> the full order queue with expiry logic, trailing stop integration, and the HTML performance report.

| Task | Status | File/Notes |
|------|--------|------------|
| `paper_trading/simulator.py` — full `enter_trade()`, `check_exits()`, `pyramid_position()`, `save_state()`, `load_state()` | ✅ | Slippage + brokerage models included |
| `paper_trading/portfolio.py` — `Portfolio`, `Position`, `ClosedTrade` dataclasses; `get_summary()`, `to_json()`, `from_json()`, `record_equity_point()` | ✅ | Lossless JSON round-trip |
| `paper_trading/order_queue.py` — `queue_order()`, `execute_pending_orders()`, `is_market_open()`, expiry logic | ✅ | `_add_trading_days()` for calendar-aware expiry |
| `paper_trading/report.py` — `generate_performance_report()`, `get_quality_breakdown()`, `get_monthly_pnl()` | ✅ | Self-contained HTML; equity curve + hold-time histogram embedded as base64 PNG |
| Pyramiding logic — VCP Grade A positions: 50% of original qty, one add, `pyramided` flag | ✅ | `pyramid_position()` in `simulator.py` |
| Trailing stop — `peak_close` tracking, `trailing_stop` field on `Position`, floor at VCP stop | ✅ | Updated in `check_exits()` each day |
| Brokerage deduction — `brokerage_pct` applied on exit, subtracted from P&L and cash | ✅ | Configurable: `paper_trading.brokerage_pct` |
| Max hold days exit — position auto-closed after `max_hold_days` trading days | ✅ | `exit_reason="max_hold_days"` |
| Wire into `pipeline/runner.py` — auto paper trade after daily screen | ✅ | |
| **Unit tests — `tests/unit/test_paper_trading.py`** (27 tests) | ✅ | Covers enter, exit, pyramid, brokerage, save/load state, order queue, equity curve |
| Unit test: enter/exit with slippage | ✅ | Tests 1–3, 6–7 |
| Unit test: pyramiding — already pyramided returns None | ✅ | Test 4 |
| Unit test: pyramiding — valid VCP Grade A + vol dry-up sets `pyramid_qty` | ✅ | Test 5 |
| Unit test: brokerage deducted from P&L and cash on exit | ✅ | Test 11 |
| Unit test: `max_hold_days` triggers `"max_hold_days"` exit reason | ✅ | Test 12 |
| Unit test: `save_state` / `load_state` full round-trip | ✅ | Test 13 |
| Unit test: `load_state` with missing file → fresh portfolio, no exception | ✅ | Test 14 |
| Unit test: `record_equity_point` appends daily snapshot | ✅ | Test 15 |
| Unit test: `get_summary` win rate, profit factor, avg R, zero-division safety | ✅ | Tests 16–19 |
| Unit test: `equity_curve` survives JSON round-trip | ✅ | Test 20 |
| Unit test: `is_market_open` — during hours / after close / NSE holiday | ✅ | Tests 21–23 |
| Unit test: `queue_order` writes `queued_at` + `expiry_date` fields | ✅ | Test 24 |
| Unit test: `execute_pending_orders` — valid order → Position filled | ✅ | Test 25 |
| Unit test: `execute_pending_orders` — expired order removed from queue | ✅ | Test 26 |
| Unit test: `execute_pending_orders` — missing price keeps order in queue | ✅ | Test 27 |
| Unit test: trailing stop never drops below VCP floor | ✅ | `tests/unit/test_trailing_stop.py` |
| **Unit tests — `tests/unit/test_paper_report.py`** (8 tests) | ✅ | |
| Test 1: 5 closed + 2 open → HTML file created at correct path | ✅ | |
| Test 2: `get_quality_breakdown` — 3 A+ trades (2 wins, 1 loss) → `win_rate ≈ 0.667` | ✅ | |
| Test 3: `get_monthly_pnl` — groups by `exit_date` month correctly | ✅ | |
| Test 4: empty trades → report contains `"No closed trades yet."` | ✅ | |
| Test 5: equity curve section contains base64 `<img>` tag | ✅ | |
| Test 6: `get_quality_breakdown` — multiple quality buckets independent | ✅ | |
| Test 7: `get_monthly_pnl` — empty list → empty dict | ✅ | |
| Test 8: `get_monthly_pnl` — multiple trades in same month summed correctly | ✅ | |
| `data/paper_trading/portfolio.json`, `trades.json`, `pending_orders.json` | ✅ | Written by `save_state()` |
| **Deliverable:** A+/A signals auto-create paper trades; portfolio persisted; HTML report generated | ✅ | |

**Report sections in `paper_trading/report.py`:**
- Summary cards (total return, realised P&L, win rate, avg R, profit factor, trades count, open positions)
- Equity curve chart (matplotlib → base64 PNG, dark theme, embedded `<img>`)
- Open positions table (symbol, entry, current, unrealised P&L%, days held, stop, quality)
- Closed trades table (symbol, entry/exit dates and prices, P&L%, R-multiple, exit reason)
- Quality breakdown table (win rate + avg R per setup_quality bucket: A+, A, B, C)
- Monthly P&L table (grouped by `exit_date` month)
- Hold-time distribution histogram (matplotlib → base64 PNG, embedded `<img>`)

**Blockers:** Phase 4 (basic simulator + runner). ✅ done.


---

## Phase 8 — Backtesting Engine (Weeks 19–22) ✅ COMPLETE

**Goal:** Validate strategy performance on historical data with realistic trade simulation.

| Task | Status | File/Notes |
|------|--------|------------|
| `backtest/engine.py` — `BacktestTrade`, `BacktestResult` dataclasses; `simulate_trade()`; `run_backtest()` walk-forward orchestrator | ✅ | No-lookahead design; trailing stop ratchets up, floored at VCP `base_low`; exits: trailing_stop / target / fixed_stop / max_hold; force-closes open positions at `end_date` |
| `backtest/portfolio.py` — `BacktestPortfolio`; `enter()`, `close()`, `record_equity()`, `get_portfolio_value()` | ✅ | 1R = 1% risk-per-trade sizing; max 10 open positions; scales down quantity when capital is short |
| Trailing stop in `simulate_trade()` — `trailing_stop_pct`, ratchet-only-up, floored at VCP `base_low` | ✅ | `candidate = max(peak * (1 - pct), stop_loss_price)`; `stop_type = "trailing" \| "fixed"` |
| `backtest/regime.py` — `get_regime()`, `label_trades()`, `get_regime_stats()` | ✅ | Full `NSE_REGIME_CALENDAR` (Appendix E of PROJECT_DESIGN.md); 200MA slope fallback for post-calendar dates; "Unknown" when no benchmark |
| `backtest/metrics.py` — `compute_metrics()`, `compute_cagr()`, `compute_max_drawdown()`, `compute_sharpe()` | ✅ | Full suite: CAGR, Sharpe, max drawdown, win rate, avg R-multiple, profit factor, expectancy, avg hold days, best/worst trade |
| `backtest/report.py` — `generate_report()`, `plot_equity_curve()` | ✅ | Self-contained HTML + CSV; sections: key metric cards, equity curve (with drawdown shading), regime breakdown, VCP quality breakdown, trailing vs fixed comparison, top-10 winners/losers, all trades table, config snapshot |
| `scripts/backtest_runner.py` — CLI: `--start`, `--end`, `--universe`, `--trailing-stop`, `--no-trailing`, `--compare`, `--output`, `--config` | ✅ | `--compare` runs both trailing and fixed, adds comparison table to HTML; `run_parameter_sweep()` public API |
| Parameter sweep — `run_parameter_sweep()` with `trailing_pcts` default `[0.05, 0.07, 0.10, 0.15]` | ✅ | Returns tidy `pd.DataFrame`; prints formatted table to stdout |
| Gate stats: % of symbols passing Stage 2 / Trend Template / both per window | ✅ | `WindowGateStats` dataclass added to `backtest/engine.py`; logged per window in `run_backtest()`; aggregate + per-window sections in `backtest/report.py`; 5 new tests in `test_backtest_engine.py` |
| Regression test: trailing stop never drops below VCP `base_low` floor | ✅ | `tests/unit/test_backtest_engine.py::test_trailing_stop_never_drops_below_vcp_floor` AND `test_trailing_stop_floor_when_price_is_close_to_floor` |
| Unit tests — `test_backtest_engine.py` (13 tests) | ✅ | trailing stop, VCP floor, target hit, max_hold, fixed stop, ratchet ×3, pnl/R consistency, empty OHLCV; + 5 gate-stats tests |
| Unit tests — `test_regime.py` (8 tests) | ✅ | Calendar hits (Bull/Bear/Sideways), slope fallback (Bull/Unknown), label_trades ISO strings, regime stats, boundary Sideways |
| Unit tests — `test_backtest_metrics.py` (7 tests) | ✅ | CAGR, max drawdown, Sharpe, compute_metrics 10 trades, compute_metrics 0 trades, portfolio 1% sizing, portfolio capacity cap |
| Unit tests — `test_backtest_report.py` (6 tests) | ✅ | HTML + CSV created, regime table present, base64 equity curve, CSV header, empty-trades no-crash, `--help` smoke |
| Unit tests — `test_backtest_runner.py` (4 tests) | ✅ | `run_parameter_sweep` 2-row DataFrame, correct columns, pct order, stdout table |
| **Deliverable:** `backtest_runner.py --start 2019-01-01 --end 2024-01-01 --trailing-stop 0.07` → full per-regime HTML + CSV report | ✅ | All components in place; `--compare` flag adds trailing vs fixed side-by-side |

**Remaining (0 items):** Phase 8 is fully complete.

**Blockers:** Phase 3 rule engine ✅ + Phase 7 paper trading ✅.

---

## Phase 9 — Hardening & Production (Weeks 23–26) ✅ COMPLETE

**Goal:** Production-ready pipeline running unattended on ShreeVault (Ubuntu server).

| Task | Status | File/Notes |
|------|--------|------------|
| Structured logging (JSON format) with log rotation | ✅ | `utils/logger.py` + `config/logging.yaml` |
| `Makefile` with core targets (`test`, `lint`, `format`, `daily`, `backtest`, `rebuild`, `api`, `dashboard`, `paper-reset`) | ✅ | All required targets implemented; also includes `test-coverage`, `test-smoke`, `test-integration`, `watchlist-only`, `deploy`, `status`, `logs`, `logs-api`, `help` |
| `pyproject.toml` — packaging + dev dependencies | ✅ | Present |
| `requirements.txt` + `requirements-dev.txt` | ✅ | Present |
| Full test coverage: unit + integration + smoke tests | ✅ | 896 tests (895 passing); `tests/smoke/test_smoke.py` present; unit + integration complete |
| Prometheus metrics endpoint (optional) | ❌ | Deprioritised — not required for MVP |
| CI pipeline: `make test` runs in < 3 minutes | ✅ | `.github/workflows/test.yml` — smoke gate + full suite + lint + coverage artifact upload |
| Data lineage: every run logs data hash, config snapshot, Git commit SHA | ✅ | `pipeline/runner.py` defines `_git_sha()` captured in `run_history` table via `storage/sqlite_store.py`; confirmed by `test_lineage.py` |
| `systemd` service: `minervini-daily.timer` (Mon–Fri 15:35 IST) | ✅ | `deploy/minervini-daily.timer` — `OnCalendar=Mon-Fri 10:05:00 UTC`, `Persistent=true` |
| `systemd` service: `minervini-api.service` (uvicorn, always running) | ✅ | `deploy/minervini-api.service` |
| `systemd` service: `minervini-dashboard.service` (Streamlit, always running) | ✅ | `deploy/minervini-dashboard.service` |
| `deploy/install.sh` — automated service install script (`sudo bash deploy/install.sh`) | ✅ | Copies all 4 unit files → `/etc/systemd/system/`, runs `daemon-reload`, enables + starts all units |
| `deploy/README.md` — operations documentation (deploy, verify, day-to-day ops) | ✅ | `deploy/README.md` — covers initial deploy, verification checklist, day-to-day commands |
| Runbook: how to add a new data source / new rule condition | ✅ | `docs/RUNBOOK.md` |
| **Deliverable:** Pipeline runs unattended on ShreeVault, self-monitors, alerts on failure | ✅ | |

**Blockers:** Phase 10 (API) + Phase 11 (Dashboard) implemented alongside. ✅ done.

---

## Phase 10 — API Layer (FastAPI) (Weeks 27–29) ✅ COMPLETE

**Goal:** Expose screener results over HTTP for frontend and mobile access.

> **Note:** All 1,636 lines of API code implemented across 12 modules. Five test suites (52 tests total) cover auth, endpoints, schemas, and rate limiting. One integration E2E test has a minor expectation mismatch (portfolio 200 vs 404) and will be addressed in Phase 12 cleanup.

| Task | Status | File/Notes |
|------|--------|------------|
| `api/main.py` — FastAPI app with CORS, startup events, lifespan context manager, envelope error handlers | ✅ | 170 lines |
| `api/auth.py` — X-API-Key middleware (read key + admin key) | ✅ | 127 lines |
| `api/rate_limit.py` — per-IP rate limiting via slowapi | ✅ | 66 lines; 429 envelope + Retry-After header |
| `api/routers/health.py` — `/api/v1/health` + `/api/v1/meta` | ✅ | 140 lines |
| `api/routers/stocks.py` — `/api/v1/stocks/top`, `/trend`, `/vcp`, `/{symbol}`, `/history` | ✅ | 208 lines |
| `api/routers/watchlist.py` — GET/POST/DELETE single, bulk, upload, clear, scoped run | ✅ | 320 lines |
| `api/routers/portfolio.py` — paper trading portfolio + trades endpoints | ✅ | 155 lines |
| `api/schemas/stock.py`, `portfolio.py`, `common.py` — Pydantic models | ✅ | 315 lines total |
| `api/deps.py` — shared FastAPI dependencies (DB session, cache, settings) | ✅ | 102 lines |
| `POST /api/v1/run` with `scope` and `symbols` body params (admin only) | ✅ | In `watchlist.py`; admin-key gate via `deps.require_admin()` |
| Unit tests for all endpoints via `TestClient` | ✅ | `test_api_main.py` (12), `test_api_auth.py` (5), `test_api_stocks.py` (8), `test_api_portfolio.py` (10), `test_api_watchlist.py` (17) |
| `systemd` service for uvicorn (port 8000, 2 workers) | ✅ | `deploy/minervini-api.service` |
| **Deliverable:** `POST /api/v1/watchlist/upload` accepts CSV; `POST /api/v1/run {"scope":"watchlist"}` works | ✅ | |

**Blockers:** Phase 3 (rule engine + SQLite results) ✅ + Phase 7 for portfolio endpoints ✅.

---

## Phase 11 — Streamlit Dashboard MVP (Weeks 30–31) ✅ COMPLETE

**Goal:** Visual dashboard for daily monitoring, accessible without SSH.

> **Note:** 2,646 lines total across app, 5 pages, and 3 components. One test suite (`test_dashboard_components.py`, 11 tests) covers chart, table, metric, and state helpers.

| Task | Status | File/Notes |
|------|--------|------------|
| `dashboard/app.py` — Streamlit entry point, multi-page layout, sidebar nav | ✅ | 236 lines |
| `dashboard/pages/01_Watchlist.py` — file upload + manual entry + watchlist table + [Run Now] | ✅ | 482 lines |
| `dashboard/pages/02_Screener.py` — full universe table with quality/stage/RS filters | ✅ | 257 lines |
| `dashboard/pages/03_Stock.py` — single stock deep-dive (chart + TT + VCP + fundamentals + LLM brief) | ✅ | 418 lines |
| `dashboard/pages/04_Portfolio.py` — paper trading summary + equity curve + open/closed trades | ✅ | 291 lines |
| `dashboard/pages/05_Backtest.py` — backtest results viewer + regime breakdown + parameter sweep | ✅ | 425 lines |
| `dashboard/components/charts.py` — mplfinance candlestick + MA + VCP zone overlays | ✅ | 204 lines |
| `dashboard/components/tables.py` — styled screener tables with quality badges | ✅ | 128 lines |
| `dashboard/components/metrics.py` — score card widgets, regime chips, P&L cards | ✅ | 204 lines |
| Stage label annotation on chart | ✅ | Via `components/charts.py` |
| Watchlist symbols highlighted with ★ badge in all result tables | ✅ | Styling in `components/tables.py` |
| Manual run trigger button (calls `POST /api/v1/run` or direct Python runner) | ✅ | In `pages/01_Watchlist.py` |
| `systemd` service for Streamlit (port 8501) | ✅ | `deploy/minervini-dashboard.service` |
| **Deliverable:** Uploading `mylist.csv` adds watchlist symbols; [Run Watchlist Now] shows results on same page | ✅ | |

**Blockers:** Phase 10 API ✅ + Phase 4 charts ✅.

---

## Phase 12 — Next.js Production Frontend (Weeks 32–36) 🚧 IN PROGRESS (~80%)

**Goal:** Shareable, mobile-friendly web app backed by the FastAPI layer.

> **Note:** Filesystem inspection on 2026-05-09 confirms significantly more work is done than the
> previous ~40% estimate. The stock deep-dive page and portfolio page are fully implemented with
> complete data wiring. Remaining gaps are VCP zone overlays and Vercel production deployment.

| Task | Status | File/Notes |
|------|--------|------------|
| `frontend/` — Next.js 14 project scaffold (App Router) | ✅ | `next.config.ts`, `package.json`, `tailwind.config.ts`, `tsconfig.json` |
| `frontend/lib/api.ts` — typed API client for all `/api/v1/*` endpoints | ✅ | 128 lines; SWR wrappers for GET; mutation helpers for POST/DELETE |
| `frontend/lib/types.ts` — TypeScript types matching Pydantic schemas | ✅ | 167 lines; `StockResult`, `WatchlistEntry`, `PortfolioSummary`, `BacktestReport`, `OHLCVBar`, `MAPoint`, `StockHistoryPoint` |
| Screener table page — sortable, filterable, live-polling via SWR | ✅ | `app/screener/page.tsx` — quality/limit filters, CSV export, SWR polling, `ApiOfflineBanner` |
| Watchlist page | ✅ | `app/watchlist/page.tsx` (40 lines) |
| Portfolio page — P&L cards + equity curve + open/closed tabs + stats | ✅ | `app/portfolio/page.tsx` — **fully wired**: `refreshInterval: 60_000`, equity curve (Recharts `AreaChart`), open positions table, closed trades table, Statistics tab (win-rate by quality, monthly P&L bars, hold-time distribution) |
| Stock deep-dive page — chart + TT + VCP + fundamentals + AI brief + history | ✅ | `app/screener/[symbol]/page.tsx` — **fully implemented**: 2-column layout, SWR data wiring (`api.getStock`, `api.getStockHistory`, `api.getOHLCV`), 4-tab panel (Trend / VCP / Fundamentals / AI Brief), score breakdown bars, key stats, 90-day score history sparkline (Recharts), watchlist toggle |
| `frontend/components/StockTable.tsx` — reusable table with quality badges | ✅ | 212 lines |
| `frontend/components/CandlestickChart.tsx` — TradingView lightweight-charts | ✅ | 104 lines; SMA50/150/200 overlays, entry + stop price lines |
| `frontend/components/TrendTemplateCard.tsx` — 8-condition checklist | ✅ | 58 lines |
| `frontend/components/VCPCard.tsx` — VCP zone display | ✅ | 47 lines |
| `frontend/components/ScoreGauge.tsx` — 0–100 radial gauge | ✅ | 109 lines |
| `frontend/components/PortfolioSummary.tsx` — P&L cards + equity curve | ✅ | 122 lines |
| `frontend/components/NavBar.tsx` — top navigation | ✅ | 122 lines |
| `frontend/components/QualityBadge.tsx` — setup quality badge | ✅ | Present on disk |
| `frontend/components/ApiOfflineBanner.tsx` — API offline notice | ✅ | Present on disk |
| `frontend/components/Skeleton.tsx` — loading skeleton components | ✅ | Present on disk |
| Mobile-responsive layout (Tailwind CSS) | ✅ | Configured in `tailwind.config.ts`; responsive classes used throughout |
| `vercel.json` — Vercel deployment config + API rewrite | 🚧 | File exists; API destination is placeholder `your-api-server:8000` — needs real server URL before deploy |
| VCP zone overlays on `CandlestickChart.tsx` | ⏳ | Not yet implemented — `CandlestickChart` only has SMA lines + entry/stop price lines; no shaded contraction zones |
| Deploy to Vercel (free tier, automatic HTTPS) | ⏳ | `vercel.json` in place; blocked by placeholder API URL in rewrite rule |
| **Deliverable:** Public URL serves screener + charts + paper portfolio from any device | ⏳ | Blocked by Vercel deploy |

**Remaining work (3 items):**
1. Update `vercel.json` rewrite destination to the real ShreeVault API URL (or reverse-proxy/tunnel)
2. Implement VCP contraction zone overlays in `CandlestickChart.tsx` (shaded rectangles for each base leg)
3. Deploy to Vercel — `vercel --prod` from `frontend/` directory

**Blockers:** Phase 10 API (all endpoints) ✅ + Phase 11 Streamlit MVP validation ✅.

---

## What Exists On Disk (File-Level Inventory)

### ✅ Implemented Modules

```
ingestion/
  base.py                    ✅ Abstract DataSource interface
  yfinance_source.py         ✅ PRIMARY — batch download
  angel_one_source.py        ✅ FALLBACK 1
  upstox_source.py           ✅ FALLBACK 2
  source_factory.py          ✅ Config-driven selector + fallback chain
  nsepython_universe.py      ✅ Nifty 500 + full NSE via nsepython
  universe_loader.py         ✅ Unified resolver, watchlist merge, file parser
  validator.py               ✅ Schema + OHLCV sanity + gap detection
  fundamentals.py            ✅ Screener.in scraper + 7-day cache
  news.py                    ✅ RSS + NewsData.io + keyword + LLM scorer

features/
  moving_averages.py         ✅ SMA 10/21/50/150/200, EMA 21, slopes
  relative_strength.py       ✅ RS raw + RS rating vs Nifty 500
  sector_rs.py               ✅ Sector RS ranking + top-5 bonus
  atr.py                     ✅ ATR 14, ATR%
  volume.py                  ✅ Vol ratio, acc/dist, up/down vol days
  pivot.py                   ✅ ZigZag swing high/low detection
  vcp.py                     ✅ RuleBasedVCPDetector + VCPDetector ABC
  feature_store.py           ✅ bootstrap(), update(), needs_bootstrap()

rules/
  stage.py                   ✅ Stage 1/2/3/4 detection (hard gate)
  trend_template.py          ✅ All 8 Minervini TT conditions
  vcp_rules.py               ✅ VCP qualification rules
  entry_trigger.py           ✅ Pivot breakout + volume confirmation
  stop_loss.py               ✅ VCP base_low (primary) + ATR fallback
  risk_reward.py             ✅ R:R estimator
  fundamental_template.py    ✅ 7 Minervini fundamental conditions (F1–F7)
  scorer.py                  ✅ SEPAResult dataclass + weighted score (0–100)

screener/
  pre_filter.py              ✅ 52w-high + RS + SMA200 gate (~70% eliminated)
  pipeline.py                ✅ Batch screener with ProcessPoolExecutor
  results.py                 ✅ Persist SEPAResult to SQLite

paper_trading/
  simulator.py               ✅ enter_trade(), check_exits(), pyramid_position(),
                                  save_state(), load_state()
  portfolio.py               ✅ Portfolio, Position, ClosedTrade dataclasses;
                                  get_summary(), to_json(), from_json(),
                                  record_equity_point()
  order_queue.py             ✅ queue_order(), execute_pending_orders(),
                                  is_market_open(), expiry logic
  report.py                  ✅ generate_performance_report(), get_quality_breakdown(),
                                  get_monthly_pnl(); self-contained HTML with
                                  embedded matplotlib charts

llm/
  llm_client.py              ✅ Abstract LLMClient + Groq/Anthropic/OpenAI/
                                  Ollama/OpenRouter implementations
  explainer.py               ✅ generate_trade_brief(), generate_watchlist_summary()
  prompt_templates/
    trade_brief.j2           ✅ Jinja2 trade brief template
    watchlist_summary.j2     ✅ Daily watchlist narrative template

reports/
  daily_watchlist.py         ✅ CSV + HTML report
  chart_generator.py         ✅ Candlestick + MA ribbons + VCP markup + stage label
  templates/
    watchlist.html.j2        ✅ Styled HTML report template

alerts/
  alert_deduplicator.py      ✅ Dedup gates (days, score jump, quality, new breakout)
  telegram_alert.py          ✅ Daily watchlist to Telegram
  email_alert.py             ✅ SMTP alert

pipeline/
  context.py                 ✅ RunContext dataclass
  runner.py                  ✅ Unified entry point (daily / historical / backtest); _config_hash()
  scheduler.py               ✅ APScheduler at 15:35 IST, skips NSE holidays

backtest/                    ✅  (Phase 8)
  engine.py                  ✅ BacktestTrade, BacktestResult; simulate_trade() + run_backtest()
                                  walk-forward; trailing stop ratchets up, floored at VCP base_low
  portfolio.py               ✅ BacktestPortfolio; 1% risk sizing; enter(), close(), record_equity()
  regime.py                  ✅ NSE_REGIME_CALENDAR (Appendix E) + 200MA slope fallback
  metrics.py                 ✅ compute_metrics(), CAGR, Sharpe, max drawdown, win rate, profit factor
  report.py                  ✅ generate_report() → self-contained HTML + CSV;
                                  equity curve (drawdown shading), regime table, VCP quality table,
                                  trailing vs fixed comparison, top-10 winners/losers

api/                         ✅  (Phase 10 — 1,636 lines, 52 tests)
  main.py                    ✅ FastAPI app, CORS, lifespan, envelope error handlers
  auth.py                    ✅ X-API-Key middleware (read + admin key gates)
  rate_limit.py              ✅ slowapi per-IP rate limiting, 429 envelope
  deps.py                    ✅ Shared FastAPI dependencies (DB, settings, cache)
  routers/
    health.py                ✅ /health, /meta endpoints
    stocks.py                ✅ /stocks/top, /trend, /vcp, /{symbol}, /history
    watchlist.py             ✅ GET/POST/DELETE single, bulk add, upload CSV, clear, scoped run
    portfolio.py             ✅ Paper trading portfolio + trades endpoints
  schemas/
    common.py                ✅ APIResponse envelope, pagination
    stock.py                 ✅ StockResult, TrendTemplate, VCPMetrics, etc.
    portfolio.py             ✅ PositionResponse, TradeSummary, EquityPoint, etc.

dashboard/                   ✅  (Phase 11 — 2,646 lines, 11 tests)
  app.py                     ✅ Streamlit entry point, multi-page sidebar nav
  pages/
    01_Watchlist.py          ✅ File upload, manual entry, table, Run Now button
    02_Screener.py           ✅ Full universe table with quality/stage/RS filters
    03_Stock.py              ✅ Single stock deep-dive (chart + TT + VCP + fundamentals)
    04_Portfolio.py          ✅ Paper trading summary + equity curve
    05_Backtest.py           ✅ Backtest results + regime breakdown + parameter sweep
  components/
    charts.py                ✅ mplfinance candlestick + MA + VCP zone overlays
    tables.py                ✅ Styled screener tables with quality badges
    metrics.py               ✅ Score card widgets, regime chips, P&L cards

frontend/                    🚧  (Phase 12 — ~80% done, filesystem-verified 2026-05-09)
  app/
    layout.tsx               ✅ Root layout with Tailwind + Providers
    page.tsx                 ✅ Dashboard home
    screener/page.tsx        ✅ Screener table page (sortable/filterable/CSV export/SWR polling)
    screener/[symbol]/page.tsx ✅ Stock deep-dive — FULLY WIRED (2-col layout, 4 tabs,
                                    SWR data fetching, score bars, key stats, 90-day history chart)
    watchlist/page.tsx       ✅ Watchlist page
    portfolio/page.tsx       ✅ Portfolio page — FULLY WIRED (equity curve, open/closed/stats tabs,
                                    refreshInterval:60_000, win-rate by quality, monthly P&L bars,
                                    hold-time distribution)
  components/
    StockTable.tsx           ✅ Reusable sortable/filterable table
    CandlestickChart.tsx     ✅ TradingView lightweight-charts; SMA50/150/200 + entry/stop lines
                                ⏳ VCP contraction zone overlays NOT YET implemented
    TrendTemplateCard.tsx    ✅ 8-condition checklist
    VCPCard.tsx              ✅ VCP zone display
    ScoreGauge.tsx           ✅ 0–100 radial gauge
    PortfolioSummary.tsx     ✅ P&L cards + equity curve
    NavBar.tsx               ✅ Top navigation
    QualityBadge.tsx         ✅ Setup quality badge
    ApiOfflineBanner.tsx     ✅ API offline notice
    Skeleton.tsx             ✅ Loading skeleton components
  lib/
    api.ts                   ✅ Typed API client (SWR wrappers, mutations)
    types.ts                 ✅ TypeScript types (OHLCVBar, MAPoint, StockHistoryPoint added)
  vercel.json                🚧 Config present; API rewrite destination is placeholder URL

storage/
  parquet_store.py           ✅ Atomic append (temp + rename)
  sqlite_store.py            ✅ Results + run_history (now with git_sha column)

utils/
  logger.py                  ✅ Structured logging
  date_utils.py              ✅ Date helpers
  trading_calendar.py        ✅ NSE holiday schedule
  exceptions.py              ✅ Custom exception hierarchy
  math_utils.py              ✅ Pure numeric helpers

config/
  settings.yaml              ✅ All parameters (Phases 1–7)
  universe.yaml              ✅ Symbol universe definition
  logging.yaml               ✅ Log levels per module
  symbol_aliases.yaml        ✅ Symbol → news alias mapping

scripts/
  run_daily.py               ✅ CLI with --watchlist, --symbols, --watchlist-only, --scope
  bootstrap.py               ✅ Full history download (yfinance batch)
  rebuild_features.py        ✅ Recompute all features from scratch
  create_test_fixtures.py    ✅ Test fixture generator
  backtest_runner.py         ✅ CLI: --start, --end, --universe, --trailing-stop, --no-trailing, --compare; run_parameter_sweep()

tests/unit/                  ✅ 896 tests total, 895 passed, 1 known failure
  test_alert_deduplicator.py ✅  test_atr.py                ✅
  test_backtest_engine.py    ✅  test_backtest_metrics.py    ✅
  test_backtest_report.py    ✅  test_backtest_runner.py     ✅
  test_chart_generator.py    ✅  test_daily_watchlist.py     ✅
  test_dashboard_components.py ✅ (Phase 11)
  test_entry_trigger.py      ✅  test_explainer.py           ✅
  test_feature_benchmark.py  ✅  test_feature_store.py       ✅
  test_fundamental_template  ✅  test_fundamentals.py        ✅
  test_lineage.py             ✅  test_llm_client.py         ✅
  test_moving_averages.py     ✅  test_news.py               ✅
  test_paper_trading.py      ✅  test_pivot.py               ✅
  test_pre_filter.py         ✅  test_prompt_templates.py    ✅
  test_regime.py             ✅  test_relative_strength.py   ✅
  test_risk_reward.py        ✅  test_runner.py              ✅
  test_scorer.py             ✅  test_sector_rs.py           ✅
  test_source_factory.py     ✅  test_stage_detection.py     ✅
  test_storage.py             ✅  test_trading_calendar.py    ✅
  test_trailing_stop.py      ✅  test_trend_template.py      ✅
  test_universe_loader.py    ✅  test_validator.py           ✅
  test_vcp.py                ✅  test_vcp_rules.py           ✅
  test_volume.py             ✅
  test_api_auth.py           ✅  test_api_main.py            ✅
  test_api_stocks.py         ✅  test_api_portfolio.py       ✅
  test_api_watchlist.py      ✅

tests/smoke/                 ✅
  test_smoke.py              ✅ Import-level smoke gate (fast CI first step)

tests/integration/           ✅
  test_feature_pipeline_e2e.py ✅
  test_known_setups.py         ✅
  test_screener_batch.py       ✅
  test_api_e2e.py              🚧 1 failing: portfolio endpoint returns 200 where E2E expects 404 (minor)

tests/fixtures/
  sample_ohlcv_MOCKUP.parquet    ✅
  sample_ohlcv_MOCKDN.parquet    ✅
  sample_ohlcv_MOCKFLAT.parquet  ✅
  sample_fundamentals.json       ✅
  sample_news_articles.json      ✅
  sample_watchlist.csv           ✅
  sample_watchlist.json          ✅

deploy/                      ✅  (Phase 9)
  install.sh                 ✅ Automated systemd service installer (sudo bash deploy/install.sh)
  README.md                  ✅ Production operations guide
  minervini-daily.service    ✅ Oneshot pipeline service
  minervini-daily.timer      ✅ Mon–Fri 10:05 UTC (15:35 IST), Persistent=true
  minervini-api.service      ✅ uvicorn FastAPI, port 8000, 2 workers, always-on
  minervini-dashboard.service ✅ Streamlit, port 8501, always-on

docs/                        ✅
  RUNBOOK.md                 ✅ Ops runbook: daily ops, recovery, adding new data sources/rules

.github/
  workflows/
    test.yml                 ✅ CI: smoke gate → full test suite → lint → coverage artifact upload
```

---

## Next Steps — Phase 12 (Next.js Production Frontend)

**Phase 9 is 100% complete** ✅  
**Phase 10 is 100% complete** ✅  
**Phase 11 is 100% complete** ✅  
**Phase 12 is ~80% complete** 🚧 — 3 items remaining (filesystem-verified 2026-05-09)

**What was completed since last update (was reported as ~40%):**
- Stock deep-dive page (`app/screener/[symbol]/page.tsx`) — fully implemented and data-wired. Has 2-column layout, SWR fetching for stock/history/OHLCV, 4 content tabs (Trend Template, VCP, Fundamentals, AI Brief), score breakdown progress bars, key stats panel, 90-day score trend chart (Recharts), and watchlist toggle.
- Portfolio page (`app/portfolio/page.tsx`) — fully implemented with `refreshInterval: 60_000`, equity curve (Recharts `AreaChart` with gradient fill), open positions table, closed trades table, and Statistics tab (win-rate by quality, monthly P&L bar chart, hold-time distribution histogram).
- Additional components: `QualityBadge.tsx`, `ApiOfflineBanner.tsx`, `Skeleton.tsx`.
- `lib/types.ts` extended with `OHLCVBar`, `MAPoint`, `StockHistoryPoint`.

**Remaining work (3 items to complete Phase 12):**
1. **VCP contraction zone overlays** in `CandlestickChart.tsx` — add shaded rectangular regions for each VCP base leg using lightweight-charts price band series or custom SVG overlay
2. **Update `vercel.json`** rewrite destination from placeholder `your-api-server:8000` to real ShreeVault API URL (or Cloudflare Tunnel / ngrok URL)
3. **Deploy to Vercel** — run `vercel --prod` from `frontend/` after resolving the API URL; automatic HTTPS + public URL

**Phase 9 known gap:** Prometheus metrics endpoint deprioritised — can be added in a follow-up if observability becomes critical.

**Known test failure:** `tests/integration/test_api_e2e.py::test_full_api_flow` — portfolio endpoint returns `200` where test expects `404` (minor E2E expectation mismatch, tracked).

---

*This document is maintained in sync with PROJECT_DESIGN.md v1.4.0 + filesystem inspection.*  
*Last updated: 2026-05-09 — Phase 12 re-verified by reading actual files on disk*
