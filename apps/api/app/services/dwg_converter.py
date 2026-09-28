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

    # auto: prefer ODA (better fidelity), fall back to LibreDWG
    if oda:
        return ConverterInfo(True, "oda", oda)
    if libre:
        return ConverterInfo(True, "libredwg", libre)
    return ConverterInfo(False, None, None,
                         "No DWG converter found. Install ODA File Converter or LibreDWG dwg2dxf "
                         "and set ODA_FILE_CONVERTER_PATH / LIBREDWG_DWG2DXF_PATH.")


class DwgConverter:
    """Converts DWG files to DXF using an external, safely-invoked binary."""

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

        if self.info.kind == "libredwg":
            return self._convert_libredwg(input_path, output_path, extra_args)
        return self._convert_oda(input_path, output_path)

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
                          extra_args: tuple[str, ...] = ()) -> Path:
        proc = self._run([str(self.info.path), *extra_args, "-y", "-o", str(output_path), str(input_path)])
        if proc.returncode != 0 or not output_path.exists() or output_path.stat().st_size == 0:
            raise DwgConversionError(
                "DWG_CONVERSION_FAILED",
                f"dwg2dxf failed (exit {proc.returncode}): {(proc.stderr or proc.stdout)[-800:]}",
            )
        for extra in self._FALLBACK_ARGS:
            if _has_eof(output_path):
                break
            log.warning("dwg2dxf output of %s is truncated; retrying with %s", input_path.name, extra)
            self._run([str(self.info.path), *extra, "-y", "-o", str(output_path), str(input_path)])
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
