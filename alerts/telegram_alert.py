"""
alerts/telegram_alert.py
-------------------------
Telegram notification layer for the SEPA daily screening pipeline.

Uses the ``python-telegram-bot`` library in synchronous mode (v13.x API).
For v20+ installations the coroutine is transparently executed via
``asyncio.get_event_loop().run_until_complete()``.

Environment variables (loaded upstream before this module is imported):
    TELEGRAM_BOT_TOKEN  — Bot token from @BotFather.
    TELEGRAM_CHAT_ID    — Target chat / channel ID.

Both can also be provided through the ``config`` dict under
``config["alerts"]["telegram"]``.
"""

from __future__ import annotations

import inspect
import os
from datetime import date
from pathlib import Path

from rules.scorer import SEPAResult
from utils.logger import get_logger

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _run(coro_or_value):
    """Execute *coro_or_value* synchronously if it is a coroutine."""
    if inspect.iscoroutine(coro_or_value):
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_closed():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            return loop.run_until_complete(coro_or_value)
        except RuntimeError:
            return asyncio.run(coro_or_value)
    return coro_or_value


def _get_credentials(config: dict) -> tuple[str, str]:
    """Return (bot_token, chat_id) from config or environment."""
    tg_cfg = config.get("alerts", {}).get("telegram", {})
    token = tg_cfg.get("bot_token") or os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = tg_cfg.get("chat_id") or os.environ.get("TELEGRAM_CHAT_ID", "")
    return token, chat_id


def _fmt_price(value: float | None) -> str:
    """Format a price value with ₹ prefix, or return 'N/A'."""
    if value is None:
        return "N/A"
    return f"₹{value:,.0f}"


def _fmt_pct(value: float | None) -> str:
    """Format a percentage value, or return 'N/A'."""
    if value is None:
        return "N/A"
    return f"{value:.1f}%"


def _build_symbol_message(r: SEPAResult, in_watchlist: bool) -> str:
    """Compose the Markdown message body for a single symbol.

    Kept under ~1000 chars (Telegram message limit).
    Fundamental and news lines are omitted when data is unavailable.
    """
    lines: list[str] = []

    if in_watchlist:
        lines.append("★ WATCHLIST")

    breakout_icon = "🔴 Triggered" if r.breakout_triggered else "⬜ Not yet"
    vcp_icon = "✅" if r.vcp_qualified else "❌"
    tt_icon = "✅" if r.trend_template_pass else "❌"

    lines.append(f"*{r.symbol}* — {r.setup_quality} (Score: {r.score}/100)")
    lines.append(f"Stage: Stage {r.stage} — {r.stage_label}")
    lines.append(
        f"TT: {tt_icon} {r.conditions_met}/8 | VCP: {vcp_icon} | Breakout: {breakout_icon}"
    )
    lines.append(
        f"Entry: {_fmt_price(r.entry_price)} | "
        f"Stop: {_fmt_price(r.stop_loss)} | "
        f"Risk: {_fmt_pct(r.risk_pct)}"
    )
    lines.append(f"RS Rating: {r.rs_rating}")

    # ── Fundamental line (Phase 5) ────────────────────────────────────────
    fd = r.fundamental_details or {}
    if fd:
        vals = fd.get("values", {})
        eps_icon = "▲ Accelerating" if fd.get("f2_eps_accelerating") else "— Flat"
        parts = [f"EPS: {eps_icon}"]
        roe = vals.get("roe")
        de  = vals.get("de_ratio")
        prm = vals.get("promoter_holding")
        if roe is not None:
            parts.append(f"ROE: {roe:.1f}%")
        if de is not None:
            parts.append(f"D/E: {de:.2f}")
        if prm is not None:
            parts.append(f"Promoter: {prm:.1f}%")
        lines.append(" | ".join(parts))

    # ── News line (Phase 5) ───────────────────────────────────────────────
    if r.news_score is not None:
        if r.news_score > 15:
            news_str = f"News: 🟢 Positive (+{r.news_score:.0f})"
        elif r.news_score < -15:
            news_str = f"News: 🔴 Negative ({r.news_score:.0f})"
        else:
            news_str = f"News: ⚪ Neutral ({r.news_score:+.0f})"
        lines.append(news_str)

    msg = "\n".join(lines)
    # Hard-trim to 1000 chars (Telegram limit)
    if len(msg) > 1000:
        msg = msg[:997] + "…"
    return msg

