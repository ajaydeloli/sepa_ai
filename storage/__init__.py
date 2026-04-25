"""
storage/__init__.py
-------------------
Public surface of the ``storage`` package.

    from storage import SQLiteStore, append_row, read_last_n_rows
"""

from storage.sqlite_store import SQLiteStore
from storage.parquet_store import append_row, read_last_n_rows, read_parquet, write_parquet, get_last_date

__all__ = [
    "SQLiteStore",
    "append_row",
    "read_last_n_rows",
    "read_parquet",
    "write_parquet",
    "get_last_date",
]
