# Session Handoff — Single Inbox

> 다음 세션에서 가장 먼저 읽을 파일.

---

## 마지막 큰 성과 (2026-04-30 16:30 KST)

### 🎯 P_MB.2 V1 Classifier SHIPPED — AUC 0.80

Walk-forward 5-fold on 110 features × 103,520 rows × 60 month-ends:
```
AUC mean       : 0.7996 (std 0.034) ★★★
Precision @ 30 : 0.193  (10× random base)
Folds AUC      : 0.77 ~ 0.85 (low variance)
Positives      : 1,909 / 103,520
```

**Pre-identified the #1 multibagger**: 효성중공업 (298040) 2020-06 → p=0.983,
realized +1,219% by 2025-10 (50 months before peak).

**Top 10 features** (펀더멘털이 momentum보다 dominant):
1. roe (6.63) ★ 2. turnaround_score (4.74) ★ 3. listed_shares (4.68)
4. debt_to_equity (3.82) 5. listed_months 6. revenue_ttm 7. market_cap
8. total_assets 9. macro_ktb_10y_3y_spread (3.08) 10. low_52w (2.98)

**Latest 2024-12-30 Top 10 candidates** (multibagger 후보 RIGHT NOW):
- 095340 ISC (반도체장비) p=0.97
- 042660 한화오션 (조선) p=0.96
- 033240 한화시스템 (방산) p=0.95
- 042700 한미반도체 ★ p=0.94 (also top in P0 backtest)
- 010140 삼성중공업 p=0.94

### 데이터 시스템 검증 완료
- **Multibagger episodes**: 398 detected, 205 quality-pass, 278 unique tickers
  - 사용자 예상 사례 모두 검증: 에코프로 (+1084%), HLB (+613%), 알테오젠 (+627%),
    에코프로비엠, 삼양식품, 한화오션
- **P0 sample backtest**: KOSPI 200 universe, 2024 Q2-Q4
  - Strategy +8.00% / KOSPI200 -15.16% / **Excess +23.16pp** ★
- **P2.5 Flow scrape (Naver)**: 외인 보유율 49.27% + 일별 매매 모두 작동
  - Samsung 60일 누적 외인 매도 -31.5조원 정확 capture

## 코드 + GitHub 상태

```
GitHub: github.com/wscha231/kr-quant-engine (private)
Branch: main, latest 67570f1
Code:   ~9,000 lines (modules + tests + tools)
Tests:  162/162 (smoke 44 + dart_pit 13 + multibagger 17 + p2_events 18
                 + technicals 17 + macro 13 + flow 14 + derivatives 12 + regime 14)
```

### Modules
- kr_config / kr_helpers / kr_pykrx_client / kr_dart_client (P0/P1)
- kr_universe / kr_features / kr_pipeline (orchestration)
- kr_macro / kr_flow / kr_derivatives / kr_regime (P3.2 / P2.5 / P2.6 / P3.3)
- kr_technicals (P3.1)
- kr_multibagger / kr_multibagger_classifier (P_MB.1 / P_MB.2)
- kr_naver_flow / kr_krx_scraper (P2.5 fallback)
- run_local.py + tools/phase_ab_quick.py + tools/build_scored_panel_mini.py

## 다음 세션 — 우선순위

### Tier 1 (즉시, ~1주)
1. **P_MB.3 sleeve integration** — Top-K picks을 main backtest에 추가, ΔCAGR 측정 (이미 alpha 검증됨, 통합만)
2. **Score blending fix** — phase A/B 정확한 비교 (panel-aware score selection)
3. **DART event panel build** (1,000+ corp × 5 endpoints) — 진짜 P2 events alpha 측정
4. **Concentrated portfolio N=5** with classifier ranking — CAGR 30%+ 목표

### Tier 2 (~2-3주)
5. **PIT survivorship fix** — 상폐 종목 history (bias 제거)
6. **Drawdown breaker port** (r1000 phase 6a/b/c)
7. **테마 phase classifier** (themes.yaml + Naver 테마 매핑)

### Tier 3 (~1-2개월)
8. **CatBoost walk-forward ensemble** (r1000 phase 14 patterns)
9. **KIS API paper trading** integration
10. **Live alpha monitoring** dashboard

## 알려진 issues (잔존)

| # | 이슈 | 영향 | Plan |
|---|---|---|---|
| 1 | pykrx 1.2.7 broken (KRX 2025+) | 가격/외인기관/PER/PBR | FDR + Naver 우회 ✅ |
| 2 | KRX direct scrape 400 Bad Request | 시장 전체 + 세분화 | endpoint 추가 R&D 또는 FnGuide |
| 3 | PIT survivorship bias | 상폐 종목 빠짐 | Tier 2 |
| 4 | listed_months stub (모두 999) | 신규상장 필터 | Tier 2 |
| 5 | TTM annual factor 단순화 | 계절성 산업 | Tier 2 |
| 6 | KOSDAQ 150 (KQ150) yfinance 404 | benchmark 부재 | KQ_FE 등 다른 코드 시도 |
| 7 | VKOSPI yfinance ^VKOSPI delisted | regime 시그널 약화 | KRX 1003 또는 보조 source |

## 차단 사항
- 사용자 결정: P_MB.3 sleeve integration vs DART event panel build vs PIT fix 우선순위
- 일부 background tasks (b4ovubao8 KOSPI 500+KOSDAQ 100 backtest) 1시간+ 진행 중 — 결과 받으면 cumulative add

## GitHub
- https://github.com/wscha231/kr-quant-engine (private, wscha231)
- Latest commit: `67570f1` — P_MB.2 V1 classifier
- 6 commits total
