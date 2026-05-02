---
phase: Phase A research
date: 2026-05-02
author: Claude (auto)
---

# Governance Risk Overlay Research (Korean specific)

차트와 재무제표만으로는 안 잡히는 **"코리아 디스카운트 리스크"**. 한국시장 특화. 기존 DART event layer를 **Governance Risk Layer**로 확장.

## 1. 리스크 유형 7가지

### A. 유상증자 / CB / BW (희석)

**감지 데이터** (DART):
- `piicDecsn` (유상증자결정): nstk_ostk_qy × bdis_pric / mcap
- `cvbdIsDecsn` (CB): bd_fta / mcap
- `bdIsDecsn` (BW): bd_fta / mcap
- `ic_mthn` (배정방법): 주주배정 / 제3자배정 / 일반공모

**점수**:
```python
owner_dilution_risk_score =
    -0.5 * min(1, dilution_pct / 0.10)
    -0.3 * related_party_allocation_flag
    -0.2 * refix_clause_flag
```

**Hard veto**:
- 희석률 ≥ 10% + 제3자배정 (특수관계자) → 신규매수 금지
- 희석률 ≥ 20% (방식 무관) → 비중 축소
- CB/BW 12개월 내 2회 이상 → multibagger pool에서 감점

### B. 자사주 매입 vs 처분 (자본환원 quality)

**핵심 차이**:
- 매입 + **소각**: strong positive
- 매입만 + 보유: weak positive
- **처분**: negative
- 처분 대상이 특수관계자/우호지분: strong negative

**점수**:
```python
capital_allocation_quality_score =
    +0.6 * treasury_cancellation_pct
    +0.3 * dividend_increase_score
    -0.5 * treasury_sale_pct_mcap
    -0.4 * related_party_sale_flag
```

**Data**:
- `tsstkAqDecsn` (취득)
- `tsstkDpDecsn` (처분) — 대상 특수관계자 여부 = `aq_pp` 사유
- `tsstkAqDecsn` 후속 자사주 소각 = 별도 공시

### C. 특수관계자 거래 / 비상장 계열사 수익 이전

**감지 데이터** (DART 사업보고서 주석):
- `related_party_sales_pct` (매출 중 특수관계자 비중)
- `related_party_purchase_pct` (매입 중)
- `related_party_fee_pct` (지급수수료/로열티/브랜드)
- `related_party_receivable_pct` (매출채권 / 대여금)
- `private_affiliate_profit_leakage_score`

**점수**:
```python
related_party_leakage_score =
    -0.3 * z(related_party_purchase_pct)
    -0.3 * z(related_party_fee_pct)
    -0.2 * z(related_party_receivable_pct)
    -0.2 * z(related_party_loan_pct)
```

**자동화 어려움**: 처음에는 `governance_entities.yaml` 수동 registry. 후 DART 주석 XML 파싱.

### D. 물적분할 / 자회사 상장 (한국 특수, 가장 치명적)

**감지 데이터**:
- `divDecsn` (분할결정): div_mth = "물적분할" / "인적분할"
- 분할 대상 사업이 핵심 성장사업인가? (수동 판정 또는 매출 비중)

**점수**:
```python
spinoff_listing_risk_score =
    -0.7 * spinoff_core_business_flag * 분할_방법_물적_flag
    -0.3 * subsidiary_listing_likelihood
```

**Hard veto**:
- 핵심 성장사업 물적분할 + 자회사 상장 가능성 → 신규매수 금지
- 인적분할 + 주주 직접 배분 → 중립

**역사 사례** (사용자 검증 필요):
- LG화학 → LG에너지솔루션 (2022, 모회사 -30%)
- DB하이텍, 포스코홀딩스 등

### E. 승계성 거래 (오너 일가)

**감지 데이터** (DART):
- `elestock` 임원·주요주주 매매 (오너 일가 식별 필요)
- 비상장 계열사 합병
- 특정 법인 제3자배정
- 자사주 우호지분 처분
- `majorstock` 최대주주 변동

**점수**:
```python
succession_risk_score =
    -0.4 * related_private_company_transaction_score
    -0.3 * family_control_entity_allocation_flag
    -0.2 * owner_family_stake_transfer_flag
    -0.1 * repeated_governance_event_count
```

**자동화**: governance_entities.yaml에 owner family + 비상장 계열사 등록. 이벤트 발생 시 자동 매칭.

### F. 감자 (보통 부실)

**Data**: `crDecsn` (감자결정)
**Score**: -0.40 (binary, hard veto)

### G. 단기과열 / 투자경고 / 관리종목 (회피)

**Data** (KRX 공시채널, 별도 scrape):
- 투자경고 종목 list
- 단기과열 종목
- 관리종목

**Score**: `overheating_avoidance_flag` (이미 phase column 있음)
**Action**: 신규매수 금지

---

## 2. 신규 phase columns (`kr_config.py` 추가)

```python
PHASE2_GOVERNANCE_COLUMNS = (
    "governance_risk_score",                    # aggregate (negative range)
    "governance_quality_score",                 # aggregate (positive)
    "owner_dilution_risk_score",                # 유상증자 + CB + BW
    "treasury_sale_overhang_score",             # 자사주 처분
    "related_party_leakage_score",              # 특수관계자
    "succession_risk_score",                    # 승계
    "minority_shareholder_discount_score",      # 소액주주 디스카운트 종합
    "spinoff_listing_risk_score",               # 물적분할
    "capital_allocation_quality_score",         # 자사주 + 배당 quality
    "governance_watchlist_flag",                # bool
    "governance_hard_veto_flag",                # bool (신규매수 금지)
)
```

