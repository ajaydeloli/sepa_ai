"""
tests/smoke/test_smoke.py
--------------------------
Smoke tests — fast sanity checks that run before any unit/integration tests.

Each test asserts that the application *loads correctly*, with no real I/O,
no real HTTP calls, and no real databases outside tmp_path.

Coverage goals
--------------
- All main modules importable without error.
- config/settings.yaml loads and validates required keys.
- SQLiteStore creates all required tables on init.
- Key public functions are callable with minimal inputs.
"""
from __future__ import annotations

import importlib
import sqlite3
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# 1. Import smoke tests
# ---------------------------------------------------------------------------

class TestImportsAllModules:
    """Every top-level package must import cleanly."""

    @pytest.mark.parametrize("module_path", [
        "ingestion.base",
        "ingestion.yfinance_source",
        "ingestion.validator",
        "ingestion.universe_loader",
        "ingestion.source_factory",
        "features.feature_store",
        "features.moving_averages",
        "features.relative_strength",
        "features.sector_rs",
        "features.vcp",
        "features.volume",
        "features.atr",
        "features.pivot",
        "rules.stage",
        "rules.trend_template",
        "rules.scorer",
        "rules.vcp_rules",
        "rules.entry_trigger",
        "rules.stop_loss",
        "rules.risk_reward",
        "rules.fundamental_template",
        "screener.pipeline",
        "screener.pre_filter",
        "screener.results",
        "storage.sqlite_store",
        "storage.parquet_store",
        "pipeline.context",
        "pipeline.runner",
        "reports.daily_watchlist",
        "alerts.alert_deduplicator",
        "alerts.telegram_alert",
        "utils.logger",
        "utils.trading_calendar",
        "utils.exceptions",
        "paper_trading.simulator",
        "paper_trading.portfolio",
        "paper_trading.report",
        "backtest.engine",
        "backtest.metrics",
        "llm.llm_client",
        "llm.explainer",
    ])
    def test_module_importable(self, module_path: str):
        """Import the module — raises ImportError → test fails."""
        mod = importlib.import_module(module_path)
        assert mod is not None, f"importlib returned None for {module_path}"


# ---------------------------------------------------------------------------
# 2. Config load smoke test
# ---------------------------------------------------------------------------

class TestConfigLoads:
    """config/settings.yaml must load without errors and have required keys."""

    def test_settings_yaml_loads(self):
        import yaml

        settings_path = (
            Path(__file__).resolve().parent.parent.parent / "config" / "settings.yaml"
        )
        assert settings_path.exists(), f"settings.yaml not found at {settings_path}"

        with settings_path.open() as fh:
            config = yaml.safe_load(fh)

        assert isinstance(config, dict)

        for section in ("universe", "data", "watchlist", "stage",
                        "trend_template", "vcp", "scoring"):
            assert section in config, f"Missing required config section: {section!r}"

        assert config["universe"]["source"] in (
            "yfinance", "angel_one", "upstox"
        ), f"Unexpected universe.source: {config['universe']['source']!r}"

    def test_required_data_paths_present(self):
        import yaml

        settings_path = (
            Path(__file__).resolve().parent.parent.parent / "config" / "settings.yaml"
        )
        with settings_path.open() as fh:
            config = yaml.safe_load(fh)

        for key in ("raw_dir", "processed_dir", "features_dir"):
            assert key in config["data"], f"Missing data.{key} in settings.yaml"


# ---------------------------------------------------------------------------
# 3. SQLiteStore table creation smoke test
# ---------------------------------------------------------------------------

class TestSQLiteStoreCreatesTables:
    """SQLiteStore creates all required tables when initialised."""

    def test_all_required_tables_created(self, tmp_path):
        from storage.sqlite_store import SQLiteStore

        db_path = tmp_path / "smoke_test.db"
        SQLiteStore(str(db_path))   # triggers schema creation

        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        actual_tables = {r[0] for r in rows}

        required = {"watchlist", "run_history", "screen_results", "alerts"}
        missing  = required - actual_tables
        assert not missing, f"SQLiteStore is missing tables: {missing}"

    def test_watchlist_crud(self, tmp_path):
        from storage.sqlite_store import SQLiteStore

        db = SQLiteStore(str(tmp_path / "crud.db"))
        db.add_symbol("SMOKETEST", note="unit test", added_via="smoke")

        rows = db.get_watchlist()
        assert any(r["symbol"] == "SMOKETEST" for r in rows)

        db.remove_symbol("SMOKETEST")
        rows2 = db.get_watchlist()
        assert not any(r["symbol"] == "SMOKETEST" for r in rows2)

    def test_save_and_get_result(self, tmp_path):
        from datetime import date
        from storage.sqlite_store import SQLiteStore

        db = SQLiteStore(str(tmp_path / "result.db"))
        run_date = date(2025, 1, 1)
        payload = {
            "symbol": "SMOKEUP",
            "stage": 2,
            "score": 87.5,
            "setup_quality": "A+",
            "trend_template_pass": True,
            "vcp_qualified": True,
            "breakout_triggered": True,
            "rs_rating": 91,
            "entry_price": 155.0,
            "stop_loss": 143.0,
            "risk_pct": 7.74,
        }
        db.save_result(run_date, payload)
        fetched = db.get_result("SMOKEUP", run_date)
        assert fetched is not None
        assert fetched["score"] == pytest.approx(87.5, abs=0.01)
        assert fetched["setup_quality"] == "A+"


