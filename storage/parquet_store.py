"""
storage/parquet_store.py
------------------------
Atomic Parquet helpers for the SEPA AI feature store.

All writes go through a temp-file + os.replace pattern so readers never
see a half-written file.  The helpers are intentionally stateless — they
operate purely on Path arguments so they can be used from any layer.
"""

from __future__ import annotations

import os
import tempfile
from datetime import date
from pathlib import Path

import pandas as pd

from utils.exceptions import FeatureStoreOutOfSyncError


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def read_parquet(path: Path) -> pd.DataFrame:
    """Read *path* and return its contents as a DataFrame.

    Returns an empty DataFrame (no columns) when the file does not exist,
    which lets callers treat a missing file the same as an empty store.
    """
    path = Path(path)
    if not path.exists():
        return pd.DataFrame()
    return pd.read_parquet(path)


def write_parquet(path: Path, df: pd.DataFrame) -> None:
    """Atomically write *df* to *path* via a temp file + os.replace.

    The temp file is created in the same directory as *path* so that
    os.replace (which requires same filesystem) always succeeds.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".parquet.tmp")
    try:
        os.close(fd)
        df.to_parquet(tmp_path, index=True)
        os.replace(tmp_path, path)
    except Exception:
        # Clean up temp file on failure; best-effort.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def append_row(path: Path, new_row: pd.DataFrame) -> None:
    """Append *new_row* to the Parquet file at *path*.

    Behaviour
    ---------
    * If the file does **not** exist: *new_row* is written directly.
    * If the file **exists**: the existing data is read, *new_row* is
      concatenated, and the result is written atomically.
    * If the index date from *new_row* already exists in the file a
      :exc:`~utils.exceptions.FeatureStoreOutOfSyncError` is raised
      **before** any write takes place.

    Parameters
    ----------
    path:
        Destination Parquet file.
    new_row:
        A single-row (or narrow) DataFrame whose index must be date-like.
        The index values are compared against the existing file's index.

    Raises
    ------
    FeatureStoreOutOfSyncError
        When any index value of *new_row* already exists in the file.
    """
    path = Path(path)

    if path.exists():
        existing = read_parquet(path)
        # Normalise both indexes to date for comparison.
        existing_dates = _index_as_dates(existing)
        new_dates = _index_as_dates(new_row)

        overlap = existing_dates & new_dates
        if overlap:
            raise FeatureStoreOutOfSyncError(
                "Duplicate date(s) detected in feature store",
                detail=f"path={path}, dates={sorted(overlap)}",
            )

        combined = pd.concat([existing, new_row])
        write_parquet(path, combined)
    else:
        write_parquet(path, new_row)


def read_last_n_rows(path: Path, n: int) -> pd.DataFrame:
    """Return the last *n* rows from the Parquet file at *path*.

    Uses ``pyarrow`` row-group metadata where possible to avoid reading
    the entire file into memory.  Falls back to a full read + tail when
    the file is small or metadata is unavailable.

    Returns an empty DataFrame when the file does not exist.
    """
    path = Path(path)
    if not path.exists():
        return pd.DataFrame()

    try:
        import pyarrow.parquet as pq  # type: ignore[import]

        pf = pq.ParquetFile(path)
        total_rows = pf.metadata.num_rows
        if total_rows <= n:
            return pd.read_parquet(path)

        # Scan row groups from the end until we have at least n rows.
        row_groups = pf.metadata.num_row_groups
        rows_needed = n
        groups_to_read: list[int] = []
        for rg_idx in range(row_groups - 1, -1, -1):
            rows_in_group = pf.metadata.row_group(rg_idx).num_rows
            groups_to_read.insert(0, rg_idx)
            rows_needed -= rows_in_group
            if rows_needed <= 0:
                break

        table = pq.read_table(path, row_groups=groups_to_read)
        df = table.to_pandas()
        return df.tail(n)

    except Exception:
        # Graceful fallback: full read + tail.
        return pd.read_parquet(path).tail(n)


def get_last_date(path: Path) -> date | None:
    """Return the most recent index date in the Parquet file.

    Returns ``None`` when the file does not exist or is empty.
    """
    path = Path(path)
    tail = read_last_n_rows(path, 1)
    if tail.empty:
        return None

    raw = tail.index[-1]
    if isinstance(raw, date):
        return raw
    # Handle pandas Timestamp and numpy datetime64.
    try:
        return pd.Timestamp(raw).date()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _index_as_dates(df: pd.DataFrame) -> set[date]:
    """Coerce a DataFrame's index to a set of :class:`datetime.date` objects."""
    result: set[date] = set()
    for val in df.index:
        if isinstance(val, date) and not isinstance(val, pd.Timestamp):
            result.add(val)
        else:
            try:
                result.add(pd.Timestamp(val).date())
            except Exception:
                pass
    return result
