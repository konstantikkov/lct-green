# shared

The API contract shared between backend and frontend lives in
[`docs/API_CONTRACT.md`](../../docs/API_CONTRACT.md); its TypeScript
transcription is `apps/web/src/api/types.ts`, and the backend Pydantic models
are in `apps/api/app/models/` + endpoint schemas in `apps/api/app/main.py`.

If the contract grows, generate types from the FastAPI OpenAPI schema here.
