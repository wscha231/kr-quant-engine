# Session Handoff — Single Inbox

> 다음 세션에서 가장 먼저 읽을 파일. "방금 뭐 했고 다음에 뭐 할지" 한 가지만 적는다.
> Phase 끝날 때마다 덮어씀. 누적하지 말 것.

---

## 마지막으로 한 일 (2026-04-28 16:15 KST)

**4개 신규 layer 순차 SHIPPED** (사용자 요청 "한국경제 + 수급 + 파생 + regime 모두"):

| Layer | 모듈 | 컬럼 | Tests |
|---|---|---|---|
| **P3.2 Macro** | `kr_macro.py` | 23 (금리/환율/경기/글로벌) | 13/13 |
| **P2.5 Flow** | `kr_flow.py` | 17 (종목/시장 외인기관) | 14/14 |
| **P2.6 Derivatives** | `kr_derivatives.py` | 7 (VKOSPI + 외인선물) | 12/12 |
| **P3.3 Regime** | `kr_regime.py` | 9 (8 regime + multipliers) | 14/14 |

전체 코드 ~5,800 → **~7,200 lines**, tests **102 → 162 (+60)**.

### 시그널 카탈로그 (158 columns 총합)

```
P0 momentum (9)        : 1m/3m/6m/12m + 12-1m skip + RS
P1 fundamentals (16)   : PER/PBR/ROE/margins/growth + value/quality/turnaround
P2 DART events (12)    : KRW-based (treasury_buyback/insider_net_buy/cap_increase 등)
P2.5 Flow (17)         : foreign/inst/individual zscore + streak + holding pct
                          + market-level KOSPI/KOSDAQ rolling
P2.6 Derivatives (7)   : VKOSPI level/zscore/panic + foreign futures OI
P3.1 Technicals (31)   : MA stack + 52w + RSI + ATR + BB + Stage + Trend Template
P3.2 Macro (23)        : BOK + FRED + yfinance: 금리/환율/PMI/수출/유가/VIX/DXY
P3.3 Regime (9)        : 8 regime label + per-sleeve multipliers (r1000 Phase 4)
```

### 8개 Regime + Sleeve Multipliers (r1000 Phase 4 ported)

| Regime | Trigger | core/future/early multiplier |
|---|---|---|
| bull_trending | KOSPI > MA200 + foreign cum buy + VKOSPI < 18 | 1.00 / 1.30 / 1.20 |
| bull_peaking | VKOSPI 18-25 + foreign sell start | 1.10 / 0.85 / 0.80 |
| bear_falling | KOSPI < MA200 + VKOSPI > 25 + foreign sell | 1.20 / 0.50 / 0.40 |
| bear_bottoming | VKOSPI > 30 + sell slowing | 0.90 / 1.10 / 1.30 |
| recovery | MA200 reclaim + foreign return + PMI < 50 | 1.00 / 1.20 / 1.40 |
| sideways | default | 1.00 / 1.00 / 1.00 |
| stagflation_kr | PMI < 48 + USDKRW z>1 + BOK hike | 1.30 / 0.70 / 0.50 |
| won_crisis | USDKRW > 1400 + 외인 대량매도 + -10%/5d | 0.60 / 0.30 / 0.20 |

### Phase Toggles (env vars)

```powershell
$env:PHASE_PHASE0_MOMENTUM_ENABLED="1"      # default ON
$env:PHASE_PHASE1_FUNDAMENTAL_ENABLED="0"
$env:PHASE_PHASE2_DART_EVENTS_ENABLED="0"
$env:PHASE_PHASE2_FLOW_ENABLED="0"           # NEW
$env:PHASE_PHASE2_DERIVATIVES_ENABLED="0"    # NEW
$env:PHASE_PHASE3_TECHNICAL_ENABLED="0"
$env:PHASE_PHASE3_MACRO_ENABLED="0"          # NEW
$env:PHASE_PHASE3_REGIME_ENABLED="0"         # NEW
```

## 다음 액션

### 1. 사용자 — pip install + 첫 fetch (필수)
```powershell
cd H:\codex\kr_quant_engine
py -3 -m pip install -r requirements.txt
py -3 kr_macro.py            # BOK + FRED + yfinance panel
py -3 kr_dart_client.py       # DART events (Samsung +0.117 expected)
```

### 2. P_MB.2 Multibagger Classifier (개발자, 다음 세션 ★ 우선)
이제 158 features 갖춰짐 → r1000 phase11 entry classifier 패턴 가능:
- `kr_multibagger.add_pre_surge_features(episodes, fund_panel, event_panel, flow_panel, macro_panel, deriv_panel, prices)` — 모든 시그널 collect
- `kr_multibagger_classifier.py` — CatBoost binary, walk-forward 5-fold
- `research/06_walkforward_baselines/p_mb_v1_classifier_results.md` — AUC, top picks

### 3. P0/P1/P2/P3 baseline 측정 (사용자, 모든 토글 ON)
```powershell
$env:PHASE_PHASE1_FUNDAMENTAL_ENABLED="1"
$env:PHASE_PHASE2_DART_EVENTS_ENABLED="1"
$env:PHASE_PHASE2_FLOW_ENABLED="1"
$env:PHASE_PHASE2_DERIVATIVES_ENABLED="1"
$env:PHASE_PHASE3_TECHNICAL_ENABLED="1"
$env:PHASE_PHASE3_MACRO_ENABLED="1"
$env:PHASE_PHASE3_REGIME_ENABLED="1"
py -3 run_local.py --quick --start-date 2019-01-01 --end-date 2024-12-31
```

### 4. 후속 (incremental, 후순위)
- D-1: DART 전체 IS/BS/CF parsing
- D-2: FCF / ROIC / Sloan accruals
- P2.7: 단기과열/투자경고/관리종목 (KRX scrape)
- P2.8: 테마 분류 + phase classifier (themes.yaml + Naver)

## 알려진 logic issues

| # | 이슈 | 위치 | 우선 |
|---|---|---|---|
| 2 | listed_months stub | kr_universe | P3 |
| 3 | TTM annual factor | kr_features._compute_ttm_from_panel | D-4 deferred |
| 4 | 가격제한폭 fill | kr_pipeline.backtest | P3 |
| 5 | OCF는 multi에서 미반환 | kr_dart_client | D-1 deferred |
| 6 | KOSIS 선행지수 미연동 | kr_macro | API 키 발급 후 |
| 7 | foreign_futures_net_oi pykrx 모듈 fragile | kr_derivatives | P3+ |

## 차단 사항
- pykrx + KOSIS API 미설치/미발급
- 그 외 OK

## GitHub
- Repo: https://github.com/wscha231/kr-quant-engine (private)
- 다음 commit: 4-layer (macro + flow + derivatives + regime)
