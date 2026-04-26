# BUILD_STATUS.md
# Minervini SEPA Stock Analysis System — Build Status

> **Last Updated:** 2026-04-26
> **Version:** 1.0.0
> **Reference Design:** PROJECT_DESIGN.md v1.4.0
> **Python:** 3.11 | **Test Suite:** 118 tests, 0 failures

---

## Quick Summary

| Phase | Title | Status | Progress |
|-------|-------|--------|----------|
| **Phase 1** | Foundation — Data Ingestion & Storage | ✅ **COMPLETE** | 100% |
| **Phase 2** | Feature Engineering | ⏳ NOT STARTED | 0% |
| **Phase 3** | Rule Engine (SEPA Logic) | ⏳ NOT STARTED | 0% |
| **Phase 4** | Reports, Charts, Alerts & Early Paper Trading | ⏳ NOT STARTED | 0% |
| **Phase 5** | Fundamentals & News Sentiment | ⏳ NOT STARTED | 0% |
| **Phase 6** | LLM Narrative Layer | ⏳ NOT STARTED | 0% |
| **Phase 7** | Paper Trading Simulator | ⏳ NOT STARTED | 0% |
| **Phase 8** | Backtesting Engine | ⏳ NOT STARTED | 0% |
| **Phase 9** | Hardening & Production | 🚧 **PARTIAL** | 25% |
| **Phase 10** | API Layer (FastAPI) | ⏳ NOT STARTED | 0% |
| **Phase 11** | Streamlit Dashboard MVP | ⏳ NOT STARTED | 0% |
| **Phase 12** | Next.js Production Frontend | ⏳ NOT STARTED | 0% |

**Overall Project Completion: ~10%** (Phase 1 + infrastructure scaffolding done)

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

## Phase 2 — Feature Engineering (Weeks 4–6) ⏳ NOT STARTED

**Goal:** All Minervini-relevant indicators computed and stored.

| Task | Status | File/Notes |
|------|--------|------------|
| `features/moving_averages.py` — SMA 10/21/50/150/200, EMA 21, slopes | ❌ | `features/` dir is stub only |
| `features/relative_strength.py` — RS raw + RS rating vs Nifty 500 | ❌ | |
| `features/sector_rs.py` — sector-level RS ranking, top-5 bonus (+5 pts) | ❌ | |
| `features/atr.py` — ATR 14, ATR% | ❌ | |
| `features/volume.py` — vol ratio, acc/dist, up/down vol days | ❌ | |
| `features/pivot.py` — swing high/low detection (ZigZag) | ❌ | |
| `features/vcp.py` — `RuleBasedVCPDetector` + `VCPDetector` ABC | ❌ | |
| `features/feature_store.py` — `bootstrap()`, `update()`, `needs_bootstrap()` | ❌ | |
| `screener/pre_filter.py` — 52w-high + RS + SMA200 gate (eliminates ~70%) | ❌ | `screener/` dir is stub only |
| Unit tests — all feature modules with fixture data | ❌ | `tests/fixtures/sample_ohlcv.parquet` missing |
| Benchmark: bootstrap 500 symbols < 15 min; daily update < 30 sec | ❌ | |
| **Deliverable:** `data/features/RELIANCE.parquet` with all indicators; `update()` < 50ms/symbol | ❌ | |

**Blockers:** Phase 1 must be complete (✅ done).


---

## Phase 3 — Rule Engine (Weeks 7–9) ⏳ NOT STARTED

**Goal:** Deterministic, fully testable SEPA screening logic.

