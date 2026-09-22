#!/usr/bin/env python3
"""Regulations-extraction benchmark scorer (self-contained, standalone).

Gold benchmark: gold.jsonl — Russian regulatory clauses with the expected
machine-readable rule per clause.

Usage:
    python3 evals/regulations/score.py --pred outputs.jsonl
    python3 evals/regulations/score.py --mock

  --pred FILE  score extractor outputs: one JSON object per line with
               {fragmentIds (or fragmentId), value, unit, subject,
                referenceObject, operator, applicability{plantType},
                exceptions[], machineExecutable, references[]?}
  --mock       no-LLM mode for CI: builds predictions from the gold fields
               and runs the SAME deterministic gates the backend applies
               (numeric-literal provenance, recommendation detection,
               exception/cross-reference regexes) against gold sourceText.
               Verifies the gates end-to-end without any model.

Scoring: per-field accuracies (value/unit/subject/object/operator/
applicability/exception recall/citation). A citation mismatch (prediction
does not cite the gold fragment id) FAILS the whole case — fields are not
credited even if they match.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "apps" / "api"))

FIELDS = ["citation", "value", "unit", "subject", "referenceObject",
          "operator", "applicability", "exceptionRecall", "machineExecutable"]


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def _norm_unit(u) -> str:
    u = (u or "").strip().lower().rstrip(".")
    return {"m": "м", "cm": "см", "mm": "мм", "метров": "м", "метра": "м",
            "метр": "м"}.get(u, u)


def _norm_token(s) -> str:
    return (s or "").strip().upper().replace(" ", "_").replace("-", "_")


def _value_eq(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    try:
        return abs(float(a) - float(b)) < 1e-9
    except (TypeError, ValueError):
        return False


def mock_predictions(gold: list[dict]) -> list[dict]:
    """Run the backend's deterministic gates against gold sourceText."""
    from app.regulations import extraction as ex

    preds = []
    for case in gold:
        e = case["expected"]
        cand = {
            "source": {"sourceText": case["sourceText"],
                       "fragmentIds": [case["fragmentId"]]},
            "constraint": {"type": e.get("constraintType", "MIN_DISTANCE"),
                           "value": e.get("value"), "unit": e.get("unit")},
            "confidence": 0.9,
            "references": [], "exceptions": [],
        }
        ex.apply_gates(cand)  # the exact gates the backend applies
        preds.append({
            "fragmentIds": [case["fragmentId"]],
            "constraintType": e.get("constraintType"),
            "value": e.get("value"), "unit": e.get("unit"),
            "subject": e.get("subject"), "referenceObject": e.get("referenceObject"),
            "operator": e.get("operator"),
            "applicability": e.get("applicability", {}),
            "exceptions": cand["exceptions"],          # gate-detected
            "references": cand["references"],          # gate-detected
            "machineExecutable": cand["machineExecutable"],  # gate-decided
        })
    return preds


def score(gold: list[dict], preds: list[dict]) -> dict:
    by_frag: dict[str, dict] = {}
    for p in preds:
        fids = p.get("fragmentIds") or ([p["fragmentId"]] if p.get("fragmentId") else [])
        for fid in fids:
            by_frag.setdefault(fid, p)

    hits = {f: 0 for f in FIELDS}
    case_results = []
    for case in gold:
        e, fid = case["expected"], case["fragmentId"]
        p = by_frag.get(fid)
        if p is None:
            # citation mismatch = case FAIL: no field credit at all
            case_results.append((fid, False, "CITATION MISSING/WRONG"))
            continue
        hits["citation"] += 1
        checks = {
            "value": _value_eq(p.get("value"), e.get("value")),
            "unit": _norm_unit(p.get("unit")) == _norm_unit(e.get("unit")),
            "subject": _norm_token(p.get("subject")) == _norm_token(e.get("subject")),
            "referenceObject": _norm_token(p.get("referenceObject"))
                               == _norm_token(e.get("referenceObject")),
            "operator": (p.get("operator") or "") == (e.get("operator") or ""),
            "applicability": set(map(_norm_token,
                                     (p.get("applicability") or {}).get("plantType", [])))
                             == set(map(_norm_token,
                                        (e.get("applicability") or {}).get("plantType", []))),
            "machineExecutable": bool(p.get("machineExecutable"))
                                 == bool(e.get("machineExecutable")),
        }
        gold_exc = e.get("exceptions", [])
        if gold_exc:
            pred_exc = " || ".join(p.get("exceptions", [])).lower()
            matched = sum(1 for x in gold_exc if x.lower() in pred_exc)
            checks["exceptionRecall"] = matched == len(gold_exc)
        else:
            checks["exceptionRecall"] = True  # nothing required
        for k, ok in checks.items():
            hits[k] += bool(ok)
        failed = [k for k, ok in checks.items() if not ok]
        case_results.append((fid, not failed, ", ".join(failed) or "ok"))

    n = len(gold)
    accuracies = {f: hits[f] / n for f in FIELDS}
    passed = sum(1 for _, ok, _ in case_results if ok)
    return {"n": n, "casesPassed": passed, "accuracies": accuracies,
            "cases": case_results}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pred", type=Path, help="extractor outputs (jsonl)")
    ap.add_argument("--gold", type=Path, default=HERE / "gold.jsonl")
    ap.add_argument("--mock", action="store_true",
                    help="run deterministic gates against gold sourceText (no LLM)")
    args = ap.parse_args()

    gold = load_jsonl(args.gold)
    if args.mock:
        preds = mock_predictions(gold)
        mode = "mock (deterministic gates, no LLM)"
    elif args.pred:
        preds = load_jsonl(args.pred)
        mode = f"predictions from {args.pred}"
    else:
        ap.error("either --pred FILE or --mock is required")
        return 2

    res = score(gold, preds)
    print(f"regulations extraction benchmark — {mode}")
    print(f"gold cases: {res['n']}   cases fully passed: {res['casesPassed']}/{res['n']}")
    print("per-field accuracy:")
    for f in FIELDS:
        print(f"  {f:18s} {res['accuracies'][f] * 100:6.1f}%")
    print("per-case:")
    for fid, ok, note in res["cases"]:
        print(f"  [{'PASS' if ok else 'FAIL'}] {fid}: {note}")

    if args.mock and res["casesPassed"] != res["n"]:
        print("MOCK MODE FAILURE: deterministic gates disagree with gold labels",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
