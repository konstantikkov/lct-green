# Progress

## Phase 0–1: Audit & baseline — DONE
- 35/35 backend tests at baseline (commit 5155234); live deployment inspected.
- Playwright baseline screenshots (run on the Lightsail host): `docs/baseline/`.
- `docs/current-state-audit.md`, `docs/improvement-plan.md`, `tests.json`.

## Phase 2 (WS1 progressive disclosure) — DONE
- Empty state → semantic layer groups → single review interrupt → overlay
  legend → Generate w/ Advanced collapsed → Quality review card →
  PLAN/REGULATIONS/RUNS/QUALITY nav → debug mode (?debug=1).
- After screenshots: `docs/after/pw-screens/` (1366/1440/1920 × stages).

## Phase 3–4 (WS2 judge redesign + prompt versioning) — DONE
- Precondition (valid-only) + deterministic repair; JudgePacket with evidence
  ids (quadrants/clusters/underused/edge density); schema v2 with
  ACCEPT/RERUN/MANUAL_REVIEW; whitelist+range+max-delta param clamps;
  prompts/judge/v1.md + JUDGE_PROMPT_VERSION; full observability per judgment;
  retention rule (≥0.02 objective gain, no >0.05 component regression).

## Phase 5–6 (WS3 evals + GPT evaluator) — DONE
- evals/ package: 10-case golden dataset (deterministic generators),
  241 Level-A checks incl. export→reparse→re-validate; judge benchmark
  (14 cases, mock+live), pairwise A/B with order swap → INCONCLUSIVE;
  GPT evaluator via OPENAI_EVAL_MODEL with explicit CapabilityError
  (no silent downgrade); `make eval` → eval-results/<ts>/results.json.

## Phase 7–11 (WS4 regulations) — DONE
- /api/regulations: PDF/DOCX upload, hierarchy with stable fragment ids,
  LLM candidate extraction (documents are data; injection-tested),
  deterministic numeric-literal provenance gate, second-pass verifier,
  UPLOADED…REJECTED status machine with version retention, review UI,
  VERIFIED+ACTIVE → REG_* rules merged into the constraint engine.
- Extraction benchmark: evals/regulations (10 gold clauses, citation-accuracy
  hard-fail), 10/10 in mock-gate mode.

## Phase 12–15 (integration, e2e, docs) — DONE
- Test matrix: apps/api 64 passed; repo-root 102 passed; frontend build green;
  `make eval` PASS (241/241 deterministic; GPT evaluator SKIPPED with explicit
  capability message — no OpenAI key in the environment).
- Deployed to https://13-61-189-48.sslip.io/ (backend incl. prompts/, frontend).
- Playwright on the deploy host: 9/9 passed across 1366×768 / 1440×900 /
  1920×1080 (core flow: example upload → generate → quality card → export;
  stage screenshots captured).
- README, API contract, tests.json updated.

## Known limitations / follow-ups
- Eval LLM is LOCAL open-source by default: Qwen2.5-7B-Instruct via llama.cpp on
  the server (port 8081, localhost-only, systemd `greenplan-evalllm`). Live run
  verified (eval-results/20260922T110910Z): structured verdicts on 3 cases, incl.
  correctly flagging the ambiguous-layer case as UNCERTAIN. Hosted endpoints
  remain configurable but require an explicit key; no silent fallback.
- Judge benchmark labels are engineering-bootstrap, not expert ground truth.
- Regulation extraction quality depends on the local model; the deterministic
  provenance gates are the safety net.
- OCR for scanned PDFs is out of scope (flagged in parseLog).
