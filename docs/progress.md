
## Iteration 6 — manual placement editing (2026-09-28)
- New endpoints: GET /placements/check (stateless drag-preview law check),
  POST /placements/{id}/move (persist + full deterministic revalidation +
  regulatory verdict + manualEdits audit trail). Contract frozen in
  API_CONTRACT.md. Moves into violation are saved AND reported, never blocked
  or hidden.
- Frontend: drag plantings in 2D plan and 3D view; floating annotation shows
  live per-rule correlation with document conditions (FAIL/UNVERIFIED/PASS
  margins with СП 42/743-ПП citations).
- Tests: apps/api/tests/test_move.py (5 cases, no LLM).
