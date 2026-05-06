.PHONY: install test test-coverage test-smoke test-integration lint format daily bootstrap watchlist-only backtest rebuild api dashboard paper-reset deploy status logs logs-api help

PYTHON := .venv/bin/python
PIP    := .venv/bin/pip
PYTEST := .venv/bin/pytest

help:
	@echo "Minervini SEPA — available targets:"
	@echo "  install       Create venv + install all dependencies"
	@echo "  test          Run full test suite with coverage"
	@echo "  test-coverage Run pytest with HTML + term-missing coverage report"
	@echo "  test-smoke    Run smoke tests only (fast, import-level checks)"
	@echo "  test-integration  Run integration tests only"
	@echo "  lint          Run ruff linter"
	@echo "  format        Auto-format with ruff + black"
	@echo "  daily         Run today's daily screen"
	@echo "  bootstrap     Full history download + feature compute"
	@echo "  backtest      Run backtest (pass START= END= env vars)"
	@echo "  rebuild       Recompute all features from scratch"
	@echo "  api           Start FastAPI server (dev mode)"
	@echo "  dashboard     Start Streamlit dashboard"
	@echo "  paper-reset   Reset paper trading portfolio"
	@echo "  deploy        Install systemd services on this host"
	@echo "  status        Show systemd service / timer status"
	@echo "  logs          Last 50 lines from daily pipeline journal"
	@echo "  logs-api      Last 50 lines from API service journal"

install:
	pip install -e ".[dev]"

test:
	$(PYTEST) tests/ -v --cov=. --cov-report=term-missing

test-coverage:
	$(PYTEST) tests/ --cov=. --cov-report=html --cov-report=term-missing
	@echo "Coverage report: htmlcov/index.html"

test-smoke:
	$(PYTEST) tests/smoke/ -v

test-integration:
	$(PYTEST) tests/integration/ -v

lint:
	.venv/bin/ruff check . && .venv/bin/ruff format --check .

format:
	.venv/bin/ruff format .

daily:
	python scripts/run_daily.py --date today

bootstrap:
	python scripts/bootstrap.py --universe nifty500

watchlist-only:
	python scripts/run_daily.py --watchlist-only

backtest:
	$(PYTHON) scripts/backtest_runner.py --start $(START) --end $(END) --universe nifty500

rebuild:
	$(PYTHON) scripts/rebuild_features.py --universe nifty500

api:
	$(PYTHON) -m uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload

dashboard:
	$(PYTHON) -m streamlit run dashboard/app.py --server.port 8501

paper-reset:
	$(PYTHON) -c "from paper_trading.simulator import reset_portfolio; reset_portfolio(confirm=True)"

deploy:
	bash deploy/install.sh

status:
	systemctl status minervini-daily.timer minervini-api.service minervini-dashboard.service

logs:
	journalctl -u minervini-daily.service -n 50 --no-pager

logs-api:
	journalctl -u minervini-api.service -n 50 --no-pager
