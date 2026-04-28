# Session Handoff — Single Inbox

> 다음 세션에서 가장 먼저 읽을 파일. "방금 뭐 했고 다음에 뭐 할지" 한 가지만 적는다.
> Phase 끝날 때마다 덮어씀. 누적하지 말 것.

---

## 마지막으로 한 일 (2026-04-28 14:30 KST)

**C + D-3 순차 SHIPPED**. 사용자 요청 "순차적으로 수정해" 따라 진행:

### C — P2 DART 이벤트 v2 재설계 (count → KRW-amount-based)
v1 Samsung 폭발 (+43.10 from 2,614 row × count) → v2 정상화 (+0.117 from 2.68조/600조 mcap × 0.50 weight). 5개 scoring modes (amount_pct_mcap, computed_dilution, binary, insider_net_buy, stkrt_change). `kr_features.prepare_event_panel` + `add_disclosure_event_signal` + `compute_p2_score` 통합.

### D-3 — 기술지표 (P3.1)
`kr_technicals.py` (340 lines) — 31 indicators:
- MA 5/20/50/60/150/200 + stack alignment
- 52w high/low + distance
- RSI 14, ATR 14, Bollinger 20/2
- Volume MA + zscore + dryup
- Volatility contraction (Minervini VCP)
- Weinstein 4-stage classifier
- **Minervini 8-condition trend template** (score 0-8, ≥7 = pass)
- Breakout flag (52w high + volume spike)

`kr_features.add_technical_indicators` integration with `PHASE_PHASE3_TECHNICAL_ENABLED` toggle.

### Test 결과 (총 102/102 통과)
- smoke: 37/37
- dart_pit: 13/13
- multibagger: 17/17
- **p2_events: 18/18** (NEW — Samsung 2,614 row 회귀 방지 회로 포함)
- **technicals: 17/17** (NEW)

## 다음 액션 (우선순위 순서)

### 1. 사용자 — pip install + sanity (선행 필수)
```powershell
cd H:\codex\kr_quant_engine
py -3 -m pip install -r requirements.txt
py -3 kr_pykrx_client.py     # KOSPI+KOSDAQ ~2,300 listed
py -3 kr_dart_client.py       # Samsung 이벤트 v2 score (+0.117 expected)
```

### 2. P_MB.2 — Multibagger Classifier (개발자, 다음 세션)
이제 P0+P1+P2 (events) + P3 (technicals)이 모두 갖춰졌으니 multibagger pre-surge feature panel 빌드 가능. r1000 phase11 entry classifier 패턴:
- `kr_multibagger.add_pre_surge_features(episodes, fund_panel, event_panel, prices)` — pre-surge window features collect
- `kr_multibagger_classifier.train_entry_classifier()` — CatBoost binary, walk-forward 5-fold
- `research/06_walkforward_baselines/p_mb_v1_classifier_results.md` — AUC, precision@K, top picks

### 3. P0/P1/P2/P3 baseline 측정 (사용자, 데이터 fetch 후)
```powershell
# 모든 phase 토글 조합 A/B
$env:PHASE_PHASE1_FUNDAMENTAL_ENABLED="1"
$env:PHASE_PHASE2_DART_EVENTS_ENABLED="1"
$env:PHASE_PHASE3_TECHNICAL_ENABLED="1"
py -3 run_local.py --quick --start-date 2019-01-01 --end-date 2024-12-31
py -3 run_local.py --verdict-only
```

### 4. 후속 D-1/D-2/D-4 (incremental, 우선순위 낮음)
- D-1: DART 전체 IS/BS/CF parsing (매출원가/판관비/매출채권/재고/차입금 등)
- D-2: FCF (영업CF − CAPEX), ROIC, Sloan accruals
- D-4: TTM rolling 4Q sum 정밀화

이건 P_MB.2 또는 P3 regime 진입 시 필요해지면 추가.

## 알려진 logic issues 잔존

| # | 이슈 | 위치 | 해결 시점 |
|---|---|---|---|
| 2 | listed_months stub (모두 999) | kr_universe | P3 (DART listing date) |
| 3 | TTM annual factor 단순화 | kr_features._compute_ttm_from_panel | D-4 (deferred) |
| 4 | 가격제한폭 fill 미구현 | kr_pipeline.backtest | P3 |
| 5 | OCF는 multi에서 미반환 | kr_dart_client | D-1 (deferred) |
| 6 | PHASE2/3 columns keep_cols 미등록 | kr_pipeline.build_feature_store | P2/P3 진입 시 (현재 add_universe_features 직접 컬럼 추가하므로 영향 없음) |

이슈 1 (insider count 폭발) ✅ **이번 세션에서 fix 완료** (C-1).

## 차단 사항
- pykrx 미설치 (사용자 pip install 필요) — multibagger 실측 차단
- 그 외 OK

## GitHub
- Repo: https://github.com/wscha231/kr-quant-engine (private)
- 다음 commit: C + D-3 (P2 events v2 + 기술지표)
