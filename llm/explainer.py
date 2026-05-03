"""
llm/explainer.py
----------------
Narrative generator for the Minervini SEPA system.

Renders Jinja2 prompt templates and calls the configured LLM client to
produce human-readable trade briefs and watchlist summaries.

Public API
----------
  generate_trade_brief(result, ohlcv_tail, config, client=None) -> str | None
  generate_watchlist_summary(results, run_date, config, client=None) -> str | None
  generate_batch_briefs(results, ohlcv_data, config) -> dict[str, str]
"""

from __future__ import annotations

import dataclasses
from datetime import date
from pathlib import Path

import pandas as pd

from llm.llm_client import LLMClient, get_llm_client, get_session_token_usage
from rules.scorer import SEPAResult
from utils.logger import get_logger

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Template environment
# ---------------------------------------------------------------------------

_TEMPLATE_DIR = Path(__file__).resolve().parent / "prompt_templates"


def _get_jinja_env():
    """Return a Jinja2 Environment pointed at the prompt_templates directory."""
    from jinja2 import Environment, FileSystemLoader

    return Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        autoescape=False,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_ohlcv_context(ohlcv_tail: pd.DataFrame) -> list[dict]:
    """Convert the last 5 OHLCV rows into a list of {date, close, vol_ratio} dicts."""
    rows: list[dict] = []
    tail = ohlcv_tail.tail(5) if not ohlcv_tail.empty else ohlcv_tail
    for _, row in tail.iterrows():
        date_val = row.get("date", row.name)
        date_str = (
            date_val.strftime("%Y-%m-%d")
            if hasattr(date_val, "strftime")
            else str(date_val)
        )
        rows.append(
            {
                "date": date_str,
                "close": round(float(row.get("close", 0.0)), 2),
                "vol_ratio": round(float(row.get("vol_ratio", 1.0)), 2),
            }
        )
    return rows


def _validate_response(text: str) -> bool:
    """Return True only if the LLM response is a clean narrative string.

    Checks:
      - non-empty after stripping whitespace
      - under 600 characters
      - no Markdown code fences (```) — catches code blocks and JSON blocks
      - does not start with a JSON object/array literal
    """
    if not text or not text.strip():
        return False
    if len(text) > 600:
        return False
    if "```" in text:
        return False
    stripped = text.strip()
    if stripped.startswith("{") or stripped.startswith("["):
        return False
    return True


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_trade_brief(
    result: SEPAResult,
    ohlcv_tail: pd.DataFrame,
    config: dict,
    client: LLMClient | None = None,
) -> str | None:
    """Generate a plain-English trade brief for a single SEPA setup.

    Steps:
      1. Return None immediately if result.setup_quality is not in
         config["llm"]["only_for_quality"] (cost-saving gate).
      2. Resolve client: if None, call get_llm_client(config).
         Return None if still None.
      3. Render trade_brief.j2 with dataclasses.asdict(result) plus a
         ``recent_prices`` key built from the last 5 OHLCV rows.
      4. Call client.complete_with_fallback(prompt, fallback=None, max_tokens=…).
      5. Validate the response (non-empty, ≤600 chars, no code/JSON blocks).
         Log a warning and return None on failure.
      6. Return the stripped narrative string.
    """
    # Step 1 — quality gate
    only_for: list[str] = config.get("llm", {}).get("only_for_quality", ["A+", "A"])
    if result.setup_quality not in only_for:
        log.debug(
            "generate_trade_brief: skipping %s (quality=%s not in %s)",
            result.symbol,
            result.setup_quality,
            only_for,
        )
        return None

    # Step 2 — resolve client
    if client is None:
        client = get_llm_client(config)
    if client is None:
        log.warning(
            "generate_trade_brief: no LLM client available for %s", result.symbol
        )
        return None

    # Step 3 — render template
    env = _get_jinja_env()
    template = env.get_template("trade_brief.j2")
    context = dataclasses.asdict(result)
    context["recent_prices"] = _build_ohlcv_context(ohlcv_tail)
    prompt = template.render(**context)

    # Step 5 — call LLM
    max_tokens: int = config.get("llm", {}).get("max_tokens", 350)
    response: str | None = client.complete_with_fallback(
        prompt, fallback=None, max_tokens=max_tokens
    )

    # Step 6 — validate
    if not _validate_response(response or ""):
        log.warning(
            "generate_trade_brief: validation failed for %s (response=%r)",
            result.symbol,
            response,
        )
        return None

    # Step 7 — return clean narrative
    return response.strip()  # type: ignore[union-attr]