| Task | Status | File/Notes |
|------|--------|------------|
| `rules/stage.py` — Stage 1/2/3/4 detection with confidence score (hard gate, runs first) | ❌ | `rules/` dir is stub only |
| `rules/trend_template.py` — all 8 conditions, configurable thresholds | ❌ | |
| `rules/vcp_rules.py` — VCP qualification rules | ❌ | |
| `rules/entry_trigger.py` — pivot breakout detection + volume confirmation | ❌ | |
| `rules/stop_loss.py` — VCP `base_low` (primary) + ATR fallback | ❌ | |
| `rules/risk_reward.py` — R:R estimator using nearest resistance | ❌ | |
| `rules/scorer.py` — `SEPAResult` dataclass + weighted composite score (0–100) | ❌ | |
| `screener/pipeline.py` — batch screener with `ProcessPoolExecutor` | ❌ | `screener/` dir is stub only |
| `screener/results.py` — persist `SEPAResult` list to SQLite | ❌ | |
| Unit tests: Stage detection with synthetic MA data | ❌ | `tests/unit/test_stage_detection.py` missing |
| Unit tests: Trend Template (each of 8 conditions pass/fail) | ❌ | `tests/unit/test_trend_template.py` missing |
| Unit tests: VCP rules with known patterns | ❌ | `tests/unit/test_vcp_rules.py` missing |
| Regression test: Stage 4 stock scores FAIL even if 8/8 TT conditions pass | ❌ | `tests/integration/test_known_setups.py` missing |
| Integration test: screen Nifty 500 on historical date, verify known setups | ❌ | |
| **Deliverable:** `run_daily.py --date 2024-01-15` produces ranked watchlist; non-Stage-2 filtered | ❌ | |

**Blockers:** Phase 2 must be complete (feature store needed as input to rule engine).


---

## Phase 4 — Reports, Charts, Alerts & Early Paper Trading (Weeks 10–12) ⏳ NOT STARTED

**Goal:** Human-consumable outputs, alert dispatch, and early paper trading for signal validation.

> **Note:** Paper trading starts here (not Phase 7) to generate real signal data sooner for backtesting calibration.

| Task | Status | File/Notes |
|------|--------|------------|
| `reports/daily_watchlist.py` — CSV + HTML report | ❌ | `reports/` dir has stub + empty `templates/` |
| `reports/chart_generator.py` — candlestick + MA ribbons + VCP markup + stage annotation | ❌ | |
| `reports/templates/watchlist.html.j2` — styled HTML template | ❌ | |
| `alerts/alert_deduplicator.py` — dedup gates (days, score jump, quality, new breakout) | ❌ | `alerts/` dir is stub only |
| `alerts/telegram_alert.py` — daily watchlist to Telegram (with deduplication) | ❌ | |
| `alerts/email_alert.py` — optional SMTP summary | ❌ | |
| `pipeline/scheduler.py` — APScheduler at 15:35 IST, skips NSE holidays | ❌ | `pipeline/context.py` ✅ exists |
| `pipeline/runner.py` — unified entry point (daily / historical / backtest modes) | ❌ | |
| `paper_trading/simulator.py` — `enter_trade()`, `exit_position()`, slippage model (0.15%) | ❌ | `paper_trading/` dir is stub only |
| `paper_trading/portfolio.py` — portfolio state + P&L tracking | ❌ | |
| `paper_trading/order_queue.py` — market-hours aware pending order queue | ❌ | |
| `data/paper_trading/` directory structure (portfolio.json, trades.json, pending_orders.json) | ❌ | Dir exists, `.gitkeep` only |
| **Deliverable:** Telegram message at 15:35 IST with A+/A setups + chart images; no duplicate alerts | ❌ | |

**Blockers:** Phase 3 rule engine must be complete (needs `SEPAResult` as input).


---

## Phase 5 — Fundamentals & News Sentiment (Weeks 13–14) ⏳ NOT STARTED

**Goal:** Add Minervini fundamental conditions and news sentiment as scoring inputs.

| Task | Status | File/Notes |
|------|--------|------------|
| `ingestion/fundamentals.py` — Screener.in scraper with 7-day cache | ❌ | Not yet in `ingestion/` |
| `rules/fundamental_template.py` — 7 Minervini fundamental conditions (F1–F7) | ❌ | |
| Unit tests: fundamental template (known PE/ROE/EPS values → expected pass/fail) | ❌ | `tests/unit/test_fundamentals.py` missing |
| `ingestion/news.py` — RSS + NewsData.io + keyword scorer + LLM re-scorer | ❌ | |
| `config/symbol_aliases.yaml` — symbol → alias list for news matching | ✅ | Already created in Phase 1 |
| Wire `fundamental_score` + `news_score` into `rules/scorer.py` composite | ❌ | Depends on Phase 3 scorer |
| Update HTML report to show fundamental conditions per candidate | ❌ | Depends on Phase 4 reports |
| Update Telegram alert to include fundamental summary line | ❌ | Depends on Phase 4 alerts |
| Unit test: fundamental template with `None` data → graceful fail (no crash) | ❌ | `tests/unit/test_fundamentals.py` missing |
| Unit test: news keyword fallback works without LLM | ❌ | `tests/unit/test_news.py` missing |
| `data/fundamentals/` — 7-day TTL cache store | ⏳ | Dir exists, `.gitkeep` only |
| `data/news/` — 30-min TTL cache store | ⏳ | Dir exists, `.gitkeep` only |
| **Deliverable:** A+/A setups show EPS acceleration, ROE, promoter holding, news score in report | ❌ | |

