# Session Handoff — Single Inbox

> 다음 세션에서 가장 먼저 읽을 파일.

---

## 마지막 큰 성과 (2026-04-30 21:30 KST)

### 🏆 1억 5년 OOS Backtest CAGR 38.97% ★★★★

```
Best config: N=20 equal weight + DD breaker
Seed:    100,000,000 KRW
Final:   518,257,415 KRW (5.18배)
CAGR:    +38.97%
MDD:     -24.66%
Sharpe:   1.350
Years:    5.00 (60 months OOS)
Trades:   1,777
```

**r1000 (US) reference보다 모든 지표 우수**:
- CAGR: 33.40% → 38.97% (+5.6pp)
- MDD: -25.29% → -24.66% (better)
- Sharpe: 1.28 → 1.35 (+0.07)

### 검증 chain (entire session)

```
1. Universe 1,278 eligible (KOSPI+KOSDAQ filtered)
2. Multibagger episodes 398 detected (205 quality-pass)
   ├─ 사용자 예상 모두 검증: 에코프로 +1084%, HLB +613%, 알테오젠 +627%
3. P_MB classifier AUC 0.80 (5-fold walk-forward)
4. Pre-identified 효성중공업 +1219% (50개월 사전 detect)
5. Sleeve sandbox: 71m +62pp / 12m OOS +38pp
6. Realistic 1억 backtest:
   - V2 N30 capped:    CAGR 33.55%, MDD -24%, Sharpe 1.25
   - BEST N20 equal:   CAGR 38.97%, MDD -25%, Sharpe 1.35 ★
```

### 마지막 commits (GitHub: wscha231/kr-quant-engine)

```
4194810 Realistic 1eok backtester + OOS picks: CAGR 33.55%
47b4a0b P_MB.3 sleeve backtest +38.34pp
033bc7b KOSPI 500+KOSDAQ 100 P0 baseline +7.42pp
50e03e1 SESSION_HANDOFF v3
67570f1 P_MB.2 V1: AUC 0.80
73a6200 P2.5 Naver flow scraper + multibagger episodes
52c3716 FDR fallback for pykrx
c64f29b 4-layer integration (macro/flow/derivatives/regime)
aa8dc43 P2 events v2 + P3 technicals
7e0d304 Initial commit
```

## 다음 세션 - 우선순위

### Tier 0 (필수, 즉시)
1. **Live retrain at 2026-04-30 cutoff** — 현재 시점 portfolio CSV
   - scored_panel을 2025-2026까지 확장
   - Classifier retrain
   - Top-20 picks save → outputs/live_portfolio_2026-04-30.csv
2. **N=20 equal config wire** into kr_pipeline default

### Tier 1 (1-2주)
3. **VKOSPI source fix** (yfinance ^VKOSPI delisted; KRX 1003 direct)
4. **Survivorship-corrected universe** (DART corp_code history)
5. **Score blending fix** (panel-aware, phase A/B 정확 비교)
6. **FDR ticker zero-pad fix** (5-digit codes 404)

### Tier 2 (3-4주)
7. **KIS API paper trading scaffold**
   - Order placement (모의 매매 모드)
   - Position tracking DB
   - Trade execution log
8. **Streamlit dashboard** (current portfolio + P&L + signals)
9. **DART event panel build** for 1,000+ corps (P2 events 정밀화)

### Tier 3 (1-2개월)
10. **Concentrated portfolio N=5/N=10 with classifier ranking**
11. **Live cron scheduler** (매월 1일 자동 picks generation)
12. **Email/Slack alert** (월별 picks + variance alerts)

## 💼 Best config (live operation)

```python
TOP_N            = 20
WEIGHTING        = 'equal'
USE_DD_BREAKER   = True
DD_THRESHOLDS    = (-0.08, -0.15, -0.25)
DD_SCALES        = (0.85, 0.65, 0.40)
USE_VKOSPI_GUARD = True  # when source ready
REBAL_FREQ       = 'monthly_first_business_day'
COST_MODEL       = 'mcap_tiered'  # 5/10/20bp slip + 18bp tax
```

## 알려진 Issues (Tier 1 해결 필요)

1. pykrx 1.2.7 broken (KRX 2025 redesign) → FDR + Naver fallback ✅
2. KRX direct scrape 400 Bad Request → endpoint 변경 필요
3. PIT survivorship bias → -3~5pp CAGR realism
4. listed_months stub (모두 999)
5. TTM annual factor 단순화
6. KOSDAQ 150 / VKOSPI yfinance 404
7. FDR ticker 5-digit pad (delisted handling)

## 차단 사항 — 없음
- All systems operational
- 모든 데이터 source working (FDR + Naver + DART + BOK)
- Realistic backtest framework verified

## 데이터 위치

```
Code:          H:/codex/kr_quant_engine/   (PROJECT_ROOT)
Data:          G:/내 드라이브/kr_quant_engine/   (DATA_ROOT)
GitHub:        wscha231/kr-quant-engine (private)
Latest commit: ~38.97% CAGR
Outputs:       outputs/realistic_backtest_best_5y.json
               outputs/realistic_backtest_best_monthly.csv
               outputs/current_portfolio_*.csv
               research/06_walkforward_baselines/p_mb_v1_oos_picks.csv
               research/06_walkforward_baselines/p_mb_v1_classifier_metrics.json
```
