"""Test-wide settings."""
import os

# the ingest cache (app/services/cache.py) would let a repeated test run take
# a cached result instead of exercising the code; its own tests enable it
os.environ.setdefault("GREENPLAN_CACHE", "0")


import pytest


@pytest.fixture(autouse=True)
def avoid_external_preview_jobs_in_unit_tests(monkeypatch):
    # Upload tests must not start real Blender processes or leave a shared
    # worker queue active after their temporary output directory is removed.
    # The import-hook test replaces this stub with a recorder; worker tests
    # exercise request/_build directly, and the real Blender canary is separate.
    from app.services import preview_jobs
    monkeypatch.setattr(preview_jobs,'prepare_after_import',lambda pid,doc:None)
