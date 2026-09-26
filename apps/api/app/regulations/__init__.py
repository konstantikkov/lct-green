"""Regulation ingestion feature (WS4): upload PDF/DOCX/RTF regulatory documents,
parse them into hierarchical fragments, extract machine-readable rule
candidates with a local LLM, verify + review them, and feed ACTIVE candidates
into the constraint engine's rule registry."""
