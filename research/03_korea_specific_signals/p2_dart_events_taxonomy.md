---
phase: P2
date: 2026-04-28
author: Claude (planning)
verdict: design (not yet measured)
ship_gate_passed: pending
---

# P2 — DART Disclosure Events Taxonomy + Alpha Hypotheses

DART OpenAPI에서 추적 가능한 corporate event들과, 각 event의 한국시장 alpha 영향을 정리한 design note. P2 구현 가이드.

## TL;DR

DART는 **11개 종류의 주요 corporate event**에 대해 전용 JSON endpoint를 제공한다 (모두 free). 각 endpoint를 `_fetch_dart_event(endpoint, corp_code, bgn_de, end_de)`로 호출하면 `rcept_dt` (PIT timestamp) 포함 long-format DataFrame 반환.

**P2 핵심 시그널** `disclosure_event_score`는 이 event들의 가중합:
- ★★★ 우선 5개: 자사주매입(+0.50), 유상증자(-0.40), 무상증자(+0.30), 임원매매(+0.30 net), 대량보유보고(+0.25 net)
- ★★ 보조 5개: 자사주처분(-0.30), CB(-0.20), BW(-0.20), 합병(case), 분할(case)
- 가중치는 r1000 Phase 1 등가물 기반 + 한국시장 단발성 alpha 문헌 ([NRF 한국증권학회](https://www.kfrm.or.kr) 자료)

## Event Catalog (DART_EVENT_CATALOG, kr_dart_client.py)

| Event | DART endpoint | 신호 | 가중 | 효과기간 | 가설 |
|---|---|---|---|---|---|
| 자기주식 취득결정 | `tsstkAqDecsn` | + | +0.50 | 3-6m | 발표 +1~3% / 공급 감소 + 자신감 시그널 |
| 무상증자결정 | `fricDecsn` | + | +0.30 | 1-3m | 심리적 양호, 액면분할 효과, +2~5% 1주 |
| 임원·주주 소유보고 | `elestock` | +/− net | +0.30 magnitude | 1-3m | 내부자 매수 → 내부정보 가설; 매도는 약신호 |
| 대량보유 (5%+) | `majorstock` | +/− net | +0.25 magnitude | 1-3m | 외부자 5%+ 인수 → 인수 프리미엄/거버넌스 변화 |
| 합병결정 | `mgDecsn` | case | 0 (P3 분리) | 6m+ | 흡수합병 vs 동등합병, 시너지 ratio 분석 필요 |
| 분할결정 | `divDecsn` | − (KR specific) | -0.15 | 3-6m | 한국 특수: 90% 물적분할 → -5~10% (지배구조 우려) |
| 자기주식 처분결정 | `tsstkDpDecsn` | − | -0.30 | 1-3m | 시장 매도, 희석 우려 |
| 전환사채(CB) 발행 | `cvbdIsDecsn` | − | -0.20 | 6-12m | 잠재 희석, 전환가 하향조정 risk |
| 신주인수권부(BW) 발행 | `bdIsDecsn` | − | -0.20 | 6-12m | CB 동일 희석 risk |
| 유상증자결정 | `piicDecsn` | − | -0.40 | 1-3m | 1주 -3~7%, 주주배정/제3자배정/공모 별로 차등 |
| 감자결정 | `crDecsn` | − | -0.40 | 1-3m | 보통 부실 시그널 (유상감자는 예외 — 별도 처리) |

## P2 시그널 설계

### Signal 1: `disclosure_event_score`

```python
# kr_features.py P2 prep:
def add_disclosure_event_signal(universe, rebalance_date, events_panel):
    """
    For each ticker in universe:
      score = sum over events in [rebalance_date - 90d, rebalance_date]:
                 DART_EVENT_CATALOG[event_category].alpha_weight
    """
```

Lookback: 90일 (대부분 event는 1-3개월 내 effect 소진).
Cross-sectional: z-score 후 winsorize.

### Signal 2: `insider_net_buy_zscore` (분리)

`elestock` 데이터에서 net buy KRW 추출:
```
trade_amt_signed = (aft_stkqy - bsis_stkqy) * trade_uv
                   sign = +1 (매수) / -1 (매도)
score = z_score(sum(trade_amt_signed) / market_cap, lookback=60d)
```

Hypothesis: 한국 내부자 매수는 12-13F 등가물보다 강한 신호 (정보 비대칭 큼).

### Signal 3: `dilution_overhang_score` (분리)

CB/BW 미상환 잔액 + 잠재 신주수 / 상장주식수:
```
dilution_pct = (outstanding_CB_shares + outstanding_BW_shares) / listed_shares
score = -z_score(dilution_pct)   # higher overhang → more negative
```

기존 발행된 CB/BW의 전환청구기간 모니터링 → 미래 희석 시점 예측.

### Signal 4: `corporate_action_calendar` (P3+)

발표일 (`rcept_dt`) → 효력일 (`pymd`, `nstk_dlprd`, `aq_prtl_dt` 등) 사이의 holding 전략:
- 무상증자 발표 → 권리락 전 매수 → 권리락 후 매도
- 자사주매입 발표 → 매입 시작 후 매수 → 매입 완료 후 매도

P3 event-driven layer.

## Implementation Plan (P2)

### P2.0: Event collector
- [ ] `kr_dart_client.fetch_all_events_for_corp` ✅ 구현 완료 (2026-04-28)
- [ ] `kr_dart_client.compute_event_score_for_corp` ✅ 구현 완료
- [ ] `kr_dart_client.DART_EVENT_CATALOG` ✅ 11개 event 등록

### P2.1: Universe panel build
- [ ] `kr_features.prepare_event_panel(tickers, start_date, end_date)` — bulk fetch all events
- [ ] Cache: `feature_store/event_panel_v0.parquet`
- [ ] 1년에 1회 fetch (rcept_dt 변하지 않음)

### P2.2: Signal columns
- [ ] `add_disclosure_event_signal` (score per ticker per rebal_date)
- [ ] `add_insider_net_buy_signal` (P2.1 분리 시그널)
- [ ] `add_dilution_overhang_signal`

### P2.3: A/B 측정
```bash
# P1 baseline
PHASE_PHASE2_KOREA_ALPHA_ENABLED=0 py -3 run_local.py --quick

# P1 + P2 events
PHASE_PHASE2_KOREA_ALPHA_ENABLED=1 py -3 run_local.py --quick
```

Ship gate: ΔCAGR ≥ +1pp AND ΔSharpe ≥ +0.05.

### P2.4: Decompose (optional)
- 각 event 종류 별 단독 IC 측정 (`research/02_factor_zoo/p2_event_ic_decomposed.csv`)
- weak event 제거

## API 호출 추정

DART 1일 호출 한도: 10,000건.

P2 첫 build (8년 × 11개 event × 800-1,000 활성 corps):
- 한 corp 8년 lookback의 event endpoint 1번 호출 ≈ 11 calls
- 1,000 corps × 11 calls = **11,000 calls** ← 1일 한도 살짝 넘김

대응:
1. 분할 fetch (4개월씩 batch)
2. 또는 우선 ★★★ 5개 event만 fetch (5,000 calls — 안전)
3. 또는 list.json (검색 endpoint) 한 번 호출 후 corp별 개별 fetch는 hit 시만

P2.0 추천: ★★★ 5개 (treasury_buyback, capital_increase, bonus_issue, insider_holdings, major_holders)부터 시작.

## 알려진 함정

1. **rcept_dt가 발표일이지 효력일 아님**: 효력 (납입일, 매수 시작일 등)은 `pymd`/`aq_prtl_dt` 별도 컬럼에서 추출 필요.
2. **CB 전환 청구는 별도 endpoint 없음**: `/cvbdIsDecsn`은 발행 결정만. 실제 전환은 주식분포보고 (`majorstock`) 또는 분기보고서에서 추출.
3. **임원 매매는 보고 의무가 5거래일 이내**: 단기 알파 (1-2일)에는 효과 미약, 1주+ 효과 있음.
4. **소형주는 event 수 적음**: KOSDAQ small-cap은 연 0-2개 event. Cross-sectional rank 시 NaN 많아 → fillna(0.0) 필수.
5. **합병/분할 case-by-case**: 단순 가중치로 처리 불가. P3에서 별도 회귀 모델.

## Reference

- DART OpenAPI 가이드: https://opendart.fss.or.kr/guide/main.do
- Endpoint 명세: https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS003&apiId=2019018
- 한국시장 corporate event alpha 관련 실증 논문 (참고용):
  - 자사주 매입: 유종민·김창수 (2018), 한국증권학회지
  - 유상증자: 박철·정현철 (2019), Asia-Pacific Journal of Financial Studies
  - 임원 매매: 김지수·이만우 (2020), 회계학연구
