.PHONY: install test lint format daily bootstrap backtest rebuild api dashboard paper-reset help

PYTHON := .venv/bin/python
PIP    := .venv/bin/pip

help:
	@echo "Minervini SEPA — available targets:"
	@echo "  install       Create venv + install all dependencies"
	@echo "  test          Run full test suite with coverage"
	@echo "  lint          Run ruff linter"
	@echo "  format        Auto-format with ruff + black"
	@echo "  daily         Run today's daily screen"
	@echo "  bootstrap     Full history download + feature compute"
	@echo "  backtest      Run backtest (pass START= END= env vars)"
	@echo "  rebuild       Recompute all features from scratch"
	@echo "  api           Start FastAPI server (dev mode)"
	@echo "  dashboard     Start Streamlit dashboard"
	@echo "  paper-reset   Reset paper trading portfolio"

install:
	python3.11 -m venv .venv
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements-dev.txt
	$(PIP) install -e .
	@echo "✅  Environment ready. Activate with: source .venv/bin/activate"

test:
	$(PYTHON) -m pytest tests/ -v --cov=. --cov-report=term-missing --cov-report=html

lint:
	$(PYTHON) -m ruff check .

format:
	$(PYTHON) -m ruff format .
	$(PYTHON) -m black .

daily:
	$(PYTHON) scripts/run_daily.py --date today

bootstrap:
	$(PYTHON) scripts/bootstrap.py --universe nifty500

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
