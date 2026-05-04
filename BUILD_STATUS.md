# BUILD_STATUS.md
# Minervini SEPA Stock Analysis System — Build Status

> **Last Updated:** 2026-05-04
> **Version:** 1.0.0
> **Reference Design:** PROJECT_DESIGN.md v1.4.0
> **Python:** 3.11 | **Test Suite:** 586 tests, 0 failures

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
| **Phase 8** | Backtesting Engine | ⏳ NOT STARTED | 0% |
| **Phase 9** | Hardening & Production | 🚧 **PARTIAL** | 30% |
| **Phase 10** | API Layer (FastAPI) | ⏳ NOT STARTED | 0% |
| **Phase 11** | Streamlit Dashboard MVP | ⏳ NOT STARTED | 0% |
| **Phase 12** | Next.js Production Frontend | ⏳ NOT STARTED | 0% |

**Overall Project Completion: ~55%** (Phases 1–7 complete + infrastructure scaffolding)

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
| `screener/results.py` — persist `SEPAResult` list to SQLite | ✅ | |
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
| Unit test: `get_summary` win rate, profit factor, avg R-multiple, zero-division safety | ✅ | Tests 16–19 |
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

## Phase 8 — Backtesting Engine (Weeks 19–22) ⏳ NOT STARTED

**Goal:** Validate strategy performance on historical data with realistic trade simulation.

| Task | Status | File/Notes |
|------|--------|------------|
| `backtest/engine.py` — walk-forward backtester (no lookahead bias) | ❌ | `backtest/` dir is stub only |
| `backtest/portfolio.py` — position sizing (1R = 1% portfolio), max 10 open positions | ❌ | |
| Trailing stop in `simulate_trade()` — `trailing_stop_pct`, floored at VCP `base_low` | ❌ | |
| `backtest/regime.py` — Bull/Bear/Sideways labelling (NSE calendar + 200MA slope fallback) | ❌ | |
| `backtest/metrics.py` — CAGR, Sharpe, max drawdown, win rate, avg R-multiple, profit factor | ❌ | |
| `backtest/report.py` — HTML + CSV with equity curve, regime table, VCP quality breakdown | ❌ | |
| `scripts/backtest_runner.py` — CLI: date range, universe, strategy config, trailing stop toggle | ❌ | |
| Parameter sweep: `trailing_stop_pct` (5%, 7%, 10%, 15%) vs fixed stop | ❌ | |
| Gate stats: % of symbols passing Stage 2 / Trend Template / both per window | ❌ | |
| Regression test: trailing stop never drops below VCP floor | ❌ | |
| **Deliverable:** `backtest_runner.py --start 2019-01-01 --end 2024-01-01 --trailing-stop 0.07` → full per-regime report | ❌ | |

**Blockers:** Phase 3 rule engine ✅ + Phase 7 paper trading results (for calibration) ✅ recommended first.

---

## Phase 9 — Hardening & Production (Weeks 23–26) 🚧 PARTIAL (~30%)

**Goal:** Production-ready pipeline running unattended on ShreeVault (Ubuntu server).

| Task | Status | File/Notes |
|------|--------|------------|
| Structured logging (JSON format) with log rotation | ✅ | `utils/logger.py` + `config/logging.yaml` |
| `Makefile` with core targets (`test`, `lint`, `format`, `daily`, `backtest`, `rebuild`, `api`, `dashboard`, `paper-reset`) | 🚧 | `Makefile` exists; not all targets implemented yet |
| `pyproject.toml` — packaging + dev dependencies | ✅ | Present |
| `requirements.txt` + `requirements-dev.txt` | ✅ | Present |
| Full test coverage: unit + integration + smoke tests | 🚧 | 586 tests pass; smoke tests not yet written |
| Prometheus metrics endpoint (optional) | ❌ | |
| CI pipeline: `make test` runs in < 3 minutes | ❌ | No CI config (`.github/workflows/`) |
| Data lineage: every run logs data hash, config snapshot, Git commit SHA | ❌ | `run_history` table schema designed but not wired |
| `systemd` service: `minervini-daily.timer` (Mon–Fri 15:35 IST) | ❌ | |
| `systemd` service: `minervini-api.service` (uvicorn, always running) | ❌ | |
| `systemd` service: `minervini-dashboard.service` (Streamlit, always running) | ❌ | |
| Runbook: how to add a new data source / new rule condition | ❌ | |
| **Deliverable:** Pipeline runs unattended on ShreeVault, self-monitors, alerts on failure | ❌ | Depends on Phases 4 ✅, 10, 11 |

