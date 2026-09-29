"""Ingest cache (app/services/cache.py): a repeated upload skips conversion,
merge and parse; a changed file or changed code misses; old entries go."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import config  # noqa: E402
from app.services import cache  # noqa: E402
from app.services.dwg_converter import ConverterInfo, DwgConverter  # noqa: E402
from app.services.dxf_parser import parse_dxf_cached  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from make_fixture import build as build_fixture  # noqa: E402

FIXTURE = Path(__file__).resolve().parents[3] / "fixtures" / "synthetic_site.dxf"


@pytest.fixture()
def cache_on(tmp_path, monkeypatch):
    monkeypatch.setenv("GREENPLAN_CACHE", "1")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    return tmp_path


def test_repeated_parse_comes_from_the_cache(cache_on):
    if not FIXTURE.exists():
        build_fixture(FIXTURE)
    first = parse_dxf_cached(FIXTURE, "p1", "a.dxf", "DXF", ["upload"])
    second = parse_dxf_cached(FIXTURE, "p2", "b.dxf", "DXF", ["upload"])
    assert second.documentId == "p2" and second.fileName == "b.dxf"
    assert len(second.entities) == len(first.entities) and second.layers == first.layers
    assert second.conversionLog[0] == "upload"
    assert any("ingest cache" in m for m in second.conversionLog)
    assert not any("ingest cache" in m for m in first.conversionLog)


def test_changed_file_misses(cache_on):
    if not FIXTURE.exists():
        build_fixture(FIXTURE)
    copy = cache_on / "copy.dxf"
    copy.write_bytes(FIXTURE.read_bytes())
    parse_dxf_cached(copy, "p1", "c.dxf")
    copy.write_bytes(FIXTURE.read_bytes() + b"\n")  # different content → different key
    again = parse_dxf_cached(copy, "p2", "c.dxf")
    assert not any("ingest cache" in m for m in again.conversionLog)


def test_repeated_dwg_conversion_skips_the_converter(cache_on, monkeypatch):
    calls = []

    def convert(self, src, out, extra_args):
        calls.append(src)
        out.write_text("0\nEOF\n")
        return out

    monkeypatch.setattr(DwgConverter, "_convert_uncached", convert)
    conv = DwgConverter(ConverterInfo(True, "oda", "/usr/local/bin/oda-convert"))
    src = cache_on / "a.dwg"
    src.write_bytes(b"AC1032 drawing")
    conv.convert_to_dxf(src, cache_on / "1.dxf")
    out = conv.convert_to_dxf(src, cache_on / "2.dxf")
    assert len(calls) == 1 and out.read_text() == "0\nEOF\n"


def test_least_recently_used_entries_are_evicted(cache_on, monkeypatch):
    monkeypatch.setenv("GREENPLAN_CACHE_MAX_GB", str(1500 / 2**30))  # room for one 1 KB entry
    cache.put("old", lambda d: (d / "x").write_bytes(b"0" * 1000))
    cache.put("new", lambda d: (d / "x").write_bytes(b"0" * 1000))
    assert cache.get("old") is None and cache.get("new") is not None
