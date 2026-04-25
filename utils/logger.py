"""
utils/logger.py
---------------
Centralised logging factory for the SEPA AI screening system.

Usage
-----
    from utils.logger import get_logger
    log = get_logger(__name__)
    log.info("Starting ingestion run")

Design notes
------------
* Configuration is loaded from ``config/logging.yaml`` the *first* time
  ``get_logger`` is called.  Subsequent calls just retrieve the named
  logger from Python's logging registry (no re-read of disk).
* If the YAML file is missing or malformed, a sensible fallback config
  is applied so the application never crashes due to missing log config.
* The ``logs/`` directory is created automatically when the file handler
  is set up, so the project works in a fresh checkout with no manual steps.
"""

from __future__ import annotations

import logging
import logging.config
import logging.handlers
import os
import sys
from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

# Resolve project root relative to this file: utils/ -> project root
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_LOGGING_CONFIG_PATH = _PROJECT_ROOT / "config" / "logging.yaml"
_LOGS_DIR = _PROJECT_ROOT / "logs"
_LOG_FILE = _LOGS_DIR / "sepa_ai.log"

# Module-level flag: has logging been configured yet?
_configured: bool = False

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def _ensure_logs_dir() -> None:
    """Create the ``logs/`` directory if it does not already exist."""
    _LOGS_DIR.mkdir(parents=True, exist_ok=True)


def _patch_log_file_path(config: dict[str, Any]) -> dict[str, Any]:
    """Rewrite any relative ``filename`` entries to absolute paths.

    ``logging.config.dictConfig`` resolves filenames relative to the
    *process* working directory, which varies depending on how the app
    is started.  We always want log files in ``<project_root>/logs/``.
    """
    handlers = config.get("handlers", {})
    for handler_cfg in handlers.values():
        if "filename" in handler_cfg:
            raw = handler_cfg["filename"]
            path = Path(raw)
            if not path.is_absolute():
                handler_cfg["filename"] = str(_PROJECT_ROOT / path)
    return config


def _apply_fallback_config() -> None:
    """Configure a minimal but correct logging setup without a YAML file."""
    _ensure_logs_dir()

    fmt = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setLevel(logging.INFO)
    stream_handler.setFormatter(fmt)

    file_handler = logging.handlers.RotatingFileHandler(
        filename=str(_LOG_FILE),
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(stream_handler)
    root.addHandler(file_handler)


def _configure_logging() -> None:
    """Load ``config/logging.yaml`` and apply it via ``dictConfig``.

    Called exactly once (guarded by ``_configured`` flag).
    Falls back to a hardcoded config if the YAML is unavailable.
    """
    global _configured
    if _configured:
        return

    _ensure_logs_dir()

    if _LOGGING_CONFIG_PATH.exists():
        try:
            with _LOGGING_CONFIG_PATH.open("r", encoding="utf-8") as fh:
                raw_config: dict[str, Any] = yaml.safe_load(fh)

            # Ensure absolute paths so handlers work from any cwd
            raw_config = _patch_log_file_path(raw_config)

            # Override the format for all formatters to the canonical layout
            for fmt_cfg in raw_config.get("formatters", {}).values():
                fmt_cfg["format"] = _LOG_FORMAT
                fmt_cfg["datefmt"] = _DATE_FORMAT

            logging.config.dictConfig(raw_config)
        except Exception as exc:  # noqa: BLE001
            # Don't let logging config crash the application
            _apply_fallback_config()
            logging.getLogger(__name__).warning(
                "Failed to load %s (%s); using fallback logging config.",
                _LOGGING_CONFIG_PATH,
                exc,
            )
    else:
        _apply_fallback_config()

    _configured = True


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_logger(name: str) -> logging.Logger:
    """Return a :class:`logging.Logger` configured for the SEPA AI project.

    Parameters
    ----------
    name:
        Typically ``__name__`` of the calling module so log records carry
        a meaningful dotted path (e.g. ``ingestion.yfinance_fetcher``).

    Returns
    -------
    logging.Logger
        A standard library logger.  All configuration (handlers, levels,
        formatters) is driven by ``config/logging.yaml``.
    """
    _configure_logging()
    return logging.getLogger(name)
