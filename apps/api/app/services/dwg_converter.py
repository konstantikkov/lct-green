"""DWG → DXF conversion abstraction.

DWG is a proprietary binary format; we never parse it directly and never send
binary data to an LLM. External converters are detected at runtime:

  * ODA File Converter (ODAFileConverter binary)
  * LibreDWG dwg2dxf

Configured via env:
  DWG_CONVERTER=auto|oda|libredwg|none
  ODA_FILE_CONVERTER_PATH=...
  LIBREDWG_DWG2DXF_PATH=...

No proprietary binaries are bundled. When no converter is available the API
returns a structured capability error and DXF support keeps working.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from .. import config

log = logging.getLogger("greenplan.dwg")


def _has_eof(path: Path) -> bool:
    """A complete DXF ends with the "0 / EOF" tag pair."""
    try:
        with path.open("rb") as fh:
            fh.seek(max(0, path.stat().st_size - 64))
            return b"EOF" in fh.read()
    except OSError:
        return False


class DwgConversionError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


@dataclass
class ConverterInfo:
    available: bool
    kind: str | None  # "oda" | "libredwg"
    path: str | None
    detail: str = ""
    # auto mode with both installed: LibreDWG takes over a file ODA fails on
    fallback: str | None = None


def _which(candidates: list[str]) -> str | None:
    for c in candidates:
        if not c:
            continue
        p = shutil.which(c)
        if p:
            return p
        if Path(c).is_file():
            return str(Path(c))
    return None


def detect_converter() -> ConverterInfo:
    mode = config.DWG_CONVERTER.lower()
    if mode == "none":
        return ConverterInfo(False, None, None, "DWG conversion disabled (DWG_CONVERTER=none)")

    oda = _which([config.ODA_FILE_CONVERTER_PATH, "ODAFileConverter"])
    libre = _which([config.LIBREDWG_DWG2DXF_PATH, "dwg2dxf"])

    if mode == "oda":
        return ConverterInfo(bool(oda), "oda" if oda else None, oda)
    if mode == "libredwg":
        return ConverterInfo(bool(libre), "libredwg" if libre else None, libre)

    # auto: prefer ODA (better fidelity), fall back to LibreDWG per file
    if oda:
        return ConverterInfo(True, "oda", oda, fallback=libre)
    if libre:
        return ConverterInfo(True, "libredwg", libre)
    return ConverterInfo(False, None, None,
                         "No DWG converter found. Install ODA File Converter or LibreDWG dwg2dxf "
                         "and set ODA_FILE_CONVERTER_PATH / LIBREDWG_DWG2DXF_PATH.")


class DwgConverter:
    """Converts DWG files to DXF using an external, safely-invoked binary."""

    # ODA runs under xvfb-run (its Linux build has only the xcb Qt plugin);
    # concurrent xvfb-run -a calls race for a display, so ODA runs one at a
    # time — a file takes seconds, a whole package well under a minute
    _oda_lock = threading.Lock()

    def __init__(self, info: ConverterInfo | None = None):
        self.info = info or detect_converter()

    def convert_to_dxf(self, input_path: Path, output_path: Path,
                       extra_args: tuple[str, ...] = ()) -> Path:
        """`extra_args` (LibreDWG only), e.g. ("--as", "r2000") to retry a
        file whose default output ezdxf cannot load."""
        if not self.info.available:
            raise DwgConversionError(
                "DWG_CONVERTER_UNAVAILABLE",
                "DWG upload received but no DWG→DXF converter is configured. "
                "Install ODA File Converter or LibreDWG dwg2dxf (see README, section 'DWG support').",
            )
        input_path = Path(input_path).resolve()
        output_path = Path(output_path).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # the same DWG converted before (a repeated upload) comes from the cache
        from . import cache
        key = None
        if cache.enabled():
            import sys
            key = cache.make_key("dwg", cache.file_sha256(input_path), str(self.info.kind),
                                 str(self.info.path), str(self.info.fallback), ",".join(extra_args),
                                 cache.code_fingerprint(sys.modules[__name__]))
            hit = cache.get(key)
            if hit is not None:
                shutil.copyfile(hit / "out.dxf", output_path)
                return output_path
        result = self._convert_uncached(input_path, output_path, extra_args)
        if key is not None:
            cache.put(key, lambda d: shutil.copyfile(result, d / "out.dxf"))
        return result

    def _convert_uncached(self, input_path: Path, output_path: Path,
                          extra_args: tuple[str, ...]) -> Path:
        if self.info.kind == "libredwg":
            return self._convert_libredwg(input_path, output_path, extra_args)
        try:
            with self._oda_lock:
                return self._convert_oda(input_path, output_path)
        except DwgConversionError as exc:
            if not self.info.fallback:
                raise
            log.warning("ODA File Converter failed on %s (%s); converting with LibreDWG",
                        input_path.name, exc)
            return self._convert_libredwg(input_path, output_path, extra_args, self.info.fallback)

    def _run(self, argv: list[str]) -> subprocess.CompletedProcess:
        # argv list form — user-controlled names are never interpolated into a shell.
        return subprocess.run(
            argv, capture_output=True, text=True,
            timeout=config.CONVERTER_TIMEOUT_S, shell=False,
        )

    # dwg2dxf sometimes writes a truncated file for recent DWGs (tables, then
    # nothing — no ENTITIES, no EOF) and still exits 0. The R2000 writer and
    # the entities-only mode get through such files.
    _FALLBACK_ARGS = (["--as", "r2000"], ["--minimal"])

    def _convert_libredwg(self, input_path: Path, output_path: Path,
                          extra_args: tuple[str, ...] = (), binary: str | None = None) -> Path:
        binary = binary or str(self.info.path)
        proc = self._run([binary, *extra_args, "-y", "-o", str(output_path), str(input_path)])
        if proc.returncode != 0 or not output_path.exists() or output_path.stat().st_size == 0:
            raise DwgConversionError(
                "DWG_CONVERSION_FAILED",
                f"dwg2dxf failed (exit {proc.returncode}): {(proc.stderr or proc.stdout)[-800:]}",
            )
        for extra in self._FALLBACK_ARGS:
            if _has_eof(output_path):
                break
            log.warning("dwg2dxf output of %s is truncated; retrying with %s", input_path.name, extra)
            self._run([binary, *extra, "-y", "-o", str(output_path), str(input_path)])
        if not _has_eof(output_path):
            raise DwgConversionError("DWG_CONVERSION_FAILED",
                                     "dwg2dxf produced an incomplete DXF (no EOF) in every mode")
        return output_path

    def _convert_oda(self, input_path: Path, output_path: Path) -> Path:
        # ODAFileConverter converts whole directories:
        #   ODAFileConverter <inDir> <outDir> <outVer> <outFormat> <recurse> <audit> [filter]
        with tempfile.TemporaryDirectory(prefix="oda_in_") as in_dir, \
                tempfile.TemporaryDirectory(prefix="oda_out_") as out_dir:
            safe_in = Path(in_dir) / "input.dwg"
            shutil.copy(input_path, safe_in)
            proc = self._run([str(self.info.path), in_dir, out_dir,
                              "ACAD2018", "DXF", "0", "1"])
            produced = list(Path(out_dir).glob("*.dxf"))
            if proc.returncode != 0 and not produced:
                raise DwgConversionError(
                    "DWG_CONVERSION_FAILED",
                    f"ODA File Converter failed (exit {proc.returncode}): {(proc.stderr or proc.stdout)[-800:]}",
                )
            if not produced:
                raise DwgConversionError("DWG_CONVERSION_FAILED", "ODA File Converter produced no DXF output.")
            shutil.copy(produced[0], output_path)
        return output_path
