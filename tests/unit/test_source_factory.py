"""
tests/unit/test_source_factory.py
----------------------------------
Unit tests for ingestion/source_factory.py.
"""

from __future__ import annotations

import pytest
from unittest.mock import patch

from ingestion.source_factory import get_source
from ingestion.yfinance_source import YFinanceSource
from utils.exceptions import ConfigurationError


# ---------------------------------------------------------------------------
# Simple config helpers
# ---------------------------------------------------------------------------


class _DictConfig:
    """Minimal config object that stores universe.source as nested attrs."""
    class _Universe:
        def __init__(self, source: str):
            self.source = source

    def __init__(self, source: str):
        self.universe = self._Universe(source)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_get_source_returns_yfinance_for_yfinance_config():
    """get_source('yfinance') should return a YFinanceSource instance."""
    cfg = _DictConfig("yfinance")
    source = get_source(cfg)
    assert isinstance(source, YFinanceSource)


def test_get_source_accepts_dict_config():
    """get_source should work when config is a plain dict."""
    cfg = {"universe": {"source": "yfinance"}}
    source = get_source(cfg)
    assert isinstance(source, YFinanceSource)


def test_get_source_falls_back_to_yfinance_on_configuration_error():
    """When AngelOneSource.__init__ raises ConfigurationError, fall back to YFinanceSource."""
    cfg = _DictConfig("angel_one")

    # AngelOneSource will raise ConfigurationError because env vars aren't set.
    # Ensure they're definitely not set during this test.
    with patch.dict("os.environ", {}, clear=False):
        # Remove keys if present
        import os
        os.environ.pop("ANGEL_ONE_API_KEY", None)
        os.environ.pop("ANGEL_ONE_CLIENT_ID", None)

        source = get_source(cfg)

    assert isinstance(source, YFinanceSource), (
        f"Expected YFinanceSource fallback, got {type(source).__name__}"
    )


def test_get_source_falls_back_to_yfinance_for_upstox_missing_key():
    """When UpstoxSource raises ConfigurationError (missing key), fall back to YFinanceSource."""
    cfg = _DictConfig("upstox")

    import os
    os.environ.pop("UPSTOX_API_KEY", None)

    source = get_source(cfg)
    assert isinstance(source, YFinanceSource)


def test_get_source_unknown_source_falls_back_to_yfinance():
    """An unrecognised source name should fall back to YFinanceSource."""
    cfg = _DictConfig("nonexistent_broker")
    source = get_source(cfg)
    assert isinstance(source, YFinanceSource)


def test_get_source_missing_config_defaults_to_yfinance():
    """If config has no universe key at all, should return YFinanceSource."""
    source = get_source({})
    assert isinstance(source, YFinanceSource)
