"""
pipeline/context.py
--------------------
RunContext dataclass — shared state for a single pipeline run.

Passed through every stage so modules don't need to accept a dozen
individual keyword arguments.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional


@dataclass
class RunContext:
    """Immutable (by convention) context for one pipeline run.

    Attributes
    ----------
    run_date:
        The trading date this run is processing.
    mode:
        One of ``"daily"`` | ``"bootstrap"`` | ``"backtest"``.
    config:
        The full ``config/settings.yaml`` parsed into a plain dict.
    scope:
        Symbol scope: ``"all"`` | ``"universe"`` | ``"watchlist"``.
    dry_run:
        When ``True`` the run logs what *would* happen but skips all writes.
    symbols_override:
        Optional explicit symbol list passed via ``--symbols`` on the CLI.
        When present it is added to (not replaces) the watchlist.
    """

    run_date: date
    mode: str                              # "daily" | "bootstrap" | "backtest"
    config: dict                           # loaded settings.yaml as dict
    scope: str = "all"                     # "all" | "universe" | "watchlist"
    dry_run: bool = False                  # if True, skip writes
    symbols_override: Optional[list[str]] = None  # for --symbols flag
