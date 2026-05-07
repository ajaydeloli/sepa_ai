"""
tests/unit/test_schemas.py
--------------------------
Unit tests for the Pydantic schema layer (api/schemas/).

Test inventory
--------------
1. test_stock_result_schema_from_sepa_result
   StockResultSchema validates a dict produced by dataclasses.asdict(SEPAResult).
   Covers: date coercion, Literal setup_quality, all Optional fields round-trip.

2. test_api_response_list_serialises
   APIResponse[list[StockResultSchema]] serialises and round-trips correctly.

3. test_portfolio_summary_schema_from_get_summary
   PortfolioSummarySchema validates the dict returned by Portfolio.get_summary().

4. test_all_optional_fields_default_to_none
   Constructing StockResultSchema with only required fields leaves every
   Optional field at None without raising a ValidationError.

5. test_api_response_error_shape
   APIResponse with error → success=False, error set, data can be None.
"""

from __future__ import annotations

import dataclasses
from datetime import date
from typing import Any

import pytest
from pydantic import ValidationError

from api.schemas.common import APIResponse, ErrorResponse, PaginationMeta
from api.schemas.portfolio import PortfolioSummarySchema, SummaryPositionSchema
from api.schemas.stock import StockResultSchema, TrendTemplateSchema, VCPSchema
from paper_trading.portfolio import ClosedTrade, Portfolio, Position
from rules.scorer import SEPAResult


# ---------------------------------------------------------------------------
# Helpers / shared fixtures
# ---------------------------------------------------------------------------


def _make_sepa_result(**overrides: Any) -> SEPAResult:
    """Return a minimal but fully-valid SEPAResult for schema tests.

    trend_template_details is populated with TrendTemplateSchema-compatible
    keys (condition booleans) so that the nested schema validates cleanly.
    vcp_details uses VCPSchema-compatible keys (numeric metrics).
    """
    defaults: dict[str, Any] = dict(
        symbol="RELIANCE",
        run_date=date(2024, 11, 1),
        stage=2,
        stage_label="Stage 2 — Advancing",
        stage_confidence=85,
        trend_template_pass=True,
        # trend_template_details keyed for TrendTemplateSchema, not the raw
        # numeric details that score_symbol stores.
        trend_template_details={
            "passes": True,
            "conditions_met": 8,
            "condition_1": True,
            "condition_2": True,
            "condition_3": True,
            "condition_4": True,
            "condition_5": True,
            "condition_6": True,
            "condition_7": True,
            "condition_8": True,
        },
        conditions_met=8,
        fundamental_pass=True,
        fundamental_details={},
        vcp_qualified=True,
        # vcp_details keyed for VCPSchema (numeric metrics from VCPMetrics)
        vcp_details={
            "qualified": True,
            "contraction_count": 3,
            "max_depth_pct": 18.5,
            "final_depth_pct": 6.2,
            "vol_contraction_ratio": 0.45,
            "base_length_weeks": 7,
            "tightness_score": 4.1,
        },
        breakout_triggered=True,
        entry_price=2450.0,
        stop_loss=2350.0,
        risk_pct=4.08,
        target_price=2750.0,
        reward_risk_ratio=3.0,
        rs_rating=88,
        sector_bonus=5,
        news_score=0.6,
        setup_quality="A+",
        score=91,
    )
    defaults.update(overrides)
    return SEPAResult(**defaults)


def _sepa_result_to_schema_dict(result: SEPAResult) -> dict[str, Any]:
    """Convert SEPAResult → dict suitable for StockResultSchema.

    dataclasses.asdict() is used for the top-level fields; run_date is left
    as a date object so the field_validator coercion path is exercised.
    sector_bonus is present in SEPAResult but not in StockResultSchema —
    it is deliberately dropped here (schema-layer concern).
    """
    d = dataclasses.asdict(result)
    # sector_bonus and fundamental_details are on SEPAResult but not on the
    # schema — drop them so model_validate does not fail on extra fields.
    d.pop("sector_bonus", None)
    d.pop("fundamental_details", None)
    return d


