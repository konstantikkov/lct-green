"""Eval runner: `PYTHONPATH=apps/api python3 -m evals.run` (or `make eval`).

  Level A  — deterministic round-trip checks + expected.json conformance
             on all golden cases (failures gate: exit code 1).
  Judge    — benchmark in mock mode by default (deterministic stub).
  Level C  — GPT evaluator, only if capability is available; otherwise
             "GPT evaluator: SKIPPED (capability: <reason>)". LLM-layer
             issues never change the exit code.

Writes eval-results/<UTC timestamp>/results.json.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
for p in (str(REPO_ROOT), str(REPO_ROOT / "apps" / "api")):
    if p not in sys.path:
        sys.path.insert(0, p)

from evals.checks import all_passed, run_level_a_checks, verify_expected  # noqa: E402
from evals.evaluator import (CapabilityError, GptEvaluator,  # noqa: E402
                             PROMPT_VERSION as EVAL_PROMPT_VERSION)
from evals.judge.score import run_benchmark  # noqa: E402
from evals.packet import build_eval_packet  # noqa: E402
from evals.pipeline_runner import DATASET_DIR, list_cases, load_case_files, run_case  # noqa: E402

RESULTS_SCHEMA = "greenplan-eval-results/v1"


def dataset_version(dataset_dir: Path = DATASET_DIR) -> str:
    """Content hash of the golden dataset (code + fixtures, no caches)."""
    h = hashlib.sha256()
    for f in sorted(dataset_dir.rglob("*")):
        if f.is_dir() or "__pycache__" in f.parts or f.suffix == ".pyc":
            continue
        h.update(str(f.relative_to(dataset_dir)).encode("utf-8"))
        h.update(f.read_bytes())
    return h.hexdigest()[:16]


def git_commit() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT,
                              capture_output=True, text=True, check=True,
                              timeout=10).stdout.strip()
    except Exception:
        return None


def run_level_a(case_dirs: list[Path]) -> dict:
    cases_out = []
    ok_cases = 0
    total_checks = passed_checks = 0
    for d in case_dirs:
        _, _, expected = load_case_files(d)
        try:
            result = run_case(d)
            checks = run_level_a_checks(result)
            conformance = verify_expected(result, expected, level_a=checks)
            metrics = result.metrics
        except Exception as exc:  # a crashing case is a failing case
            cases_out.append({"case": d.name, "passed": False,
                              "error": f"{type(exc).__name__}: {exc}",
                              "checks": [], "expectedConformance": []})
            continue
        all_checks = checks + conformance
        case_ok = all_passed(all_checks)
        ok_cases += case_ok
        total_checks += len(all_checks)
        passed_checks += sum(c.passed for c in all_checks)
        cases_out.append({
            "case": d.name,
            "passed": case_ok,
            "placements": len(result.placements),
            "metrics": {k: metrics[k] for k in
                        ("treeCount", "coverageScore", "distributionScore")},
            "checks": [c.to_json() for c in checks],
            "expectedConformance": [c.to_json() for c in conformance],
        })
    return {"passed": ok_cases == len(case_dirs), "casesPassed": ok_cases,
            "casesTotal": len(case_dirs), "checksPassed": passed_checks,
            "checksTotal": total_checks, "cases": cases_out}


def run_level_c(case_dirs: list[Path]) -> dict:
    evaluator = GptEvaluator()
    try:
        model = evaluator.probe_capability()
    except CapabilityError as exc:
        return {"status": "SKIPPED", "reason": str(exc), "model": None,
                "promptVersion": EVAL_PROMPT_VERSION}
    reports = []
    for d in case_dirs:
        _, _, expected = load_case_files(d)
        result = run_case(d)
        packet = build_eval_packet(result, expected)
        try:
            report = evaluator.evaluate(packet)
            reports.append({"case": d.name, "report": report.model_dump()})
        except CapabilityError as exc:
            reports.append({"case": d.name, "error": str(exc)})
    return {"status": "RAN", "model": model,
            "promptVersion": EVAL_PROMPT_VERSION, "reports": reports}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m evals.run",
                                 description="GreenPlan eval & regression runner")
    ap.add_argument("--baseline", default=None,
                    help="baseline git commit for regression bookkeeping")
    ap.add_argument("--judge-mode", choices=["mock", "live"], default="mock")
    ap.add_argument("--cases", nargs="*", default=None,
                    help="subset of case ids (default: all)")
    args = ap.parse_args(argv)

    case_dirs = list_cases()
    if args.cases:
        case_dirs = [d for d in case_dirs if d.name in set(args.cases)]

    print("== GreenPlan eval & regression run ==")
    ds_version = dataset_version()
    commit = git_commit()
    print(f"dataset {ds_version} | candidate {commit or 'no-git'}"
          + (f" | baseline {args.baseline}" if args.baseline else ""))

    # ---- Level A (gating) --------------------------------------------------
    level_a = run_level_a(case_dirs)
    print(f"\nLevel A (deterministic): {level_a['casesPassed']}/{level_a['casesTotal']}"
          f" cases, {level_a['checksPassed']}/{level_a['checksTotal']} checks")
    for c in level_a["cases"]:
        if c.get("error"):
            print(f"  FAIL {c['case']}: crashed: {c['error']}")
            continue
        mark = "PASS" if c["passed"] else "FAIL"
        m = c["metrics"]
        print(f"  {mark} {c['case']}: {c['placements']} placements, "
              f"coverage={m['coverageScore']}, distribution={m['distributionScore']}")
        for chk in c["checks"] + c["expectedConformance"]:
            if not chk["passed"]:
                print(f"       x {chk['id']}: {chk['detail']}")

    # ---- Judge benchmark (reported, not gating) -----------------------------
    judge = run_benchmark(mode=args.judge_mode)
    if judge.get("status") == "RAN":
        print(f"\nJudge benchmark ({judge['mode']}): {judge['cases']} cases | "
              f"accept_acc={judge['acceptAccuracy']} rerun_prec={judge['rerunPrecision']} "
              f"review_recall={judge['manualReviewRecall']} "
              f"param_valid={judge['parameterChangeValidity']} "
              f"agreement={judge['overallAgreement']}")
        print("  labels: engineering-bootstrap (not expert ground truth)")
    else:
        print(f"\nJudge benchmark: SKIPPED ({judge.get('reason')})")

    # ---- Level C (capability-gated, never gating) ---------------------------
    level_c = run_level_c(case_dirs)
    if level_c["status"] == "SKIPPED":
        print(f"\nGPT evaluator: SKIPPED (capability: {level_c['reason']})")
    else:
        n_err = sum(1 for r in level_c["reports"] if "error" in r)
        print(f"\nGPT evaluator ({level_c['model']}): {len(level_c['reports'])} reports"
              + (f", {n_err} errors" if n_err else ""))

    # ---- results.json --------------------------------------------------------
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = REPO_ROOT / "eval-results" / ts
    out_dir.mkdir(parents=True, exist_ok=True)
    results = {
        "schema": RESULTS_SCHEMA,
        "created_utc": ts,
        "dataset_version": ds_version,
        "candidate_commit": commit,
        "baseline_commit": args.baseline,
        "evaluator_model": level_c.get("model"),
        "evaluator_prompt_version": EVAL_PROMPT_VERSION,
        "levels": {
            "level_a": level_a,
            "judge_benchmark": judge,
            "gpt_evaluator": level_c,
        },
    }
    out_path = out_dir / "results.json"
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    print(f"\nresults: {out_path.relative_to(REPO_ROOT)}")

    # deterministic failures gate; LLM-layer issues are reported, not gating
    if not level_a["passed"]:
        print("RESULT: FAIL (deterministic Level A)")
        return 1
    print("RESULT: PASS (deterministic gates green)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
