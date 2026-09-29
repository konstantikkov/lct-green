"""GreenPlan evaluation & regression pipeline (WS3).

Three layers:
  Level A — deterministic export/geometry round-trip checks (gating, CI).
  Judge benchmark — synthetic JudgePacket cases scored against bootstrap labels.
  Level C — offline GPT evaluator (OPENAI_EVAL_MODEL); capability-gated,
            never a silent fallback, never gating CI.

Run: PYTHONPATH=apps/api python3 -m evals.run   (or `make eval`).
"""