ALL_PHASE_COLUMNS과 PHASE2_KOREA_ALPHA_COLUMNS 모두에 등록.

---

## 3. 신규 모듈 `kr_governance.py`

```python
def load_governance_entities() -> dict:
    """governance_entities.yaml 로드 — 수동 registry"""

def prepare_governance_event_panel(tickers, start_date, end_date) -> pd.DataFrame:
    """DART events + governance keyword + related-party 통합"""

def compute_governance_score_for_ticker(events, docs, as_of, mcap) -> dict:
    """PIT-safe score 산출, 11 component returned"""

def add_governance_signals(universe, rebalance_date, governance_panel) -> pd.DataFrame:
    """월별 universe에 governance columns join"""

def is_hard_veto(governance_score_dict) -> bool:
    """hard veto 조건 체크"""

def governance_weight_cap(governance_risk_score: float, normal_weight: float) -> float:
    """risk score → weight cap"""
```

## 4. 수동 registry `governance_entities.yaml`

```yaml
groups:
  samsung_group:
    listed_tickers: ["005930", "009150", "006400", ...]
    controlling_family: ["이재용", ...]
    private_affiliates: []
    watch_keywords: ["승계", "물적분할", "제3자배정"]
    risk_override:
      base_governance_risk: 0.10  # 대형 그룹 안정성 prior

  hanwha_group:
    listed_tickers: ["009830", "012450", ...]
    ...

  # 기타 4대 그룹 + 중견그룹
```

초기엔 4대 그룹 (삼성/SK/현대차/LG) + 한화/롯데/CJ 등 ~15-20개 그룹. 후 confirmed cases (sample backtest에서 -50% 이상 손실 종목) 별도 watchlist 추가.

---

## 5. 적용 방식 — 3가지 옵션 비교

| 방식 | 장점 | 단점 | 추천 |
|---|---|---|---|
| **Soft penalty** | 부드러운 감점 | 극단악재 못막음 | 약함 |
| **Hard veto** | 대형손실 방지 강력 | 회생형 종목 놓침 | 너무 강함 |
| **Weight cap** ★ | balanced | 구현 복잡 | **최적** |

**Weight cap 추천**:
```python
if governance_hard_veto_flag:
    max_weight = 0.00      # 신규매수 금지
elif governance_risk_score >= 0.80:
    max_weight = 0.00
elif governance_risk_score >= 0.60:
    max_weight = 0.02      # 2% 제한
elif governance_risk_score >= 0.40:
    max_weight = 0.04      # 4% 제한
else:
    max_weight = normal_weight   # ~5%
```

---

## 6. Realistic backtester 통합

`kr_backtester_realistic.run_realistic_backtest`에 두 곳 추가:

### A. Picks selection 단계 (ranking)
```python
# After p_pre_surge ranking, before top_n cut
sel["governance_risk"] = governance_risk_lookup(sel["ticker"], rd)
sel["adjusted_score"] = sel["p_pre_surge"] * (1 - 0.35 * sel["governance_risk"])
sel = sel[sel["governance_hard_veto"] == False]
sel = sel.sort_values("adjusted_score", ascending=False).head(top_n)
```

### B. Position sizing 단계
```python
# After weight calculation
for tk, w in weights.items():
    risk = governance_risk_for_ticker(tk, rd)
    cap = governance_weight_cap(risk, w)
    weights[tk] = min(w, cap)
weights = weights / sum(weights)  # renormalize
```

---

## 7. Event study 검증 (필수, 구현 전)

각 governance event 별로 PIT 초과수익 측정:

```
이벤트: 유상증자, CB/BW, 자사주처분, 자사주소각, 물적분할, 최대주주변경
Window: -20d, -1d, 0d, +20d, +60d, +120d
Benchmark: KOSPI200 또는 업종지수

출력:
  평균 초과수익
  중앙값 초과수익
  하위 10% 손실
  MDD 변화
  positive rate
```

이 결과로 score weight를 calibrate.

---

## 8. Ship gate (Phase B plan)

Governance overlay 별도 ship 기준:

```
✅ CAGR 하락 ≤ 2pp (vs no governance)
✅ MDD 개선 ≥ 2pp
✅ worst 5% monthly return 개선
✅ 대형 희석/자사주처분/물적분할 사례 회피 검증
✅ 회전율 과도 증가 없음 (월 turnover +5pp 이내)
```

---

## 9. 구현 cost

- `kr_governance.py`: ~400 lines
- `governance_entities.yaml`: 초기 ~15-20 그룹
- `kr_config.py` 확장: 11 columns
- `kr_features.py` 통합: ~100 lines
- `kr_backtester_realistic.py` 통합: ~50 lines
- Tests: ~200 lines (5-7 tests)
- Event study tool: ~150 lines

**Total: ~1주**.

---

## 10. 정리

| 측면 | 평가 |
|---|---|
| 한국시장 적합성 | ★★★ 필수 |
| 구현 난이도 | ★★ 중간 (DART event 기반은 OK, 주석 파싱은 어려움) |
| MDD 개선 기대 | ★★★ -2~5pp |
| CAGR 영향 | ★★ -1~2pp (필터로 일부 잃음) |
| Sharpe 개선 | ★★★ +0.10~0.20 |
| Tail risk | ★★★★ 대형 손실 회피 |

**결론**: 한국 quant에서 **있으면 좋은 기능 X, 필수 기능**. CAGR 40% / MDD -20% 목표 달성에 critical.

**우선순위**: Phase C2로 PIT 수정(C1) 직후 즉시 도입.
