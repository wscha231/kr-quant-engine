# 04 — Regime Detection

VKOSPI, USD/KRW, 외인 누적 net flow, BOK 금리 등을 input으로 한국시장 regime 분류. P3 타겟.

## Regime Taxonomy (제안)

- `bull_trending` — KOSPI MA200 위 + 외인 누적 매수 + VKOSPI < 20
- `bull_peaking` — RSI > 70 + breadth 약화 + 외인 net sell 시작
- `bear_falling` — KOSPI MA200 아래 + VKOSPI > 25 + 외인 sell
- `bear_bottoming` — RSI < 30 + breadth 회복 + 외인 sell 둔화
- `recovery` — KOSPI MA200 회복 + 외인 net buy 재개
- `sideways` — 위 어디에도 안 맞음
- `stagflation_kr` — 한국 PMI < 50 + USD/KRW 절상 + BOK 금리 인상
- `won_crisis` — USD/KRW 1,400원 돌파 + 외인 대량 sell + KOSPI -10% in 5d

## TODO P3
- [ ] `p3_regime_taxonomy.md` — regime 정의 + threshold
- [ ] `p3_regime_detector_backtest.md` — 2016-2024 regime label 시계열
- [ ] `p3_regime_sleeve_multipliers.md` — regime별 sleeve weight 조정 효과
