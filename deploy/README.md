# deploy/README.md — Minervini SEPA Production Operations

## Services

| Unit | Type | Purpose |
|---|---|---|
| `minervini-daily.timer` | Timer | Fires Mon–Fri at 15:35 IST (10:05 UTC) |
| `minervini-daily.service` | Oneshot | Runs `scripts/run_daily.py --date today` |
| `minervini-api.service` | Always-on | FastAPI on port 8000, 2 workers |
| `minervini-dashboard.service` | Always-on | Streamlit on port 8501 |

---

## Initial Deployment

```bash
# From the project root:
sudo bash deploy/install.sh
```

The script will:
1. Check that `.venv` exists (run `make install` first if not).
2. Copy all four unit files to `/etc/systemd/system/`.
3. Run `systemctl daemon-reload`.
4. Enable and start all units.
5. Print a status summary.

---

## Verification Checklist

### 1. Verify timer is active and shows next trigger time

```bash
systemctl list-timers | grep minervini
# Expected: minervini-daily.timer   next weekday at 10:05 UTC
```

### 2. Trigger a manual run (without waiting for the timer)

```bash
make daily
# or directly:
.venv/bin/python scripts/run_daily.py --date today
```

### 3. Check result of the last run

```bash
sqlite3 data/sepa_ai.db \
  "SELECT * FROM run_history ORDER BY id DESC LIMIT 1;"
```

Fields to check: `status` (should be `success`), `a_plus_count`, `a_count`, `duration_sec`.

### 4. Reset paper trading portfolio

```bash
make paper-reset
```

This calls `paper_trading.simulator.reset_portfolio(confirm=True)`. All open positions and
history are cleared. **Irreversible** — take a SQLite backup first if needed.

### 5. Rebuild all features from scratch

```bash
make rebuild
# Equivalent to:
.venv/bin/python scripts/rebuild_features.py --universe nifty500
```

Use this after a schema migration or if `data/features/` becomes inconsistent. It replaces
the incremental update with a full recompute for every symbol. Takes ~5–15 min for Nifty 500.

---

## Day-to-Day Operations

```bash
# Show live service + timer status
make status

# Tail daily pipeline logs (last 50 lines)
make logs

# Tail API logs (last 50 lines)
make logs-api

# Follow live API logs
journalctl -u minervini-api.service -f

# Restart a service (e.g. after a code push)
sudo systemctl restart minervini-api.service
sudo systemctl restart minervini-dashboard.service

# View next 3 timer fires
systemctl list-timers minervini-daily.timer
```

---

## Notes

- **IST ↔ UTC:** 15:35 IST = 10:05 UTC. `OnCalendar` in the timer uses UTC.
- **Persistent=true:** If the server is off at 10:05 UTC, the timer fires immediately on next boot,
  so no daily run is silently skipped.
- **EnvironmentFile:** `/home/ubuntu/projects/sepa_ai/.env` is loaded by all three services.
  Copy `.env.example` → `.env` and fill in API keys before first deploy.
- **Log rotation:** journald handles rotation automatically; no logrotate config needed.
- **Port conflicts:** API=8000, Dashboard=8501. Verify with `ss -tlnp | grep -E '8000|8501'`.