**Blockers:** Phase 3 (scorer wiring) + Phase 4 (report/alert templates) should be done first.


---

## Phase 6 — LLM Narrative Layer (Weeks 15–16) ⏳ NOT STARTED

**Goal:** AI-generated trade briefs as an optional overlay.

| Task | Status | File/Notes |
|------|--------|------------|
| `llm/llm_client.py` — abstract `LLMClient` base class | ❌ | `llm/` has `__init__.py` + empty `prompt_templates/` |
| `llm/explainer.py` — `generate_trade_brief()` + `generate_watchlist_summary()` | ❌ | |
| `llm/prompt_templates/trade_brief.j2` — Jinja2 trade brief template | ❌ | `prompt_templates/` dir is empty |
| `llm/prompt_templates/watchlist_summary.j2` — daily watchlist narrative template | ❌ | |
| `GroqClient` implementation (default — free, fast) | ❌ | |
| `AnthropicClient` implementation | ❌ | |
| `OpenAIClient` implementation | ❌ | |
| `OllamaClient` — local model fallback (zero API cost) | ❌ | |
| `OpenRouterClient` — deepseek-r1:free for best reasoning | ❌ | |
| Add narrative field to HTML report | ❌ | Depends on Phase 4 reports |
| Token cost logging per run | ❌ | |
| Graceful degradation — LLM failure skips narrative, logs warning, pipeline continues | ❌ | |
| **Deliverable:** HTML report includes 3-sentence AI trade brief for each A+/A setup via Groq | ❌ | |

**Blockers:** Phase 3 (`SEPAResult`) + Phase 4 (report rendering) must be done first.


---

## Phase 7 — Paper Trading Simulator (Weeks 17–18) ⏳ NOT STARTED

**Goal:** Full paper trading with pyramiding and market-hours-aware order execution.

> **Note:** Basic paper trading (enter/exit + slippage) is started in Phase 4. Phase 7 adds pyramiding,
> full order queue, and performance reports.

| Task | Status | File/Notes |
|------|--------|------------|
| `paper_trading/simulator.py` — full `enter_trade()`, `exit_position()`, `check_exits()` | ❌ | Stub only from Phase 4 start |
| `paper_trading/portfolio.py` — portfolio state, P&L, win rate, `get_portfolio_summary()` | ❌ | |
| `paper_trading/order_queue.py` — `queue_order()` + `execute_pending_orders()` at 9:15 IST | ❌ | |
| `paper_trading/report.py` — return, win rate, avg R-multiple performance summary | ❌ | |
| Pyramiding logic — add to winning VCP Grade A positions (50% qty, one add, `pyramided` flag) | ❌ | |
| Wire into `pipeline/runner.py` — auto paper trade after daily screen | ❌ | Depends on Phase 4 runner |
| Unit tests: enter/exit/pyramid with known prices | ❌ | |
| Unit test: trailing stop never drops below VCP floor | ❌ | |
| `data/paper_trading/portfolio.json`, `trades.json`, `pending_orders.json` | ❌ | Dir exists, files not yet created |
| **Deliverable:** A+/A signals auto-create paper trades; portfolio persisted; run 4–8 weeks | ❌ | |

**Blockers:** Phase 4 (basic simulator + runner) must be in place first.


---

## Phase 8 — Backtesting Engine (Weeks 19–22) ⏳ NOT STARTED

**Goal:** Validate strategy performance on historical data with realistic trade simulation.

