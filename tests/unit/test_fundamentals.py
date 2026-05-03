"""
tests/unit/test_fundamentals.py
--------------------------------
Unit tests for ``ingestion/fundamentals.py``.

All HTTP calls are mocked — Screener.in is NEVER contacted in CI.

Test matrix
-----------
1.  Cache miss    → requests.get called, result saved, dict returned
2.  Cache hit     → requests.get NOT called, cached dict returned as-is
3.  Cache expired → requests.get called, fresh data saved
4.  HTTP 404      → returns None, no exception raised
5.  HTTP timeout  → returns None, no exception raised
6.  BS parse error → returns None, no exception raised
7.  force_refresh → requests.get called even when cache is valid
8.  Returned dict contains a valid ISO ``fetched_at`` timestamp
9.  eps_accelerating=True when latest QoQ growth > previous QoQ growth
10. fii_trend="rising" when last 3 FII quarters increase monotonically
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# Ensure sleep is always skipped in the entire test module
os.environ["SEPA_SKIP_SLEEP"] = "1"

import ingestion.fundamentals as fund  # noqa: E402  (import after env-var set)
from ingestion.fundamentals import (  # noqa: E402
    _compute_eps_metrics,
    _compute_fii_trend,
    clear_fundamentals_cache,
    fetch_fundamentals,
    get_fundamentals_age_days,
)

# ---------------------------------------------------------------------------
# Minimal Screener.in-like HTML fixture
# ---------------------------------------------------------------------------

_SAMPLE_HTML = """
<html><body>
<ul id="top-ratios">
  <li><span class="name">Stock P/E</span><span class="number">28.5</span></li>
  <li><span class="name">Price to Book</span><span class="number">4.2</span></li>
  <li><span class="name">Return on Equity</span><span class="number">22.3</span></li>
  <li><span class="name">ROCE</span><span class="number">26.1</span></li>
  <li><span class="name">Debt to equity</span><span class="number">0.35</span></li>
  <li><span class="name">Earning Per Share</span><span class="number">42.6</span></li>
</ul>

<section id="quarterly-results">
  <table class="data-table">
    <thead><tr><th>Quarter</th><th>Sep 23</th><th>Dec 23</th><th>Mar 24</th><th>Jun 24</th></tr></thead>
    <tbody>
      <tr><td>EPS in Rs</td><td>8.5</td><td>9.2</td><td>10.8</td><td>12.1</td></tr>
    </tbody>
  </table>
</section>

<h2>Shareholding Pattern</h2>
<table class="data-table">
  <thead><tr><th>Holder</th><th>Sep 23</th><th>Dec 23</th><th>Mar 24</th></tr></thead>
  <tbody>
    <tr><td>Promoters</td><td>51.0</td><td>51.5</td><td>52.4</td></tr>
    <tr><td>FII</td><td>16.2</td><td>17.5</td><td>18.7</td></tr>
    <tr><td>DII</td><td>10.1</td><td>10.3</td><td>10.8</td></tr>
    <tr><td>Public</td><td>22.7</td><td>20.7</td><td>18.1</td></tr>
  </tbody>
</table>

<section id="profit-loss">
  <table class="data-table">
    <thead><tr><th>Year</th><th>Mar 22</th><th>Mar 23</th><th>Mar 24</th></tr></thead>
    <tbody>
      <tr><td>Sales</td><td>3850.0</td><td>4570.0</td><td>5420.3</td></tr>
      <tr><td>Net Profit</td><td>412.0</td><td>501.5</td><td>612.8</td></tr>
    </tbody>
  </table>
</section>
</body></html>
"""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolate_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect the cache directory to a temp path for every test."""
    monkeypatch.setattr(fund, "_CACHE_DIR", tmp_path / "fundamentals")


def _mock_200(html: str = _SAMPLE_HTML) -> MagicMock:
    """Return a mock requests.Response with status_code=200."""
    resp = MagicMock()
    resp.status_code = 200
    resp.text = html
    return resp


def _mock_status(code: int) -> MagicMock:
    resp = MagicMock()
    resp.status_code = code
    resp.text = ""
    return resp


# ---------------------------------------------------------------------------
# Test 1 — Cache miss: HTTP called, result saved, dict returned
# ---------------------------------------------------------------------------

def test_cache_miss_fetches_and_saves(tmp_path: Path) -> None:
    """On cache miss, requests.get must be called and the result cached."""
    with patch("ingestion.fundamentals.requests.get", return_value=_mock_200()) as mock_get:
        result = fetch_fundamentals("TESTCO")

    assert mock_get.called, "requests.get must be called on cache miss"
    assert result is not None
    assert result["symbol"] == "TESTCO"

    # File must be written to cache
    cache_file = fund._CACHE_DIR / "TESTCO.json"
    assert cache_file.exists(), "Cache file must be created after a successful fetch"

    saved = json.loads(cache_file.read_text())
    assert saved["symbol"] == "TESTCO"
    assert "fetched_at" in saved


