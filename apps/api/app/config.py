"""Application configuration via environment variables."""
from __future__ import annotations

import os
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
API_DIR = APP_DIR.parent
REPO_ROOT = API_DIR.parent.parent

DATA_DIR = Path(os.environ.get("GREENPLAN_DATA_DIR", REPO_ROOT / "data"))
OUTPUT_DIR = Path(os.environ.get("GREENPLAN_OUTPUT_DIR", REPO_ROOT / "outputs"))
RULES_FILE = Path(os.environ.get("GREENPLAN_RULES_FILE", REPO_ROOT / "rules" / "regulations.yaml"))
SPECIES_FILE = Path(os.environ.get("GREENPLAN_SPECIES_FILE", REPO_ROOT / "rules" / "species.yaml"))
FIXTURES_DIR = REPO_ROOT / "fixtures"

# Persistence: empty → SQLite + local JSON; postgresql://... → Postgres (Neon,
# AWS managed DB, ...) with gzip blobs + local disk cache.
DATABASE_URL = os.environ.get("DATABASE_URL", "")

# DWG conversion
DWG_CONVERTER = os.environ.get("DWG_CONVERTER", "auto")  # auto | oda | libredwg | none
ODA_FILE_CONVERTER_PATH = os.environ.get("ODA_FILE_CONVERTER_PATH", "")
LIBREDWG_DWG2DXF_PATH = os.environ.get("LIBREDWG_DWG2DXF_PATH", "")
CONVERTER_TIMEOUT_S = int(os.environ.get("CONVERTER_TIMEOUT_S", "120"))
PARSER_TIMEOUT_S = int(os.environ.get("PARSER_TIMEOUT_S", "120"))

# Local LLM (OpenAI-compatible endpoint: Ollama, llama.cpp server, vLLM, ...)
LOCAL_LLM_BASE_URL = os.environ.get("LOCAL_LLM_BASE_URL", "http://localhost:11434")
LOCAL_LLM_MODEL = os.environ.get("LOCAL_LLM_MODEL", "")
LOCAL_LLM_ENABLED = os.environ.get("LOCAL_LLM_ENABLED", "true").lower() in ("1", "true", "yes")
LOCAL_LLM_TIMEOUT_S = float(os.environ.get("LOCAL_LLM_TIMEOUT_S", "180"))

# Placement
PLACEMENT_RANDOM_SEED = int(os.environ.get("PLACEMENT_RANDOM_SEED", "42"))
MAX_ITERATIONS_DEFAULT = int(os.environ.get("MAX_ITERATIONS", "3"))

# Upload safety
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", str(200 * 1024 * 1024)))
ALLOWED_EXTENSIONS = {".dxf", ".dwg"}

# Rendering caps
MAX_RENDER_ENTITIES = int(os.environ.get("MAX_RENDER_ENTITIES", "20000"))
CURVE_SEGMENTS = 32

ALGORITHM_VERSION = "0.3.0"
PARSER_VERSION = "0.2.0"


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "uploads").mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "projects").mkdir(parents=True, exist_ok=True)
