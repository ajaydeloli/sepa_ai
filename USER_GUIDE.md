# USER_GUIDE.md
# Minervini SEPA Stock Analysis System — End-User Guide

> **Version:** 1.1 | **Updated:** 2026-05-09  
> **System:** Minervini SEPA Stock Screener v1.1.0  
> **Target reader:** Anyone setting up or operating the system — no prior DevOps knowledge required.

---

## Table of Contents

1. [What This System Does](#1-what-this-system-does)
2. [Prerequisites](#2-prerequisites)
3. [Installation](#3-installation)
4. [Configuration](#4-configuration)
5. [Bootstrap — First-Time Data Download](#5-bootstrap--first-time-data-download)
6. [Running the Daily Screen](#6-running-the-daily-screen)
7. [Automated Scheduling (Hands-Free Operation)](#7-automated-scheduling-hands-free-operation)
8. [Accessing the Dashboard](#8-accessing-the-dashboard)
9. [Accessing the API](#9-accessing-the-api)
10. [Accessing the Next.js Frontend](#10-accessing-the-nextjs-frontend)
11. [Watchlist Management](#11-watchlist-management)
12. [Paper Trading](#12-paper-trading)
13. [Backtesting](#13-backtesting)
14. [Alerts — Telegram & Email](#14-alerts--telegram--email)
15. [Resetting the System](#15-resetting-the-system)
    - [Full Project Reset (`scripts/reset.py`)](#full-project-reset-scriptsresetpy)
    - [Reset Scope Flags](#reset-scope-flags)
    - [Common Reset Examples](#common-reset-examples)
    - [Reset Paper Trading Only](#reset-paper-trading-only)
    - [Rebuild Feature Files](#rebuild-feature-files)
16. [Uninstallation](#16-uninstallation)
17. [Troubleshooting](#17-troubleshooting)
18. [Quick Reference Cheat Sheet](#18-quick-reference-cheat-sheet)

---

## 1. What This System Does

The Minervini SEPA system screens Indian NSE stocks every trading day using
Mark Minervini's SEPA (Specific Entry Point Analysis) methodology. It:

- Downloads OHLCV data for up to 2,000 NSE symbols every trading day
- Computes technical indicators (moving averages, ATR, relative strength, VCP patterns)
- Classifies every stock into Stage 1 / 2 / 3 / 4 — only Stage 2 is buyable
- Scores each stock 0–100 and tags setups as **A+, A, B, C, or FAIL**
- Generates daily reports (CSV + HTML), candlestick charts, and Telegram alerts
- Runs a paper trading simulator to track signal quality in real time
- Exposes everything through a Streamlit dashboard and a REST API

**You do not need to know Python or Linux to operate the system once it is installed.**

---

## 2. Prerequisites

### Hardware

| Requirement | Minimum | Recommended |
|-------------|---------|-------------|
| OS | Ubuntu 22.04 LTS | Ubuntu 24.04 LTS |
| RAM | 2 GB | 4 GB |
| Disk | 10 GB free | 20 GB free |
| CPU | 2 cores | 4 cores |
| Network | Broadband | Broadband |

The system runs fine on a home server, a VPS (₹500–800/month), or a
cloud VM (AWS t3.small, GCP e2-small). A Raspberry Pi 4 with 4 GB RAM
also works.

### Software

```bash
# Check Python version — must be 3.11 or newer
python3 --version

# If not installed:
sudo apt update && sudo apt install python3.11 python3.11-venv python3.11-dev -y

# Git
sudo apt install git -y

# Node.js 18+ (only needed for the Next.js frontend — optional)
curl -fsSL https://deb.nodesource.com/setup_18.x | sudo -E bash -
sudo apt install nodejs -y
```

### API Keys (what you actually need)

| Key | Required? | Where to get it | Cost |
|-----|-----------|-----------------|------|
| **GROQ_API_KEY** | Recommended | [console.groq.com](https://console.groq.com) | Free |
| **TELEGRAM_BOT_TOKEN** | Optional | [@BotFather on Telegram](https://t.me/BotFather) | Free |
| **TELEGRAM_CHAT_ID** | Optional | Send a message to your bot, then call the getUpdates API | Free |
| NEWSDATA_API_KEY | Optional | [newsdata.io](https://newsdata.io) | Free tier |
| ANTHROPIC_API_KEY | Optional | [console.anthropic.com](https://console.anthropic.com) | Paid |
| OPENAI_API_KEY | Optional | [platform.openai.com](https://platform.openai.com) | Paid |

> **Minimum viable setup:** No API keys at all. yfinance (the data source) needs no key.
> LLM naratives and Telegram alerts are skipped gracefully if keys are absent.

---

## 3. Installation

### Step 1 — Clone the repository

```bash
git clone https://github.com/your-org/sepa_ai.git
cd sepa_ai
```

### Step 2 — Create a Python virtual environment and install dependencies

```bash
make install
```

This runs `pip install -e ".[dev]"` inside a fresh virtual environment at `.venv/`.
Expected duration: 2–5 minutes on first run (downloads ~80 packages).

Verify the installation:

```bash
make test-smoke
```

You should see `X passed` with no failures. This confirms all imports resolve correctly.

### Step 3 — Copy and fill in your environment file

```bash
cp .env.example .env
nano .env      # or: code .env / vim .env
```

Minimum `.env` for a functional system:

```bash
# Paste your Groq key here for AI trade briefs (free)
GROQ_API_KEY=gsk_xxxxxxxxxxxxxxxxxxxx

# Telegram bot (optional — skip these two lines if you don't want alerts)
TELEGRAM_BOT_TOKEN=1234567890:ABCDxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TELEGRAM_CHAT_ID=-1001234567890

# API access keys — change these to something secure
API_READ_KEY=my_read_key_here
API_ADMIN_KEY=my_admin_key_here
```

Save and close. The file is never committed to git (it is in `.gitignore`).

### Step 4 — Verify the installation

```bash
# Should print the Nifty 500 symbol count and exit cleanly
python scripts/run_daily.py --dry-run --scope universe
```

---

## 4. Configuration

All system behaviour is controlled by two files:

| File | Purpose |
|------|---------|
| `.env` | API keys and secrets (never commit this) |
| `config/settings.yaml` | All tunable parameters (thresholds, toggles, limits) |

### Key settings in `config/settings.yaml`

**Universe — which stocks to screen**

```yaml
universe:
  source: "yfinance"      # primary data source — no key needed
  index: "nifty500"       # nifty500 | nse_all (2000 symbols, slower)
  min_price: 50           # filter out penny stocks below ₹50
  min_avg_volume: 100000  # minimum avg daily volume
```

**Scoring thresholds**

```yaml
scoring:
  setup_quality_thresholds:
    a_plus: 85    # score >= 85 → A+
    a:     70     # score >= 70 → A
    b:     55     # score >= 55 → B
    c:     40     # score >= 40 → C
```

**LLM provider (AI trade briefs)**

```yaml
llm:
  enabled: true
  provider: "groq"            # groq | anthropic | openai | ollama | openrouter
  model: "llama-3.3-70b-versatile"
  only_for_quality: ["A+", "A"]   # only generate for top setups (saves cost)
```

**Paper trading capital**

```yaml
paper_trading:
  initial_capital: 100000   # starting capital in INR
  max_positions: 10         # never hold more than 10 open positions
  risk_per_trade_pct: 2.0   # risk 2% of portfolio per trade
```

**Alerts**

```yaml
alerts:
  telegram:
    enabled: true
    min_quality: "A"    # only alert for A and A+ setups
  dedup_days: 3         # don't re-alert the same symbol within 3 days
```

**Fundamentals**

```yaml
fundamentals:
  enabled: true       # set to false to skip Screener.in scraping
  hard_gate: false    # true → FAIL if any of the 7 conditions fail
```

---

## 5. Bootstrap — First-Time Data Download

**Bootstrap downloads 5–10 years of price history for all universe symbols and
computes all technical indicators from scratch. Run this once before your first
daily screen.**

```bash
make bootstrap
```

This is equivalent to:

```bash
python scripts/bootstrap.py --universe nifty500
```

| Universe | Expected duration | Disk usage |
|----------|------------------|-----------|
| Nifty 500 | 5–15 minutes | ~500 MB |
| NSE All (~2000) | 60–90 minutes | ~2 GB |

You only ever run bootstrap once (or when recovering from corruption). Daily runs
are incremental and take ~30 seconds regardless of how many months pass.

### Monitor bootstrap progress

```bash
# In a separate terminal:
tail -f logs/sepa_ai.log
```

### `bootstrap.py` — all arguments

```
python scripts/bootstrap.py [OPTIONS]

Options:
  --universe {nifty500,nse_all}
                          Universe to bootstrap.  [default: nifty500]
                          nifty500  → Nifty 500 constituents (~500 symbols, 5–15 min)
                          nse_all   → All NSE equity symbols (~2000, 60–90 min)
  --start-date DATE       Override start date as YYYY-MM-DD.
                          [default: 5 years before today]
  --symbols SYMBOLS       Comma-separated inline symbols, e.g. "RELIANCE,TCS"
                          Merged with --watchlist-file; both override --universe.
  --watchlist-file FILE   Path to watchlist file (.csv / .json / .xlsx / .txt)
                          Merged with --symbols; both override --universe.
  --force                 Re-download even if the parquet file already exists.
                          Without this flag, existing files are skipped.
```

### Common bootstrap examples

```bash
# Full Nifty 500 bootstrap (run once on first setup)
make bootstrap
python scripts/bootstrap.py --universe nifty500

# Full NSE universe (~2000 symbols, takes ~60–90 min — run overnight)
python scripts/bootstrap.py --universe nse_all

# Bootstrap starting from a specific date (shorter history = faster)
python scripts/bootstrap.py --universe nifty500 --start-date 2022-01-01

# Bootstrap only the symbols in a watchlist file
python scripts/bootstrap.py --watchlist-file mylist.csv

# Bootstrap specific symbols inline
python scripts/bootstrap.py --symbols "RELIANCE,TCS,INFY,DIXON,HDFCBANK"

# Combine inline + file (merged and deduplicated)
python scripts/bootstrap.py --symbols "RELIANCE" --watchlist-file extra.txt

# Force re-download even if files already exist (repair corruption)
python scripts/bootstrap.py --universe nifty500 --force
python scripts/bootstrap.py --symbols "RELIANCE" --force

# Repair a single symbol
python scripts/bootstrap.py --symbols "RELIANCE" --force
```

| Universe | Expected duration | Disk usage |
|----------|-----------------|-----------|
| Nifty 500 | 5–15 minutes | ~500 MB |
| NSE All (~2000) | 60–90 minutes | ~2 GB |

Monitor progress in a separate terminal:

```bash
tail -f logs/sepa_ai.log
```

---

## 6. Running the Daily Screen

### `run_daily.py` — all arguments

```
python scripts/run_daily.py [OPTIONS]

Options:
  --date DATE             Run date as YYYY-MM-DD or "today"  [default: today]
  --watchlist FILE        Path to watchlist file (.csv / .json / .xlsx / .txt)
                          Symbols are loaded into SQLite and scanned in addition
                          to (or instead of) the configured universe.
  --symbols SYMBOLS       Comma-separated inline symbols, e.g. "RELIANCE,TCS"
                          Takes highest priority — overrides universe and watchlist.
  --watchlist-only        Skip the full universe scan; process only watchlist symbols.
                          Equivalent to --scope watchlist.
  --scope {all,universe,watchlist}
                          all       → universe + watchlist (default)
                          universe  → universe only, ignore watchlist
                          watchlist → watchlist only, skip universe
                          [default: all]
  --dry-run               Print resolved symbol list and exit without fetching
                          or writing any data. Use to sanity-check before a run.
```

### Common run_daily examples

```bash
# Run today's full screen (universe + watchlist)
make daily
python scripts/run_daily.py --date today

# Run for a specific past date
python scripts/run_daily.py --date 2026-04-15

# Watchlist symbols only — skip the full universe
make watchlist-only
python scripts/run_daily.py --watchlist-only
python scripts/run_daily.py --scope watchlist

# Load a file and scan those symbols (merged into persistent watchlist)
python scripts/run_daily.py --watchlist mylist.csv
python scripts/run_daily.py --watchlist mylist.xlsx
python scripts/run_daily.py --watchlist mylist.json
python scripts/run_daily.py --watchlist mylist.txt

# Scan specific symbols inline (ad-hoc, not persisted to watchlist)
python scripts/run_daily.py --symbols "RELIANCE,TCS,DIXON,HDFCBANK"

# Universe only — ignore the persistent watchlist entirely
python scripts/run_daily.py --scope universe

# Dry run — preview resolved symbols without fetching or writing
python scripts/run_daily.py --date today --dry-run
python scripts/run_daily.py --watchlist mylist.csv --dry-run
```

### What a run does
1. Fetches today's OHLCV data via yfinance (one batch call, ~10 seconds)
2. Updates feature files for all symbols (~20 seconds)
3. Runs the rule engine — Stage 2 gate, Trend Template, VCP, scoring
4. Generates a daily watchlist report in `data/reports/`
5. Generates candlestick charts for all A+/A candidates
6. Sends Telegram alerts for new A+/A setups (if enabled and not deduped)
7. Creates paper trades for qualifying signals (if paper trading is enabled)

**Total time:** ~30–90 seconds for Nifty 500.

### Screen output

After a successful run, check:

```
data/reports/
├── watchlist_2026-05-09.csv       ← machine-readable results
├── watchlist_2026-05-09.html      ← human-readable report (open in browser)
└── charts/
    ├── DIXON_2026-05-09.png
    ├── RELIANCE_2026-05-09.png
    └── ...
```

Open the HTML report in any browser:

```bash
xdg-open data/reports/watchlist_2026-05-09.html   # Linux
# or: copy the file path and open it in your browser manually
```

---

## 7. Automated Scheduling (Hands-Free Operation)

The system includes `systemd` services that run the daily screen automatically
every trading day at 15:35 IST (five minutes after NSE market close).

### Install the services

```bash
make deploy
# or:
sudo bash deploy/install.sh
```

This installs four units:

| Unit | Type | What it does |
|------|------|-------------|
| `minervini-daily.timer` | Timer | Fires Mon–Fri at 15:35 IST |
| `minervini-daily.service` | Oneshot | Runs the daily screen |
| `minervini-api.service` | Always-on | Keeps the REST API running on port 8000 |
| `minervini-dashboard.service` | Always-on | Keeps the Streamlit dashboard on port 8501 |

### Verify the timer is active

```bash
systemctl list-timers | grep minervini
# Should show: minervini-daily.timer   next weekday at 10:05 UTC
```

### Check service status

```bash
make status
```

### View logs

```bash
make logs        # last 50 lines from the daily pipeline
make logs-api    # last 50 lines from the API server
```

### Trigger a manual run without waiting for the timer

```bash
sudo systemctl start minervini-daily.service
```

### Stop / start services

```bash
sudo systemctl stop minervini-api.service
sudo systemctl start minervini-api.service
sudo systemctl restart minervini-api.service
```

### Disable the timer (pause auto-runs)

```bash
sudo systemctl disable --now minervini-daily.timer
# To re-enable:
sudo systemctl enable --now minervini-daily.timer
```

---

## 8. Accessing the Dashboard

The Streamlit dashboard gives you a visual interface for the screener — no
command line needed after the API is running.

### Start the dashboard (without systemd)

```bash
make dashboard
```

Opens at: **http://localhost:8501**

If running on a remote server, replace `localhost` with the server's IP address.
You may need to open port 8501 in your firewall:

```bash
sudo ufw allow 8501
```

### Dashboard pages

| Page | What you can do |
|------|-----------------|
| **01 Watchlist** | Upload a CSV/XLSX of symbols, add symbols manually, run an on-demand screen, see today's A+/A setups |
| **02 Screener** | Browse all universe results, filter by quality/stage/RS rating, export to CSV |
| **03 Stock** | Deep-dive on any symbol — candlestick chart with MA ribbons, 8-condition Trend Template checklist, VCP metrics, fundamentals, AI trade brief |
| **04 Portfolio** | View paper trading P&L, open positions, closed trade history, equity curve |
| **05 Backtest** | Run a backtest over any date range, view performance metrics, per-regime breakdown |

### Add a watchlist via the dashboard

1. Go to **01 Watchlist**
2. Click **Browse files** and upload a `.csv`, `.xlsx`, `.json`, or `.txt` file
3. One symbol per row (CSV column header: `symbol`) or one per line (TXT)
4. Click **Run Watchlist Now** to analyse immediately

---

## 9. Accessing the API

The FastAPI server exposes all screener data over HTTP. It is used by the
Next.js frontend and can be queried directly with `curl` or any HTTP client.

### Start the API (without systemd)

```bash
make api
```

Runs at: **http://localhost:8000**

Interactive API docs (Swagger UI): **http://localhost:8000/docs**

### Authentication

Every request needs an `X-API-Key` header. Two tiers:

| Key | Env variable | Permissions |
|-----|-------------|-------------|
| Read key | `API_READ_KEY` | All GET endpoints |
| Admin key | `API_ADMIN_KEY` | All endpoints including `POST /api/v1/run` |

```bash
# Example — get today's top A+ setups
curl http://localhost:8000/api/v1/stocks/top?quality=A%2B \
     -H "X-API-Key: your_read_key"
```

### Common API calls

```bash
# Top-ranked setups today
GET /api/v1/stocks/top?quality=A&limit=20

# Full SEPA result for one symbol
GET /api/v1/stock/DIXON

# Historical scores for a symbol (last 30 days)
GET /api/v1/stock/DIXON/history?days=30

# Current watchlist
GET /api/v1/watchlist

# Add a symbol to the watchlist
POST /api/v1/watchlist/RELIANCE

# Remove a symbol
DELETE /api/v1/watchlist/RELIANCE

# Paper trading portfolio summary
GET /api/v1/portfolio

# System health
GET /api/v1/health
```

### Trigger a screen via the API (admin only)

```bash
# Full universe + watchlist
curl -X POST http://localhost:8000/api/v1/run \
     -H "X-API-Key: your_admin_key" \
     -H "Content-Type: application/json" \
     -d '{"scope": "all"}'

# Watchlist only
curl -X POST http://localhost:8000/api/v1/run \
     -H "X-API-Key: your_admin_key" \
     -H "Content-Type: application/json" \
     -d '{"scope": "watchlist"}'

# Specific symbols
curl -X POST http://localhost:8000/api/v1/run \
     -H "X-API-Key: your_admin_key" \
     -H "Content-Type: application/json" \
     -d '{"symbols": ["RELIANCE", "DIXON", "TCS"]}'
```

---

## 10. Accessing the Next.js Frontend

The Next.js frontend is a polished, mobile-friendly web app that talks to the
FastAPI layer. It runs separately from the Streamlit dashboard.

### Development mode (local)

```bash
make frontend-dev
```

Opens at: **http://localhost:3000**

Requires Node.js 18+. The FastAPI server must also be running (`make api`).

### Production build

```bash
make frontend-build
```

### Deploy to Vercel (public URL)

1. Install the Vercel CLI: `npm install -g vercel`
2. Update `frontend/vercel.json` — replace `your-api-server:8000` with your real
   server IP or hostname in the `rewrites` section:
   ```json
   { "source": "/api/:path*", "destination": "http://YOUR_SERVER_IP:8000/api/:path*" }
   ```
3. Run the deploy:
   ```bash
   make frontend-deploy
   ```
4. Follow the prompts — Vercel will give you a public HTTPS URL.

---


## 11. Watchlist Management

A watchlist is a personal list of symbols you want tracked and screened at
higher priority — they appear first in reports and always get a chart, even
if their score is low.

### Add symbols — 4 ways

**Option A — Upload a file (recommended)**

Supported file formats:

| Format | Structure |
|--------|-----------|
| `.csv` | Column named `symbol`, one row per symbol |
| `.xlsx` | First sheet, column A or column named `symbol` |
| `.json` | `["RELIANCE", "TCS", "DIXON"]` |
| `.txt` | One symbol per line |

Via CLI:
```bash
python scripts/run_daily.py --watchlist /path/to/mylist.csv
```

Via API:
```bash
curl -X POST http://localhost:8000/api/v1/watchlist/upload \
     -H "X-API-Key: your_admin_key" \
     -F "file=@mylist.csv"
```

Via dashboard: Go to **01 Watchlist** → **Browse files** → upload file.

**Option B — Add individual symbols via CLI**

```bash
python scripts/run_daily.py --symbols "RELIANCE,TCS,DIXON"
```

**Option C — Add via API**

```bash
# Single symbol
curl -X POST http://localhost:8000/api/v1/watchlist/DIXON \
     -H "X-API-Key: your_admin_key"

# Multiple at once
curl -X POST http://localhost:8000/api/v1/watchlist/bulk \
     -H "X-API-Key: your_admin_key" \
     -H "Content-Type: application/json" \
     -d '{"symbols": ["RELIANCE", "TCS", "INFY"]}'
```

**Option D — Add via Streamlit dashboard**

Go to **01 Watchlist** → type symbols in the text box (comma-separated) → click **Add**.

### View the watchlist

```bash
# Via API
curl http://localhost:8000/api/v1/watchlist -H "X-API-Key: your_read_key"

# In the database directly
sqlite3 data/sepa_ai.db "SELECT symbol, last_score, last_quality FROM watchlist;"
```

### Remove a symbol

```bash
# Via API
curl -X DELETE http://localhost:8000/api/v1/watchlist/DIXON \
     -H "X-API-Key: your_admin_key"
```

Or use the dashboard: **01 Watchlist** → find the symbol row → click **Remove**.

### Clear the entire watchlist

```bash
curl -X DELETE http://localhost:8000/api/v1/watchlist \
     -H "X-API-Key: your_admin_key"
```

---

## 12. Paper Trading

Paper trading lets you validate signals with virtual money before committing
real capital. The system automatically places paper trades after every daily
screen for any A+/A setups.

### View the paper portfolio

**Via dashboard:** Go to **04 Portfolio** for a full visual summary.

**Via API:**
```bash
curl http://localhost:8000/api/v1/portfolio -H "X-API-Key: your_read_key"
curl http://localhost:8000/api/v1/portfolio/trades -H "X-API-Key: your_read_key"
```

**Via file:**
```
data/paper_trading/portfolio.json    ← open positions + cash
data/paper_trading/trades.json       ← full trade history
```

### Generate a paper trading report

```bash
python -c "
from paper_trading.report import generate_performance_report
generate_performance_report()
print('Report saved to data/paper_trading/report.html')
"
```

Open `data/paper_trading/report.html` in your browser.

### Change starting capital

Edit `config/settings.yaml`:

```yaml
paper_trading:
  initial_capital: 200000   # change to your desired amount (INR)
```

Then reset the portfolio (see below).

### Reset the paper portfolio

**Warning: this permanently deletes all trade history.**

```bash
# Take a backup first
cp data/sepa_ai.db data/sepa_ai_backup_$(date +%Y%m%d).db

# Reset
make paper-reset
```

### Disable paper trading

```yaml
# config/settings.yaml
paper_trading:
  enabled: false
```

---

## 13. Backtesting

Backtesting replays the SEPA screening logic over historical data to measure
how the strategy performed in the past.

### `backtest_runner.py` — all arguments

```
python scripts/backtest_runner.py [OPTIONS]

Required:
  --start DATE            Backtest start date (YYYY-MM-DD).
  --end   DATE            Backtest end date   (YYYY-MM-DD).

Options:
  --universe STR          "nifty500" | "nse_all" | path to a CSV file
                          with a 'symbol' column.  [default: nifty500]
  --trailing-stop FLOAT   Trailing stop percentage, e.g. 0.07 for 7%.
                          Reads from config/settings.yaml when not supplied.
  --no-trailing           Disable trailing stop; use fixed stop from config only.
                          Mutually exclusive with --compare.
  --compare               Run BOTH trailing and fixed stop passes side-by-side
                          and include a comparison table in the HTML report.
                          Mutually exclusive with --no-trailing.
  --output DIR            Directory to write HTML and CSV reports.
                          [default: reports/]
  --config FILE           Path to a settings.yaml override.
                          [default: config/settings.yaml]
```

### Common backtest examples

```bash
# Basic backtest — Nifty 500, last 5 years, default trailing stop (7%)
make backtest START=2021-01-01 END=2026-01-01

# Specify trailing stop explicitly
python scripts/backtest_runner.py \
  --start 2021-01-01 --end 2026-01-01 \
  --trailing-stop 0.07

# Fixed stop only (no trailing)
python scripts/backtest_runner.py \
  --start 2021-01-01 --end 2026-01-01 \
  --no-trailing

# Side-by-side trailing vs fixed comparison (adds comparison table to HTML)
python scripts/backtest_runner.py \
  --start 2021-01-01 --end 2026-01-01 \
  --compare

# Run on the full NSE universe
python scripts/backtest_runner.py \
  --start 2021-01-01 --end 2026-01-01 \
  --universe nse_all

# Save report to a custom directory
python scripts/backtest_runner.py \
  --start 2021-01-01 --end 2026-01-01 \
  --output /tmp/my_backtest_results/

# Use an alternate config file (e.g. looser thresholds)
python scripts/backtest_runner.py \
  --start 2021-01-01 --end 2026-01-01 \
  --config config/settings_loose.yaml
```

### What the HTML report contains
- Key metric cards: CAGR, Sharpe, max drawdown, win rate, avg R-multiple, profit factor
- Equity curve with drawdown shading
- Per-regime breakdown (Bull / Sideways / Bear)
- VCP quality breakdown (A+ vs A vs B win rates)
- Trailing vs fixed stop comparison table (when `--compare` is used)
- Top 10 winners and losers
- Full trade list

---

## 14. Alerts — Telegram & Email

### Setting up Telegram alerts

**Step 1 — Create a Telegram bot**

1. Open Telegram and message [@BotFather](https://t.me/BotFather)
2. Send `/newbot` and follow prompts
3. Copy the token (looks like `1234567890:ABCDxxx...`)

**Step 2 — Get your Chat ID**

1. Start a conversation with your new bot (send any message)
2. Open this URL in your browser (replace TOKEN):
   ```
   https://api.telegram.org/botTOKEN/getUpdates
   ```
3. Find `"chat":{"id":XXXXXXX}` — that number is your Chat ID

For a Telegram channel: add your bot as admin, then get the channel's Chat ID
(it will start with `-100`).

**Step 3 — Add to `.env`**

```bash
TELEGRAM_BOT_TOKEN=1234567890:ABCDxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TELEGRAM_CHAT_ID=-1001234567890
```

**Step 4 — Verify the bot works**

```bash
curl -s "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
     -d chat_id=${TELEGRAM_CHAT_ID} \
     -d text="SEPA system is live 🚀"
```

**Step 5 — Enable in config**

```yaml
# config/settings.yaml
alerts:
  telegram:
    enabled: true
    min_quality: "A"    # only alert for A and A+ setups
```

### Setting up email alerts

```bash
# .env
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=you@gmail.com
SMTP_PASS=your_app_password   # use a Gmail App Password, not your real password
```

For Gmail, create an App Password at: **Google Account → Security → App Passwords**

```yaml
# config/settings.yaml
alerts:
  email:
    enabled: true
```

### Alert deduplication

The system avoids sending the same alert repeatedly. A symbol is only re-alerted if:
- It hasn't been alerted in the last 3 days, **or**
- Its setup quality improved (e.g. B → A), **or**
- Its score jumped by 10+ points, **or**
- A breakout was newly triggered

```yaml
alerts:
  dedup_days: 3          # change to 1 for daily re-alerts
  dedup_score_jump: 10   # minimum score improvement to re-alert
```

---

## 15. Resetting the System

The project ships with a dedicated reset script — `scripts/reset.py` — that
returns any part (or all) of the project to a clean, fresh-installation state.
It handles every subsystem in one command, creates a correct empty DB schema,
restores paper-trading JSON files to their initial state, and prints a clear
summary of what was deleted.

> **Always take a backup before a destructive reset:**
> ```bash
> cp data/sepa_ai.db data/sepa_ai_backup_$(date +%Y%m%d).db
> ```

---

### Full Project Reset (`scripts/reset.py`)

```
python scripts/reset.py [OPTIONS]

Behaviour flags:
  --dry-run          Print every file that would be deleted — touch nothing.
  --yes  / -y        Skip the confirmation prompt (for scripts / CI).

Scope flags (combinable; --all is the default when none are given):
  --all              Everything listed below.
  --db               SQLite databases (wiped + schema re-created).
  --paper            Paper-trading portfolio.json + trades.json.
  --features         Computed feature Parquets   (data/features/).
  --processed        Processed OHLCV Parquets    (data/processed/).
  --raw              Raw ticker downloads        (data/raw/).
  --fundamentals     Fundamentals JSON cache     (data/fundamentals/).
  --news             News JSON cache             (data/news/).
  --reports          CSV / HTML daily reports    (data/reports/ + reports/).
  --logs             Rotating log files          (logs/).
  --metadata         Symbol-info CSV             (data/metadata/).
  --frontend         Next.js build cache         (frontend/.next/).
  --keep-downloaded  Same as --all but SKIP --processed and --raw
                     (keeps fetched OHLCV so re-bootstrap is not needed).
```

Two `make` shortcuts are also available:

```bash
make reset        # interactive full reset (asks for confirmation)
make reset-dry    # dry-run preview — lists every file, deletes nothing
```

---

### Reset Scope Flags

What each flag wipes, and whether a re-bootstrap is needed afterwards:

| Flag | What is deleted | Re-bootstrap needed? |
|------|----------------|---------------------|
| `--db` | `data/sepa_ai.db`, `data/minervini.db`, WAL/SHM files — schema is **re-created** automatically | No |
| `--paper` | `data/paper_trading/portfolio.json`, `trades.json` — restored to blank initial state | No |
| `--features` | `data/features/*.parquet` — computed indicators | Yes (`make rebuild`) |
| `--processed` | `data/processed/*.parquet` — downloaded OHLCV | Yes (`make bootstrap`) |
| `--raw` | `data/raw/*` — raw downloads | No |
| `--fundamentals` | `data/fundamentals/*.json` | No (re-fetched on next run) |
| `--news` | `data/news/market_news.json` | No (re-fetched on next run) |
| `--reports` | `data/reports/*.csv/html`, `reports/*.csv/html` | No |
| `--logs` | `logs/sepa_ai.log*` | No |
| `--metadata` | `data/metadata/symbol_info.csv` | No (re-fetched on next run) |
| `--frontend` | `frontend/.next/` build cache | No (`make frontend-build` to rebuild) |

---

### Common Reset Examples

```bash
# ── Preview ────────────────────────────────────────────────────────────────

# See exactly what a full reset would delete — no files touched
make reset-dry
python scripts/reset.py --dry-run --all

# Preview a selective reset
python scripts/reset.py --dry-run --db --paper

# ── Full resets ────────────────────────────────────────────────────────────

# Full interactive reset (shows confirmation prompt)
make reset
python scripts/reset.py --all

# Full reset, no confirmation (CI / automation)
python scripts/reset.py --all --yes

# Full reset but keep downloaded OHLCV data (no re-bootstrap needed)
python scripts/reset.py --keep-downloaded --yes

# ── Selective resets ───────────────────────────────────────────────────────

# Databases only (watchlist, run history, screen results — schema re-created)
python scripts/reset.py --db --yes

# Paper-trading portfolio only
python scripts/reset.py --paper --yes

# Databases + paper trading (common after a strategy change)
python scripts/reset.py --db --paper --yes

# Computed features only (when indicators look wrong — run 'make rebuild' after)
python scripts/reset.py --features --yes

# All downloaded + computed data, but keep the database and paper portfolio
python scripts/reset.py --features --processed --raw --yes

# Caches only — news, fundamentals, metadata (refresh stale data)
python scripts/reset.py --news --fundamentals --metadata --yes

# Reports + logs only (housekeeping)
python scripts/reset.py --reports --logs --yes

# ── After reset ────────────────────────────────────────────────────────────

# If you wiped --processed or --raw:
make bootstrap            # re-download 5 years of OHLCV

# If you wiped --features (but kept --processed):
make rebuild              # recompute all indicators from existing OHLCV

# Run today's screen once data is ready
make daily
```

---

### Reset Paper Trading Only

Clears all open positions and trade history. Starting capital is read from
`config/settings.yaml → paper_trading.initial_capital` and restored automatically.

```bash
# Via reset script (recommended — reads capital from config)
python scripts/reset.py --paper --yes

# Via Makefile shortcut
make paper-reset
```

To change the starting capital before resetting:

```yaml
# config/settings.yaml
paper_trading:
  initial_capital: 200000   # change to your desired amount (INR)
```

Then reset:

```bash
python scripts/reset.py --paper --yes
```

---

### Rebuild Feature Files

If you only need to recompute technical indicators without re-downloading OHLCV,
use `rebuild_features.py` directly — it is faster than a full reset + bootstrap.

```
python scripts/rebuild_features.py [OPTIONS]

Options:
  --universe UNIVERSE     Symbol universe to rebuild.  [default: nifty500]
                          Accepts: "nifty500", "nse_all", or a path to a CSV
                          file with a 'symbol' column.
                          Ignored when --symbol is provided.
  --symbol STR            Rebuild a single named symbol only.
  --force                 Rebuild every symbol even if its feature file
                          already exists and looks valid.
  --dry-run               List which symbols would be rebuilt and exit.
  --workers N             Number of parallel worker processes.  [default: 4]
  --config FILE           Path to settings.yaml.  [default: config/settings.yaml]
```

```bash
# Smart rebuild — only fixes missing or empty feature files
make rebuild
python scripts/rebuild_features.py --universe nifty500

# Rebuild one specific symbol (useful when its chart looks wrong)
python scripts/rebuild_features.py --symbol RELIANCE --force

# Force-rebuild the entire universe from existing OHLCV
python scripts/rebuild_features.py --universe nifty500 --force

# Preview which symbols need rebuilding without touching files
python scripts/rebuild_features.py --universe nifty500 --dry-run

# Faster rebuild with more CPU cores
python scripts/rebuild_features.py --universe nifty500 --force --workers 8
```

| Situation | Recommended command |
|-----------|-------------------|
| One symbol's chart / indicators look wrong | `--symbol SYMBOLNAME --force` |
| After pulling a major code update | `--universe nifty500 --force` |
| After `python scripts/reset.py --features` | `make rebuild` |
| Routine stale-file cleanup (fast) | `make rebuild` (no `--force`) |
| Check what needs rebuilding | `--dry-run` |

---

## 16. Uninstallation

### Remove systemd services (stop auto-runs)

```bash
sudo systemctl stop minervini-daily.timer minervini-api.service minervini-dashboard.service
sudo systemctl disable minervini-daily.timer minervini-api.service minervini-dashboard.service
sudo rm /etc/systemd/system/minervini-daily.{service,timer}
sudo rm /etc/systemd/system/minervini-api.service
sudo rm /etc/systemd/system/minervini-dashboard.service
sudo systemctl daemon-reload
```

### Remove the project files

```bash
# Navigate one level up from the project root, then:
cd ~
rm -rf projects/sepa_ai
```

### Remove the Python virtual environment only (keep project files)

```bash
rm -rf sepa_ai/.venv
```

### Remove Node.js frontend dependencies only

```bash
rm -rf sepa_ai/frontend/node_modules
```

---

## 17. Troubleshooting

### "No data for symbol XYZ" / symbols missing from results

**Cause:** The symbol might be delisted, renamed, or not covered by yfinance.

```bash
# Check if yfinance can fetch it
python -c "
import yfinance as yf
df = yf.download('XYZ.NS', period='5d', progress=False)
print(df)
"
```

If empty, the symbol is likely delisted or uses a different ticker suffix.
Remove it from your watchlist and use NSE's official symbol lookup.

---

### Daily screen takes longer than expected

**Cause A:** First run after a new symbol was added (triggers bootstrap for that symbol).  
**Cause B:** Network latency from yfinance.  
**Cause C:** `universe.index` is set to `nse_all` (~2000 symbols vs 500).

```yaml
# config/settings.yaml — switch back to nifty500 for speed
universe:
  index: "nifty500"
```

---

### No Telegram messages received

1. Check the bot token and chat ID are correct in `.env`
2. Verify alerts are enabled in `config/settings.yaml`
3. Check deduplication — the same symbol won't be re-alerted within 3 days
4. Look at the alert log:
   ```bash
   sqlite3 data/sepa_ai.db "SELECT * FROM alerts ORDER BY id DESC LIMIT 10;"
   ```
5. Test the bot manually:
   ```bash
   curl -s "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        -d chat_id=${TELEGRAM_CHAT_ID} -d text="Test"
   ```

---

### API returns 401 Unauthorized

Your `X-API-Key` header doesn't match `API_READ_KEY` or `API_ADMIN_KEY` in `.env`.

```bash
grep API_READ_KEY .env     # check your key
```

---

### Dashboard won't load (connection refused on port 8501)

```bash
# Start it manually
make dashboard

# Or check systemd
systemctl status minervini-dashboard.service
sudo systemctl restart minervini-dashboard.service
```

If running on a remote server, open the port in the firewall:
```bash
sudo ufw allow 8501
```

---

### LLM trade briefs are missing from reports

**Cause A:** `GROQ_API_KEY` (or your chosen provider's key) is missing from `.env`  
**Cause B:** LLM is disabled in config

```yaml
# config/settings.yaml
llm:
  enabled: true
  provider: "groq"
```

The system runs fine without LLM — briefs are simply omitted from the report.
There are no errors; this is intentional graceful degradation.

---

### Screener shows 0 results / all stocks fail

**Cause A:** Market was closed (weekend/holiday) — no data to process.  
**Cause B:** Bootstrap hasn't been run yet — feature files are missing.

```bash
# Check feature file count
python -c "
from pathlib import Path
print(len(list(Path('data/features').glob('*.parquet'))), 'feature files')
"
# If 0: run make bootstrap
```

**Cause C:** Overly strict thresholds during a bear market — most stocks fail Stage 2.
This is correct behaviour. Minervini's methodology holds cash in bear markets.

---

### `make install` fails with dependency errors

```bash
# Upgrade pip first
pip install --upgrade pip setuptools wheel

# Then retry
pip install -e ".[dev]"
```

If a specific package fails, check `requirements.txt` and `requirements-dev.txt`
for pinned versions that may conflict with your Python version.

---

### Timer fires but service fails silently

```bash
# Check the last service run in detail
journalctl -u minervini-daily.service --since "1 hour ago"

# Check the rotating log file
tail -100 logs/sepa_ai.log
```

Common causes: missing `.env` file, network timeout from yfinance,
or disk space full (`df -h`).

---

### Database lock error

If you see `database is locked`, another process is writing to SQLite.
Wait 30 seconds and retry. If persistent, check for zombie processes:

```bash
ps aux | grep run_daily
kill -9 <PID>    # kill the stuck process
```

---

## 18. Quick Reference Cheat Sheet

```
┌─────────────────────────────────────────────────────────────────────────┐
│                  MINERVINI SEPA — QUICK REFERENCE                       │
├─────────────────────────┬───────────────────────────────────────────────┤
│ SETUP                   │                                               │
│ First-time install      │  make install                                 │
│ Download all history    │  make bootstrap                               │
│ Install systemd timers  │  make deploy                                  │
├─────────────────────────┼───────────────────────────────────────────────┤
│ DAILY OPERATIONS        │                                               │
│ Run today's screen      │  make daily                                   │
│ Watchlist only          │  make watchlist-only                          │
│ Specific symbols        │  python scripts/run_daily.py --symbols "A,B"  │
│ Past date               │  python scripts/run_daily.py --date 2026-04-15│
│ Dry run (no writes)     │  python scripts/run_daily.py --dry-run        │
├─────────────────────────┼───────────────────────────────────────────────┤
│ INTERFACES              │                                               │
│ Streamlit dashboard     │  make dashboard   → localhost:8501            │
│ FastAPI server          │  make api         → localhost:8000            │
│ Next.js frontend        │  make frontend-dev → localhost:3000           │
│ API docs (Swagger)      │  http://localhost:8000/docs                   │
├─────────────────────────┼───────────────────────────────────────────────┤
│ BACKTEST                │                                               │
│ Run a backtest          │  make backtest START=2021-01-01 END=2026-01-01│
│ With trailing stop      │  python scripts/backtest_runner.py            │
│                         │    --start 2021-01-01 --end 2026-01-01        │
│                         │    --trailing-stop 0.07                       │
├─────────────────────────┼───────────────────────────────────────────────┤
│ RESET / RECOVERY        │                                               │
│ Preview full reset      │  make reset-dry                               │
│ Full interactive reset  │  make reset                                   │
│ Full reset (no prompt)  │  python scripts/reset.py --all --yes          │
│ Keep downloaded OHLCV   │  python scripts/reset.py --keep-downloaded    │
│ Databases only          │  python scripts/reset.py --db --yes           │
│ Paper portfolio only    │  python scripts/reset.py --paper --yes        │
│ DB + paper trading      │  python scripts/reset.py --db --paper --yes   │
│ Caches only             │  python scripts/reset.py --news --fundamentals│
│                         │    --metadata --yes                           │
│ Reports + logs          │  python scripts/reset.py --reports --logs     │
│ Rebuild one symbol      │  python scripts/rebuild_features.py           │
│                         │    --symbol RELIANCE --force                  │
│ Rebuild all features    │  make rebuild                                 │
├─────────────────────────┼───────────────────────────────────────────────┤
│ MONITORING              │                                               │
│ Service status          │  make status                                  │
│ Pipeline logs           │  make logs                                    │
│ API logs                │  make logs-api                                │
│ Live log tail           │  tail -f logs/sepa_ai.log                     │
│ Last 5 runs in DB       │  sqlite3 data/sepa_ai.db                      │
│                         │    "SELECT * FROM run_history                 │
│                         │     ORDER BY id DESC LIMIT 5;"                │
└─────────────────────────┴───────────────────────────────────────────────┘
```

---

## Appendix — File Locations Reference

```
sepa_ai/
├── .env                         ← Your API keys (NEVER commit this)
├── config/
│   ├── settings.yaml            ← All tunable parameters
│   ├── universe.yaml            ← Symbol universe definition
│   └── symbol_aliases.yaml      ← Symbol→news alias mapping
├── data/
│   ├── sepa_ai.db               ← Main SQLite database
│   │                               (watchlist, run history, results, alerts)
│   ├── processed/{symbol}.parquet  ← Clean daily OHLCV per symbol
│   ├── features/{symbol}.parquet   ← Computed indicators per symbol
│   ├── reports/                 ← Daily watchlist CSV + HTML + charts
│   ├── fundamentals/            ← Screener.in cache (7-day TTL)
│   ├── news/                    ← News cache (30-min TTL)
│   └── paper_trading/           ← Paper portfolio state files
├── logs/
│   └── sepa_ai.log              ← Rotating application log
├── deploy/
│   ├── install.sh               ← Run this to set up systemd services
│   └── README.md                ← Production ops guide
├── docs/
│   └── RUNBOOK.md               ← Developer/ops runbook
├── USER_GUIDE.md                ← This file
├── BUILD_STATUS.md              ← Current build completion status
└── PROJECT_DESIGN.md            ← Full system architecture reference
```

---

*For architecture details, see `PROJECT_DESIGN.md`.  
For developer/ops procedures, see `docs/RUNBOOK.md`.  
For current build status, see `BUILD_STATUS.md`.*
