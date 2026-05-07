"""
dashboard/pages/05_Backtest.py
-------------------------------
Backtest Results page — SEPA Streamlit dashboard.

Sections (when results exist)
------------------------------
1. Summary metric cards   (CAGR, Sharpe, Max Drawdown, Win Rate, Profit Factor)
2. Tabs
   ├── 📈 Equity Curve         — reconstructed from trades
   ├── 🌍 Regime Breakdown     — trades grouped by market regime
   ├── 🏷 Quality Breakdown    — A+/A/B/C win rate & avg R
   ├── ⚖️ Stop Comparison      — trailing vs fixed (only if CSV has both stop_types)
   └── 📋 All Trades           — full sortable trades table
3. Download CSV button

When no results exist: form to launch backtest_runner.py via subprocess.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st
import yaml

# ── project root on sys.path ──────────────────────────────────────────────────
_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dashboard.components.charts import render_equity_curve

# ── page config ───────────────────────────────────────────────────────────────
st.set_page_config(page_title="Backtest Results", layout="wide")

# ── constants ─────────────────────────────────────────────────────────────────
_REPORTS_DIR = _ROOT / "reports"
_RUNNER      = _ROOT / "scripts" / "backtest_runner.py"
_CONFIG_PATH = _ROOT / "config" / "settings.yaml"
_PYTHON      = sys.executable


# ── helpers ───────────────────────────────────────────────────────────────────

def _find_backtest_csvs() -> list[Path]:
    """Return backtest CSV paths sorted newest-first."""
    if not _REPORTS_DIR.exists():
        return []
    return sorted(_REPORTS_DIR.glob("backtest_*.csv"), reverse=True)


@st.cache_data(ttl=120)
def _load_trades_df(csv_path: str) -> pd.DataFrame:
    df = pd.read_csv(csv_path, parse_dates=["entry_date", "exit_date"])
    return df


def _compute_metrics_from_df(df: pd.DataFrame) -> dict:
    """Re-derive key metrics from a trades DataFrame."""
    if df.empty:
        return {}
    n          = len(df)
    wins       = df[df["pnl"] > 0]
    losses     = df[df["pnl"] <= 0]
    win_rate   = len(wins) / n
    gp         = wins["pnl"].sum()
    gl         = abs(losses["pnl"].sum())
    pf         = gp / gl if gl > 0 else float("inf")

    # Simple equity curve: sort by entry_date, cumulative pnl + initial_cap
    initial_cap = 100_000.0
    try:
        cfg_path = _CONFIG_PATH
        if cfg_path.exists():
            with cfg_path.open() as fh:
                cfg = yaml.safe_load(fh) or {}
            initial_cap = float(cfg.get("paper_trading", {}).get("initial_capital", 100_000))
    except Exception:
        pass

    total_pnl      = df["pnl"].sum()
    total_ret_pct  = (total_pnl / initial_cap) * 100.0

    # CAGR
    try:
        days  = (df["exit_date"].max() - df["entry_date"].min()).days
        years = max(days / 365.25, 1 / 365.25)
        final = initial_cap + total_pnl
        cagr  = (final / initial_cap) ** (1.0 / years) - 1.0
    except Exception:
        cagr = 0.0

    # Max drawdown from cumulative pnl timeline
    df_sorted = df.sort_values("entry_date")
    cumulative = initial_cap + df_sorted["pnl"].cumsum()
    peak       = cumulative.cummax()
    dd_series  = (peak - cumulative) / peak * 100.0
    max_dd     = float(dd_series.max())

    avg_r = float(df["r_multiple"].mean())

    # Sharpe (daily return proxy)
    sharpe = 0.0
    if len(df_sorted) >= 2:
        daily_ret = df_sorted["pnl_pct"] / 100.0
        if daily_ret.std() > 0:
            sharpe = round(float(daily_ret.mean() / daily_ret.std() * (252 ** 0.5)), 4)

    return {
        "cagr":             round(cagr, 6),
        "total_return_pct": round(total_ret_pct, 4),
        "sharpe_ratio":     round(sharpe, 4),
        "max_drawdown_pct": round(max_dd, 4),
        "win_rate":         round(win_rate, 4),
        "profit_factor":    round(pf, 4) if pf != float("inf") else pf,
        "total_trades":     n,
        "avg_r_multiple":   round(avg_r, 4),
        "initial_capital":  initial_cap,
    }


def _build_equity_curve(df: pd.DataFrame, initial_cap: float) -> list[dict]:
    """Build a simple equity curve from cumulative closed-trade PnL."""
    if df.empty:
        return []
    df_s = df.sort_values("entry_date").copy()
    df_s["portfolio_value"] = initial_cap + df_s["pnl"].cumsum()
    return [
        {"date": str(row["entry_date"].date()), "total_value": row["portfolio_value"]}
        for _, row in df_s.iterrows()
    ]


def _render_summary_cards(metrics: dict) -> None:
    pf = metrics.get("profit_factor", 0)
    pf_str = "∞" if pf == float("inf") else f"{pf:.2f}"

    c1, c2, c3, c4, c5 = st.columns(5)
    cagr_val = metrics.get("cagr", 0) * 100
    c1.metric("CAGR",         f"{cagr_val:.2f}%",
              delta=f"{cagr_val:.2f}%",
              delta_color="normal" if cagr_val >= 0 else "inverse")
    c2.metric("Sharpe Ratio", f"{metrics.get('sharpe_ratio', 0):.2f}")
    c3.metric("Max Drawdown", f"{metrics.get('max_drawdown_pct', 0):.2f}%")
    wr = metrics.get("win_rate", 0) * 100
    c4.metric("Win Rate",     f"{wr:.1f}%")
    c5.metric("Profit Factor", pf_str)


# ── page title ────────────────────────────────────────────────────────────────

st.title("🔬 Backtest Results")

csv_files = _find_backtest_csvs()

# ──────────────────────────────────────────────────────────────────────────────
# NO RESULTS: show run form
# ──────────────────────────────────────────────────────────────────────────────

if not csv_files:
    st.info("No backtest results yet.")

    with st.form("run_backtest_form"):
        st.subheader("▶ Run Backtest")
        col_l, col_r = st.columns(2)
        start_date = col_l.date_input(
            "Start Date", value=date(2019, 1, 1), key="bt_start"
        )
        default_end = date.today() - timedelta(days=365)
        end_date = col_r.date_input(
            "End Date", value=default_end, key="bt_end"
        )
        universe = st.selectbox(
            "Universe", ["nifty500", "nse_all"], key="bt_universe"
        )
        trailing_pct = st.slider(
            "Trailing Stop %", min_value=5, max_value=20, value=7, step=1,
            key="bt_trailing",
            help="e.g. 7 means 7% trailing stop"
        )
        compare = st.checkbox(
            "Compare with Fixed Stop", value=False, key="bt_compare"
        )
        submitted = st.form_submit_button("▶ Run", type="primary")

    if submitted:
        if start_date >= end_date:
            st.error("Start date must be before end date.")
        else:
            cmd = [
                _PYTHON, str(_RUNNER),
                "--start", str(start_date),
                "--end",   str(end_date),
                "--universe",     universe,
                "--trailing-stop", f"{trailing_pct / 100:.2f}",
                "--output", str(_REPORTS_DIR),
            ]
            if compare:
                cmd.append("--compare")

            progress = st.progress(0, text="Starting backtest…")
            status   = st.empty()

            with st.spinner("Running backtest (this may take several minutes)…"):
                try:
                    proc = subprocess.Popen(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        cwd=str(_ROOT),
                    )
                    log_lines: list[str] = []
                    for i, line in enumerate(proc.stdout):  # type: ignore[union-attr]
                        log_lines.append(line.rstrip())
                        status.code("\n".join(log_lines[-15:]))
                        progress.progress(min(0.95, i / 500), text="Running…")
                    proc.wait()
                    progress.progress(1.0, text="Complete!")
                    if proc.returncode == 0:
                        st.success("✅ Backtest completed. Refresh the page to load results.")
                        st.cache_data.clear()
                        st.rerun()
                    else:
                        st.error(f"Backtest failed (exit code {proc.returncode}).")
                except Exception as exc:
                    st.error(f"Failed to launch backtest runner: {exc}")
    st.stop()


# ──────────────────────────────────────────────────────────────────────────────
# RESULTS EXIST: load & display
# ──────────────────────────────────────────────────────────────────────────────

# ── file selector ─────────────────────────────────────────────────────────────
csv_names = [f.name for f in csv_files]
selected_csv_name = st.sidebar.selectbox(
    "Backtest Run", csv_names, index=0, key="bt_file_select"
)
selected_csv = _REPORTS_DIR / selected_csv_name

df = _load_trades_df(str(selected_csv))
metrics = _compute_metrics_from_df(df)
initial_cap = metrics.get("initial_capital", 100_000.0)

# ── summary cards ─────────────────────────────────────────────────────────────
_render_summary_cards(metrics)

st.divider()

# ── tabs ──────────────────────────────────────────────────────────────────────
has_compare = (
    "stop_type" in df.columns
    and df["stop_type"].nunique() > 1
)

tab_names = [
    "📈 Equity Curve",
    "🌍 Regime Breakdown",
    "🏷 Quality Breakdown",
    "📋 All Trades",
]
if has_compare:
    tab_names.insert(3, "⚖️ Stop Comparison")

tabs = st.tabs(tab_names)
tab_equity  = tabs[0]
tab_regime  = tabs[1]
tab_quality = tabs[2]
tab_trades  = tabs[-1]
tab_compare = tabs[3] if has_compare else None

# ── Tab: Equity Curve ─────────────────────────────────────────────────────────

with tab_equity:
    eq_curve = _build_equity_curve(df, initial_cap)
    render_equity_curve(eq_curve)

# ── Tab: Regime Breakdown ─────────────────────────────────────────────────────

with tab_regime:
    if "regime" not in df.columns:
        st.info("No regime column in this backtest CSV.")
    else:
        regime_rows = []
        for regime, grp in df.groupby("regime"):
            wins  = grp[grp["pnl"] > 0]
            wr    = len(wins) / len(grp)
            avg_r = grp["r_multiple"].mean()
            regime_rows.append({
                "Regime":      regime,
                "Trades":      len(grp),
                "Win Rate":    f"{wr * 100:.1f}%",
                "Avg R-Multiple": round(avg_r, 3),
            })
        df_reg = pd.DataFrame(regime_rows).sort_values("Trades", ascending=False)
        st.dataframe(df_reg, use_container_width=True, hide_index=True)

        # Bar chart: win rate by regime
        wr_vals  = [float(r.replace("%","")) for r in df_reg["Win Rate"]]
        colours  = ["#22c55e" if v >= 50 else "#ef4444" for v in wr_vals]
        fig_r, ax_r = plt.subplots(figsize=(max(5, len(df_reg) * 0.9), 4))
        ax_r.bar(df_reg["Regime"], wr_vals, color=colours, edgecolor="#374151")
        ax_r.axhline(50, color="#9ca3af", linestyle="--", linewidth=0.8, label="50% line")
        ax_r.set_ylabel("Win Rate (%)")
        ax_r.set_title("Win Rate by Market Regime")
        ax_r.legend(fontsize=8)
        ax_r.grid(True, axis="y", linestyle=":", alpha=0.5)
        plt.xticks(rotation=30, ha="right")
        fig_r.tight_layout()
        st.pyplot(fig_r)
        plt.close(fig_r)


# ── Tab: Quality Breakdown ────────────────────────────────────────────────────

with tab_quality:
    if "setup_quality" not in df.columns:
        st.info("No setup_quality column in this backtest CSV.")
    else:
        order = ["A+", "A", "B", "C"]
        qual_rows = []
        for q in order + [v for v in df["setup_quality"].unique() if v not in order]:
            grp = df[df["setup_quality"] == q]
            if grp.empty:
                continue
            wins  = grp[grp["pnl"] > 0]
            wr    = len(wins) / len(grp)
            avg_r = grp["r_multiple"].mean()
            qual_rows.append({
                "Quality":       q,
                "Trades":        len(grp),
                "Wins":          len(wins),
                "Win Rate":      f"{wr * 100:.1f}%",
                "Avg R-Multiple": round(avg_r, 3),
            })
        st.dataframe(
            pd.DataFrame(qual_rows), use_container_width=True, hide_index=True
        )

# ── Tab: Stop Comparison ─────────────────────────────────────────────────────

if has_compare and tab_compare is not None:
    with tab_compare:
        st.subheader("Trailing vs Fixed Stop Comparison")
        stop_rows = []
        for stype, grp in df.groupby("stop_type"):
            wins  = grp[grp["pnl"] > 0]
            wr    = len(wins) / len(grp)
            gp    = wins["pnl"].sum()
            gl    = abs(grp[grp["pnl"] <= 0]["pnl"].sum())
            pf    = gp / gl if gl > 0 else float("inf")

            # CAGR for this stop type
            try:
                days  = (grp["exit_date"].max() - grp["entry_date"].min()).days
                years = max(days / 365.25, 1 / 365.25)
                tot   = initial_cap + grp["pnl"].sum()
                cagr  = (tot / initial_cap) ** (1.0 / years) - 1.0
            except Exception:
                cagr = 0.0

            # Sharpe proxy
            sharpe = 0.0
            if grp["pnl_pct"].std() > 0:
                sharpe = float(grp["pnl_pct"].mean() / grp["pnl_pct"].std() * (252 ** 0.5))

            # Max drawdown
            eq = initial_cap + grp.sort_values("entry_date")["pnl"].cumsum()
            peak = eq.cummax()
            dd   = float(((peak - eq) / peak * 100).max())

            pf_str = "∞" if pf == float("inf") else f"{pf:.2f}"
            stop_rows.append({
                "Stop Type":    stype,
                "CAGR":         f"{cagr * 100:.2f}%",
                "Sharpe":       round(sharpe, 3),
                "Max DD%":      round(dd, 2),
                "Win Rate":     f"{wr * 100:.1f}%",
                "Profit Factor": pf_str,
                "Total Trades": len(grp),
            })

        st.dataframe(
            pd.DataFrame(stop_rows), use_container_width=True, hide_index=True
        )

# ── Tab: All Trades ───────────────────────────────────────────────────────────

with tab_trades:
    display_cols = [c for c in [
        "symbol", "entry_date", "exit_date", "entry_price", "exit_price",
        "pnl_pct", "r_multiple", "exit_reason", "regime", "setup_quality",
        "stop_type", "sepa_score", "quantity", "pnl",
    ] if c in df.columns]

    df_display = df[display_cols].copy()

    def _style_trades(row: pd.Series) -> list[str]:
        colour = "#16a34a18" if row.get("pnl_pct", 0) > 0 else "#dc262618"
        return [f"background-color: {colour}"] * len(row)

    st.dataframe(
        df_display.style.apply(_style_trades, axis=1),
        use_container_width=True,
        hide_index=True,
    )

# ── Download CSV ──────────────────────────────────────────────────────────────

st.divider()
with open(selected_csv, "rb") as fh:
    st.download_button(
        label="📥 Download CSV",
        data=fh.read(),
        file_name=selected_csv_name,
        mime="text/csv",
    )