def _send_one(bot, chat_id: str, text: str, photo_path: str | None = None) -> bool:
    """Send a single Telegram message (text or photo).  Returns True on success."""
    try:
        if photo_path and Path(photo_path).exists():
            with open(photo_path, "rb") as img:
                _run(bot.send_photo(chat_id=chat_id, photo=img, caption=text,
                                    parse_mode="Markdown"))
        else:
            _run(bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown"))
        return True
    except Exception as exc:  # noqa: BLE001
        log.error("Telegram send failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def send_daily_watchlist(
    results: list[SEPAResult],
    chart_paths: dict[str, str],
    config: dict,
    run_date: date,
    watchlist_symbols: list[str] | None = None,
) -> int:
    """Send Telegram alerts for all qualifying results.

    A+ and A results are always sent (subject to deduplication upstream).
    Watchlist symbols with quality >= B are also sent.

    Returns the count of messages successfully dispatched.
    Skips silently when Telegram is disabled or the bot token is absent.
    """
    tg_cfg = config.get("alerts", {}).get("telegram", {})
    if not tg_cfg.get("enabled", True):
        log.info("Telegram alerts disabled in config — skipping")
        return 0

    token, chat_id = _get_credentials(config)
    if not token:
        log.warning("TELEGRAM_BOT_TOKEN not set — skipping Telegram alerts")
        return 0

    try:
        import telegram
        bot = telegram.Bot(token=token)
    except ImportError:
        log.error("python-telegram-bot not installed — cannot send Telegram alerts")
        return 0

    watchlist_set: set[str] = set(watchlist_symbols or [])
    min_quality = tg_cfg.get("min_quality", "A")
    min_quality_rank = {"FAIL": 0, "C": 1, "B": 2, "A": 3, "A+": 4}.get(min_quality, 3)
    watchlist_min_rank = 2  # B or better for watchlist symbols

    sent = 0
    counts: dict[str, int] = {"A+": 0, "A": 0, "B": 0}

    quality_rank = {"FAIL": 0, "C": 1, "B": 2, "A": 3, "A+": 4}
    for r in results:
        r_rank = quality_rank.get(r.setup_quality, 0)
        in_watchlist = r.symbol in watchlist_set
        # Determine whether to send
        qualifies = (r_rank >= min_quality_rank) or (in_watchlist and r_rank >= watchlist_min_rank)
        if not qualifies:
            continue

        text = _build_symbol_message(r, in_watchlist=in_watchlist)
        chart = chart_paths.get(r.symbol)
        if _send_one(bot, chat_id, text, chart):
            sent += 1
            if r.setup_quality in counts:
                counts[r.setup_quality] += 1

    # ── Summary message ──────────────────────────────────────────────────────
    total = len(results)
    summary = (
        f"📊 SEPA Screen — {run_date}\n"
        f"A+: {counts.get('A+', 0)} | A: {counts.get('A', 0)} | "
        f"B: {counts.get('B', 0)} | Total screened: {total}\n"
        f"Next run: tomorrow at 15:35 IST"
    )
    if _send_one(bot, chat_id, summary):
        sent += 1

    log.info("Telegram: sent %d messages (%s)", sent, dict(counts))
    return sent


def send_error_alert(error_msg: str, config: dict) -> None:
    """Send a plain-text error notification when the pipeline fails.

    Called by ``pipeline/runner.py`` in the ``except`` block.
    Logs and returns silently on any send failure.
    """
    tg_cfg = config.get("alerts", {}).get("telegram", {})
    if not tg_cfg.get("enabled", True):
        return

    token, chat_id = _get_credentials(config)
    if not token:
        log.warning("TELEGRAM_BOT_TOKEN not set — cannot send error alert")
        return

    try:
        import telegram
        bot = telegram.Bot(token=token)
        text = f"🚨 SEPA Pipeline Error\n{error_msg}"
        _run(bot.send_message(chat_id=chat_id, text=text))
        log.info("Telegram error alert sent")
    except Exception as exc:  # noqa: BLE001
        log.error("Failed to send Telegram error alert: %s", exc)
