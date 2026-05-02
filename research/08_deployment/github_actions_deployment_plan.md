---
phase: Phase A research
date: 2026-05-02
author: Claude (auto)
---

# GitHub Actions Deployment — kr-quant-engine 자동 운영

## TL;DR

가능. **하이브리드 아키텍처** 추천:
- **GitHub Actions**: 매월 자동 picks 생성 + smoke test + classifier retrain
- **Streamlit Cloud (free)**: dashboard host
- **GDrive (rclone)**: 데이터 캐시 + outputs sync
- **Local + KIS API**: paper/real trading (latency 민감)

총 cost: **무료** (GitHub Free + Streamlit Free + GDrive 2TB 기존)

---

## 1. GitHub Actions 가능 작업

### ✅ Cron-based 자동 작업

| Workflow | Cron | Runtime | 목적 |
|---|---|---|---|
| `smoke_test.yml` | push hook | ~5분 | Tests pass 확인 |
| `monthly_picks.yml` | 매월 1일 04:00 KST | ~30-60분 | picks CSV 자동 생성 |
| `quarterly_backtest.yml` | 분기 1일 06:00 | 1-2시간 | 5-year backtest 갱신 |
| `monthly_classifier_retrain.yml` | 매월 1일 02:00 | 1-2시간 | Classifier 재학습 |
| `daily_event_monitor.yml` (선택) | 평일 18:00 KST | 5분 | DART 이벤트 + 단기과열 alert |

### ⚠️ 제약사항

- **Free tier**: public 무제한 / private **2,000분/월** (충분)
- **Job timeout**: 6시간 (충분)
- **Memory**: 7GB ubuntu-latest (충분)
- **Storage**: 14GB workspace (충분)
- **Real-time**: ❌ 5-15분 lag (실시간 매매 부적절)

---

## 2. 추천 아키텍처

```
┌──────────────────────────────────────────────────────────────┐
│ GitHub Repo (wscha231/kr-quant-engine, private)              │
│  ↓ push                                                       │
│ GitHub Actions                                                │
│  ├─ smoke_test (every push)                                   │
│  ├─ monthly_picks (1st 04:00 KST cron)                       │
│  │   ├─ Universe build (FDR + DART)                          │
│  │   ├─ Features compute                                      │
│  │   ├─ Classifier predict (saved model)                     │
│  │   ├─ Top-20 picks CSV                                      │
│  │   └─ → GDrive upload + Slack notify                       │
│  ├─ quarterly_backtest (Jan/Apr/Jul/Oct 1st)                 │
│  │   └─ → research/06/quarterly_*.md commit                  │
│  └─ monthly_classifier_retrain (1st 02:00)                   │
│      ├─ Walk-forward 5-fold + new fold                       │
│      └─ → models/classifier_2026-MM.cbm to GDrive            │
└──────────────────────────────────────────────────────────────┘
                         ↓
┌──────────────────────────────────────────────────────────────┐
│ Streamlit Cloud (kr-quant-engine-dashboard, free)            │
│  - Reads CSV from GDrive (rclone or gdown)                   │
│  - Current portfolio + history                                │
│  - P&L curve                                                  │
│  - Recent DART events                                         │
│  - Risk dashboard (governance/regime/VKOSPI)                  │
└──────────────────────────────────────────────────────────────┘
                         ↓
┌──────────────────────────────────────────────────────────────┐
│ Local Machine (사용자 PC)                                     │
│  - Paper trading (KIS API 모의 매매)                          │
│  - Live execution (확정 후)                                   │
│  - Manual approval workflow                                   │
└──────────────────────────────────────────────────────────────┘
```

---

## 3. Workflow files (Phase C 작업)

### `.github/workflows/smoke_test.yml`

```yaml
name: Smoke Test
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.11' }
      - run: pip install -r requirements.txt
      - run: py -3 tests/smoke_test.py --quick
      - run: py -3 tests/test_dart_pit.py
      - run: py -3 tests/test_multibagger.py
```

### `.github/workflows/monthly_picks.yml`

