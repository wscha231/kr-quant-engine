# KR Research Decision V1 export

This one-shot input exporter owns KR security identity, KRW units and the XKRX
completed-session cutoff. It uses the independent shared pure package in
`wscha231/r1000-quant-engine/tools/research_decision_v1` with an explicit source
hash. It never calls `kr_pipeline`, the v5 backtest, model, target or order path.

Related tracking: r1000 issue #396. US H1 admission and H2 scenario decisions are
separate PRs. KR PR #1 (old automation proposal) is not a dependency.

`python tools/export_research_decision_market.py --input <KR-input.json>
--engine-root <r1000-local-repo> --engine-source-hash <recorded-package-sha256>`

Install the shared package's `requirements_research_decision_v1.txt`. Output is
immutable `outputs/research_decision_v1/exports/<hash>.json`. Exit 2 means
blocked/partial coverage. US inputs and mismatched source hashes fail closed.
The resulting explicit export may be consumed by the independent combined
research proposal, after cutoff/FX validation. This does not grant the US
official engine Korean account authority.

Reuse: `kr_dart_client.fetch_single_company_financials` and
`kr_pykrx_client.fetch_ticker_history` remain the existing collectors. This
exporter accepts their reconciled raw data with explicit provenance; it does not
reinterpret their disabled-feature zero placeholders as financial facts.
No new API credentials, schedule, paper account or live book is created.

Current blockers and real-data run results are recorded in the final US/KR V1
handoff. May 2026 SESSION_HANDOFF production claims are historical, not a new
approval or current evidence in this change.

Review hardening: source pins include recursive Python files and the parent
initializer; the loader executes a temporary snapshot made from the exact bytes
that were hashed. It does not reopen the mutable US checkout for imports. The
source-pin regression tests are registered in the existing PR smoke workflow;
no new trigger or schedule is added.
