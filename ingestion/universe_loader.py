"""
ingestion/universe_loader.py
-----------------------------
Unified symbol resolver for the SEPA screening pipeline.

The :func:`resolve_symbols` function merges symbols from three sources:

1. **Watchlist** — rows stored in SQLite + optional CLI file/symbols.
2. **Universe** — nsepython (Nifty-500 or NSE-all) or a custom list from config.
3. **CLI overrides** — ``--watchlist-file`` or ``--symbols`` flags passed at runtime.

The result is a :class:`RunSymbols` dataclass that the pipeline can use
without caring where each ticker came from.

Supported watchlist file formats
---------------------------------
=======  =========================================================
Format   Convention
=======  =========================================================
``.csv`` ``symbol`` column; first column used as fallback.
``.json``  ``["TCS", "RELIANCE"]`` *or* ``{"symbols": [...]}``
``.xlsx``  First sheet; ``symbol`` column or column A.
``.txt``  One ticker per line; blank lines and ``#`` comments ignored.
=======  =========================================================
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import pandas as pd

from ingestion.nsepython_universe import get_universe
from utils.exceptions import WatchlistParseError
from utils.logger import get_logger

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------

@dataclass
class RunSymbols:
    """Resolved symbol sets for a single pipeline run.

    Attributes
    ----------
    watchlist:
        Symbols from the SQLite watchlist plus any CLI-supplied overrides.
    universe:
        Symbols from nsepython / config universe.
    all:
        Deduplicated union of *watchlist* ∪ *universe*, with watchlist
        symbols appearing first so they are always processed.
    scope:
        The scope that was requested: ``"all"``, ``"universe"``, or
        ``"watchlist"``.
    """
    watchlist: list[str] = field(default_factory=list)
    universe: list[str] = field(default_factory=list)
    all: list[str] = field(default_factory=list)
    scope: str = "all"


# ---------------------------------------------------------------------------
# Symbol validation
# ---------------------------------------------------------------------------

# NSE tickers are 1-20 chars: uppercase letters, digits, hyphens, ampersands.
_SYMBOL_RE = re.compile(r"^[A-Z0-9]{1,20}$")


def validate_symbol(symbol: str) -> bool:
    """Return ``True`` if *symbol* is a well-formed NSE ticker.

    Rules
    -----
    * Uppercase ASCII letters and digits only.
    * Length: 1–20 characters.
    * No spaces, hyphens, ampersands, or other special characters.

    Parameters
    ----------
    symbol:
        Candidate ticker string.

    Examples
    --------
    >>> validate_symbol("RELIANCE")
    True
    >>> validate_symbol("reliance")   # lowercase — False
    False
    >>> validate_symbol("REL IANCE")  # space — False
    False
    """
    if not isinstance(symbol, str):
        return False
    return bool(_SYMBOL_RE.match(symbol))


# ---------------------------------------------------------------------------
# Watchlist file parser
# ---------------------------------------------------------------------------

def _parse_csv(path: Path) -> list[str]:
    try:
        df = pd.read_csv(path, dtype=str)
    except Exception as exc:
        raise WatchlistParseError(
            f"Cannot read CSV file '{path}'", detail=str(exc)
        ) from exc

    if df.empty:
        raise WatchlistParseError(f"CSV file '{path}' is empty.")

    # Prefer a column named 'symbol' (case-insensitive)
    col_map = {c.lower(): c for c in df.columns}
    if "symbol" in col_map:
        col = col_map["symbol"]
    else:
        col = df.columns[0]
        log.debug("_parse_csv: no 'symbol' column found; using first column '%s'.", col)

    return df[col].dropna().astype(str).tolist()


def _parse_json(path: Path) -> list[str]:
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as exc:
        raise WatchlistParseError(
            f"Cannot read JSON file '{path}'", detail=str(exc)
        ) from exc

    if isinstance(data, list):
        return [str(v) for v in data]
    if isinstance(data, dict):
        for key in ("symbols", "Symbols", "SYMBOLS"):
            if key in data and isinstance(data[key], list):
                return [str(v) for v in data[key]]
    raise WatchlistParseError(
        f"JSON file '{path}' must be a list or a dict with a 'symbols' key."
    )


def _parse_xlsx(path: Path) -> list[str]:
    try:
        df = pd.read_excel(path, sheet_name=0, dtype=str)
    except Exception as exc:
        raise WatchlistParseError(
            f"Cannot read XLSX file '{path}'", detail=str(exc)
        ) from exc

    if df.empty:
        raise WatchlistParseError(f"XLSX file '{path}' is empty.")

    col_map = {c.lower(): c for c in df.columns}
    if "symbol" in col_map:
        col = col_map["symbol"]
    else:
        col = df.columns[0]
        log.debug("_parse_xlsx: no 'symbol' column; using first column '%s'.", col)

    return df[col].dropna().astype(str).tolist()


def _parse_txt(path: Path) -> list[str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception as exc:
        raise WatchlistParseError(
            f"Cannot read TXT file '{path}'", detail=str(exc)
        ) from exc

    result = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        result.append(stripped)
    return result


def load_watchlist_file(path: Path) -> list[str]:
    """Parse a watchlist file and return validated ticker symbols.

    Supported formats: ``.csv``, ``.json``, ``.xlsx``, ``.txt``.

    Parameters
    ----------
    path:
        Path to the watchlist file.

    Returns
    -------
    list[str]
        Valid, uppercased ticker symbols.  Invalid entries are logged and
        skipped.

    Raises
    ------
    WatchlistParseError
        If the file cannot be read, is in an unsupported format, or
        contains zero valid symbols after parsing.
    """
    path = Path(path)
    suffix = path.suffix.lower()

    if not path.exists():
        raise WatchlistParseError(f"Watchlist file not found: '{path}'")

    dispatch = {
        ".csv": _parse_csv,
        ".json": _parse_json,
        ".xlsx": _parse_xlsx,
        ".xls": _parse_xlsx,
        ".txt": _parse_txt,
    }

    if suffix not in dispatch:
        raise WatchlistParseError(
            f"Unsupported watchlist file format '{suffix}'. "
            "Expected one of: .csv, .json, .xlsx, .txt"
        )

    raw_symbols: list[str] = dispatch[suffix](path)

    if not raw_symbols:
        raise WatchlistParseError(
            f"Watchlist file '{path}' is empty or contains no symbols."
        )

    valid: list[str] = []
    for raw in raw_symbols:
        candidate = str(raw).strip().upper()
        if validate_symbol(candidate):
            valid.append(candidate)
        else:
            log.warning(
                "load_watchlist_file: skipping invalid symbol %r from '%s'.",
                raw,
                path.name,
            )

    if not valid:
        raise WatchlistParseError(
            f"Watchlist file '{path}' contains no valid symbols after validation."
        )

    log.info(
        "load_watchlist_file: loaded %d symbol(s) from '%s'.",
        len(valid),
        path.name,
    )
    return valid


# ---------------------------------------------------------------------------
# Resolve symbols
# ---------------------------------------------------------------------------

def resolve_symbols(
    config: object,
    db,  # SQLiteStore — typed loosely to avoid circular imports
    cli_watchlist_file: Path | None = None,
    cli_symbols: list[str] | None = None,
    scope: str = "all",
) -> RunSymbols:
    """Build the complete :class:`RunSymbols` for a pipeline run.

    Parameters
    ----------
    config:
        Parsed settings object (or dict-like).  Read for universe config.
    db:
        :class:`~storage.sqlite_store.SQLiteStore` instance used to
        retrieve persisted watchlist rows.
    cli_watchlist_file:
        Optional path to a watchlist file supplied on the CLI.
    cli_symbols:
        Optional list of ticker strings supplied directly on the CLI
        (e.g. ``--symbols RELIANCE TCS``).
    scope:
        One of ``"all"`` | ``"universe"`` | ``"watchlist"``.

        * ``"watchlist"`` — only watchlist symbols are returned in ``.all``.
        * ``"universe"`` — only universe symbols are returned in ``.all``.
        * ``"all"``       — deduplicated union (watchlist first).

    Returns
    -------
    RunSymbols
        Populated dataclass.
    """
    # ── 1. Watchlist from SQLite ──────────────────────────────────────────
    db_symbols: list[str] = []
    try:
        db_symbols = [row["symbol"] for row in db.get_watchlist()]
    except Exception as exc:  # noqa: BLE001
        log.warning("resolve_symbols: failed to load watchlist from DB (%s).", exc)

    # ── 2. CLI overrides ──────────────────────────────────────────────────
    file_symbols: list[str] = []
    if cli_watchlist_file is not None:
        try:
            file_symbols = load_watchlist_file(Path(cli_watchlist_file))
        except WatchlistParseError as exc:
            log.warning("resolve_symbols: watchlist file error — %s", exc)

    extra_cli: list[str] = []
    if cli_symbols:
        for s in cli_symbols:
            candidate = str(s).strip().upper()
            if validate_symbol(candidate):
                extra_cli.append(candidate)
            else:
                log.warning(
                    "resolve_symbols: skipping invalid CLI symbol %r.", s
                )

    # Deduplicate watchlist, preserving order; db symbols first
    seen: set[str] = set()
    watchlist: list[str] = []
    for sym in db_symbols + file_symbols + extra_cli:
        if sym not in seen:
            seen.add(sym)
            watchlist.append(sym)

    # ── 3. Universe ───────────────────────────────────────────────────────
    universe: list[str] = []
    try:
        # Resolve index name from config (supports both dict and object styles)
        index = "nifty500"
        try:
            if isinstance(config, dict):
                index = config.get("universe", {}).get("index", "nifty500")
            else:
                index = getattr(getattr(config, "universe", None) or {}, "get",
                                lambda k, d=None: d)("index", "nifty500")
        except Exception:  # noqa: BLE001
            pass

        universe = get_universe(index)
    except Exception as exc:  # noqa: BLE001
        log.warning("resolve_symbols: universe load failed (%s).", exc)

    # ── 4. Merge by scope ─────────────────────────────────────────────────
    if scope == "watchlist":
        combined = watchlist
    elif scope == "universe":
        combined = universe
    else:  # "all"
        seen2: set[str] = set()
        combined = []
        for sym in watchlist + universe:
            if sym not in seen2:
                seen2.add(sym)
                combined.append(sym)

    log.info(
        "resolve_symbols: scope=%s | watchlist=%d | universe=%d | all=%d",
        scope,
        len(watchlist),
        len(universe),
        len(combined),
    )

    return RunSymbols(
        watchlist=watchlist,
        universe=universe,
        all=combined,
        scope=scope,
    )
