# Session Handoff — Research Decision V1

Updated 2026-09-07. Current source inspected: main
`80cb0721896a834a45d257fc4f65255134338f36`.

New branch: `codex/research-decision-v1-kr-export-20260907`.
Related US issue: https://github.com/wscha231/r1000-quant-engine/issues/396
US H1 dependency: https://github.com/wscha231/r1000-quant-engine/pull/399

Implemented a separate KR-only input export entrypoint in
`tools/export_research_decision_market.py`. It imports the shared independent
research package only after verifying the caller's expected source hash.
The original KR engine, v5 parameters, trading/account paths and schedules remain
unchanged. Old KR automation PR #1 was inspected and is not a dependency.

Validation completed: 2 export source-pin tests; 23 existing quick smoke checks.
Real pilot: SK hynix has a partial official Q2 press-release extract; verified
current raw close/corporate-action/benchmark history, TTM/share/debt/FCF inputs,
FX and regime are incomplete in this environment. No current rank, position,
OOS validation or production promotion is asserted. US/KR integration must
consume this repo's explicit export and independently validate cutoff and FX.

Next: materialize existing DART/KRX raw sources in an authorized runtime, preserve
source availability and raw hashes, reconcile the required fields, and rerun the
small real pilot before expanding to US5/KR2. Do not create replacement keys or
activate a schedule. Keep merge, fullrun, migration, model/policy promotion,
official target/account changes and orders blocked without separate approval.

The prior May 2026 handoff and historical claims remain in Git history at
`80cb0721896a834a45d257fc4f65255134338f36:SESSION_HANDOFF.md`; they are not today's
operating evidence. See `research/research_decision_v1.md` for the new contract.

Review update: PR #2 first review identified incomplete import pin coverage and
check/import races. Both are covered by verified-byte snapshot execution and
new regression tests, now included in the existing smoke workflow. A fresh
exact-head review is still required; no operational promotion or merge allowed.
