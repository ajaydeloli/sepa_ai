"""
tests/unit/test_daily_watchlist.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Unit tests for reports/daily_watchlist.py
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

import pytest

from reports.daily_watchlist import (
    generate_csv_report,
    generate_html_report,
    get_report_summary,
)
from rules.scorer import SEPAResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_result(
    symbol: str,
    score: int,
    setup_quality: str = "A",
    stage: int = 2,
    entry_price: float = 100.0,
    stop_loss: float = 95.0,
    rs_rating: int = 90,
    vcp_qualified: bool = True,
    breakout_triggered: bool = False,
    conditions_met: int = 8,
    run_date: date = date(2025, 7, 14),
) -> SEPAResult:
    """Convenience factory aligned with the actual SEPAResult dataclass fields."""
    return SEPAResult(
        symbol=symbol,
        run_date=run_date,
        stage=stage,
        stage_label=f"Stage {stage}",
        stage_confidence=80,
        trend_template_pass=(conditions_met >= 6),
        trend_template_details={},
        conditions_met=conditions_met,
        vcp_qualified=vcp_qualified,
        breakout_triggered=breakout_triggered,
        entry_price=entry_price,
        stop_loss=stop_loss,
        risk_pct=round(abs(entry_price - stop_loss) / entry_price * 100, 2)
            if entry_price else None,
        rs_rating=rs_rating,
        setup_quality=setup_quality,
        score=score,
    )


@pytest.fixture()
def sample_results() -> list[SEPAResult]:
    return [
        _make_result("AAPL", 92, "A+", stage=2),
        _make_result("MSFT", 85, "A",  stage=2),
        _make_result("GOOG", 75, "B",  stage=1),
        _make_result("AMZN", 60, "C",  stage=3),
        _make_result("META", 40, "FAIL", stage=1),
    ]


@pytest.fixture()
def run_date() -> date:
    return date(2025, 7, 14)


# ---------------------------------------------------------------------------
# Test 1 – CSV has correct columns and correct row count
# ---------------------------------------------------------------------------

class TestGenerateCsvReport:
    def test_correct_columns(self, sample_results, run_date, tmp_path):
        path = generate_csv_report(sample_results, str(tmp_path), run_date)
        with open(path, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            assert reader.fieldnames == [
                "rank", "symbol", "score", "setup_quality", "stage",
                "conditions_met", "vcp_qualified", "breakout_triggered",
                "entry_price", "stop_loss", "risk_pct", "rs_rating", "is_watchlist",
            ]

    def test_row_count_top_quality_only(self, sample_results, run_date, tmp_path):
        """By default only A+ and A rows appear."""
        path = generate_csv_report(sample_results, str(tmp_path), run_date)
        with open(path, newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        # AAPL (A+) + MSFT (A) = 2 rows
        assert len(rows) == 2

    def test_output_filename(self, sample_results, run_date, tmp_path):
        path = generate_csv_report(sample_results, str(tmp_path), run_date)
        assert Path(path).name == f"watchlist_{run_date}.csv"


    # -----------------------------------------------------------------------
    # Test 2 – Rows are sorted by score DESC
    # -----------------------------------------------------------------------
    def test_sorted_by_score_desc(self, sample_results, run_date, tmp_path):
        path = generate_csv_report(
            sample_results, str(tmp_path), run_date, include_all=True
        )
        with open(path, newline="", encoding="utf-8") as fh:
            scores = [float(r["score"]) for r in csv.DictReader(fh)
                      if r["symbol"] != "No candidates"]
        assert scores == sorted(scores, reverse=True)

    # -----------------------------------------------------------------------
    # Test 3 – Watchlist symbols have is_watchlist=True
    # -----------------------------------------------------------------------
    def test_watchlist_flag(self, sample_results, run_date, tmp_path):
        # GOOG is B-quality but forced onto watchlist
        path = generate_csv_report(
            sample_results, str(tmp_path), run_date, watchlist_symbols=["GOOG"]
        )
        with open(path, newline="", encoding="utf-8") as fh:
            rows = {r["symbol"]: r for r in csv.DictReader(fh)}
        assert rows["GOOG"]["is_watchlist"] == "True"
        assert rows["AAPL"]["is_watchlist"] == "False"


# ---------------------------------------------------------------------------
# Test 4 – HTML report creates valid HTML with <table> and quality badges
# ---------------------------------------------------------------------------

class TestGenerateHtmlReport:
    def test_creates_html_file(self, sample_results, run_date, tmp_path):
        path = generate_html_report(sample_results, str(tmp_path), run_date)
        assert Path(path).exists()
        assert path.endswith(".html")

    def test_contains_table(self, sample_results, run_date, tmp_path):
        path = generate_html_report(sample_results, str(tmp_path), run_date)
        html = Path(path).read_text(encoding="utf-8")
        assert "<table" in html
        assert "</table>" in html

    def test_quality_badges_present(self, sample_results, run_date, tmp_path):
        path = generate_html_report(
            sample_results, str(tmp_path), run_date, include_all=True
        )
        html = Path(path).read_text(encoding="utf-8")
        assert "badge-aplus" in html
        assert "badge-a" in html

    def test_watchlist_star_present(self, sample_results, run_date, tmp_path):
        path = generate_html_report(
            sample_results, str(tmp_path), run_date, watchlist_symbols=["MSFT"]
        )
        html = Path(path).read_text(encoding="utf-8")
        assert "★" in html

    def test_llm_brief_shown_when_provided(self, sample_results, run_date, tmp_path):
        briefs = {"AAPL": "Strong breakout above 52-week high on above-average volume."}
        path = generate_html_report(
            sample_results, str(tmp_path), run_date, llm_briefs=briefs
        )
        html = Path(path).read_text(encoding="utf-8")
        assert "AI Brief:" in html
        assert "Strong breakout" in html

    def test_llm_brief_hidden_when_none(self, sample_results, run_date, tmp_path):
        path = generate_html_report(sample_results, str(tmp_path), run_date)
        html = Path(path).read_text(encoding="utf-8")
        assert "AI Brief:" not in html

    def test_llm_brief_hidden_when_empty_dict(self, sample_results, run_date, tmp_path):
        """llm_briefs={} → no brief section rendered, no crash."""
        path = generate_html_report(
            sample_results, str(tmp_path), run_date, llm_briefs={}
        )
        html = Path(path).read_text(encoding="utf-8")
        assert "AI Brief:" not in html

    def test_watchlist_summary_shown_when_provided(self, sample_results, run_date, tmp_path):
        """watchlist_summary string → market-commentary section appears in HTML."""
        summary_text = "Broad market participation with 8 quality setups across sectors."
        path = generate_html_report(
            sample_results, str(tmp_path), run_date,
            watchlist_summary=summary_text,
        )
        html = Path(path).read_text(encoding="utf-8")
        assert "market-commentary" in html
        assert "Daily Market Commentary" in html
        assert "Broad market participation" in html

    def test_watchlist_summary_absent_when_none(self, sample_results, run_date, tmp_path):
        """No watchlist_summary → commentary section not rendered, no crash."""
        path = generate_html_report(sample_results, str(tmp_path), run_date)
        html = Path(path).read_text(encoding="utf-8")
        assert "Daily Market Commentary" not in html

    def test_brief_text_is_html_escaped(self, sample_results, run_date, tmp_path):
        """Brief containing <, >, & is escaped by Jinja2 autoescape — never raw HTML."""
        briefs = {"AAPL": "EPS > 20% & revenue < estimates; watch <AAPL>."}
        path = generate_html_report(
            sample_results, str(tmp_path), run_date, llm_briefs=briefs
        )
        html = Path(path).read_text(encoding="utf-8")
        # Raw dangerous characters must not appear unescaped in the brief section
        assert "<AAPL>" not in html
        # Jinja2 autoescape should have encoded them as HTML entities
        assert "&lt;" in html or "&#" in html

    def test_run_date_in_title(self, sample_results, run_date, tmp_path):
        path = generate_html_report(sample_results, str(tmp_path), run_date)
        html = Path(path).read_text(encoding="utf-8")
        assert str(run_date) in html


# ---------------------------------------------------------------------------
# Test 5 – get_report_summary counts correctly
# ---------------------------------------------------------------------------

class TestGetReportSummary:
    def test_counts_known_list(self, sample_results):
        summary = get_report_summary(sample_results)
        assert summary["total_screened"] == 5
        assert summary["a_plus"] == 1        # AAPL
        assert summary["a"] == 1             # MSFT
        assert summary["b"] == 1             # GOOG
        assert summary["c"] == 1             # AMZN
        assert summary["fail"] == 1          # META
        assert summary["stage2_count"] == 2  # AAPL + MSFT are stage=2

    def test_all_keys_present(self, sample_results):
        summary = get_report_summary(sample_results)
        required = {"total_screened", "a_plus", "a", "b", "c", "fail", "stage2_count"}
        assert required.issubset(summary.keys())

    def test_unknown_quality_counted_as_fail(self):
        r = _make_result("XYZ", 50, setup_quality="UNKNOWN")
        summary = get_report_summary([r])
        assert summary["fail"] == 1


# ---------------------------------------------------------------------------
# Test 6 – Empty results list → no crash, sentinel "No candidates" row
# ---------------------------------------------------------------------------

class TestEmptyResults:
    def test_csv_no_crash(self, run_date, tmp_path):
        path = generate_csv_report([], str(tmp_path), run_date)
        assert Path(path).exists()
        with open(path, newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        assert len(rows) == 1
        assert "No candidates" in rows[0]["symbol"]

    def test_html_no_crash(self, run_date, tmp_path):
        path = generate_html_report([], str(tmp_path), run_date)
        html = Path(path).read_text(encoding="utf-8")
        assert "<table" in html
        assert "No candidates" in html

    def test_summary_all_zeros(self):
        summary = get_report_summary([])
        assert summary["total_screened"] == 0
        assert all(v == 0 for k, v in summary.items() if k != "total_screened")


# ---------------------------------------------------------------------------
# Phase 5 — Test P6: HTML shows F-conditions table when fundamental_details present
# ---------------------------------------------------------------------------

class TestHtmlFundamentals:
    """P6 & P7: fundamental_details in SEPAResult drives fundamentals card in HTML."""

    def _make_result_with_fundamentals(self) -> SEPAResult:
        """SEPAResult with a fully populated fundamental_details dict."""
        fd = {
            "passes": True,
            "conditions_met": 7,
            "f1_eps_positive": True,
            "f2_eps_accelerating": True,
            "f3_sales_growth": True,
            "f4_roe": True,
            "f5_de_ratio": True,
            "f6_promoter_holding": True,
            "f7_profit_growth": True,
            "score": 100,
            "hard_fails": [],
            "values": {
                "eps": 12.5,
                "eps_accelerating": True,
                "sales_growth_yoy": 25.0,
                "roe": 24.3,
                "de_ratio": 0.4,
                "promoter_holding": 52.1,
                "profit_growth": 18.0,
            },
        }
        r = _make_result("FUNDCO", 88, "A+", stage=2)
        # Patch in Phase 5 fields
        r.fundamental_pass = True
        r.fundamental_details = fd
        r.news_score = 42.0
        return r

    def _make_result_no_fundamentals(self) -> SEPAResult:
        """SEPAResult with empty fundamental_details (Phase 3 / data unavailable)."""
        r = _make_result("NOFUND", 72, "A", stage=2)
        r.fundamental_pass = False
        r.fundamental_details = {}
        r.news_score = None
        return r

    def test_fundamental_conditions_table_shown(self, run_date, tmp_path):
        """P6: HTML with fundamental_details → fund-card div with F-conditions."""
        results = [self._make_result_with_fundamentals()]
        path = generate_html_report(results, str(tmp_path), run_date, include_all=True)
        html = Path(path).read_text(encoding="utf-8")
        assert "fund-card" in html, "Expected fundamental conditions card in HTML"
        assert "F1 EPS" in html
        assert "F4 ROE" in html

    def test_fundamental_details_values_shown(self, run_date, tmp_path):
        """ROE, D/E, and promoter holding values should appear in the card."""
        results = [self._make_result_with_fundamentals()]
        path = generate_html_report(results, str(tmp_path), run_date, include_all=True)
        html = Path(path).read_text(encoding="utf-8")
        assert "24.3" in html   # ROE
        assert "0.40" in html   # D/E
        assert "52.1" in html   # Promoter

    def test_news_indicator_positive(self, run_date, tmp_path):
        """news_score=42 → 🟢 Positive indicator in HTML."""
        results = [self._make_result_with_fundamentals()]
        path = generate_html_report(results, str(tmp_path), run_date, include_all=True)
        html = Path(path).read_text(encoding="utf-8")
        assert "Positive" in html or "+42" in html

    def test_eps_badge_accelerating(self, run_date, tmp_path):
        """f2_eps_accelerating=True → EPS badge shows ▲ Accelerating."""
        results = [self._make_result_with_fundamentals()]
        path = generate_html_report(results, str(tmp_path), run_date, include_all=True)
        html = Path(path).read_text(encoding="utf-8")
        assert "Accelerating" in html

    def test_empty_fundamental_details_shows_na(self, run_date, tmp_path):
        """P7: empty fundamental_details → 'Fundamentals: N/A' shown, no crash."""
        results = [self._make_result_no_fundamentals()]
        path = generate_html_report(results, str(tmp_path), run_date, include_all=True)
        assert Path(path).exists()
        html = Path(path).read_text(encoding="utf-8")
        assert "N/A" in html or "fund-na" in html

    def test_empty_fundamental_details_no_fund_card(self, run_date, tmp_path):
        """No fund-card should be rendered when fundamental_details is empty."""
        results = [self._make_result_no_fundamentals()]
        path = generate_html_report(results, str(tmp_path), run_date, include_all=True)
        html = Path(path).read_text(encoding="utf-8")
        assert "F1 EPS" not in html
        assert "F4 ROE" not in html