def generate_watchlist_summary(
    results: list[SEPAResult],
    run_date: date,
    config: dict,
    client: LLMClient | None = None,
) -> str | None:
    """Generate a 2-paragraph daily watchlist commentary.

    Uses watchlist_summary.j2 template.
    Returns None gracefully if LLM is disabled or unavailable.
    """
    if not config.get("llm", {}).get("enabled", True):
        log.debug("generate_watchlist_summary: LLM disabled in config")
        return None

    if client is None:
        client = get_llm_client(config)
    if client is None:
        log.warning("generate_watchlist_summary: no LLM client available")
        return None

    a_plus = [r for r in results if r.setup_quality == "A+"]
    a_grade = [r for r in results if r.setup_quality == "A"]
    b_grade = [r for r in results if r.setup_quality == "B"]

    top_candidates = sorted(
        a_plus + a_grade, key=lambda r: r.score, reverse=True
    )[:5]

    total_quality = len(a_plus) + len(a_grade) + len(b_grade)
    if total_quality >= 10:
        market_mood = "Broad"
    elif total_quality >= 5:
        market_mood = "Moderate"
    else:
        market_mood = "Selective"

    env = _get_jinja_env()
    template = env.get_template("watchlist_summary.j2")
    context = {
        "run_date": run_date.strftime("%Y-%m-%d"),
        "a_plus_count": len(a_plus),
        "a_count": len(a_grade),
        "b_count": len(b_grade),
        "market_mood": market_mood,
        "top_candidates": [
            {
                "symbol": r.symbol,
                "quality": r.setup_quality,
                "score": r.score,
                "stage_label": r.stage_label,
            }
            for r in top_candidates
        ],
        "sector_leaders": [],  # Phase 5 — wired in later
    }

    prompt = template.render(**context)
    max_tokens: int = config.get("llm", {}).get("max_tokens", 350)
    response: str | None = client.complete_with_fallback(
        prompt, fallback=None, max_tokens=max_tokens
    )

    if not response or not response.strip():
        log.warning("generate_watchlist_summary: empty response from LLM")
        return None

    return response.strip()


def generate_batch_briefs(
    results: list[SEPAResult],
    ohlcv_data: dict[str, pd.DataFrame],
    config: dict,
) -> dict[str, str]:
    """Generate trade briefs for all qualifying results in a batch.

    Instantiates a single LLM client for the whole batch (avoids repeated
    availability checks).  Individual failures are caught and logged so one
    bad symbol never aborts the rest of the run.

    Returns
    -------
    dict[str, str]
        Mapping of symbol → brief text, containing only symbols that
        successfully produced a brief.
    """
    client = get_llm_client(config)
    # client may be None — generate_trade_brief handles that case gracefully.

    briefs: dict[str, str] = {}
    for result in results:
        symbol = result.symbol
        ohlcv_tail: pd.DataFrame = ohlcv_data.get(symbol, pd.DataFrame())
        try:
            brief = generate_trade_brief(result, ohlcv_tail, config, client=client)
            if brief is not None:
                briefs[symbol] = brief
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "generate_batch_briefs: skipping %s due to unexpected error — %s",
                symbol,
                exc,
            )

    usage = get_session_token_usage()
    log.info(
        "generate_batch_briefs complete: %d/%d briefs generated. "
        "Session tokens — prompt=%d  completion=%d",
        len(briefs),
        len(results),
        usage["prompt"],
        usage["completion"],
    )
    return briefs