```yaml
name: Monthly Picks Generation
on:
  schedule:
    - cron: '0 19 1 * *'   # 1st of month 04:00 KST (UTC+9)
  workflow_dispatch:        # 수동 trigger 가능

jobs:
  generate:
    runs-on: ubuntu-latest
    timeout-minutes: 90
    env:
      DART_API_KEY: ${{ secrets.DART_API_KEY }}
      BOK_ECOS_API_KEY: ${{ secrets.BOK_ECOS_API_KEY }}
      RCLONE_CONFIG_GDRIVE: ${{ secrets.RCLONE_CONFIG_GDRIVE }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.11' }
      - run: pip install -r requirements.txt
      - name: Setup rclone
        run: |
          curl https://rclone.org/install.sh | sudo bash
          mkdir -p ~/.config/rclone
          echo "$RCLONE_CONFIG_GDRIVE" > ~/.config/rclone/rclone.conf
      - name: Sync from GDrive
        run: |
          mkdir -p data/cache_pykrx data/cache_dart data/feature_store data/models
          rclone copy gdrive:kr_quant_engine/cache_pykrx data/cache_pykrx -P
          rclone copy gdrive:kr_quant_engine/feature_store data/feature_store -P
          rclone copy gdrive:kr_quant_engine/models data/models -P
      - name: Generate picks
        run: |
          export KR_DATA_DIR=$(pwd)/data
          py -3 tools/generate_monthly_picks.py
      - name: Sync to GDrive + outputs
        run: |
          rclone copy data/outputs gdrive:kr_quant_engine/outputs -P
      - name: Commit picks CSV
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add outputs/live_portfolio_*.csv
          git commit -m "auto: monthly picks $(date +%Y-%m-%d)" || exit 0
          git push
      - name: Slack notification
        if: success()
        run: |
          curl -X POST -H 'Content-type: application/json' \
            --data "{\"text\":\"🎯 Monthly picks generated: outputs/live_portfolio_$(date +%Y-%m-%d).csv\"}" \
            ${{ secrets.SLACK_WEBHOOK_URL }}
```

### `.github/workflows/quarterly_backtest.yml`

비슷한 구조, cron `0 21 1 1,4,7,10 *` (분기 1일 06:00 KST), 1-2시간 timeout, 5-year backtest 결과를 `research/06/quarterly_backtest_*.md` commit.

### `.github/workflows/monthly_classifier_retrain.yml`

매월 1일 02:00, classifier 새 fold 추가하여 retrain, model `*.cbm` 파일 GDrive upload + git commit `models/classifier_2026-MM.cbm` (or LFS).

---

## 4. Secrets 설정 (GitHub Settings → Secrets)

```
DART_API_KEY          : 957b28c85928fd94986c55a44646b506d0e1354d
BOK_ECOS_API_KEY      : X1LY1YE5FWEFSF7QB0HZ
RCLONE_CONFIG_GDRIVE  : (rclone config show output)
SLACK_WEBHOOK_URL     : https://hooks.slack.com/...
```

**모든 keys는 Secrets에 저장** — 코드에 절대 hardcoded X.

---

## 5. Streamlit Cloud Dashboard

### `streamlit_app.py` (~300 lines)

```python
import streamlit as st
import pandas as pd
from gdown import download
import os

st.title("kr-quant-engine — Live Dashboard")

# 1. Load latest picks from GDrive
@st.cache_data(ttl=3600)
def load_picks():
    # rclone or gdown
    path = "outputs/live_portfolio_latest.csv"
    return pd.read_csv(path)

picks = load_picks()

# 2. Current portfolio table
st.header("📊 Current Portfolio (월별 자동 갱신)")
st.dataframe(picks)

# 3. P&L curve (since inception)
@st.cache_data(ttl=3600)
def load_history():
    return pd.read_csv("outputs/realistic_backtest_monthly.csv")

history = load_history()
st.line_chart(history["capital"])

# 4. Recent DART events (governance overlay)
st.header("⚠️ Governance Risk Alerts (최근 30일)")
events = pd.read_csv("outputs/governance_alerts_latest.csv")
st.dataframe(events[events["risk_score"] > 0.5])

# 5. Regime indicator
st.header("🌊 Market Regime")
regime = pd.read_csv("outputs/regime_latest.json")
st.metric("Current Regime", regime["regime_label_kr"])
st.metric("VKOSPI", regime["vkospi_level"])
st.metric("Foreign net 60d", f"{regime['foreign_cum_60d']/1e12:.1f}조")
```

