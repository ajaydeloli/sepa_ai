"""
reports/daily_watchlist.py
~~~~~~~~~~~~~~~~~~~~~~~~~~
Generates CSV and HTML daily watchlist reports for the SEPA screening system.
"""

from __future__ import annotations

import csv
import os
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from jinja2 import Environment, FileSystemLoader

from rules.scorer import SEPAResult

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

QUALITY_ORDER = {"A+": 0, "A": 1, "B": 2, "C": 3, "FAIL": 4}
TOP_QUALITY = {"A+", "A"}

CSV_COLUMNS = [
    "rank",
    "symbol",
    "score",
    "setup_quality",
    "stage",
    "conditions_met",
    "vcp_qualified",
    "breakout_triggered",
    "entry_price",
    "stop_loss",
    "risk_pct",
    "rs_rating",
    "is_watchlist",
]

_TEMPLATES_DIR = Path(__file__).parent / "templates"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _sort_results(results: list[SEPAResult]) -> list[SEPAResult]:
    """Sort descending by score; secondary key = quality tier."""
    return sorted(
        results,
        key=lambda r: (-r.score, QUALITY_ORDER.get(r.setup_quality, 99)),
    )


def _filter_results(
    results: list[SEPAResult],
    watchlist_symbols: Optional[list[str]],
    include_all: bool = False,
) -> list[SEPAResult]:
    """
    Return rows that should appear in the report.
    Always includes watchlist symbols; otherwise A+/A only (unless include_all).
    """
    wl_set = set(watchlist_symbols or [])
    if include_all:
        return results
    return [
        r for r in results
        if r.setup_quality in TOP_QUALITY or r.symbol in wl_set
    ]


def _risk_pct(result: SEPAResult) -> Optional[float]:
    """Percentage distance between entry price and stop-loss."""
    if result.entry_price and result.stop_loss and result.entry_price != 0:
        return round(
            abs(result.entry_price - result.stop_loss) / result.entry_price * 100, 2
        )
    return None


def _news_indicator(news_score: float | None) -> str:
    """Return an emoji+label for the news score."""
    if news_score is None:
        return ""
    if news_score > 15:
        return f"🟢 Positive (+{news_score:.0f})"
    if news_score < -15:
        return f"🔴 Negative ({news_score:.0f})"
    return f"⚪ Neutral ({news_score:+.0f})"


def _eps_badge(fundamental_details: dict) -> str:
    """Return EPS acceleration badge text."""
    if not fundamental_details:
        return ""
    accel = fundamental_details.get("f2_eps_accelerating", False)
    return "▲ Accelerating" if accel else "— Flat"


def _as_csv_row(rank: int, result: SEPAResult, is_watchlist: bool) -> dict:
    return {
        "rank": rank,
        "symbol": result.symbol,
        "score": result.score,
        "setup_quality": result.setup_quality,
        "stage": getattr(result, "stage", ""),
        "conditions_met": getattr(result, "conditions_met", ""),
        "vcp_qualified": getattr(result, "vcp_qualified", False),
        "breakout_triggered": getattr(result, "breakout_triggered", False),
        "entry_price": getattr(result, "entry_price", ""),
        "stop_loss": getattr(result, "stop_loss", ""),
        "risk_pct": _risk_pct(result),
        "rs_rating": getattr(result, "rs_rating", ""),
        "is_watchlist": is_watchlist,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_csv_report(
    results: list[SEPAResult],
    output_dir: str,
    run_date: date,
    watchlist_symbols: list[str] = None,
    include_all: bool = False,
) -> str:
    """
    Write ``watchlist_{run_date}.csv`` to *output_dir*.

    Columns (in order): rank, symbol, score, setup_quality, stage,
    conditions_met, vcp_qualified, breakout_triggered, entry_price,
    stop_loss, risk_pct, rs_rating, is_watchlist.

    Rows are sorted by score DESC.  Only A+/A quality rows are included
    unless *include_all* is True.  Symbols present in *watchlist_symbols*
    are always included and receive ``is_watchlist=True``.

    Returns the path to the written file.
    """
    os.makedirs(output_dir, exist_ok=True)
    wl_set = set(watchlist_symbols or [])

    filtered = _filter_results(results, watchlist_symbols, include_all)
    sorted_rows = _sort_results(filtered)

    out_path = Path(output_dir) / f"watchlist_{run_date}.csv"
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        if not sorted_rows:
            # write a single sentinel row so consumers know the file is valid
            writer.writerow({col: "" for col in CSV_COLUMNS} | {"rank": 0, "symbol": "No candidates"})
        else:
            for rank, result in enumerate(sorted_rows, start=1):
                writer.writerow(_as_csv_row(rank, result, result.symbol in wl_set))

    return str(out_path)


def generate_html_report(
    results: list[SEPAResult],
    output_dir: str,
    run_date: date,
    watchlist_symbols: list[str] = None,
    llm_briefs: dict[str, str] = None,
    include_all: bool = False,
) -> str:
    """
    Render ``watchlist_{run_date}.html`` using the Jinja2 template
    located at ``reports/templates/watchlist.html.j2``.

    *llm_briefs* maps symbol → plain-text brief produced by an LLM.
    When ``None`` (or an empty dict) the brief rows are hidden in the template.

    Returns the path to the written file.
    """
    os.makedirs(output_dir, exist_ok=True)
    wl_set = set(watchlist_symbols or [])

    filtered = _filter_results(results, watchlist_symbols, include_all)
    sorted_rows = _sort_results(filtered)

    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=True,
    )
    template = env.get_template("watchlist.html.j2")

    # Build template context
    rows = []
    for rank, result in enumerate(sorted_rows, start=1):
        fd = result.fundamental_details or {}
        row_data = (
            _as_csv_row(rank, result, result.symbol in wl_set)
            | {"brief": (llm_briefs or {}).get(result.symbol, "")}
            | {
                "fundamental_pass": result.fundamental_pass,
                "fundamental_details": fd,
                "has_fundamentals": bool(fd),
                "eps_badge": _eps_badge(fd),
                "news_indicator": _news_indicator(result.news_score),
                "news_score_raw": result.news_score,
                "fii_trend": fd.get("fii_trend", ""),
            }
        )
        rows.append(row_data)

    summary = get_report_summary(results)
    html_content = template.render(
        run_date=run_date,
        generated_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        rows=rows,
        summary=summary,
        has_briefs=bool(llm_briefs),
        no_candidates=len(rows) == 0,
    )

    out_path = Path(output_dir) / f"watchlist_{run_date}.html"
    out_path.write_text(html_content, encoding="utf-8")
    return str(out_path)


def get_report_summary(results: list[SEPAResult]) -> dict:
    """
    Return a summary dict with quality-tier counts and Stage 2 count.

    Keys: total_screened, a_plus, a, b, c, fail, stage2_count.
    """
    counts: dict[str, int] = {"A+": 0, "A": 0, "B": 0, "C": 0, "FAIL": 0}
    stage2 = 0
    for r in results:
        quality = r.setup_quality if r.setup_quality in counts else "FAIL"
        counts[quality] += 1
        if getattr(r, "stage", None) == 2:
            stage2 += 1
    return {
        "total_screened": len(results),
        "a_plus": counts["A+"],
        "a": counts["A"],
        "b": counts["B"],
        "c": counts["C"],
        "fail": counts["FAIL"],
        "stage2_count": stage2,
    }
