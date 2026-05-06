"""tools/run_theme_rotation_backtest.py — Phase G3 theme rotation backtest.

Strategy
--------
- Weekly rebalance (Monday).
- Hold up to `max_themes` simultaneously, `stocks_per_theme` leaders each.
- New theme entry rule: stage transitions to "markup" AND not already held.
- Theme exit rule: stage transitions to "distribute" or "markdown",
  OR rs_kospi_20d_zscore_60d <= -2.0 (emergency exit).
- Stock SELL within a held theme: leader's 20d RS goes < -2 sigma.
- Stock BUY: top `stocks_per_theme` leaders by composite_score.

Trade log
---------
Per-trade record with action / reason / holding period / realized return.
Used to inspect "왜 우리가 이 종목을 샀고/팔았는지" for every dip.

Output
------
    outputs/theme_rotation_backtest_<stamp>.json   (metrics)
    outputs/theme_rotation_trades_<stamp>.csv      (매매 내역)
    outputs/theme_rotation_monthly_<stamp>.csv     (equity curve)
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from kr_config import DATA_ROOT, DEFAULT_ROUND_TRIP_COST  # noqa: E402
from kr_helpers import log  # noqa: E402


COST_BP = 31      # round-trip basis points (mid-cap baseline)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Theme rotation backtester")
    p.add_argument("--theme-panel", required=True,
                   help="Parquet from tools/mine_theme_history.py")
    p.add_argument("--start", default="2020-01-01")
    p.add_argument("--end", default=None)
    p.add_argument("--seed-krw", type=float, default=1e8)
    p.add_argument("--max-themes", type=int, default=3)
    p.add_argument("--stocks-per-theme", type=int, default=3)
    p.add_argument("--rebalance-day", default="Mon",
                   help="Day of week for rebalance (Mon/Tue/.../Fri).")
    # Anti-shake-out hold logic — Korean market shakes positions hard;
    # short markdowns are often noise. Require N consecutive weeks of bad
    # stage before exiting a held theme.
    p.add_argument("--exit-confirmation-weeks", type=int, default=3,
                   help="Hold a position even if stage flips bad until "
                        "the bad stage persists for N weeks (default 3).")
    p.add_argument("--hard-stop-pct", type=float, default=-0.25,
                   help="Hard stop loss per holding (default -25pct). "
                        "Bypasses shake-out resistance.")
    p.add_argument("--g5-classifier-weight", type=float, default=0.0,
                   help="Phase G5: blend multibagger classifier into "
                        "leader scoring (0 = theme-only, 0.4 recommended).")
    p.add_argument("--out-prefix", default=None)
    return p.parse_args()


def _parse_day_of_week(name: str) -> int:
    mapping = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4,
                "Sat": 5, "Sun": 6}
    return mapping.get(name, 0)


def _load_theme_panel(path: Path) -> pd.DataFrame:
    panel = pd.read_parquet(path)
    panel["date"] = pd.to_datetime(panel["date"])
    panel = panel.sort_values(["theme_key", "date"]).reset_index(drop=True)
    return panel


def _load_kospi_index(start: str, end: str) -> pd.Series:
    from kr_pykrx_client import fetch_index_ohlcv
    df = fetch_index_ohlcv(
        "1028",
        pd.Timestamp(start).strftime("%Y%m%d"),
        pd.Timestamp(end).strftime("%Y%m%d"),
        refresh_days=30,
    )
    if df.empty or "close" not in df.columns:
        return pd.Series(dtype=float)
    df = df.sort_values("date").set_index("date")["close"].astype(float)
    df.index = pd.to_datetime(df.index)
    return df


def _theme_active_today(theme_panel: pd.DataFrame, day: pd.Timestamp,
                        themes_cfg: dict) -> dict[str, str]:
    """Return {theme_key: stage} for all themes available today."""
    today_rows = theme_panel[theme_panel["date"] == day]
    if today_rows.empty:
        return {}
    out = {}
    for _, r in today_rows.iterrows():
        stage = r.get("stage")
        if stage:
            out[r["theme_key"]] = stage
    return out


def _select_leader_stocks(theme_key: str, themes_cfg: dict,
                            as_of: pd.Timestamp, n: int = 3,
                            classifier_weight: float = 0.0) -> list[str]:
    """Phase G5: when classifier_weight > 0, route through the blended
    selector. Otherwise keep the legacy theme-only ranking."""
    if classifier_weight > 0:
        from kr_themes import select_theme_leaders_with_classifier
        rows = select_theme_leaders_with_classifier(
            theme_key, themes_cfg, as_of, n=n,
            classifier_weight=classifier_weight,
        )
    else:
        from kr_themes import select_theme_leaders
        rows = select_theme_leaders(theme_key, themes_cfg, as_of, n=n)
    return [r["ticker"] for r in rows]


def _fetch_close(ticker: str, day: pd.Timestamp,
                  prices_cache: dict,
                  window_start: Optional[pd.Timestamp] = None,
                  window_end: Optional[pd.Timestamp] = None) -> Optional[float]:
    """Returns close price on `day`. Cache is keyed once with the FULL
    backtest window (window_start..window_end) so subsequent calls for
    later dates return correct historical closes (the v1 bug fetched up
    to first-call day only, freezing all sells at entry price)."""
    if ticker in prices_cache:
        df = prices_cache[ticker]
    else:
        from kr_pykrx_client import fetch_ticker_history
        ws = window_start or (day - pd.Timedelta(days=400))
        we = window_end or (day + pd.Timedelta(days=400))
        try:
            df = fetch_ticker_history(
                ticker,
                ws.strftime("%Y%m%d"),
                we.strftime("%Y%m%d"),
                refresh_days=30,
            )
        except Exception:
            df = pd.DataFrame()
        if df.empty:
            return None
        df = df.sort_values("date").set_index(pd.to_datetime(df["date"]))["close"].astype(float)
        prices_cache[ticker] = df
    if hasattr(df, "loc"):
        try:
            return float(df.loc[df.index <= day].iloc[-1])
        except Exception:
            return None
    return None


def main() -> int:
    args = parse_args()
    panel_path = Path(args.theme_panel)
    if not panel_path.exists():
        log(f"[bt] theme panel missing: {panel_path}", level="ERROR")
        return 1
    theme_panel = _load_theme_panel(panel_path)
    log(f"[bt] theme panel: {len(theme_panel)} rows × "
        f"{theme_panel['theme_key'].nunique()} themes")

    end = args.end or theme_panel["date"].max().strftime("%Y-%m-%d")
    start = args.start
    log(f"[bt] backtest window {start} -> {end}")

    from kr_themes import load_themes_yaml
    themes_cfg = load_themes_yaml()

    kospi = _load_kospi_index(start, end)
    if kospi.empty:
        log("[bt] KOSPI index empty -> abort", level="ERROR")
        return 2

    cap = float(args.seed_krw)
    cash = cap
    holdings: dict[str, dict] = {}   # ticker -> {qty, theme_key, entry_date, entry_price}
    trade_log: list[dict] = []
    monthly_log: list[dict] = []
    prices_cache: dict[str, pd.Series] = {}
    bt_start_ts = pd.Timestamp(start)
    bt_end_ts = pd.Timestamp(end)

    rebalance_dow = _parse_day_of_week(args.rebalance_day)
    rebalance_dates = [
        d for d in pd.date_range(start, end, freq="B")
        if d.weekday() == rebalance_dow
    ]
    log(f"[bt] {len(rebalance_dates)} rebalance dates")

    cost = COST_BP / 10000.0

    for k, day in enumerate(rebalance_dates, 1):
        # 1. Snapshot today's portfolio value
        portfolio_value = cash
        for tk, h in holdings.items():
            px = _fetch_close(tk, day, prices_cache,
                                window_start=bt_start_ts,
                                window_end=bt_end_ts)
            if px is not None:
                portfolio_value += h["qty"] * px

        # 2. Determine active themes
        stages = _theme_active_today(theme_panel, day, themes_cfg)
        markup_themes = [t for t, s in stages.items() if s == "markup"]
        bad_themes = [t for t, s in stages.items() if s in ("distribute", "markdown")]

        # 3. Sell holdings — Korean market specific anti-shake-out logic:
        #    a) Hard-stop firing if drawdown <= --hard-stop-pct (always)
        #    b) Theme bad-stage streak >= --exit-confirmation-weeks
        #       (default 3 weeks → ignore single-week noise)
        sells_this_week = 0
        for tk in list(holdings.keys()):
            h = holdings[tk]
            px = _fetch_close(tk, day, prices_cache,
                            window_start=bt_start_ts,
                            window_end=bt_end_ts)
            if px is None:
                continue
            position_dd = px / h["entry_price"] - 1.0

            # (a) Hard stop — bypass anti-shake-out
            hard_stop_fire = position_dd <= float(args.hard_stop_pct)

            # (b) Bad-stage streak tracker
            theme_now_bad = h["theme_key"] in bad_themes
            h["bad_stage_streak"] = (
                (h.get("bad_stage_streak", 0) + 1) if theme_now_bad else 0
            )
            confirmed_bad = h["bad_stage_streak"] >= int(args.exit_confirmation_weeks)

            sell_now = hard_stop_fire or confirmed_bad
            if not sell_now:
                continue

            proceeds = h["qty"] * px * (1 - cost)
            cash += proceeds
            realized_ret = position_dd - cost
            reason = ("hard_stop" if hard_stop_fire
                       else f"theme_exit_{stages.get(h['theme_key'], '?')}_streak{h['bad_stage_streak']}w")
            trade_log.append({
                "date": day.date(),
                "ticker": tk,
                "action": "SELL",
                "reason": reason,
                "theme_key": h["theme_key"],
                "qty": h["qty"],
                "price": px,
                "value_krw": proceeds,
                "holding_days": (day - h["entry_date"]).days,
                "realized_return": realized_ret,
            })
            del holdings[tk]
            sells_this_week += 1

        # 4. Determine target themes (held + new markup)
        held_themes = {h["theme_key"] for h in holdings.values()}
        # Add new themes up to max_themes
        new_target_themes = []
        for t in markup_themes:
            if t not in held_themes and len(held_themes) + len(new_target_themes) < args.max_themes:
                new_target_themes.append(t)

        # 5. Buy stocks for new target themes
        if new_target_themes:
            target_invest_per_theme = portfolio_value / max(args.max_themes, 1)
            target_per_stock = target_invest_per_theme / max(args.stocks_per_theme, 1)
            for theme in new_target_themes:
                leaders = _select_leader_stocks(
                    theme, themes_cfg, day, n=args.stocks_per_theme,
                    classifier_weight=float(args.g5_classifier_weight),
                )
                for tk in leaders:
                    if tk in holdings or cash < target_per_stock * 0.5:
                        continue
                    px = _fetch_close(tk, day, prices_cache,
                                window_start=bt_start_ts,
                                window_end=bt_end_ts)
                    if px is None or px <= 0:
                        continue
                    qty = int(target_per_stock // (px * (1 + cost)))
                    if qty <= 0:
                        continue
                    cost_krw = qty * px * (1 + cost)
                    if cash < cost_krw:
                        continue
                    cash -= cost_krw
                    holdings[tk] = {
                        "qty": qty,
                        "theme_key": theme,
                        "entry_date": day,
                        "entry_price": px,
                    }
                    trade_log.append({
                        "date": day.date(),
                        "ticker": tk,
                        "action": "BUY",
                        "reason": "theme_markup_entry",
                        "theme_key": theme,
                        "qty": qty,
                        "price": px,
                        "value_krw": cost_krw,
                        "holding_days": 0,
                        "realized_return": 0.0,
                    })

        # Update portfolio value for log (after trades)
        portfolio_value = cash
        for tk, h in holdings.items():
            px = _fetch_close(tk, day, prices_cache,
                                window_start=bt_start_ts,
                                window_end=bt_end_ts)
            if px is not None:
                portfolio_value += h["qty"] * px

        monthly_log.append({
            "date": day.date(),
            "capital": portfolio_value,
            "cash": cash,
            "n_holdings": len(holdings),
            "n_themes_held": len({h["theme_key"] for h in holdings.values()}),
            "sells_today": sells_this_week,
        })
        if k % 10 == 0:
            log(f"[bt] {k}/{len(rebalance_dates)}: capital={portfolio_value:,.0f} "
                f"holdings={len(holdings)}")

    # Final snapshot
    final_value = cash
    last_day = pd.Timestamp(end)
    for tk, h in holdings.items():
        px = _fetch_close(tk, last_day, prices_cache)
        if px is not None:
            final_value += h["qty"] * px

    # Compute metrics
    df_log = pd.DataFrame(monthly_log)
    if not df_log.empty:
        df_log["date"] = pd.to_datetime(df_log["date"])
        df_log = df_log.sort_values("date")
        df_log["peak"] = df_log["capital"].cummax()
        df_log["dd"] = df_log["capital"] / df_log["peak"] - 1
        years = (df_log["date"].iloc[-1] - df_log["date"].iloc[0]).days / 365.25
        cagr = (final_value / float(args.seed_krw)) ** (1 / max(years, 1e-6)) - 1 if years > 0 else 0
        mdd = float(df_log["dd"].min())
        rets = df_log["capital"].pct_change().dropna()
        sharpe = float(rets.mean() / rets.std() * np.sqrt(52)) if rets.std() > 0 else 0
    else:
        cagr = mdd = sharpe = years = 0
    df_trades = pd.DataFrame(trade_log)

    # Persist
    out_dir = DATA_ROOT / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    prefix = args.out_prefix or f"theme_rotation_backtest_{stamp}"
    metrics_path = out_dir / f"{prefix}.json"
    monthly_path = out_dir / f"{prefix}_monthly.csv"
    trades_path = out_dir / f"{prefix}_trades.csv"

    metrics = {
        "stamp": stamp,
        "window": [args.start, args.end],
        "seed_krw": float(args.seed_krw),
        "final_krw": float(final_value),
        "cum_return": float(final_value / args.seed_krw - 1),
        "cagr": float(cagr),
        "mdd": float(mdd),
        "sharpe": float(sharpe),
        "years": float(years),
        "n_trades": int(len(df_trades)),
        "n_unique_tickers": int(df_trades["ticker"].nunique() if not df_trades.empty else 0),
        "n_themes_traded": int(df_trades["theme_key"].nunique() if not df_trades.empty else 0),
        "max_themes": args.max_themes,
        "stocks_per_theme": args.stocks_per_theme,
    }
    with open(metrics_path, "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2, default=str)
    if not df_log.empty:
        df_log.to_csv(monthly_path, index=False)
    if not df_trades.empty:
        df_trades.to_csv(trades_path, index=False, encoding="utf-8-sig")

    log(f"[bt] CAGR={cagr*100:.2f}pct  MDD={mdd*100:.2f}pct  "
        f"Sharpe={sharpe:.2f}  Years={years:.2f}")
    log(f"[bt] wrote {metrics_path.name}, {monthly_path.name}, {trades_path.name}")
    return 0


if __name__ == "__main__":
    from typing import Optional   # local import to keep top clean
    sys.exit(main())
