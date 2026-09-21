# GreenPlan — Current State Audit (Phase 0)

Audited against the live deployment (https://13-61-189-48.sslip.io/), the repo at
commit `5155234`, a full test run (35/35 pass), and Playwright baseline
screenshots in `docs/baseline/` (landing at 1366/1440/1920; opened project,
constraints, iterations, 3D at 1440×900).

## What currently works (verified)

- **CAD ingestion**: DWG (via LibreDWG) and DXF upload; malformed-converter-output
  sanitizer; units normalization to meters; 12.8k-entity real drawings parse in ~30 s.
- **Semantic classification**: keyword+geometry rules for Russian topo layers,
  confidence + requiresReview, user override via PATCH, optional local-LLM refinement.
- **Constraint engine**: rule registry (YAML) → exclusion buffers with provenance;
  planting-domain detection (site boundary / vegetation-evidence / extents fallback);
  allowed area via Shapely with repair logging.
- **Placement**: seeded Poisson-disc candidates, hard filtering, greedy+local-search
  optimizer with 4 strategies, cross-species spacing.
- **Deterministic validator**: final authority; per-rule distance checks, spacing,
  inside-site; metrics (coverage, distribution).
- **Judge (current)**: local LLM (llama.cpp qwen2.5-1.5b on the server) with
  grammar-constrained JSON; heuristic fallback when unreachable; rerun loop ≤ 3
  with clamped parameter changes; decision recorded per iteration.
- **Export**: original-preserving DXF with AI_GREENING_* layers + XDATA; JSON report
  with per-tree explanations; round-trip verified by tests.
- **Persistence**: Postgres (Lightsail managed) with gzip blobs + disk cache;
  SQLite fallback; project list/reopen.
- **Frontend**: three.js 2D plan + 3D massing (species-diverse trees, lanterns,
  fences, buildings, plan underlay, Simple/Detailed, light/dark), mobile bottom
  sheet, PWA with auto-update.
- **Tests**: 35 pytest (unit/integration/round-trip/regressions). No frontend tests.

## What is confusing / exposed too early (baseline screenshots)

- Opening a project immediately shows **29 raw CAD layers** with entity counts and
  confidence badges (`02_project_opened_1440.png`). Semantic grouping exists only
  as badges; there is no compact category view.
- **Overlay checkboxes (8) + CONSTRAINTS internals** (zone count 522, allowed m²,
  `site boundary: extents_fallback`, an endless "ZONES (CLICK TO INSPECT)" list)
  are visible before the user has done anything.
- The landing screen shows drop zone + recent projects — acceptable, but the shell
  (panels chrome) appears with zeroed content in some states.
- Jargon leaks: `extents_fallback`, chunk grid toggle, per-zone rule IDs, seeds.
- Generate panel always shows seed / max iterations / LLM toggle — should be
  Advanced.
- Iterations panel shows optimizer parameter JSON-ish detail permanently.
- No staged flow: nothing tells the user "review 2 uncertain layers, then generate".
- Debug-ish info (px/m, entity counts, judge mode chips) always visible.

## What is duplicated

- Judge's heuristic fallback duplicates deterministic metrics thresholds that the
  validator/metrics already own (acceptable as fallback, but the LLM prompt also
  re-receives validator verdicts it cannot override — wasted tokens).
- Constraints panel repeats rule provenance that the Inspector also shows.

## What is hidden incorrectly

- Nothing critical is hidden; the problem is the inverse (everything visible).
- The per-tree explanation (the strongest feature) is only discoverable by knowing
  to click a tree; no affordance points to it after generation.

## Current Judge behavior (audited from code, `apps/api/app/services/judge.py`)

- **Input**: objective, iteration index, optimizer params, validator metrics,
  validator verdict + violation count, constraint summary (zone count, allowed m²,
  inactive-rule count), ≤ 6 chunk digests (bbox + category counts).
- **Prompt**: single inline string in code; not versioned; asks for terse issues.
- **Output**: `JudgeResponse` (decision ACCEPT|RERUN, 4-part score incl. a fuzzy
  `overall`, issues[], recommendedParameterChanges, reasoningSummary), grammar-
  constrained via response_format json_schema, Pydantic-validated, one repair retry.
- **Guards**: cannot ACCEPT when validator invalid (code-clamped); param changes
  clamped to SAFE_BOUNDS; last-iteration forced ACCEPT.
- **Gaps vs target**: judge IS invoked on invalid plans (then overridden) instead of
  being skipped; no MANUAL_REVIEW / abstention; no evidence IDs — critiques are
  free text; "overall" score is an LLM-invented number; no spatial diagnostics
  (quadrants/clusters/underused zones); no per-parameter max_delta; prompt not
  versioned; no benchmark; rerun retention rule exists (compliance not worse +
  objective better) but thresholds are crude.

## Current regulatory model

- `rules/regulations.yaml`: 10 documents, ~20 rules; every numeric distance is
  `null` + `status: missing_source_data` with an explicitly flagged demo
  `assumption` block (enabled by default, ASSUMED badges throughout UI/report).
- User overrides stored in `data/rule_overrides.json`.
- No document ingestion: PDFs/DOCX cannot be uploaded; no extraction, no
  provenance beyond hand-written YAML; no versioning beyond a registry version
  string; no review workflow. `INVASIVE_SPECIES_BLACKLIST` is wired but empty.

## Current test coverage

- Backend: 35 tests — parsing/units, classification, rules semantics
  (missing_source_data, blacklist), geometry, Poisson determinism, validator
  violations, full pipeline, seed reproducibility, explanation provenance, DXF
  round-trip, real-DWG conversion, cross-species spacing, DXF sanitizer.
- Not covered: judge behavior (any), API endpoints (HTTP layer), frontend (zero),
  eval/regression infrastructure (none), regulation ingestion (n/a), exported-DXF
  re-validation of hard constraints (round-trip checks structure, not compliance).

## Architecture (as-built)

Monorepo: `apps/api` (FastAPI; services: dxf_parser, dwg_converter, classification,
chunking, geometry, rules, constraints, candidates, optimizer, validator, explain,
judge, llm, dxf_export; pipeline.py state machine; store.py PG/SQLite) +
`apps/web` (React/TS/three.js; SceneManager + massing3d; panels; bottom sheet;
PWA) + `rules/` + `fixtures/` + `scripts/deploy_lightsail.sh`. Deployment: nginx
(HTTPS via sslip.io) → uvicorn + llama.cpp systemd units; Vercel frontend exists
but account-blocked (older build).

## Environment constraints noted for this iteration

- Sandbox has no OpenAI credentials: the GPT evaluator (Layer 3) must be built
  config-driven (`OPENAI_EVAL_MODEL`) with explicit capability errors when the
  key/model is absent — its live path can only be smoke-tested when a key is
  provided. Never silently downgraded.
- Playwright cannot run in the dev sandbox (missing system libs, no sudo); it runs
  on the Lightsail host (installed, verified) — e2e/screenshot suites execute there.
