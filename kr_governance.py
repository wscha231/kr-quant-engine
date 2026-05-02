"""kr_governance — Korea-specific governance risk overlay (Phase C2).

Layered ON TOP of the existing P2 DART event signals (kr_features
add_disclosure_event_signal). The DART-event scores feed RETURN prediction
(alpha), this module feeds RISK prediction (downside / governance discount).

Module purpose
--------------
Korean equities suffer chronic discounts ("코리아 디스카운트") driven by
governance pathologies that price-and-fundamentals factors do not capture:
  1. Owner dilution via 유상증자 / CB / BW (esp. 제3자배정 to related parties)
  2. Treasury share sale (vs cancellation = positive)
  3. Related-party leakage (비상장 계열사로 수익 이전)
  4. Spinoff & subsidiary re-listing (물적분할 → 자회사 IPO)
  5. Succession transactions (오너 일가 승계)
  6. Capital reduction (감자)
  7. Overheating / managed-stock listings (already covered by P2.7)

Scoring model
-------------
governance_risk_score is a unitless [0, 1] composite where
  0   = no governance flags in lookback window (24m default)
  1   = maximum risk (multiple severe events)

Per-component scores are sum-bounded; the aggregate is clipped to [0, 1].
A small set of HARD VETO triggers force `governance_hard_veto_flag=True`
regardless of the numeric score (e.g. active 물적분할 with subsidiary
listing risk).

Integration points
------------------
1. kr_features.add_governance_signals(universe, rebal_date, event_panel)
   -> universe with 11 PHASE2_GOVERNANCE_COLUMNS appended.
2. kr_backtester_realistic.run_realistic_backtest:
   - Picks ranking: adjusted_score = p_pre_surge * (1 - 0.35*risk)
   - Hard-veto filter
   - Position sizing: governance_weight_cap(risk, normal_weight)

Design choice (per research/03_korea_specific_signals/governance_risk_overlay_research.md)
-----------------------------------------------------------------------------------------
WEIGHT CAP > soft penalty > hard veto: weight cap balances tail-risk control
with avoiding over-filtering of recoverable names. Hard veto reserved for
the worst structural events.

PIT-safe: every event lookup uses rcept_dt <= rebalance_date.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd
import yaml

from kr_config import DATA_ROOT, PROJECT_ROOT
from kr_helpers import log


# ---------------------------------------------------------------------------
# Registry of major chaebol / governance-watch groups
# ---------------------------------------------------------------------------
_GOVERNANCE_ENTITIES_PATH = PROJECT_ROOT / "governance_entities.yaml"

_DEFAULT_LOOKBACK_MONTHS = 24      # rolling window for most components
_DILUTION_LOOKBACK_MONTHS = 12     # tighter window for dilution events
_TREASURY_LOOKBACK_MONTHS = 12
_SPINOFF_LOOKBACK_MONTHS = 18
_CAPITAL_REDUCTION_LOOKBACK_MONTHS = 18

# Hard-veto trigger thresholds (all calibrated to research note §1.A-§1.F)
HARD_VETO_THRESHOLDS = {
    "dilution_pct_3rd_party": 0.10,    # >=10% dilution via 제3자배정 -> veto
    "dilution_pct_any": 0.20,           # >=20% dilution any method -> veto
    "treasury_sale_pct": 0.05,          # >=5% mcap treasury share sale -> veto
    "capital_reduction_active_months": _CAPITAL_REDUCTION_LOOKBACK_MONTHS,
    "spinoff_physical_active_months": _SPINOFF_LOOKBACK_MONTHS,
}


def load_governance_entities(path: Optional[Path] = None) -> dict:
    """Load governance_entities.yaml. Returns empty dict on missing file.

    Schema:
        groups:
          samsung_group:
            listed_tickers: ["005930", ...]
            controlling_family: ["이재용", ...]
            private_affiliates: [...]
            watch_keywords: ["승계", "물적분할", ...]
            risk_override:
              base_governance_risk: 0.10
    """
    p = path or _GOVERNANCE_ENTITIES_PATH
    if not p.exists():
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data.get("groups", {}) or {}
    except Exception as e:
        log(f"[governance] failed to load {p}: {e}", level="WARN")
        return {}


def _ticker_to_group(entities: dict) -> dict[str, str]:
    """Reverse map ticker -> group_key. Empty when entities empty."""
    out: dict[str, str] = {}
    for group_key, meta in (entities or {}).items():
        for tk in meta.get("listed_tickers", []) or []:
            out[str(tk).zfill(6)] = group_key
    return out


# ---------------------------------------------------------------------------
# Per-component risk scorers (PIT-safe; operate on event subsets only)
# ---------------------------------------------------------------------------
def _filter_lookback(events: pd.DataFrame, as_of: pd.Timestamp,
                     months: int) -> pd.DataFrame:
    """Slice event_panel rows with rcept_dt in (as_of - months, as_of]."""
    if events.empty or "rcept_dt" not in events.columns:
        return events.iloc[0:0]
    cutoff = as_of - pd.DateOffset(months=months)
    return events[
        events["rcept_dt"].notna()
        & (events["rcept_dt"] > cutoff)
        & (events["rcept_dt"] <= as_of)
    ]


def compute_owner_dilution_risk(
    ticker_events: pd.DataFrame,
    as_of: pd.Timestamp,
    mcap: float,
) -> dict:
    """유상증자 + CB + BW dilution risk.

    Returns dict with:
        owner_dilution_risk_score  in [0, 1]
        dilution_pct_12m           sum of (qty*price)/mcap for piicDecsn within 12m
        cb_bw_burden_pct           sum of bd_fta/mcap for cvbdIsDecsn + bdIsDecsn
        third_party_allocation_flag  1 if any 12m piicDecsn used 제3자배정
    """
    sub = _filter_lookback(ticker_events, as_of, _DILUTION_LOOKBACK_MONTHS)
    if sub.empty or mcap <= 0:
        return {
            "owner_dilution_risk_score": 0.0,
            "dilution_pct_12m": 0.0,
            "cb_bw_burden_pct": 0.0,
            "third_party_allocation_flag": 0,
        }

    cap_inc = sub[sub["event_category"] == "capital_increase"]
    cb = sub[sub["event_category"] == "convertible_bond"]
    bw = sub[sub["event_category"] == "warrant_bond"]

    # Capital-increase dilution: nstk_ostk_qy * bdis_pric / mcap.
    # Defensive: pd.DataFrame.get(col, default) returns SCALAR default when
    # column is absent, which then breaks .fillna(). Guard with explicit
    # column-presence check.
    dilution_amt = 0.0
    if not cap_inc.empty:
        if "nstk_ostk_qy" in cap_inc.columns:
            qty = pd.to_numeric(cap_inc["nstk_ostk_qy"],
                                  errors="coerce").fillna(0)
        else:
            qty = pd.Series(0.0, index=cap_inc.index)
        if "bdis_pric" in cap_inc.columns:
            pric = pd.to_numeric(cap_inc["bdis_pric"],
                                   errors="coerce").fillna(0)
        else:
            pric = pd.Series(0.0, index=cap_inc.index)
        dilution_amt = float((qty * pric).sum())
    dilution_pct = (dilution_amt / mcap) if mcap > 0 else 0.0

    # 제3자배정 detection: ic_mthn column contains "제3자배정"
    third_party_flag = 0
    if not cap_inc.empty and "ic_mthn" in cap_inc.columns:
        third_party_flag = int(
            cap_inc["ic_mthn"].astype(str).str.contains("제3자|3자배정",
                                                          na=False).any()
        )

    # CB/BW notional / mcap
    cb_bw_amt = 0.0
    for sub_df in (cb, bw):
        if sub_df.empty:
            continue
        if "bd_fta" in sub_df.columns:
            amt = pd.to_numeric(sub_df["bd_fta"],
                                 errors="coerce").fillna(0).sum()
            cb_bw_amt += float(amt)
    cb_bw_pct = (cb_bw_amt / mcap) if mcap > 0 else 0.0

    # Composite risk:
    #   0.5 * min(1, dilution_pct / 0.10)   [10% dilution = max contribution]
    #   0.3 * min(1, cb_bw_pct / 0.20)      [20% CB/BW notional = max]
    #   0.2 * third_party_flag
    score = (
        0.5 * min(1.0, dilution_pct / 0.10)
        + 0.3 * min(1.0, cb_bw_pct / 0.20)
        + 0.2 * third_party_flag
    )
    return {
        "owner_dilution_risk_score": float(min(1.0, score)),
        "dilution_pct_12m": float(dilution_pct),
        "cb_bw_burden_pct": float(cb_bw_pct),
        "third_party_allocation_flag": int(third_party_flag),
    }


def compute_treasury_overhang_risk(
    ticker_events: pd.DataFrame,
    as_of: pd.Timestamp,
    mcap: float,
) -> dict:
    """자사주 처분 (treasury share sale) overhang risk.

    Note: treasury BUYBACK is a positive signal (alpha-side, kr_features).
    This scorer is for SALE only — supply pressure + governance signal.
    """
    sub = _filter_lookback(ticker_events, as_of, _TREASURY_LOOKBACK_MONTHS)
    sales = sub[sub["event_category"] == "treasury_sell"] if not sub.empty \
        else sub
    if sales.empty or mcap <= 0:
        return {
            "treasury_sale_overhang_score": 0.0,
            "treasury_sale_pct_12m": 0.0,
        }
    if "dppln_prc_ostk" in sales.columns:
        amt = pd.to_numeric(sales["dppln_prc_ostk"],
                              errors="coerce").fillna(0).sum()
    else:
        amt = 0.0
    pct = (float(amt) / mcap) if mcap > 0 else 0.0
    # 5% mcap sale = max risk
    score = min(1.0, pct / 0.05)
    return {
        "treasury_sale_overhang_score": float(score),
        "treasury_sale_pct_12m": float(pct),
    }


def compute_spinoff_risk(ticker_events: pd.DataFrame,
                          as_of: pd.Timestamp) -> dict:
    """물적분할 (physical spinoff) risk. Korea-specific.

    Returns:
        spinoff_listing_risk_score in [0, 1]
        physical_spinoff_active_flag  1 if any 18m divDecsn with 물적분할
    """
    sub = _filter_lookback(ticker_events, as_of, _SPINOFF_LOOKBACK_MONTHS)
    spinoff = sub[sub["event_category"] == "spinoff"] if not sub.empty else sub
    if spinoff.empty:
        return {
            "spinoff_listing_risk_score": 0.0,
            "physical_spinoff_active_flag": 0,
        }
    physical_flag = 0
    if "div_mth" in spinoff.columns:
        physical_flag = int(
            spinoff["div_mth"].astype(str).str.contains("물적", na=False).any()
        )
    elif len(spinoff) > 0:
        # Schema fallback: any spinoff event in window treated as risky
        physical_flag = 1
    score = 0.7 if physical_flag else 0.3 * (1 if len(spinoff) > 0 else 0)
    return {
        "spinoff_listing_risk_score": float(score),
        "physical_spinoff_active_flag": int(physical_flag),
    }


def compute_capital_reduction_risk(ticker_events: pd.DataFrame,
                                    as_of: pd.Timestamp) -> dict:
    """감자 (capital reduction) — usually distress signal."""
    sub = _filter_lookback(ticker_events, as_of,
                            _CAPITAL_REDUCTION_LOOKBACK_MONTHS)
    cr = sub[sub["event_category"] == "capital_reduction"] if not sub.empty \
        else sub
    if cr.empty:
        return {"capital_reduction_active_flag": 0}
    return {"capital_reduction_active_flag": 1}


def compute_succession_proxy(
    ticker_events: pd.DataFrame,
    as_of: pd.Timestamp,
    is_chaebol_member: bool,
) -> dict:
    """승계 위험 — heavy proxy via repeated insider/major-holder activity in
    chaebol-affiliated names + repeated governance events.

    Full implementation requires private-affiliate registry parsing (P_C2_b);
    this proxy uses chaebol membership × frequent insider trading activity
    as a stand-in.
    """
    sub = _filter_lookback(ticker_events, as_of, _DEFAULT_LOOKBACK_MONTHS)
    if sub.empty:
        return {"succession_risk_score": 0.0}
    insider_count = int((sub["event_category"] == "insider_holdings").sum())
    major_count = int((sub["event_category"] == "major_holders").sum())
    # >=20 insider + >=5 major in chaebol = elevated
    base = (
        0.4 * min(1.0, insider_count / 30.0)
        + 0.2 * min(1.0, major_count / 10.0)
    )
    if is_chaebol_member:
        base = min(1.0, base * 1.5)
    return {"succession_risk_score": float(base)}


def compute_capital_allocation_quality(
    ticker_events: pd.DataFrame,
    as_of: pd.Timestamp,
    mcap: float,
) -> dict:
    """+ side: treasury BUYBACK (intent: cancellation), bonus issue.

    Cancellation is hard to detect from event data alone (separate filing).
    Conservative: treat all buyback as moderately positive; if accompanied by
    bonus_issue in same window, infer shareholder-friendly stance.
    """
    sub = _filter_lookback(ticker_events, as_of, _TREASURY_LOOKBACK_MONTHS)
    if sub.empty or mcap <= 0:
        return {"capital_allocation_quality_score": 0.0}
    buyback = sub[sub["event_category"] == "treasury_buyback"]
    bonus = sub[sub["event_category"] == "bonus_issue"]
    buyback_pct = 0.0
    if not buyback.empty:
        if "aqpln_prc_ostk" in buyback.columns:
            amt = pd.to_numeric(buyback["aqpln_prc_ostk"],
                                  errors="coerce").fillna(0).sum()
        else:
            amt = 0.0
        buyback_pct = (float(amt) / mcap) if mcap > 0 else 0.0
    quality = 0.6 * min(1.0, buyback_pct / 0.02)         # 2% buyback = max
    quality += 0.3 * (1 if not bonus.empty else 0)
    return {"capital_allocation_quality_score": float(min(1.0, quality))}


# ---------------------------------------------------------------------------
# Top-level: per-ticker score aggregator
# ---------------------------------------------------------------------------
def compute_governance_score_for_ticker(
    ticker_events: pd.DataFrame,
    as_of: pd.Timestamp,
    mcap: float,
    is_chaebol_member: bool = False,
) -> dict:
    """Aggregate 11 PHASE2_GOVERNANCE_COLUMNS for one ticker.

    Returns dict keyed by exact column names in
    kr_config.PHASE2_GOVERNANCE_COLUMNS plus a few diagnostic fields.
    """
    out: dict[str, float] = {
        "governance_risk_score": 0.0,
        "governance_quality_score": 0.0,
        "owner_dilution_risk_score": 0.0,
        "treasury_sale_overhang_score": 0.0,
        "related_party_leakage_score": 0.0,    # P_C2_b — placeholder
        "succession_risk_score": 0.0,
        "minority_shareholder_discount_score": 0.0,  # composite
        "spinoff_listing_risk_score": 0.0,
        "capital_allocation_quality_score": 0.0,
        "governance_watchlist_flag": 0.0,
        "governance_hard_veto_flag": 0.0,
    }

    dil = compute_owner_dilution_risk(ticker_events, as_of, mcap)
    out["owner_dilution_risk_score"] = dil["owner_dilution_risk_score"]

    treasury = compute_treasury_overhang_risk(ticker_events, as_of, mcap)
    out["treasury_sale_overhang_score"] = treasury["treasury_sale_overhang_score"]

    spinoff = compute_spinoff_risk(ticker_events, as_of)
    out["spinoff_listing_risk_score"] = spinoff["spinoff_listing_risk_score"]

    cap_red = compute_capital_reduction_risk(ticker_events, as_of)

    succession = compute_succession_proxy(ticker_events, as_of, is_chaebol_member)
    out["succession_risk_score"] = succession["succession_risk_score"]

    quality = compute_capital_allocation_quality(ticker_events, as_of, mcap)
    out["capital_allocation_quality_score"] = quality["capital_allocation_quality_score"]

    # Aggregate risk: weighted sum, clipped to [0, 1]
    risk = (
        0.30 * out["owner_dilution_risk_score"]
        + 0.20 * out["treasury_sale_overhang_score"]
        + 0.20 * out["spinoff_listing_risk_score"]
        + 0.15 * out["succession_risk_score"]
        + 0.15 * (1.0 if cap_red["capital_reduction_active_flag"] else 0.0)
    )
    out["governance_risk_score"] = float(min(1.0, risk))
    out["governance_quality_score"] = float(out["capital_allocation_quality_score"])
    out["minority_shareholder_discount_score"] = float(
        max(0.0, out["governance_risk_score"] - out["governance_quality_score"])
    )

    # Hard-veto rules:
    #   1. dilution_pct_12m >= 10% AND third_party_flag       -> veto
    #   2. dilution_pct_12m >= 20% (any)                     -> veto
    #   3. treasury_sale_pct_12m >= 5%                       -> veto
    #   4. capital_reduction_active_flag                     -> veto
    #   5. physical_spinoff_active_flag                      -> veto
    veto = False
    if (dil["dilution_pct_12m"] >= HARD_VETO_THRESHOLDS["dilution_pct_3rd_party"]
            and dil["third_party_allocation_flag"]):
        veto = True
    if dil["dilution_pct_12m"] >= HARD_VETO_THRESHOLDS["dilution_pct_any"]:
        veto = True
    if treasury["treasury_sale_pct_12m"] >= HARD_VETO_THRESHOLDS["treasury_sale_pct"]:
        veto = True
    if cap_red["capital_reduction_active_flag"]:
        veto = True
    if spinoff["physical_spinoff_active_flag"]:
        veto = True
    out["governance_hard_veto_flag"] = float(int(veto))

    # Watchlist: risk >= 0.40 OR any single component >= 0.60
    watch = out["governance_risk_score"] >= 0.40 or any(
        v >= 0.60 for v in (
            out["owner_dilution_risk_score"],
            out["treasury_sale_overhang_score"],
            out["spinoff_listing_risk_score"],
        )
    )
    out["governance_watchlist_flag"] = float(int(watch))

    return out


# ---------------------------------------------------------------------------
# Backtester integration helpers
# ---------------------------------------------------------------------------
def is_hard_veto(scores: dict) -> bool:
    """Convenience: True if governance_hard_veto_flag set."""
    return float(scores.get("governance_hard_veto_flag", 0.0)) >= 0.5


def governance_weight_cap(governance_risk_score: float,
                            normal_weight: float) -> float:
    """Risk-tiered weight cap (per research/03/governance_risk_overlay).

        risk >= 0.80    -> 0.00 (block)
        risk >= 0.60    -> 0.02
        risk >= 0.40    -> 0.04
        risk <  0.40    -> normal_weight
    """
    if governance_risk_score >= 0.80:
        return 0.0
    if governance_risk_score >= 0.60:
        return min(normal_weight, 0.02)
    if governance_risk_score >= 0.40:
        return min(normal_weight, 0.04)
    return float(normal_weight)


# ---------------------------------------------------------------------------
# Universe-level wiring (called from kr_features and/or backtester)
# ---------------------------------------------------------------------------
def add_governance_signals(
    universe: pd.DataFrame,
    rebalance_date: pd.Timestamp,
    event_panel: Optional[pd.DataFrame] = None,
    governance_entities: Optional[dict] = None,
) -> pd.DataFrame:
    """Append 11 PHASE2_GOVERNANCE_COLUMNS to `universe` for `rebalance_date`.

    Args:
        universe: snapshot (must contain `ticker` and `market_cap`).
        rebalance_date: PIT cutoff timestamp.
        event_panel: long-format DART event panel from
                     kr_features.prepare_event_panel. If None or empty, all
                     governance columns zero-filled.
        governance_entities: pre-loaded YAML registry, else loaded on demand.
    """
    from kr_config import PHASE2_GOVERNANCE_COLUMNS

    out = universe.copy()
    # Zero-fill defaults
    for col in PHASE2_GOVERNANCE_COLUMNS:
        out[col] = 0.0

    if out.empty:
        return out

    if event_panel is None or event_panel.empty or "ticker" not in event_panel.columns:
        log("[governance] empty event_panel -> all governance scores 0",
            level="WARN")
        return out

    # PIT slice
    rd = pd.Timestamp(rebalance_date).normalize()
    pit_panel = event_panel[
        event_panel["rcept_dt"].notna()
        & (event_panel["rcept_dt"] <= rd)
    ]
    if pit_panel.empty:
        return out

    if governance_entities is None:
        governance_entities = load_governance_entities()
    chaebol_map = _ticker_to_group(governance_entities)
    chaebol_set = set(chaebol_map.keys())

    # Group events by ticker for vectorized lookup. Normalize ticker
    # representation on BOTH sides — for real production tickers (6-digit
    # numeric) zfill is a no-op; for synthetic test tickers we leave them
    # untouched so the groupby key matches the universe key.
    pit_panel_norm = pit_panel.copy()
    pit_panel_norm["ticker"] = pit_panel_norm["ticker"].astype(str)
    if pit_panel_norm["ticker"].str.fullmatch(r"\d+").any():
        pit_panel_norm.loc[
            pit_panel_norm["ticker"].str.fullmatch(r"\d+"), "ticker"
        ] = pit_panel_norm.loc[
            pit_panel_norm["ticker"].str.fullmatch(r"\d+"), "ticker"
        ].str.zfill(6)
    by_ticker = {tk: g for tk, g in pit_panel_norm.groupby("ticker")}

    out["ticker"] = out["ticker"].astype(str)
    numeric_mask = out["ticker"].str.fullmatch(r"\d+")
    out.loc[numeric_mask, "ticker"] = out.loc[numeric_mask, "ticker"].str.zfill(6)

    rows = []
    for _, row in out.iterrows():
        tk = str(row["ticker"])
        mcap = float(row.get("market_cap") or 0.0)
        ticker_events = by_ticker.get(tk, pit_panel_norm.iloc[0:0])
        scores = compute_governance_score_for_ticker(
            ticker_events, rd, mcap, is_chaebol_member=(tk in chaebol_set),
        )
        rows.append({"ticker": tk, **scores})

    scores_df = pd.DataFrame(rows)

    # Replace zero-fills with computed values
    for col in PHASE2_GOVERNANCE_COLUMNS:
        if col in scores_df.columns:
            mapping = dict(zip(scores_df["ticker"], scores_df[col]))
            out[col] = out["ticker"].map(mapping).fillna(0.0).astype(float)

    n_veto = int((out["governance_hard_veto_flag"] >= 0.5).sum())
    n_watch = int((out["governance_watchlist_flag"] >= 0.5).sum())
    log(f"[governance] {rd.date()}: {len(out)} tickers, {n_veto} hard-veto, "
        f"{n_watch} watchlist, mean risk="
        f"{out['governance_risk_score'].mean():.3f}")
    return out


# ---------------------------------------------------------------------------
# Sanity test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("=" * 60)
    print("kr_governance sanity test")
    print("=" * 60)
    # Synthetic event panel: dilution, treasury sale, capital reduction
    events = pd.DataFrame([
        {
            "ticker": "TEST1", "event_category": "capital_increase",
            "rcept_dt": pd.Timestamp("2024-03-15"),
            "nstk_ostk_qy": 1_000_000, "bdis_pric": 10_000,
            "ic_mthn": "제3자배정",
        },
        {
            "ticker": "TEST1", "event_category": "treasury_sell",
            "rcept_dt": pd.Timestamp("2024-04-01"),
            "dppln_prc_ostk": 5_000_000_000,
        },
        {
            "ticker": "TEST2", "event_category": "spinoff",
            "rcept_dt": pd.Timestamp("2024-01-15"),
            "div_mth": "물적분할",
        },
        {
            "ticker": "TEST3", "event_category": "treasury_buyback",
            "rcept_dt": pd.Timestamp("2024-05-01"),
            "aqpln_prc_ostk": 20_000_000_000,
        },
    ])
    universe = pd.DataFrame({
        "ticker": ["TEST1", "TEST2", "TEST3"],
        "market_cap": [100_000_000_000, 200_000_000_000, 1_000_000_000_000],
    })
    enriched = add_governance_signals(
        universe, pd.Timestamp("2024-06-30"), event_panel=events,
    )
    print()
    cols = [
        "ticker", "governance_risk_score", "governance_quality_score",
        "owner_dilution_risk_score", "treasury_sale_overhang_score",
        "spinoff_listing_risk_score", "governance_hard_veto_flag",
        "governance_watchlist_flag",
    ]
    print(enriched[cols].to_string(index=False))

    print()
    print("Hard-veto sanity:")
    for _, r in enriched.iterrows():
        veto = is_hard_veto(r.to_dict())
        cap = governance_weight_cap(r["governance_risk_score"], 0.05)
        print(f"  {r['ticker']}: hard_veto={veto}, weight_cap_at_5pct={cap:.3f}")