# ---------------------------------------------------------------------------
# Test 2 — Cache hit (within 7 days): NO HTTP call
# ---------------------------------------------------------------------------

def test_cache_hit_within_ttl_skips_http(tmp_path: Path) -> None:
    """Within TTL, the cache is returned directly — requests.get not called."""
    # Pre-populate cache with a fresh timestamp
    cache_dir = fund._CACHE_DIR
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached_data = {
        "symbol": "TESTCO",
        "fetched_at": datetime.now(tz=timezone.utc).isoformat(),
        "pe_ratio": 30.0,
    }
    (cache_dir / "TESTCO.json").write_text(json.dumps(cached_data))

    with patch("ingestion.fundamentals.requests.get") as mock_get:
        result = fetch_fundamentals("TESTCO")

    mock_get.assert_not_called()
    assert result is not None
    assert result["pe_ratio"] == 30.0


# ---------------------------------------------------------------------------
# Test 3 — Cache expired (> 7 days): HTTP called, fresh data saved
# ---------------------------------------------------------------------------

def test_expired_cache_refetches(tmp_path: Path) -> None:
    """An expired cache (> 7 days old) must trigger a fresh HTTP fetch."""
    cache_dir = fund._CACHE_DIR
    cache_dir.mkdir(parents=True, exist_ok=True)
    old_ts = (datetime.now(tz=timezone.utc) - timedelta(days=8)).isoformat()
    stale = {"symbol": "TESTCO", "fetched_at": old_ts, "pe_ratio": 99.0}
    (cache_dir / "TESTCO.json").write_text(json.dumps(stale))

    with patch("ingestion.fundamentals.requests.get", return_value=_mock_200()) as mock_get:
        result = fetch_fundamentals("TESTCO")

    assert mock_get.called, "requests.get must be called for expired cache"
    # Fresh data replaces stale pe_ratio
    assert result is not None
    assert result["pe_ratio"] == 28.5   # parsed from _SAMPLE_HTML


# ---------------------------------------------------------------------------
# Test 4 — HTTP 404: returns None, no exception
# ---------------------------------------------------------------------------

def test_http_404_returns_none() -> None:
    """A 404 from both consolidated and standalone URLs must return None gracefully."""
    with patch(
        "ingestion.fundamentals.requests.get",
        return_value=_mock_status(404),
    ):
        result = fetch_fundamentals("NOEXIST")

    assert result is None, "HTTP 404 must return None, not raise"


# ---------------------------------------------------------------------------
# Test 5 — HTTP timeout: returns None, no exception
# ---------------------------------------------------------------------------

def test_http_timeout_returns_none() -> None:
    """requests.exceptions.Timeout must be caught and return None."""
    import requests as req_lib

    with patch(
        "ingestion.fundamentals.requests.get",
        side_effect=req_lib.exceptions.Timeout("timed out"),
    ):
        result = fetch_fundamentals("TIMEOUT")

    assert result is None, "Timeout must return None, not raise"


# ---------------------------------------------------------------------------
# Test 6 — BeautifulSoup parse error: returns None, no exception
# ---------------------------------------------------------------------------

def test_bs_parse_error_returns_none() -> None:
    """If BeautifulSoup raises during parsing, fetch_fundamentals must return None."""
    with patch("ingestion.fundamentals.requests.get", return_value=_mock_200()):
        with patch(
            "ingestion.fundamentals.BeautifulSoup",
            side_effect=Exception("parse exploded"),
        ):
            result = fetch_fundamentals("BSCRASH")

    assert result is None, "BeautifulSoup parse error must return None, not raise"


# ---------------------------------------------------------------------------
# Test 7 — force_refresh bypasses valid cache
# ---------------------------------------------------------------------------

def test_force_refresh_bypasses_cache(tmp_path: Path) -> None:
    """force_refresh=True must call requests.get even when cache is still valid."""
    cache_dir = fund._CACHE_DIR
    cache_dir.mkdir(parents=True, exist_ok=True)
    fresh_ts = datetime.now(tz=timezone.utc).isoformat()
    cached = {"symbol": "TESTCO", "fetched_at": fresh_ts, "pe_ratio": 99.0}
    (cache_dir / "TESTCO.json").write_text(json.dumps(cached))

    with patch("ingestion.fundamentals.requests.get", return_value=_mock_200()) as mock_get:
        result = fetch_fundamentals("TESTCO", force_refresh=True)

    assert mock_get.called, "requests.get must be called when force_refresh=True"
    assert result is not None
    assert result["pe_ratio"] == 28.5   # fresh value from _SAMPLE_HTML


# ---------------------------------------------------------------------------
# Test 8 — Returned dict always has a valid ISO fetched_at timestamp
# ---------------------------------------------------------------------------