---

## Phase 10 — API Layer (FastAPI) (Weeks 27–29) ⏳ NOT STARTED

**Goal:** Expose screener results over HTTP for frontend and mobile access.

| Task | Status | File/Notes |
|------|--------|------------|
| `api/main.py` — FastAPI app with CORS, startup events | ❌ | `api/` has `__init__.py` + empty `routers/` + `schemas/` dirs |
| `api/auth.py` — X-API-Key middleware (read key + admin key) | ❌ | |
| `api/rate_limit.py` — per-IP rate limiting via slowapi | ❌ | |
| `api/routers/stocks.py` — `/api/v1/stocks/top`, `/trend`, `/vcp`, `/{symbol}`, `/history` | ❌ | |
| `api/routers/watchlist.py` — GET/POST/DELETE single, bulk, upload, clear, scoped run | ❌ | |
| `api/routers/portfolio.py` — paper trading portfolio + trades endpoints | ❌ | |
| `api/routers/health.py` — `/api/v1/health` + `/api/v1/meta` | ❌ | |
| `api/schemas/stock.py`, `portfolio.py`, `common.py` — Pydantic models | ❌ | `api/schemas/` dir is empty |
| `api/deps.py` — shared FastAPI dependencies (DB session, cache) | ❌ | |
| `POST /api/v1/run` with `scope` and `symbols` body params (admin only) | ❌ | |
| Unit tests for all endpoints via `TestClient` | ❌ | |
| `systemd` service for uvicorn (port 8000, 2 workers) | ❌ | Covered in Phase 9 |
| **Deliverable:** `POST /api/v1/watchlist/upload` accepts CSV; `POST /api/v1/run {"scope":"watchlist"}` works | ❌ | |

**Blockers:** Phase 3 (rule engine + SQLite results) ✅ + Phase 7 for portfolio endpoints ✅.

---

## Phase 11 — Streamlit Dashboard MVP (Weeks 30–31) ⏳ NOT STARTED

**Goal:** Visual dashboard for daily monitoring, accessible without SSH.

| Task | Status | File/Notes |
|------|--------|------------|
| `dashboard/app.py` — Streamlit entry point, multi-page layout | ❌ | `dashboard/` has `__init__.py` + empty `pages/` + `components/` |
| `dashboard/pages/01_Watchlist.py` — file upload + manual entry + watchlist table + [Run Now] | ❌ | |
| `dashboard/pages/02_Screener.py` — full universe table with quality/stage/RS filters | ❌ | |
| `dashboard/pages/03_Stock.py` — single stock deep-dive (chart + TT + VCP + fundamentals + LLM brief) | ❌ | |
| `dashboard/pages/04_Portfolio.py` — paper trading summary + equity curve | ❌ | |
| `dashboard/pages/05_Backtest.py` — backtest results viewer + regime breakdown | ❌ | |
| `dashboard/components/charts.py` — mplfinance candlestick + MA + VCP zone overlays | ❌ | |
| `dashboard/components/tables.py`, `metrics.py` — styled tables + score card widgets | ❌ | |
| Stage label annotation on chart | ❌ | |
| Watchlist symbols highlighted with ★ badge in all result tables | ❌ | |
| Manual run trigger button (calls `POST /api/v1/run`) | ❌ | Depends on Phase 10 API |
| `systemd` service for Streamlit (port 8501) | ❌ | Covered in Phase 9 |
| **Deliverable:** Uploading `mylist.csv` adds watchlist symbols; [Run Watchlist Now] shows results on same page | ❌ | |

**Blockers:** Phase 10 API (for manual-run button) + Phase 4 charts ✅.

---

## Phase 12 — Next.js Production Frontend (Weeks 32–36) ⏳ NOT STARTED

**Goal:** Shareable, mobile-friendly web app backed by the FastAPI layer.

