"""Test-wide settings."""
import os

# the ingest cache (app/services/cache.py) would let a repeated test run take
# a cached result instead of exercising the code; its own tests enable it
os.environ.setdefault("GREENPLAN_CACHE", "0")