def test_returned_dict_has_fetched_at_timestamp() -> None:
    """Every successful result must contain a parseable ``fetched_at`` field."""
    with patch("ingestion.fundamentals.requests.get", return_value=_mock_200()):
        result = fetch_fundamentals("TESTCO")

    assert result is not None
    assert "fetched_at" in result, "fetched_at key must be present in result"
    ts = result["fetched_at"]
    # Must be parseable as an ISO datetime
    parsed = datetime.fromisoformat(ts)
    assert parsed is not None


# ---------------------------------------------------------------------------
# Test 9 — eps_accelerating=True when latest QoQ growth > previous
# ---------------------------------------------------------------------------

def test_eps_accelerating_true_when_growth_rising() -> None:
    """
    Given EPS values [8, 9, 11, 15] the growth rates are [12.5%, 22.2%, 36.4%].
    The most recent rate (36.4%) > previous (22.2%) → eps_accelerating=True.
    """
    rates, accel = _compute_eps_metrics([8.0, 9.0, 11.0, 15.0])
    assert len(rates) == 3
    assert accel is True, f"Expected accelerating=True, got rates={rates}"


def test_eps_accelerating_false_when_growth_decelerating() -> None:
    """
    Given EPS values [8, 12, 13, 13.5] the rates decelerate.
    eps_accelerating must be False.
    """
    rates, accel = _compute_eps_metrics([8.0, 12.0, 13.0, 13.5])
    assert accel is False, f"Expected accelerating=False, got rates={rates}"


# ---------------------------------------------------------------------------
# Test 10 — fii_trend="rising" when last 3 quarters increase monotonically
# ---------------------------------------------------------------------------

def test_fii_trend_rising_when_increasing() -> None:
    """fii_trend must be 'rising' when each quarter's FII% > the previous."""
    assert _compute_fii_trend([14.0, 16.5, 18.7]) == "rising"


def test_fii_trend_falling_when_decreasing() -> None:
    assert _compute_fii_trend([20.0, 17.5, 15.0]) == "falling"


def test_fii_trend_flat_when_mixed() -> None:
    assert _compute_fii_trend([15.0, 17.0, 16.0]) == "flat"


def test_fii_trend_flat_on_insufficient_data() -> None:
    assert _compute_fii_trend([15.0]) == "flat"
    assert _compute_fii_trend([]) == "flat"


# ---------------------------------------------------------------------------
# Integration-style: full parse of _SAMPLE_HTML
# ---------------------------------------------------------------------------

def test_full_parse_matches_fixture(tmp_path: Path) -> None:
    """
    Parsing _SAMPLE_HTML must produce values consistent with
    tests/fixtures/sample_fundamentals.json.
    """
    fixture_path = (
        Path(__file__).parent.parent / "fixtures" / "sample_fundamentals.json"
    )
    expected = json.loads(fixture_path.read_text())

    with patch("ingestion.fundamentals.requests.get", return_value=_mock_200()):
        result = fetch_fundamentals("TESTCO")

    assert result is not None
    assert result["pe_ratio"] == expected["pe_ratio"]
    assert result["roe"] == expected["roe"]
    assert result["promoter_holding"] == expected["promoter_holding"]
    assert result["fii_trend"] == expected["fii_trend"]
    assert result["eps_values"] == expected["eps_values"]
    # EPS values [8.5, 9.2, 10.8, 12.1] → rates decelerate at the end
    assert result["eps_accelerating"] == expected["eps_accelerating"]
    assert result["sales_growth_yoy"] == expected["sales_growth_yoy"]


# ---------------------------------------------------------------------------
# Cache utility tests
# ---------------------------------------------------------------------------

def test_get_fundamentals_age_days_returns_none_when_no_cache() -> None:
    assert get_fundamentals_age_days("GHOST") is None


def test_get_fundamentals_age_days_returns_float(tmp_path: Path) -> None:
    with patch("ingestion.fundamentals.requests.get", return_value=_mock_200()):
        fetch_fundamentals("TESTCO")
    age = get_fundamentals_age_days("TESTCO")
    assert age is not None
    assert isinstance(age, float)
    assert 0.0 <= age < 0.1    # just fetched, should be near-zero


def test_clear_fundamentals_cache_single_symbol(tmp_path: Path) -> None:
    with patch("ingestion.fundamentals.requests.get", return_value=_mock_200()):
        fetch_fundamentals("TESTCO")
    assert (fund._CACHE_DIR / "TESTCO.json").exists()
    clear_fundamentals_cache("TESTCO")
    assert not (fund._CACHE_DIR / "TESTCO.json").exists()


def test_clear_fundamentals_cache_all_symbols(tmp_path: Path) -> None:
    with patch("ingestion.fundamentals.requests.get", return_value=_mock_200()):
        fetch_fundamentals("ALPHA")
        fetch_fundamentals("BETA")
    assert len(list(fund._CACHE_DIR.glob("*.json"))) == 2
    clear_fundamentals_cache()   # symbol=None → clear all
    assert len(list(fund._CACHE_DIR.glob("*.json"))) == 0