| Task | Status | File/Notes |
|------|--------|------------|
| `backtest/engine.py` — walk-forward backtester (no lookahead bias) | ❌ | `backtest/` dir is stub only |
| `backtest/portfolio.py` — position sizing (1R = 1% portfolio), max 10 open positions | ❌ | |
| Trailing stop logic in `simulate_trade()` — `trailing_stop_pct`, floored at VCP `base_low` | ❌ | |
| `backtest/regime.py` — Bull/Bear/Sideways labelling (NSE calendar + 200MA slope fallback) | ❌ | |
| `backtest/metrics.py` — CAGR, Sharpe, max drawdown, win rate, avg R-multiple, profit factor | ❌ | |
| `backtest/report.py` — HTML + CSV with equity curve, regime table, VCP quality breakdown | ❌ | |
| `scripts/backtest_runner.py` — CLI: date range, universe, strategy config, trailing stop toggle | ❌ | |
| Parameter sweep: `trailing_stop_pct` (5%, 7%, 10%, 15%) vs fixed stop | ❌ | |
| Gate stats: % of symbols passing Stage 2 / Trend Template / both per window | ❌ | |
| Regression test: trailing stop never drops below VCP floor | ❌ | |
| **Deliverable:** `backtest_runner.py --start 2019-01-01 --end 2024-01-01 --trailing-stop 0.07` produces full per-regime report | ❌ | |

**Blockers:** Phase 3 rule engine + Phase 7 paper trading results (for calibration) recommended first.


---

## Phase 9 — Hardening & Production (Weeks 23–26) 🚧 PARTIAL (~25%)

**Goal:** Production-ready pipeline running unattended on ShreeVault (Ubuntu server).

| Task | Status | File/Notes |
|------|--------|------------|
| Structured logging (JSON format) with log rotation | ✅ | `utils/logger.py` + `config/logging.yaml` |
| `Makefile` with core targets (`test`, `lint`, `format`, `daily`, `backtest`, `rebuild`, `api`, `dashboard`, `paper-reset`) | 🚧 | `Makefile` exists; not all targets implemented yet |
| `pyproject.toml` — packaging + dev dependencies | ✅ | Present |
| `requirements.txt` + `requirements-dev.txt` | ✅ | Present |
| Prometheus metrics endpoint (optional) | ❌ | |
| Full test coverage: unit + integration + smoke tests | 🚧 | 118 tests pass; integration dir is empty; no smoke tests yet |
| CI pipeline: `make test` runs in < 3 minutes | ❌ | No CI config (`.github/workflows/` or similar) |
| Data lineage: every run logs data hash, config snapshot, Git commit SHA | ❌ | `run_history` table schema designed but not wired |
| `systemd` service: `minervini-daily.timer` (Mon–Fri 15:35 IST) | ❌ | |
| `systemd` service: `minervini-api.service` (uvicorn, always running) | ❌ | |
| `systemd` service: `minervini-dashboard.service` (Streamlit, always running) | ❌ | |
| Runbook: how to add a new data source / new rule condition | ❌ | |
| **Deliverable:** Pipeline runs unattended on ShreeVault, self-monitors, alerts on failure | ❌ | Depends on Phases 4, 10, 11 |


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
| `api/schemas/stock.py` — Pydantic response models | ❌ | `api/schemas/` dir is empty |
| `api/schemas/portfolio.py` — paper trading response models | ❌ | |
| `api/schemas/common.py` — `APIResponse[T]` envelope + pagination | ❌ | |
| `api/deps.py` — shared FastAPI dependencies (DB session, cache) | ❌ | |
| `POST /api/v1/run` with `scope` and `symbols` body params (admin only) | ❌ | |
| Unit tests for all endpoints via `TestClient` | ❌ | |
| `systemd` service for uvicorn (port 8000, 2 workers) | ❌ | Covered in Phase 9 |
| **Deliverable:** `POST /api/v1/watchlist/upload` accepts CSV; `POST /api/v1/run {"scope":"watchlist"}` works | ❌ | |

**Blockers:** Phase 3 (rule engine + SQLite results) must be complete. Phase 7 for portfolio endpoints.


---

## Phase 11 — Streamlit Dashboard MVP (Weeks 30–31) ⏳ NOT STARTED

**Goal:** Visual dashboard for daily monitoring, accessible without SSH.

