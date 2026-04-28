"""
tests/unit/test_chart_generator.py
-----------------------------------
Unit tests for reports/chart_generator.py.

Test matrix (6 required + supplementary):
  1. generate_chart with valid OHLCV + SEPAResult → PNG created at expected path
  2. generate_chart with empty DataFrame → raises ChartGenerationError
  3. Missing sma_150 column → chart generates without sma_150 MA (no exception)
  4. generate_batch_charts: watchlist symbol with quality="C" still gets a chart
  5. generate_batch_charts: min_quality="B" skips non-watchlist "C" symbols
  6. Output directory is created automatically if it does not exist

All tests are self-contained: the MOCKUP fixture is loaded once per session,
a minimal SEPAResult is built by a factory helper, and no network I/O occurs.
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from features.vcp import VCPMetrics
from reports.chart_generator import generate_batch_charts, generate_chart
from rules.scorer import SEPAResult
from utils.exceptions import ChartGenerationError

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_FIXTURE_DIR = Path(__file__).parent.parent / "fixtures"
_OHLCV_FIXTURE = _FIXTURE_DIR / "sample_ohlcv_MOCKUP.parquet"

_TODAY = date(2025, 6, 1)
_SYMBOL = "MOCKUP"

# ---------------------------------------------------------------------------
# Fixture: shared OHLCV DataFrame (loaded once per session)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def ohlcv_df() -> pd.DataFrame:
    """Load the MOCKUP OHLCV fixture and add required MA columns."""
    df = pd.read_parquet(_OHLCV_FIXTURE)

    # Normalise column names to lowercase
    df.columns = [c.lower() for c in df.columns]

    # Compute MA columns if absent (needed by chart overlays)
    if "sma_50" not in df.columns:
        df["sma_50"] = df["close"].rolling(50, min_periods=1).mean()
    if "sma_150" not in df.columns:
        df["sma_150"] = df["close"].rolling(150, min_periods=1).mean()
    if "sma_200" not in df.columns:
        df["sma_200"] = df["close"].rolling(200, min_periods=1).mean()

    return df


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------

def _make_result(
    symbol: str = _SYMBOL,
    stage: int = 2,
    stage_label: str = "Stage 2 — Advancing",
    quality: str = "A",
    score: int = 75,
    trend_template_pass: bool = True,
    breakout_triggered: bool = False,
    entry_price: float | None = None,
    stop_loss: float | None = None,
    vcp_qualified: bool = True,
) -> SEPAResult:
    """Build a minimal SEPAResult suitable for chart generation tests."""
    return SEPAResult(
        symbol=symbol,
        run_date=_TODAY,
        stage=stage,
        stage_label=stage_label,
        stage_confidence=80,
        trend_template_pass=trend_template_pass,
        trend_template_details={},
        conditions_met=8,
        fundamental_pass=False,
        fundamental_details={},
        vcp_qualified=vcp_qualified,
        vcp_details={},
        breakout_triggered=breakout_triggered,
        entry_price=entry_price,
        stop_loss=stop_loss,
        risk_pct=None,
        target_price=None,
        reward_risk_ratio=None,
        rs_rating=80,
        sector_bonus=0,
        news_score=None,
        setup_quality=quality,
        score=score,
    )


def _make_vcp(contraction_count: int = 3, is_valid: bool = True) -> VCPMetrics:
    """Build a minimal VCPMetrics for chart overlay tests."""
    return VCPMetrics(
        contraction_count=contraction_count,
        max_depth_pct=18.0,
        final_depth_pct=7.0,
        vol_contraction_ratio=0.45,
        base_length_weeks=8,
        base_low=90.0,
        is_valid_vcp=is_valid,
        tightness_score=3.5,
    )


# ===========================================================================
# Test 1 — Valid OHLCV + SEPAResult → PNG created at expected path
# ===========================================================================

class TestGenerateChartSuccess:
    """Happy-path: chart is written to the expected path and is non-empty."""

    def test_file_created_at_expected_path(self, ohlcv_df, tmp_path):
        result = _make_result()
        out = generate_chart(
            symbol=_SYMBOL,
            ohlcv_df=ohlcv_df,
            result=result,
            vcp_metrics=_make_vcp(),
            output_dir=str(tmp_path),
            run_date=_TODAY,
        )
        expected = tmp_path / "charts" / f"{_SYMBOL}_{_TODAY}.png"
        assert Path(out) == expected
        assert expected.exists(), "Chart PNG was not created"

    def test_file_is_non_empty(self, ohlcv_df, tmp_path):
        result = _make_result()
        out = generate_chart(
            symbol=_SYMBOL,
            ohlcv_df=ohlcv_df,
            result=result,
            vcp_metrics=None,
            output_dir=str(tmp_path),
            run_date=_TODAY,
        )
        assert os.path.getsize(out) > 0, "Chart PNG is empty"

    def test_with_entry_and_stop_lines(self, ohlcv_df, tmp_path):
        """Entry + stop dashed lines should not raise."""
        close = float(ohlcv_df["close"].iloc[-1])
        result = _make_result(
            breakout_triggered=True,
            entry_price=close,
            stop_loss=close * 0.93,
        )
        out = generate_chart(
            symbol=_SYMBOL,
            ohlcv_df=ohlcv_df,
            result=result,
            vcp_metrics=_make_vcp(),
            output_dir=str(tmp_path),
            run_date=_TODAY,
        )
        assert Path(out).exists()

    def test_returns_string_path(self, ohlcv_df, tmp_path):
        result = _make_result()
        out = generate_chart(
            symbol=_SYMBOL, ohlcv_df=ohlcv_df, result=result,
            vcp_metrics=None, output_dir=str(tmp_path), run_date=_TODAY,
        )
        assert isinstance(out, str)


# ===========================================================================
# Test 2 — Empty DataFrame → raises ChartGenerationError
# ===========================================================================

class TestGenerateChartEmptyDf:
    """Passing an empty DataFrame must raise ChartGenerationError immediately."""

    def test_empty_df_raises(self, tmp_path):
        empty = pd.DataFrame()
        result = _make_result()
        with pytest.raises(ChartGenerationError):
            generate_chart(
                symbol=_SYMBOL,
                ohlcv_df=empty,
                result=result,
                vcp_metrics=None,
                output_dir=str(tmp_path),
                run_date=_TODAY,
            )

    def test_none_df_raises(self, tmp_path):
        result = _make_result()
        with pytest.raises(ChartGenerationError):
            generate_chart(
                symbol=_SYMBOL,
                ohlcv_df=None,   # type: ignore[arg-type]
                result=result,
                vcp_metrics=None,
                output_dir=str(tmp_path),
                run_date=_TODAY,
            )


# ===========================================================================
# Test 3 — Missing sma_150 → chart still generates (MA silently skipped)
# ===========================================================================

class TestMissingMaColumn:
    """When sma_150 is absent the chart should render without it — no exception."""

    def test_missing_sma_150_no_exception(self, ohlcv_df, tmp_path):
        df_no_ma = ohlcv_df.drop(columns=["sma_150"], errors="ignore").copy()
        result = _make_result()
        out = generate_chart(
            symbol=_SYMBOL,
            ohlcv_df=df_no_ma,
            result=result,
            vcp_metrics=None,
            output_dir=str(tmp_path),
            run_date=_TODAY,
        )
        assert Path(out).exists(), "Chart PNG not created when sma_150 is missing"

    def test_no_ma_columns_at_all(self, ohlcv_df, tmp_path):
        """Dropping all MA columns should also not raise."""
        df_bare = ohlcv_df.drop(
            columns=["sma_50", "sma_150", "sma_200"], errors="ignore"
        ).copy()
        result = _make_result()
        out = generate_chart(
            symbol=_SYMBOL,
            ohlcv_df=df_bare,
            result=result,
            vcp_metrics=None,
            output_dir=str(tmp_path),
            run_date=_TODAY,
        )
        assert Path(out).exists()


# ===========================================================================
# Test 4 — Watchlist symbol with quality="C" always gets a chart
# ===========================================================================

class TestBatchChartsWatchlist:
    """Watchlist symbols bypass the quality gate."""

    def test_c_quality_watchlist_symbol_charted(self, ohlcv_df, tmp_path):
        result_c = _make_result(symbol="LOWQ", quality="C", score=42)
        generated = generate_batch_charts(
            results=[result_c],
            ohlcv_data={"LOWQ": ohlcv_df},
            vcp_data={},
            output_dir=str(tmp_path),
            run_date=_TODAY,
            min_quality="B",
            watchlist_symbols=["LOWQ"],       # force-include
        )
        assert "LOWQ" in generated, "Watchlist 'C' symbol should always be charted"
        assert Path(generated["LOWQ"]).exists()

    def test_returns_dict_of_paths(self, ohlcv_df, tmp_path):
        result = _make_result()
        generated = generate_batch_charts(
            results=[result],
            ohlcv_data={_SYMBOL: ohlcv_df},
            vcp_data={},
            output_dir=str(tmp_path),
            run_date=_TODAY,
            min_quality="B",
        )
        assert isinstance(generated, dict)
        assert _SYMBOL in generated


# ===========================================================================
# Test 5 — min_quality="B" filter skips non-watchlist "C" symbols
# ===========================================================================

class TestBatchChartsQualityFilter:
    """Non-watchlist symbols below min_quality must be excluded."""

    def test_c_quality_skipped_when_not_on_watchlist(self, ohlcv_df, tmp_path):
        result_c = _make_result(symbol="POORQ", quality="C", score=42)
        generated = generate_batch_charts(
            results=[result_c],
            ohlcv_data={"POORQ": ohlcv_df},
            vcp_data={},
            output_dir=str(tmp_path),
            run_date=_TODAY,
            min_quality="B",
            watchlist_symbols=[],             # not on watchlist
        )
        assert "POORQ" not in generated, "C-quality non-watchlist symbol should be skipped"

    def test_b_quality_is_included(self, ohlcv_df, tmp_path):
        result_b = _make_result(symbol="MIDQ", quality="B", score=60)
        generated = generate_batch_charts(
            results=[result_b],
            ohlcv_data={"MIDQ": ohlcv_df},
            vcp_data={},
            output_dir=str(tmp_path),
            run_date=_TODAY,
            min_quality="B",
        )
        assert "MIDQ" in generated, "B-quality should be charted when min_quality='B'"

    def test_never_raises_on_bad_symbol(self, tmp_path):
        """Missing OHLCV entry must be logged and skipped — never raises."""
        result = _make_result(symbol="NOSUCHSYM", quality="A", score=72)
        generated = generate_batch_charts(
            results=[result],
            ohlcv_data={},           # no OHLCV for this symbol
            vcp_data={},
            output_dir=str(tmp_path),
            run_date=_TODAY,
            min_quality="B",
        )
        assert "NOSUCHSYM" not in generated


# ===========================================================================
# Test 6 — Output directory is created if it does not exist
# ===========================================================================

class TestOutputDirCreation:
    """generate_chart must create the charts/ sub-directory automatically."""

    def test_creates_nested_output_dir(self, ohlcv_df, tmp_path):
        new_dir = tmp_path / "deep" / "nested" / "run"
        assert not new_dir.exists(), "Pre-condition: directory must not exist yet"
        result = _make_result()
        out = generate_chart(
            symbol=_SYMBOL,
            ohlcv_df=ohlcv_df,
            result=result,
            vcp_metrics=None,
            output_dir=str(new_dir),
            run_date=_TODAY,
        )
        assert Path(out).exists(), "Chart PNG not created in new nested directory"

    def test_charts_subdir_created(self, ohlcv_df, tmp_path):
        result = _make_result()
        generate_chart(
            symbol=_SYMBOL, ohlcv_df=ohlcv_df, result=result,
            vcp_metrics=None, output_dir=str(tmp_path), run_date=_TODAY,
        )
        assert (tmp_path / "charts").is_dir()
