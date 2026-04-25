"""
ingestion/source_factory.py
----------------------------
Config-driven factory that returns the appropriate :class:`~ingestion.base.DataSource`.

Source selection
----------------
The ``universe.source`` key in ``config/settings.yaml`` controls which
adapter is instantiated:

=============================  ==============================
Config value                   Adapter class
=============================  ==============================
``"yfinance"`` (default)       :class:`~ingestion.yfinance_source.YFinanceSource`
``"angel_one"``                :class:`~ingestion.angel_one_source.AngelOneSource`
``"upstox"``                   :class:`~ingestion.upstox_source.UpstoxSource`
=============================  ==============================

Fallback behaviour
------------------
If a non-yfinance source fails to initialise (e.g. missing API keys), the
factory logs a warning and transparently returns a
:class:`~ingestion.yfinance_source.YFinanceSource` so the pipeline keeps
running without crashing.
"""

from __future__ import annotations

from ingestion.base import DataSource
from utils.exceptions import ConfigurationError
from utils.logger import get_logger

log = get_logger(__name__)

# ── Lazy imports to avoid pulling in optional dependencies at module load ─


def _get_yfinance_source():
    from ingestion.yfinance_source import YFinanceSource
    return YFinanceSource


def _get_angel_one_source():
    from ingestion.angel_one_source import AngelOneSource
    return AngelOneSource


def _get_upstox_source():
    from ingestion.upstox_source import UpstoxSource
    return UpstoxSource


_SOURCE_MAP = {
    "yfinance": _get_yfinance_source,
    "angel_one": _get_angel_one_source,
    "upstox": _get_upstox_source,
}


def get_source(config) -> DataSource:
    """Return the configured :class:`~ingestion.base.DataSource` instance.

    Parameters
    ----------
    config:
        The parsed application config object or dict.  Must expose
        ``config.universe.source`` (attribute-style) **or**
        ``config["universe"]["source"]`` (dict-style).  Defaults to
        ``"yfinance"`` if the key is absent.

    Returns
    -------
    DataSource
        A fully initialised data source adapter.  Always returns a
        :class:`~ingestion.yfinance_source.YFinanceSource` on fallback.
    """
    # ── Resolve source name from config ──────────────────────────────────
    source_name = "yfinance"
    try:
        if isinstance(config, dict):
            source_name = config.get("universe", {}).get("source", "yfinance")
        else:
            universe_cfg = getattr(config, "universe", None)
            if universe_cfg is not None:
                if isinstance(universe_cfg, dict):
                    source_name = universe_cfg.get("source", "yfinance")
                else:
                    source_name = getattr(universe_cfg, "source", "yfinance")
    except Exception as exc:  # noqa: BLE001
        log.warning("get_source: could not read source from config (%s); defaulting to yfinance.", exc)
        source_name = "yfinance"

    source_name = (source_name or "yfinance").strip().lower()

    # ── Resolve class loader ──────────────────────────────────────────────
    loader = _SOURCE_MAP.get(source_name)
    if loader is None:
        log.warning(
            "get_source: unknown source %r — falling back to yfinance. "
            "Valid options: %s",
            source_name,
            ", ".join(_SOURCE_MAP),
        )
        from ingestion.yfinance_source import YFinanceSource
        return YFinanceSource()

    # ── Instantiate — fall back to yfinance on init failure ──────────────
    if source_name == "yfinance":
        cls = loader()
        return cls()

    try:
        cls = loader()
        instance = cls()
        log.info("get_source: using %s as data source.", cls.__name__)
        return instance
    except ConfigurationError as exc:
        log.warning(
            "get_source: %s failed to initialise (%s) — falling back to YFinanceSource.",
            source_name,
            exc,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "get_source: unexpected error initialising %s (%s) — falling back to YFinanceSource.",
            source_name,
            exc,
        )

    from ingestion.yfinance_source import YFinanceSource
    return YFinanceSource()