# ---------------------------------------------------------------------------
# 4. Pre-filter smoke test
# ---------------------------------------------------------------------------

class TestPreFilterSmoke:
    """pre_filter must handle empty input without errors."""

    def test_empty_universe_returns_empty(self, tmp_path):
        from screener.pre_filter import build_features_index, pre_filter

        cfg = {
            "data": {"features_dir": str(tmp_path / "features")},
            "pre_filter": {"min_close_pct_of_52w_high": 0.70, "min_rs_rating": 50},
        }
        index = build_features_index([], cfg)
        passed = pre_filter(index, cfg)
        assert passed == []


# ---------------------------------------------------------------------------
# 5. Report generation smoke tests
# ---------------------------------------------------------------------------

class TestReportGenerationSmoke:
    """generate_csv_report / generate_html_report must not raise on zero results."""

    def test_csv_report_no_results(self, tmp_path):
        from datetime import date
        from reports.daily_watchlist import generate_csv_report

        csv_path = generate_csv_report(
            results=[],
            output_dir=str(tmp_path),
            run_date=date(2025, 1, 1),
        )
        p = Path(csv_path)
        assert p.exists()
        assert p.stat().st_size > 0, "CSV must contain sentinel row even for zero results"

    def test_html_report_no_results(self, tmp_path):
        from datetime import date
        from reports.daily_watchlist import generate_html_report

        html_path = generate_html_report(
            results=[],
            output_dir=str(tmp_path),
            run_date=date(2025, 1, 1),
        )
        p = Path(html_path)
        assert p.exists()
        assert p.stat().st_size > 0


# ---------------------------------------------------------------------------
# 6. Rules module callability smoke tests (critical-path coverage gate)
# ---------------------------------------------------------------------------

class TestRulesModulesSmoke:
    """Key rule functions are importable and callable (critical-path 100% gate)."""

    @pytest.mark.parametrize("module,func", [
        ("rules.stage",          "detect_stage"),
        ("rules.trend_template", "check_trend_template"),
        ("rules.scorer",         "score_symbol"),
        ("rules.vcp_rules",      "qualify_vcp"),
        ("rules.entry_trigger",  "check_entry_trigger"),
        ("rules.stop_loss",      "compute_stop_loss"),
        ("rules.risk_reward",    "compute_risk_reward"),
    ])
    def test_function_callable(self, module: str, func: str):
        mod = importlib.import_module(module)
        fn  = getattr(mod, func, None)
        assert callable(fn), f"{module}.{func} is not callable"


# ---------------------------------------------------------------------------
# 7. Feature module smoke tests
# ---------------------------------------------------------------------------

class TestFeatureModulesSmoke:
    """features/ public functions are callable (critical-path 100% gate)."""

    @pytest.mark.parametrize("module,func", [
        ("features.moving_averages",   "compute"),          # public API is compute()
        ("features.relative_strength", "run_rs_rating_pass"),
        ("features.sector_rs",         "compute_sector_ranks"),
        ("features.atr",               "compute"),          # public API is compute()
        ("features.volume",            "compute"),          # public API is compute()
    ])
    def test_function_callable(self, module: str, func: str):
        mod = importlib.import_module(module)
        fn  = getattr(mod, func, None)
        assert callable(fn), f"{module}.{func} is not callable"


# ---------------------------------------------------------------------------
# 8. Screener pre_filter smoke test (critical-path 100% gate)
# ---------------------------------------------------------------------------

class TestScreenerPreFilterSmoke:
    """screener/pre_filter.py public API is importable and callable."""

    def test_build_features_index_callable(self):
        from screener.pre_filter import build_features_index
        assert callable(build_features_index)

    def test_pre_filter_callable(self):
        from screener.pre_filter import pre_filter
        assert callable(pre_filter)
