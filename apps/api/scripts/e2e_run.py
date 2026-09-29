"""End-to-end run of drawings / xref packages through the running API, with
every result checked against the invariants (scripts/invariants.py).

    python apps/api/scripts/e2e_run.py <object.zip|.dxf|.dwg> [...] \
        [--reference plan.dwg] [--species tree_rowan,shrub_mockorange] [--keep]

For each input: upload → generate → report, constraints, result-only DXF and
full DXF → invariants (report, result layers, AutoCAD-safe DXF, source layers
untouched for single drawings) → optional comparison with the designers'
planting plan (scripts/compare_reference.py inside the API container).

Results go to outputs/e2e/<name>/ (report.json, summary.json, DXFs); a table
of all objects is printed and outputs/e2e/summary.json written. Exit 1 when a
run fails or an invariant is broken.

Guards (a full system disk once killed the API container and made the OS
purge temporary files): the run refuses to start when the output drive or the
system drive (Docker Desktop keeps its disk there) has less than
--min-free-gb free; on a failure the container's state and log tail are saved
next to the results; the project is deleted afterwards unless --keep.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import shutil
import subprocess
import sys
import time
import traceback
import urllib.request
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import e2e_golden  # noqa: E402
from e2e_visual import render_plan, write_gallery  # noqa: E402
from invariants import (check_full_dxf, check_report, check_result_layers,  # noqa: E402
                        check_source_untouched)

REPO = HERE.parents[2]
DEFAULT_SPECIES = "tree_rowan,tree_linden,shrub_mockorange,shrub_lilac"


def call(api: str, method: str, path: str, body: bytes | None = None,
         headers: dict | None = None, raw: bool = False, timeout: int = 3600):
    req = urllib.request.Request(api + path, data=body, method=method, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
        return data if raw else (json.loads(data) if data else None)


def free_gb(path: Path) -> float:
    return shutil.disk_usage(path).free / 2**30


def guard_disk(out: Path, min_gb: float) -> None:
    system = Path(os.environ.get("SystemDrive", "C:") + "\\") if os.name == "nt" else Path("/")
    for label, p in (("output drive", out), ("system drive (Docker's disk)", system)):
        gb = free_gb(p)
        if gb < min_gb:
            raise SystemExit(f"refusing to run: {label} {p} has {gb:.1f} GB free, need {min_gb:g} GB "
                             f"(a full system disk kills the API container)")


def docker(*args: str, timeout: int = 600) -> subprocess.CompletedProcess:
    env = {**os.environ, "MSYS_NO_PATHCONV": "1"}  # Git Bash must not rewrite /tmp paths
    return subprocess.run(["docker", *args], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, env=env)


def diagnostics(container: str | None, dest: Path) -> None:
    if not container:
        return
    state = docker("inspect", "-f", "{{json .State}}", container)
    logs = docker("logs", "--tail", "200", container)
    dest.write_text(f"# docker inspect State\n{state.stdout or state.stderr}\n\n"
                    f"# docker logs --tail 200\n{logs.stdout}{logs.stderr}", encoding="utf-8")


def run_one(src: Path, a, out_root: Path) -> dict:
    name = src.stem
    d = out_root / name
    d.mkdir(parents=True, exist_ok=True)
    row: dict = {"input": str(src), "ok": False, "problems": []}
    pid = None
    try:
        b = uuid.uuid4().hex
        ctype = "application/zip" if src.suffix.lower() == ".zip" else "application/octet-stream"
        body = (f"--{b}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{src.name}\"\r\n"
                f"Content-Type: {ctype}\r\n\r\n").encode() + src.read_bytes() + f"\r\n--{b}--\r\n".encode()
        t0 = time.time()
        proj = call(a.api, "POST", "/projects", body, {"Content-Type": f"multipart/form-data; boundary={b}"})
        row["uploadS"] = round(time.time() - t0)
        pid = row["projectId"] = proj["projectId"]
        pkg = proj.get("package") or {}
        row["sources"] = dict(collections.Counter(s["status"] for s in pkg.get("sources", [])))
        row["sourcesNotMerged"] = [f"{s['name']}: {s['status']}" for s in pkg.get("sources", [])
                                   if s["status"] != "merged"]
        species = a.species.split(",")
        kinds = sorted({"TREE" if s.startswith("tree") else "SHRUB" for s in species})
        run = call(a.api, "POST", f"/projects/{pid}/generate",
                   json.dumps({"plantKinds": kinds, "speciesIds": species, "strategy": a.strategy,
                               "useLlmJudge": False, "maxIterations": 1}).encode(),
                   {"Content-Type": "application/json"})
        t1 = time.time()
        while True:
            st = call(a.api, "GET", f"/projects/{pid}/runs/{run['runId']}")
            if st["state"] in ("COMPLETED", "FAILED"):
                break
            time.sleep(3)
        row["generateS"] = round(time.time() - t1)
        if st["state"] == "FAILED":
            raise RuntimeError(f"generation failed: {st.get('error') or st}")

        report = call(a.api, "GET", f"/projects/{pid}/export/report")
        constraints = call(a.api, "GET", f"/projects/{pid}/constraints")
        (d / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        layers_dxf = d / "greening_layers.dxf"
        layers_dxf.write_bytes(call(a.api, "GET", f"/projects/{pid}/export/dxf?layers=result", raw=True))
        problems = check_report(report, constraints) + check_result_layers(layers_dxf)
        if not a.no_full_dxf:
            full_dxf = d / "greening.dxf"
            full_dxf.write_bytes(call(a.api, "GET", f"/projects/{pid}/export/dxf", raw=True))
            problems += check_full_dxf(full_dxf)
            if src.suffix.lower() == ".dxf":
                problems += check_source_untouched(src, full_dxf)

        reg = report.get("regulatory") or {}
        pl = collections.Counter(p["kind"] for p in report.get("placements") or [])
        rej = report.get("rejections") or {}
        row.update({
            "trees": pl.get("TREE", 0), "shrubs": pl.get("SHRUB", 0),
            "lawnM2": (report.get("lawn") or {}).get("areaM2"),
            "allowedAreaM2": (constraints.get("allowedArea") or {}).get("areaM2"),
            "status": reg.get("status"), "blockers": reg.get("blockers"),
            "coverage": (reg.get("coverage") or {}).get("regulatoryCoverage"),
            "rejected": rej.get("total"),
            "topReasons": [[r["key"], r["count"]] for r in (rej.get("byReason") or [])[:5]],
        })
        ref = a.references.get(name) or a.references.get("*")
        if ref:
            row["reference"] = compare_reference(pid, ref, a.container, d)
        if not a.no_picture:
            its = call(a.api, "GET", f"/projects/{pid}/iterations")
            sel = next((i for i in its if i.get("selected")), its[-1])
            iteration = call(a.api, "GET", f"/projects/{pid}/iterations/{sel['iterationId']}")
            document = call(a.api, "GET", f"/projects/{pid}/document")
            render_plan(constraints, iteration, document, d / "plan.svg", title=name)
        # golden metrics: a change of results beyond the tolerances is drift
        drift, note = e2e_golden.check(name, row, a.golden_dir, a.update_golden)
        row["drift"], row["goldenNote"] = drift, note
        row["problems"] = problems
        row["ok"] = not problems and not drift
    except Exception as exc:  # noqa: BLE001 — one broken object must not stop the batch
        row["problems"].append(f"run failed: {type(exc).__name__}: {exc}")
        (d / "failure.txt").write_text(traceback.format_exc(), encoding="utf-8")
        diagnostics(a.container, d / "container.txt")
    finally:
        if pid and not a.keep:
            try:
                call(a.api, "DELETE", f"/projects/{pid}", timeout=120)
            except Exception:  # noqa: BLE001
                pass
    (d / "summary.json").write_text(json.dumps(row, ensure_ascii=False, indent=1), encoding="utf-8")
    return row


def compare_reference(pid: str, ref: Path, container: str | None, d: Path) -> dict:
    if not container:
        return {"error": "--container is needed for the reference comparison"}
    target = f"/tmp/reference{ref.suffix.lower()}"
    cp = docker("cp", str(ref), f"{container}:{target}")
    if cp.returncode != 0:
        return {"error": cp.stderr.strip()}
    res = docker("exec", container, "python", "scripts/compare_reference.py", pid, target, timeout=1800)
    if res.returncode != 0:
        return {"error": res.stderr.strip()[-500:]}
    data = json.loads(res.stdout)
    (d / "reference.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return {k: data[k] for k in ("reference", "ours", "referenceBreakingNorms",
                                 "referenceTreesInOurAllowedArea", "ourTreesWithin3mOfReferenceTree")}


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("inputs", nargs="+", type=Path)
    ap.add_argument("--api", default="http://127.0.0.1:8000/api")
    ap.add_argument("--species", default=DEFAULT_SPECIES)
    ap.add_argument("--strategy", default="balanced")
    ap.add_argument("--out", type=Path, default=REPO / "outputs" / "e2e")
    ap.add_argument("--reference", action="append", default=[],
                    help="designers' planting plan (DWG/DXF) to compare with: PATH for every input, "
                         "or NAME=PATH for the input named NAME (file stem); repeatable")
    ap.add_argument("--container", default="greenplan-api", help="API container (logs, reference comparison)")
    ap.add_argument("--min-free-gb", type=float, default=5.0)
    ap.add_argument("--no-full-dxf", action="store_true", help="skip the (large) full export")
    ap.add_argument("--keep", action="store_true", help="keep the projects in the service")
    ap.add_argument("--golden-dir", type=Path, default=e2e_golden.GOLDEN_DIR)
    ap.add_argument("--update-golden", action="store_true",
                    help="record the current metrics as the golden ones (after reviewing the change)")
    ap.add_argument("--no-picture", action="store_true", help="skip the plan picture")
    a = ap.parse_args()
    a.references = {}
    for spec in a.reference:
        name, sep, path = spec.partition("=")
        # "C:\..." has no "=": only NAME=PATH with a plain name maps to one input
        if sep and "/" not in name and "\\" not in name:
            a.references[name] = Path(path)
        else:
            a.references["*"] = Path(spec)
    a.out.mkdir(parents=True, exist_ok=True)
    guard_disk(a.out, a.min_free_gb)

    rows = {}
    for src in a.inputs:
        guard_disk(a.out, a.min_free_gb)
        print(f"… {src.name}", flush=True)
        rows[src.stem] = r = run_one(src, a, a.out)
        mark = "OK " if r["ok"] else "FAIL"
        print(f"{mark} {src.stem}: upload {r.get('uploadS', '-')} s, generate {r.get('generateS', '-')} s, "
              f"{r.get('trees', '-')} trees + {r.get('shrubs', '-')} shrubs, lawn {r.get('lawnM2', '-')} m², "
              f"status {r.get('status', '-')}", flush=True)
        for p in r["problems"]:
            print(f"     ! {p}", flush=True)
        for p in r.get("drift") or []:
            print(f"     ~ drift {p}", flush=True)
        if r.get("goldenNote"):
            print(f"     · {r['goldenNote']}", flush=True)
    # the overall summary and gallery cover every object run into this
    # directory, not only this invocation's
    everything = {}
    for d in sorted(p for p in a.out.iterdir() if (p / "summary.json").is_file()):
        try:
            everything[d.name] = json.loads((d / "summary.json").read_text(encoding="utf-8"))
        except ValueError:
            pass
    everything.update(rows)
    (a.out / "summary.json").write_text(json.dumps(everything, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"gallery: {write_gallery(everything, a.out)}")
    failed = [n for n, r in rows.items() if not r["ok"]]
    print(f"\n{len(rows) - len(failed)} of {len(rows)} passed" + (f"; failed: {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
