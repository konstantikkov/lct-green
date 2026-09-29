"""Golden dataset: 10 deterministic synthetic cases (case_001..case_010)."""
from pathlib import Path

DATASET_DIR = Path(__file__).resolve().parent

CASE_IDS = [f"case_{i:03d}" for i in range(1, 11)]
