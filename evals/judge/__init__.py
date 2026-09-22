"""Judge benchmark: synthetic JudgePacket-shaped cases + bootstrap labels.

The packet field names follow docs/improvement-plan.md §WS2 (planMetrics,
validation, spatialDiagnostics, classificationUncertainty,
allowedParameterChanges). Coupling to the WS2 implementation is deliberately
loose: everything here is plain dicts, no imports from app.services.judge*.
"""
from pathlib import Path

JUDGE_DIR = Path(__file__).resolve().parent
CASES_PATH = JUDGE_DIR / "cases.jsonl"
LABELS_PATH = JUDGE_DIR / "human-labels.jsonl"