| Task | Status | File/Notes |
|------|--------|------------|
| `dashboard/app.py` — Streamlit entry point, multi-page layout | ❌ | `dashboard/` has `__init__.py` + empty `pages/` + `components/` |
| `dashboard/pages/01_Watchlist.py` — file upload + manual entry + watchlist table + [Run Now] | ❌ | |
| `dashboard/pages/02_Screener.py` — full universe table with quality/stage/RS filters | ❌ | |
| `dashboard/pages/03_Stock.py` — single stock deep-dive (chart + TT checklist + VCP + fundamentals + LLM brief) | ❌ | |
| `dashboard/pages/04_Portfolio.py` — paper trading summary + equity curve | ❌ | |
| `dashboard/pages/05_Backtest.py` — backtest results viewer + regime breakdown | ❌ | |
| `dashboard/components/charts.py` — mplfinance candlestick + MA + VCP zone overlays | ❌ | |
| `dashboard/components/tables.py` — styled screener tables | ❌ | `dashboard/components/` dir is empty |
| `dashboard/components/metrics.py` — score card widgets | ❌ | |
| Stage label annotation on chart | ❌ | |
| Watchlist symbols highlighted with ★ badge in all result tables | ❌ | |
| Manual run trigger button (calls `POST /api/v1/run`) | ❌ | Depends on Phase 10 API |
| `systemd` service for Streamlit (port 8501) | ❌ | Covered in Phase 9 |
| **Deliverable:** Uploading `mylist.csv` adds watchlist symbols; [Run Watchlist Now] shows results on same page | ❌ | |

**Blockers:** Phase 10 API should be running for the manual-run button; Phase 4 charts for chart components.


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

### ✅ Implemented Modules (Phase 1)

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

storage/
  parquet_store.py           ✅ Atomic append (temp + rename)
  sqlite_store.py            ✅ Results + run_history

utils/
  logger.py                  ✅ Structured logging
  date_utils.py              ✅ Date helpers
  trading_calendar.py        ✅ NSE holiday schedule
  exceptions.py              ✅ Custom exception hierarchy
  math_utils.py              ✅ Pure numeric helpers

pipeline/
  context.py                 ✅ RunContext dataclass

config/
  settings.yaml              ✅ All parameters
  universe.yaml              ✅ Symbol universe definition
  logging.yaml               ✅ Log levels per module
  symbol_aliases.yaml        ✅ Symbol → news alias mapping

scripts/
  run_daily.py               ✅ CLI with --watchlist, --symbols, --watchlist-only, --scope
  bootstrap.py               ✅ Full history download skeleton

tests/unit/
  test_universe_loader.py    ✅
  test_validator.py          ✅
  test_trading_calendar.py   ✅
  test_source_factory.py     ✅
  test_storage.py            ✅

tests/fixtures/
  sample_watchlist.csv       ✅
  sample_watchlist.json      ✅
```

### 📁 Stub Directories (exist, no implementation yet)

```
features/          __init__.py only  (Phase 2)
rules/             __init__.py only  (Phase 3)
screener/          __init__.py only  (Phase 3)
alerts/            __init__.py only  (Phase 4)
paper_trading/     __init__.py only  (Phase 4/7)
reports/           __init__.py only  (Phase 4)
backtest/          __init__.py only  (Phase 8)
llm/               __init__.py + empty prompt_templates/ (Phase 6)
pipeline/          __init__.py + context.py (runner.py missing — Phase 4)
api/               __init__.py + empty routers/ + schemas/ (Phase 10)
dashboard/         __init__.py + empty pages/ + components/ (Phase 11)
```

### ❌ Entirely Missing (no directory either)

```
frontend/                  (Phase 12 — Next.js, not expected yet)
scripts/backtest_runner.py (Phase 8)
scripts/rebuild_features.py (Phase 2/9)
```

---

## Next Steps

**To start Phase 2**, implement in order:
1. `features/moving_averages.py` — SMA/EMA/slopes
2. `features/atr.py` + `features/volume.py`
3. `features/relative_strength.py` + `features/sector_rs.py`
4. `features/pivot.py` + `features/vcp.py` (`RuleBasedVCPDetector`)
5. `features/feature_store.py` (`bootstrap()` + `update()` + `needs_bootstrap()`)
6. `screener/pre_filter.py`
7. Add `tests/fixtures/sample_ohlcv.parquet` + unit tests per module
8. Benchmark and verify: bootstrap 500 symbols < 15 min, daily update < 30 sec

---

*This document is auto-generated from PROJECT_DESIGN.md v1.4.0 + filesystem inspection.*
*Last regenerated: 2026-04-26*
