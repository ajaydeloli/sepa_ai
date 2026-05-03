"""
tests/conftest.py
-----------------
Session-wide pytest configuration for the SEPA AI test suite.

Problem this file solves
------------------------
``config/logging.yaml`` sets ``propagate: false`` on every project logger
(pipeline, ingestion, features, …) so that production log records are written
directly to the ``console`` and ``file`` handlers without being duplicated on
the root logger.

pytest's ``caplog`` fixture works by installing a ``LogCaptureHandler`` on the
**root** logger and relies on log-record propagation to intercept messages.
When ``propagate: false`` is set, records never reach the root logger, so
``caplog.records`` stays empty even though the logs appear in stdout — causing
any test that asserts on caplog content to fail.

The ``enable_log_propagation`` fixture (applied automatically to every test)
temporarily flips ``propagate`` to ``True`` for all project loggers before the
test body runs, then restores the original value in the teardown phase.  This
has zero effect on production behaviour.
"""
from __future__ import annotations

import logging

import pytest

# All top-level logger namespaces defined in config/logging.yaml
_PROJECT_LOGGER_NAMES: tuple[str, ...] = (
    "pipeline",
    "ingestion",
    "features",
    "rules",
    "screener",
    "storage",
    "utils",
    "alerts",
    "reports",
    "backtest",
    "llm",
    "api",
    "dashboard",
    "paper_trading",
)


@pytest.fixture(autouse=True)
def enable_log_propagation() -> None:
    """Temporarily enable log propagation for all project loggers.

    This allows pytest's ``caplog`` fixture to capture log records that are
    emitted by modules whose top-level logger has ``propagate=False`` set in
    ``config/logging.yaml``.

    The fixture is ``autouse=True`` so it applies to every test in the suite
    without requiring an explicit parameter.
    """
    # --- setup: save originals and enable propagation -----------------------
    original_propagate: dict[str, bool] = {}
    for name in _PROJECT_LOGGER_NAMES:
        logger = logging.getLogger(name)
        original_propagate[name] = logger.propagate
        logger.propagate = True

    yield  # test body runs here

    # --- teardown: restore original propagation flags ----------------------
    for name, original in original_propagate.items():
        logging.getLogger(name).propagate = original