| Task | Status | File/Notes |
|------|--------|------------|
| `frontend/` — Next.js 14 project scaffold (App Router) | ❌ | No `frontend/` directory exists yet |
| `frontend/lib/api.ts` — typed API client for all `/api/v1/*` endpoints | ❌ | |
| `frontend/lib/types.ts` — TypeScript types matching Pydantic schemas | ❌ | |
| Screener table page — sortable, filterable, live-polling via SWR | ❌ | |
| Stock deep-dive page — TradingView lightweight-charts candlestick + MA ribbons | ❌ | |
| VCP zone overlays on chart | ❌ | |
| Trend Template checklist card (8 conditions, pass/fail badges) | ❌ | |
| Fundamental scorecard card (7 conditions) | ❌ | |
| Score gauge widget (0–100 visual indicator) | ❌ | |
| Paper trading portfolio page — P&L cards + equity curve (Recharts) | ❌ | |
| Mobile-responsive layout (Tailwind CSS) | ❌ | |
| Deploy to Vercel (free tier, automatic HTTPS) | ❌ | |
| **Deliverable:** Public URL serves screener + charts + paper portfolio from any device | ❌ | |

**Blockers:** Phase 10 API (all endpoints) + Phase 11 Streamlit MVP validation must be complete first.


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
  runner.py                  ✅ Unified entry point (daily / historical / backtest)
  scheduler.py               ✅ APScheduler at 15:35 IST, skips NSE holidays

storage/
  parquet_store.py           ✅ Atomic append (temp + rename)
  sqlite_store.py            ✅ Results + run_history

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

tests/unit/                  ✅ 586 tests, 0 failures
  test_alert_deduplicator.py ✅  test_atr.py                ✅
  test_chart_generator.py    ✅  test_daily_watchlist.py     ✅
  test_entry_trigger.py      ✅  test_explainer.py           ✅
  test_feature_benchmark.py  ✅  test_feature_store.py       ✅
  test_fundamental_template  ✅  test_fundamentals.py        ✅
  test_llm_client.py         ✅  test_moving_averages.py     ✅
  test_news.py               ✅  test_paper_report.py        ✅
  test_paper_trading.py      ✅  test_pivot.py               ✅
  test_pre_filter.py         ✅  test_prompt_templates.py    ✅
  test_relative_strength.py  ✅  test_risk_reward.py         ✅
  test_runner.py             ✅  test_scorer.py              ✅
  test_sector_rs.py          ✅  test_source_factory.py      ✅
  test_stage_detection.py    ✅  test_stop_loss.py           ✅
  test_storage.py            ✅  test_trading_calendar.py    ✅
  test_trailing_stop.py      ✅  test_trend_template.py      ✅
  test_universe_loader.py    ✅  test_validator.py           ✅
  test_vcp.py                ✅  test_vcp_rules.py           ✅
  test_volume.py             ✅

tests/integration/           ✅
  test_feature_pipeline_e2e.py ✅
  test_known_setups.py         ✅
  test_screener_batch.py       ✅

tests/fixtures/
  sample_ohlcv_MOCKUP.parquet    ✅
  sample_ohlcv_MOCKDN.parquet    ✅
  sample_ohlcv_MOCKFLAT.parquet  ✅
  sample_fundamentals.json       ✅
  sample_news_articles.json      ✅
  sample_watchlist.csv           ✅
  sample_watchlist.json          ✅
```

### 📁 Stub Directories (Phase 8+)

```
backtest/          __init__.py only  (Phase 8)
api/               __init__.py + empty routers/ + schemas/  (Phase 10)
dashboard/         __init__.py + empty pages/ + components/ (Phase 11)
```

### ❌ Entirely Missing (no directory either)

```
frontend/                  (Phase 12 — Next.js, not expected yet)
scripts/backtest_runner.py (Phase 8)
```

---

## Next Steps — Phase 8 (Backtesting Engine)

**To start Phase 8**, implement in order:
1. `backtest/regime.py` — NSE Bull/Bear/Sideways calendar + 200MA slope fallback
2. `backtest/portfolio.py` — position sizing (1R = 1% portfolio), max 10 open positions
3. `backtest/engine.py` — walk-forward backtester, trailing stop logic, no lookahead bias
4. `backtest/metrics.py` — CAGR, Sharpe, max drawdown, win rate, profit factor, expectancy
5. `backtest/report.py` — HTML + CSV report with equity curve + regime breakdown table
6. `scripts/backtest_runner.py` — CLI: `--start`, `--end`, `--universe`, `--trailing-stop`
7. Parameter sweep: trailing_stop_pct 5/7/10/15% vs fixed stop
8. Gate stats: % passing Stage 2 / Trend Template / both per window
9. Regression: trailing stop never drops below VCP `base_low` floor

---

*This document is maintained in sync with PROJECT_DESIGN.md v1.4.0 + filesystem inspection.*
*Last updated: 2026-05-04*
