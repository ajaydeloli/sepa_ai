"""
utils/exceptions.py
-------------------
Custom exception hierarchy for the SEPA AI screening system.

All project-specific exceptions inherit from SEPABaseError so callers
can catch the entire family with a single `except SEPABaseError` clause,
or be precise and catch only the sub-type they care about.
"""


class SEPABaseError(Exception):
    """Root exception for every SEPA AI error.

    Carries an optional *detail* string that sub-systems can populate
    with context (e.g. symbol name, offending value) without forcing
    every raise site to format a full message.
    """

    def __init__(self, message: str = "", detail: str = "") -> None:
        super().__init__(message)
        self.detail = detail

    def __str__(self) -> str:
        base = super().__str__()
        return f"{base} | detail={self.detail}" if self.detail else base


# ---------------------------------------------------------------------------
# Data quality
# ---------------------------------------------------------------------------


class DataValidationError(SEPABaseError):
    """Raised when an OHLCV DataFrame fails a schema or value check.

    Typical triggers
    ----------------
    * Missing required columns (open, high, low, close, volume)
    * Non-numeric values in price/volume columns
    * High < Low or Close outside [Low, High] range
    * Duplicate timestamps
    """


class InsufficientDataError(SEPABaseError):
    """Raised when a symbol has fewer rows than the minimum required.

    Example: computing SMA-200 requires at least 200 trading-day rows;
    if the DataFrame has only 150, this exception is raised so the
    caller can skip or flag the symbol instead of silently returning NaN.

    Attributes
    ----------
    required : int
        The minimum number of rows needed.
    available : int
        The actual number of rows present.
    """

    def __init__(
        self,
        message: str = "",
        required: int = 0,
        available: int = 0,
        detail: str = "",
    ) -> None:
        super().__init__(message, detail)
        self.required = required
        self.available = available

    def __str__(self) -> str:
        base = Exception.__str__(self)
        parts = [base]
        if self.required or self.available:
            parts.append(f"required={self.required}, available={self.available}")
        if self.detail:
            parts.append(f"detail={self.detail}")
        return " | ".join(parts)


# ---------------------------------------------------------------------------
# External data sources
# ---------------------------------------------------------------------------


class DataSourceError(SEPABaseError):
    """Raised when an API call or network request fails.

    Wraps third-party exceptions (requests.HTTPError, yfinance errors,
    nsepython failures) so the rest of the pipeline only needs to handle
    one exception type for all upstream data failures.
    """


# ---------------------------------------------------------------------------
# Feature store
# ---------------------------------------------------------------------------


class FeatureStoreOutOfSyncError(SEPABaseError):
    """Raised when today's feature row already exists in the store.

    This prevents accidental double-writes and signals that the daily
    pipeline has already run for the current trading date.
    """


# ---------------------------------------------------------------------------
# Configuration & watchlist
# ---------------------------------------------------------------------------


class WatchlistParseError(SEPABaseError):
    """Raised when the watchlist file cannot be read or is empty.

    Covers both IO errors (file not found, permission denied) and
    content errors (empty file, unrecognised format).
    """


class ConfigurationError(SEPABaseError):
    """Raised when a value in settings.yaml is invalid or missing.

    Examples: negative lookback window, unknown data-source name,
    or a required key that is absent from the config file.
    """


# ---------------------------------------------------------------------------
# Reports / chart generation
# ---------------------------------------------------------------------------


class ChartGenerationError(SEPABaseError):
    """Raised when a candlestick chart cannot be produced.

    Typical triggers
    ----------------
    * Empty or None OHLCV DataFrame passed to generate_chart()
    * mplfinance / matplotlib rendering failure
    * Output directory cannot be created (permission error)

    The underlying exception is stored in ``__cause__`` via ``raise ... from exc``.
    """