# ---------------------------------------------------------------------------
# 1. StockResultSchema validates SEPAResult dict (via dataclasses.asdict)
# ---------------------------------------------------------------------------


def test_stock_result_schema_from_sepa_result() -> None:
    """StockResultSchema.model_validate(dataclasses.asdict(sepa_result)) succeeds."""
    result = _make_sepa_result()
    d = _sepa_result_to_schema_dict(result)

    schema = StockResultSchema.model_validate(d)

    # Core identity fields
    assert schema.symbol == "RELIANCE"
    assert schema.run_date == "2024-11-01"          # date coerced to str
    assert schema.score == 91
    assert schema.setup_quality == "A+"

    # Stage
    assert schema.stage == 2
    assert schema.stage_confidence == 85

    # Trend template
    assert schema.trend_template_pass is True
    assert schema.conditions_met == 8
    assert isinstance(schema.trend_template_details, TrendTemplateSchema)
    assert schema.trend_template_details.condition_1 is True
    assert schema.trend_template_details.conditions_met == 8

    # VCP
    assert schema.vcp_qualified is True
    assert isinstance(schema.vcp_details, VCPSchema)
    assert schema.vcp_details.contraction_count == 3
    assert schema.vcp_details.tightness_score == pytest.approx(4.1)

    # Entry / risk
    assert schema.entry_price == pytest.approx(2450.0)
    assert schema.stop_loss == pytest.approx(2350.0)
    assert schema.reward_risk_ratio == pytest.approx(3.0)

    # RS
    assert schema.rs_rating == 88

    # API-layer extras default correctly
    assert schema.is_watchlist is False
    assert schema.llm_brief is None


# ---------------------------------------------------------------------------
# 2. APIResponse[list[StockResultSchema]] serialises correctly
# ---------------------------------------------------------------------------


def test_api_response_list_serialises() -> None:
    """APIResponse[list[StockResultSchema]] serialises without error."""
    r1 = StockResultSchema.model_validate(_sepa_result_to_schema_dict(_make_sepa_result()))
    r2 = StockResultSchema.model_validate(
        _sepa_result_to_schema_dict(
            _make_sepa_result(
                symbol="TCS",
                score=76,
                setup_quality="A",
                rs_rating=81,
                trend_template_details={
                    "passes": True,
                    "conditions_met": 8,
                    **{f"condition_{i}": True for i in range(1, 9)},
                },
            )
        )
    )

    response: APIResponse[list[StockResultSchema]] = APIResponse(
        success=True,
        data=[r1, r2],
        meta={"total": 2, "page": 1, "per_page": 20},
    )

    payload = response.model_dump()

    assert payload["success"] is True
    assert payload["error"] is None
    assert len(payload["data"]) == 2
    assert payload["data"][0]["symbol"] == "RELIANCE"
    assert payload["data"][1]["symbol"] == "TCS"
    assert payload["meta"]["total"] == 2

    # Round-trip: re-validate from dict
    reparsed: APIResponse[list[StockResultSchema]] = APIResponse[
        list[StockResultSchema]
    ].model_validate(payload)
    assert reparsed.data[1].setup_quality == "A"



# ---------------------------------------------------------------------------
# 3. PortfolioSummarySchema validates portfolio.get_summary() output
# ---------------------------------------------------------------------------


def _build_portfolio_with_trades() -> tuple[Portfolio, dict[str, float]]:
    """Build a Portfolio with one open position and one closed trade."""
    config: dict = {}
    portfolio = Portfolio(initial_capital=500_000.0, config=config)

    # Open position — INFY
    pos = Position(
        symbol="INFY",
        entry_date=date(2024, 10, 1),
        entry_price=1800.0,
        quantity=100,
        stop_loss=1700.0,
        target_price=2100.0,
        sepa_score=78,
        setup_quality="A",
        trailing_stop=1720.0,
        days_held=22,
    )
    portfolio.add_position(pos)

    # Closed trade — HDFCBANK (manually append to bypass close_position flow)
    closed = ClosedTrade(
        symbol="HDFCBANK",
        entry_date=date(2024, 9, 1),
        exit_date=date(2024, 9, 20),
        entry_price=1500.0,
        exit_price=1650.0,
        quantity=50,
        pnl=7500.0,
        pnl_pct=10.0,
        exit_reason="target",
        r_multiple=1.5,
    )
    portfolio.closed_trades.append(closed)

    current_prices = {"INFY": 1950.0}
    return portfolio, current_prices