### Streamlit Cloud 등록
1. GitHub repo connect
2. `streamlit_app.py` entry point
3. `secrets.toml` 에 GDrive token (Streamlit Secrets)
4. URL: `https://kr-quant-engine.streamlit.app`

**Cost**: Free tier 1GB RAM, 1 app/account, sleep after 7 days inactivity (cron으로 keep-alive 가능).

---

## 6. 운영 흐름

### 매월 1일 (자동)
```
04:00  GitHub Actions: monthly_picks.yml
         → Universe build (cached + delta fetch)
         → Classifier predict
         → Top-20 picks CSV
         → GDrive upload + Slack alert
04:30  Streamlit Cloud auto-refresh (cache TTL 1h)
05:00  사용자 Slack에서 picks 확인
```

### 평일 매일 (선택)
```
18:00  GitHub Actions: daily_event_monitor.yml
         → DART events 검색 (전일+당일)
         → 보유 종목 governance event check
         → Slack alert (hard veto 발생 시)
```

### 분기 1일 (자동)
```
06:00  GitHub Actions: quarterly_backtest.yml
         → 5-year OOS backtest 갱신
         → research/06/quarterly_backtest_2026-Q2.md commit
07:00  사용자 review
```

### 사용자 manual (paper or live)
```
1. Slack 알림 → CSV 확인
2. KIS API 모의매매 → fill price + slippage 측정
3. Live execution (확신 후)
```

---

## 7. 비용 분석

| Service | Tier | 사용량 | Cost |
|---|---|---|---|
| GitHub Actions (private) | Free | ~600분/월 (4 workflows) | $0 (2,000분/월 무료) |
| Streamlit Cloud | Free | 1 app | $0 |
| GDrive | 2TB | 5GB | 기존 plan |
| Slack workspace | Free | 1 channel | $0 |
| KIS API (모의) | Free | unlimited | $0 |
| **Total** | | | **$0/월** ★ |

---

## 8. 보안 고려

```
✅ API keys → GitHub Secrets (encrypted)
✅ rclone config → encrypted secret
✅ Repo private (코드 + signal 노출 방지)
✅ Slack webhook → personal channel
✅ KIS API → 사용자 PC에서만 (cloud에 X)
⚠️ GDrive sync는 read-only 권한 토큰 사용 가능
```

---

## 9. 구현 cost

| 작업 | 예상 |
|---|---|
| `.github/workflows/*.yml` (4 files) | 4시간 |
| `tools/generate_monthly_picks.py` | 2시간 |
| `tools/run_quarterly_backtest.py` | 1시간 |
| `streamlit_app.py` | 6시간 |
| rclone setup + secrets | 2시간 |
| Slack webhook | 1시간 |
| Testing + first cron run | 4시간 |

**Total: ~3일**.

---

## 10. 정리

```
✅ GitHub Actions로 매월 자동 picks 생성 + classifier retrain → 가능
✅ Streamlit dashboard host → free
✅ Slack alert → free
✅ Total cost $0/월
⚠️ Real-time 매매는 local + KIS API (lag 5-15min 부적절)
```

**우선순위**: Phase C 끝난 후 도입. 최적 시점은 PIT/Governance fix 후 진정한 strategy 안정 확인 시점.

**대안**: 즉시 simple version만 도입 — `monthly_picks.yml` + Slack alert만 (Streamlit 없이도 CSV로 충분).

---

## 11. Phase 순서

```
Phase C1-C4 완료 (~3-4주, 코드 안정)
   ↓
Phase D — GitHub deployment (~3일)
   ├─ .github/workflows 추가
   ├─ Secrets 설정
   ├─ rclone GDrive sync
   ├─ Slack webhook
   └─ Streamlit dashboard (선택)
   ↓
Phase E — Paper trading (~1-2개월)
   └─ KIS API 모의매매
   ↓
Phase F — Live (~1개월 후)
   └─ 소액 시작 → scale up
```

---

## 12. 결론

**가능 + 추천**. 단 PIT survivorship + governance + purged WF (Phase C) 먼저 완료한 후 deployment 권장. 그 전에 deploy하면 in-sample biased picks을 자동 발송하게 됨.

순서 엄격히 지키면 **3-4주 후 자동 운영 가능**.
