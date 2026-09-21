# GreenPlan — Improvement Plan (four workstreams)

Baseline: commit `5155234`, 35/35 tests green, audit in `current-state-audit.md`.
Principle: three reasoning layers stay separated — deterministic engine (final
authority), local production judge (soft quality only), offline GPT evaluator
(dev/CI only, `OPENAI_EVAL_MODEL`, explicit capability errors, no silent
downgrade).

## WS1 — Progressive disclosure (apps/web)

- Empty state: logo + tagline + drop/choose + "Open example" (loads
  fixtures/synthetic_site.dxf) + recent projects only when they exist. No panels.
- Stage model in the store: `imported → interpreted → constrained → generated`.
  Panels appear per stage; Inspector only on selection; Layers default to a
  compact semantic-category summary (Buildings ✓ / Utilities ⚠ 1 uncertain …)
  expandable to raw layers; uncertainty surfaces as one interrupt chip
  ("1 layer requires review — Review") not permanent warnings.
- Top-level nav: PLAN (default workflow) · REGULATIONS · RUNS · QUALITY — slim
  switcher, not dashboards. RUNS = iterations/compare (only populated when >1
  run). QUALITY = eval results viewer (dev-facing). REGULATIONS = WS4 UI.
- Advanced settings (seed, density, weights, max iterations, LLM toggle, debug
  overlays incl. chunk grid, rejected candidates) behind an "Advanced" disclosure.
- Judge appears only post-generation as a compact "Quality review" card
  (hard-pass ✓, deterministic component metrics, N opportunities → View review).
  Summary score, if shown, computed deterministically from component metrics.
- Debug mode (localStorage flag, ?debug=1): raw JSON, prompts, timings, entity ids.
- Playwright e2e (runs on the Lightsail host) covering the demo path; screenshot
  states at 1366×768 / 1440×900 / 1920×1080.

## WS2 — Judge redesign (apps/api)

- Precondition: judge only runs when `validator.valid == true`; otherwise the
  pipeline repairs deterministically (drop violating placements, revalidate) and
  records `judgeSkipped: hard_violations`.
- `judge_packet.py`: compact evidence-bearing packet — plan metrics, validation
  summary, spatialDiagnostics (quadrant occupancy grid, DBSCAN-lite cluster
  detection, underused allowed sub-zones with ids `zone_q23`, edge density),
  classification uncertainty list, previous iteration deltas, allowedParameterChanges
  (min/max/max_delta per param).
- Output schema v2: decision ACCEPT|RERUN|MANUAL_REVIEW, confidence, five
  assessment dimensions each with status + evidence ids, issues[] with severity +
  evidenceIds, parameterChanges[] with current/proposed/reason/evidenceIds,
  summary. No LLM-invented overall score.
- Prompts in `prompts/judge/v*.md`, loaded at runtime; every judgment records
  prompt_version, model, temperature, input schema version, judge_input_hash.
- Enforcement in code: whitelist + min/max + max_delta clamps; evidence-id
  validation (unknown ids stripped); MANUAL_REVIEW routed to UI as "needs human
  look" without rerun; retention rule: keep rerun only if valid && objective
  improves ≥ epsilon && no component regresses > threshold.

## WS3 — Eval & regression pipeline (`evals/`)

- Level A deterministic: parse every exported DXF back; layers/entities preserved
  (canonical signatures, not bytes), placements re-validated against rules from
  the parsed file, NaN/finite checks, metadata presence. `tests/export/`,
  `tests/geometry/`, `tests/e2e/`.
- Level B golden dataset `evals/dataset/case_*/` (input.dxf generated
  deterministically + rules.json + expected.json + notes.md): rectangular site,
  center building, crossing utility, disconnected zones, narrow corridor,
  existing trees, impossible site, ambiguous layer, invalid geometry, multi-optima.
- Level C GPT evaluator `evals/evaluator.py`: EvalPacket (no raw CAD), strict
  schema (criteria PASS/FAIL/UNCERTAIN + evidence, criticalFailures, regressions),
  `OPENAI_EVAL_MODEL` config, model availability probed via /v1/models;
  missing key/model → CapabilityError (eval report says SKIPPED/CAPABILITY, CI
  deterministic gates unaffected). Pairwise A/B with order swap; disagreement ⇒
  INCONCLUSIVE. Blind labels.
- Judge benchmark `evals/judge/cases.jsonl` + `human-labels.jsonl` (seeded from
  real iteration records + synthetic edge cases; labels marked as
  `reviewer: engineering-bootstrap` until real expert labels exist). Metrics:
  accept accuracy, rerun precision, manual-review recall, param-change validity.
- `make eval` / `python -m evals.run` → console summary + `eval-results/<ts>/results.json`.
  Deterministic failures exit non-zero; LLM-layer results reported, not gating.

## WS4 — Regulation ingestion (apps/api + web)

- `documents` feature: upload PDF/DOCX (pypdf + python-docx; OCR out of scope,
  flagged when native text absent), store original + metadata + hash; parse to a
  hierarchy with stable fragment ids (`<doc>:<chapter>:...:<clause>`), pages,
  tables preserved.
- Extraction (local LLM or configured model) produces RULE CANDIDATES with exact
  provenance (documentId, page, hierarchyPath, fragmentIds, sourceText) —
  documents are DATA (prompt-injection defense: extraction model only emits
  structured candidates; nothing it says executes).
- Second-pass verifier (different prompt): SUPPORTED/PARTIALLY/UNSUPPORTED/
  AMBIGUOUS; numeric value+unit must literally appear in the source fragment
  (deterministic check too — regex the number+unit; failure ⇒ NEEDS_REVIEW
  regardless of LLM opinion).
- Status machine UPLOADED→PARSED→EXTRACTED→NEEDS_REVIEW/VERIFIED→ACTIVE/
  DEPRECATED/REJECTED; only VERIFIED+ACTIVE feed the constraint engine (merged
  into the registry as `status: verified` rules with full provenance; YAML
  registry remains for the hand-maintained baseline).
- Review UI: source fragment beside extracted rule; Approve/Edit/Reject.
- Versioning: rule_version, document_version, extraction model+prompt versions,
  reviewer, dates; re-upload diffs clauses and flags obsolete rules.
- Extraction benchmark `evals/regulations/`: gold clauses → expected rules;
  measures value/unit/subject/object/operator/applicability/exception/citation
  accuracy; wrong citation = FAIL even if the rule matches.

## Sequencing (keeping the app runnable)

Wave 0 (done): audit, baselines. Wave 1: WS2 + WS3 (backend, disjoint files).
Wave 2: WS1 (web) + WS4 (api). Integration: wire, test, deploy, Playwright verify
on the server, docs, push. Existing 35 tests must stay green throughout.