def test_portfolio_summary_schema_from_get_summary() -> None:
    """PortfolioSummarySchema.model_validate(portfolio.get_summary()) succeeds."""
    portfolio, current_prices = _build_portfolio_with_trades()
    summary = portfolio.get_summary(current_prices)

    schema = PortfolioSummarySchema.model_validate(summary)

    # Cash and values
    assert schema.cash == pytest.approx(500_000.0 - 1800.0 * 100, abs=1.0)
    assert schema.total_value > schema.cash          # open position has gain
    assert schema.initial_capital == pytest.approx(500_000.0)

    # Trade counts
    assert schema.total_trades == 1
    assert schema.open_count == 1
    assert schema.closed_count == 1

    # Win rate: 1 win / 1 trade = 1.0
    assert schema.win_rate == pytest.approx(1.0)

    # Positions list uses SummaryPositionSchema
    assert len(schema.positions) == 1
    pos = schema.positions[0]
    assert isinstance(pos, SummaryPositionSchema)
    assert pos.symbol == "INFY"
    assert pos.current_price == pytest.approx(1950.0)
    assert pos.quality == "A"
    assert pos.trailing_stop == pytest.approx(1720.0)


# ---------------------------------------------------------------------------
# 4. All Optional fields default to None without error
# ---------------------------------------------------------------------------


def test_all_optional_fields_default_to_none() -> None:
    """StockResultSchema accepts only required fields; all Optional fields → None."""
    minimal = StockResultSchema(
        symbol="WIPRO",
        run_date="2024-11-15",
        score=55,
        setup_quality="B",
        stage=2,
        stage_label="Stage 2 — Advancing",
        stage_confidence=70,
        trend_template_pass=True,
        conditions_met=7,
        vcp_qualified=False,
        breakout_triggered=False,
        rs_rating=72,
    )

    assert minimal.entry_price is None
    assert minimal.stop_loss is None
    assert minimal.risk_pct is None
    assert minimal.target_price is None
    assert minimal.reward_risk_ratio is None
    assert minimal.news_score is None
    assert minimal.trend_template_details is None
    assert minimal.vcp_details is None
    assert minimal.llm_brief is None
    assert minimal.fundamental_pass is False
    assert minimal.is_watchlist is False


# ---------------------------------------------------------------------------
# 5. APIResponse with error → success=False, error set, data can be None
# ---------------------------------------------------------------------------


def test_api_response_error_shape() -> None:
    """APIResponse error variant: success=False, error populated, data=None."""
    error_response: APIResponse[None] = APIResponse(
        success=False,
        data=None,
        error="Symbol not found",
        meta=None,
    )

    assert error_response.success is False
    assert error_response.error == "Symbol not found"
    assert error_response.data is None
    assert error_response.meta is None

    payload = error_response.model_dump()
    assert payload["success"] is False
    assert payload["error"] == "Symbol not found"
    assert payload["data"] is None

    # ErrorResponse schema also validates the same shape
    err = ErrorResponse(error="Symbol not found", detail="WIPRO not in universe")
    assert err.success is False
    assert err.detail == "WIPRO not in universe"


# ---------------------------------------------------------------------------
# 6. Bonus: setup_quality Literal rejects invalid values
# ---------------------------------------------------------------------------


def test_invalid_setup_quality_raises() -> None:
    """setup_quality outside Literal values raises ValidationError."""
    with pytest.raises(ValidationError):
        StockResultSchema(
            symbol="BAD",
            run_date="2024-11-01",
            score=60,
            setup_quality="S+",          # invalid grade
            stage=2,
            stage_label="Stage 2",
            stage_confidence=80,
            trend_template_pass=True,
            conditions_met=8,
            vcp_qualified=True,
            breakout_triggered=False,
            rs_rating=75,
        )
