# Minervini SEPA — Operations Runbook

This document covers day-to-day operations, recovery procedures, and
extension patterns for the SEPA AI screening system.

---

## 1. Daily Operations

### Check today's run status

```bash
# Was the systemd timer triggered?
systemctl status minervini-daily.timer

# Did the service complete successfully?
systemctl status minervini-daily.service

# Last 5 pipeline runs in the database
sqlite3 data/sepa_ai.db \
  "SELECT * FROM run_history ORDER BY id DESC LIMIT 5;"

# Tail the rotating log file
tail -100 logs/sepa_ai.log
```

### Re-run manually if the timer missed

```bash
# Run today's screen right now
make daily
# or directly:
python scripts/run_daily.py --date today
```

Add `--dry-run` first to sanity-check symbol resolution without writing anything.

### Check that the Telegram bot is working

```bash
# 1. Verify the API key is set
grep TELEGRAM .env

# 2. Send a test message (replace TOKEN and CHAT_ID from .env)
curl -s "https://api.telegram.org/bot<TOKEN>/sendMessage" \
     -d chat_id=<CHAT_ID> \
     -d text="SEPA bot health check"

# 3. Check alert history in the database
sqlite3 data/sepa_ai.db \
  "SELECT * FROM alerts ORDER BY id DESC LIMIT 10;"
```

---

## 2. Adding a New Data Source

Follow these steps to plug in an alternative OHLCV provider (e.g. Zerodha, Upstox):

1. **Create the source module**

   ```
   ingestion/{source_name}_source.py
   ```

   Implement the `DataSource` interface (same as `YFinanceSource`):

   ```python
   class MySource:
       def fetch(self, symbol: str, start: date, end: date) -> pd.DataFrame:
           ...
   ```

2. **Register it in the factory**

   Open `ingestion/source_factory.py` and add an entry to `SOURCES`:

   ```python
   SOURCES = {
       "yfinance": YFinanceSource,
       "my_source": MySource,   # ← add this
   }
   ```

3. **Add the API key**

   ```bash
   # .env.example  (commit this)
   MY_SOURCE_API_KEY=your_key_here

   # .env  (never commit)
   MY_SOURCE_API_KEY=actual_secret
   ```

4. **Point the config at the new source**

   In `config/settings.yaml`:

   ```yaml
   universe:
     source: "my_source"   # matches the SOURCES key
   ```

5. **Smoke-test the connection**

   ```bash
   python -c "
   from ingestion.source_factory import get_source
   src = get_source('my_source')
   from datetime import date, timedelta
   df = src.fetch('RELIANCE', date.today() - timedelta(days=5), date.today())
   print(df.tail())
   "
   ```

---

## 3. Adding a New Rule Condition

1. **Add the feature computation** (if a new indicator is required)

   Create or extend a module in `features/`:

   ```python
   # features/my_indicator.py
   def compute(df: pd.DataFrame, config: dict) -> pd.DataFrame:
       df["my_col"] = ...
       return df
   ```

   Then wire it into the pipeline in `features/feature_store.py`
   (import and call inside `_run_pipeline()`).

2. **Add the rule condition**

   ```python
   # rules/trend_template.py  or  rules/vcp_rules.py
   def check_my_condition(row: pd.Series, config: dict) -> bool:
       return row["my_col"] > config["my_threshold"]
   ```

3. **Write a unit test with a pass case and a fail case**

   ```bash
   tests/rules/test_my_condition.py
   make test
   ```

4. **Update `SCORE_WEIGHTS` if the condition affects the final score**

   ```python
   # rules/scorer.py
   SCORE_WEIGHTS = {
       ...
       "my_condition": 5,   # ← weight out of 100
   }
   ```

5. **Run the full test suite**

   ```bash
   make test
   ```

---

## 4. Recovering from Data Corruption

### Feature file corrupt

Re-run bootstrap for a single symbol:

```bash
make rebuild --symbol SYMBOLNAME
# or directly:
python scripts/rebuild_features.py --symbol SYMBOLNAME --force
```

To rebuild the entire universe from scratch:

```bash
python scripts/rebuild_features.py --universe nifty500 --force
```

### SQLite database corrupt

```bash
# Option A — restore from the most recent backup
ls -lth data/backups/
cp data/backups/sepa_ai_YYYYMMDD.db data/sepa_ai.db

# Option B — recreate from scratch (loses watchlist and run history)
rm data/sepa_ai.db
python scripts/run_daily.py --dry-run   # re-initialises the schema
```

### Processed OHLCV Parquet corrupt for one symbol

```bash
# Remove the bad file and re-bootstrap OHLCV + features
rm data/processed/SYMBOLNAME.parquet
python scripts/bootstrap.py --symbols SYMBOLNAME
python scripts/rebuild_features.py --symbol SYMBOLNAME --force
```

### Paper-trading state corrupt

```bash
make paper-reset   # resets the portfolio simulator from scratch
```

---

## 5. Adding Symbols to the Watchlist

**One-off via CLI (current run only)**

```bash
python scripts/run_daily.py --symbols "RELIANCE,TCS"
```

**From a watchlist file (one-off, persisted to SQLite)**

```bash
python scripts/run_daily.py --watchlist mylist.csv
```

Supported file formats: `.csv` (needs a `symbol` column), `.json`
(`["SYM1","SYM2"]` or `{"symbols": [...]}`), `.xlsx`, `.txt` (one per line).

**Persistent via the REST API (Phase 10)**

```bash
curl -X POST http://localhost:8000/api/v1/watchlist/bulk \
     -H "Content-Type: application/json" \
     -d '{"symbols": ["RELIANCE", "TCS"]}'
```

---

## 6. Server Health Checks

```bash
# Systemd timer and services
systemctl status minervini-daily.timer
systemctl status minervini-api.service
systemctl status minervini-dashboard.service

# API liveness endpoint
curl http://localhost:8000/api/v1/health

# Last 5 pipeline run records
sqlite3 data/sepa_ai.db \
  "SELECT * FROM run_history ORDER BY id DESC LIMIT 5;"

# Feature-file counts per symbol (quick sanity check)
python -c "
from pathlib import Path
files = list(Path('data/features').glob('*.parquet'))
print(f'{len(files)} feature files present')
"
```

---

## 7. Log Locations

| Source | How to view |
|---|---|
| Daily pipeline | `journalctl -u minervini-daily.service` |
| API server | `journalctl -u minervini-api.service` |
| Dashboard | `journalctl -u minervini-dashboard.service` |
| Rotating file log | `tail -f logs/sepa_ai.log` |

The file log rotates at 10 MB, keeps 5 backups, and is auto-created on
first run.  All log entries use the format:

```
YYYY-MM-DD HH:MM:SS | LEVEL | module.name | message
```

Makefile shortcuts for the two most common service logs:

```bash
make logs       # last 50 lines from minervini-daily.service
make logs-api   # last 50 lines from minervini-api.service
```
