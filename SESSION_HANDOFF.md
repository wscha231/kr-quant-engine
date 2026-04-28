# Session Handoff — Single Inbox

> 다음 세션에서 가장 먼저 읽을 파일. "방금 뭐 했고 다음에 뭐 할지" 한 가지만 적는다.
> Phase 끝날 때마다 덮어씀. 누적하지 말 것.

---

## 마지막으로 한 일 (2026-04-28 12:55 KST)

**P_MB v0 (Multibagger Episode Discovery) SHIPPED**:
- 사용자 결정: threshold **300%**, window **24m**, min mcap **5,000억**
- `kr_multibagger.py` (470 lines) — episode definition + surge_start + quality filter + 캐시
- `tests/test_multibagger.py` (310 lines, **17/17 통과**) — mock data로 logic 완전 검증
- `tools/multibagger_explorer.py` — 분포 분석 + Top 30 출력 도구
- `research/06_walkforward_baselines/multibagger_v0_design.md` — 설계 + 사용자 가이드

**전체 시스템 상태 (2026-04-28)**:
- 코드: 4,500+ lines (P0 + P1 + P2 prep + P_MB)
- Tests: smoke 31/31, dart_pit 13/13, multibagger 17/17 = **총 61/61 통과**
- 데이터: G:/내 드라이브/kr_quant_engine/cache_dart/ (35MB, Samsung 2023 financials + 2024 events 검증)
- API 키: BOK ✅, DART ✅ (.env, gitignored)
- Reference: r1000-quant-engine 패턴 (read-only at H:/codex/tmp_r1000_quant_engine/)

## 다음 액션 (사용자)

### A. pykrx 설치 (필수, ~5분)
```bash
cd H:/codex/kr_quant_engine
py -3 -m pip install -r requirements.txt
```

### B. 시스템 sanity (sequential, ~5분)
```bash
py -3 kr_pykrx_client.py     # KOSPI+KOSDAQ ~2,300 listed
py -3 kr_bok_client.py        # BOK 매크로
py -3 kr_dart_client.py       # Samsung 2023 + 2024 events
```

### C. 핵심 작업 옵션 (선택)

**Option 1 — P0/P1 backtest baseline 측정** (45-100min 첫 run):
```powershell
$env:PHASE_PHASE1_FUNDAMENTAL_ENABLED="0"
py -3 run_local.py --quick --start-date 2019-01-01 --end-date 2024-12-31

$env:PHASE_PHASE1_FUNDAMENTAL_ENABLED="1"
py -3 run_local.py --quick --start-date 2019-01-01 --end-date 2024-12-31

py -3 run_local.py --verdict-only
```

**Option 2 — Multibagger episode discovery** (~30-60min 첫 build):
```bash
py -3 -c "from kr_multibagger import load_or_build_episode_panel; load_or_build_episode_panel()"
py -3 tools/multibagger_explorer.py
```

**Option 1 + Option 2 병렬**: 가능. 가격 데이터 캐시는 양쪽 공유.

## 다음 코드 작업 (개발자)

P_MB.2 (사용자 실측 후):
- `kr_features.add_multibagger_pre_signals()` — pre-surge window features
- `kr_multibagger_classifier.py` — CatBoost binary classifier + walk-forward
- `research/06/p_mb_v1_classifier_results.md`

P2.2 (이전 task):
- `kr_dart_client.compute_event_score_for_corp` 재설계 (insider count → KRW/mktcap)
- `kr_features.add_disclosure_event_signal`

P3 (재무제표 확장):
- DART fnlttSinglAcntAll 전체 IS/BS/CF parsing
- 기술지표 (MA stack, 52w high, ATR, RSI, trend template)
- TTM 정밀화 (rolling 4Q sum)

## 알려진 logic issues (audit completed)

| # | 이슈 | 위치 | 우선 |
|---|---|---|---|
| 1 | insider count score 폭발 | DART_EVENT_CATALOG | P2.2 |
| 2 | listed_months stub (모두 999) | kr_universe | P3 |
| 3 | TTM annual factor 단순화 | kr_features._compute_ttm_from_panel | P3 |
| 4 | 가격제한폭 fill 미구현 | kr_pipeline.backtest | P3 |
| 5 | OCF는 multi에서 미반환 | kr_dart_client | P3 |
| 6 | PHASE2/3 columns keep_cols 미등록 | kr_pipeline.build_feature_store | P2/P3 |

## 차단 사항

- pykrx 미설치 (사용자 pip install 필요)
- 그 외 모두 OK

## 참고

- 원본 r1000 reference: `H:/codex/tmp_r1000_quant_engine/` (read-only)
- API 키: `H:/codex/kr_quant_engine/.env` (gitignored)
- 데이터: `G:/내 드라이브/kr_quant_engine/`
- GitHub: 신규 repo 푸시 예정 (kr-quant-engine)
