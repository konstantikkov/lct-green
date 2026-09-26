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

Gate cases (gold entries with a "kind" field) run through the REAL backend
gates/merge logic with scripted inputs, no LLM:
  * hierarchy_mismatch — clause/table reference in sourceText conflicting
    with the fragment hierarchyPath must yield NEEDS_REVIEW (never
    VERIFIED-able) with machineExecutable gated [audit 3];
  * title_trap — digits-only/short titles replaced by a synthesized
    subject → referenceObject title [audit 3];
  * canonical_merge — an ACTIVE candidate whose canonical key is covered by
    a registry rule PATCHES that rule in place, no duplicate [audit 4].
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


def _gate_hierarchy_mismatch(case: dict) -> tuple[bool, str]:
    from app.regulations import extraction as ex

    cand = {
        "source": {"sourceText": case["sourceText"],
                   "hierarchyPath": case["hierarchyPath"],
                   "fragmentIds": [case["id"]]},
        "constraint": dict(case.get("constraint")
                           or {"type": "MIN_DISTANCE", "value": None, "unit": None}),
        "confidence": 0.9, "references": [], "exceptions": [],
    }
    ex.apply_gates(cand)
    e = case["expected"]
    notes = (cand.get("verification") or {}).get("notes") or ""
    # the SAME deterministic check verification.py recomputes: while it
    # returns a ref, the verifier forces AMBIGUOUS → VERIFIED unreachable
    ref = ex.hierarchy_reference_mismatch(case["sourceText"], case["hierarchyPath"])
    problems = []
    if cand["status"] != e["status"]:
        problems.append(f"status={cand['status']} want {e['status']}")
    if cand["machineExecutable"] != e["machineExecutable"]:
        problems.append(f"machineExecutable={cand['machineExecutable']}")
    if e.get("mismatchRef") is None:
        if ref is not None:
            problems.append(f"unexpected mismatch ref {ref!r}")
    else:
        if ref != e["mismatchRef"]:
            problems.append(f"mismatch ref {ref!r} want {e['mismatchRef']!r}")
        if "hierarchy/clause mismatch" not in notes:
            problems.append("verification.notes missing mismatch note")
    return not problems, ", ".join(problems) or "ok"


def _gate_title_trap(case: dict) -> tuple[bool, str]:
    from app.regulations import extraction as ex

    title = ex.sanitize_title(case.get("llmTitle"), case.get("subject"),
                              case.get("referenceObject"))
    problems = []
    if title != case["expectedTitle"]:
        problems.append(f"title {title!r} want {case['expectedTitle']!r}")
    if not title or len(title) < 4 or title.strip().isdigit():
        problems.append(f"title {title!r} is still a bare number/too short")
    return not problems, ", ".join(problems) or "ok"


def _gate_canonical_merge(case: dict) -> tuple[bool, str]:
    from app.regulations import integration as integ
    from app.services.rules import (RegulatoryRule, RuleAssumption,
                                    RuleRegistry, RuleSource)

    rule = RegulatoryRule(
        id=case["canonicalRuleId"],
        appliesTo=case["appliesTo"],
        constraint={"type": "min_distance", "distance_m": None, "units": "m"},
        source=RuleSource(document="СП 42.13330.2016 (без значения)"),
        status="missing_source_data",
        assumption=RuleAssumption(assumedDistanceM=3.0, enabled=True,
                                  note="демо-значение"),
    )
    registry = RuleRegistry(version="bench", rules=[rule])
    cand, doc = case["candidate"], case["document"]
    integ.apply_active_candidates(registry, candidates=[cand],
                                  documents={doc["id"]: doc})
    e = case["expected"]
    problems = []
    if len(registry.rules) != 1:
        problems.append(f"{len(registry.rules)} rules — duplicate appended")
    if rule.status != e["status"]:
        problems.append(f"status={rule.status}")
    if rule.effective_distance_m != e["distanceM"]:
        problems.append(f"effectiveDistanceM={rule.effective_distance_m}")
    if rule.effective_source != "verified":
        problems.append(f"effectiveSource={rule.effective_source}")
    if rule.source.clause != cand["source"]["hierarchyPath"]:
        problems.append(f"clause={rule.source.clause!r}")
    if cand["source"]["sourceText"][:40] not in (rule.source.excerpt or ""):
        problems.append("excerpt missing sourceText")
    if "верифицировано" not in rule.note or cand["id"] not in rule.note:
        problems.append(f"merge note missing: {rule.note!r}")
    return not problems, ", ".join(problems) or "ok"


GATE_RUNNERS = {
    "hierarchy_mismatch": _gate_hierarchy_mismatch,
    "title_trap": _gate_title_trap,
    "canonical_merge": _gate_canonical_merge,
}


def run_gate_cases(cases: list[dict]) -> list[tuple[str, bool, str]]:
    """Scripted inputs through the REAL backend gates/merge logic (no LLM)."""
    out = []
    for case in cases:
        cid = case.get("id") or case.get("fragmentId") or "?"
        runner = GATE_RUNNERS.get(case.get("kind"))
        if runner is None:
            out.append((cid, False, f"unknown gate kind {case.get('kind')!r}"))
            continue
        try:
            ok, note = runner(case)
        except Exception as exc:  # noqa: BLE001
            ok, note = False, f"EXCEPTION: {exc}"
        out.append((cid, ok, note))
    return out


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

    all_cases = load_jsonl(args.gold)
    gold = [c for c in all_cases if not c.get("kind")]       # field cases
    gate_cases = [c for c in all_cases if c.get("kind")]     # gate/merge cases
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

    gate_res = run_gate_cases(gate_cases)
    gate_passed = sum(1 for _, ok, _ in gate_res if ok)
    print(f"gate/merge cases (real backend logic, scripted inputs): "
          f"{gate_passed}/{len(gate_res)}")
    for cid, ok, note in gate_res:
        print(f"  [{'PASS' if ok else 'FAIL'}] {cid}: {note}")

    failed = gate_passed != len(gate_res)
    if args.mock and res["casesPassed"] != res["n"]:
        print("MOCK MODE FAILURE: deterministic gates disagree with gold labels",
              file=sys.stderr)
        failed = True
    if failed and (args.mock or gate_res):
        if gate_passed != len(gate_res):
            print("GATE CASE FAILURE: backend gates/merge logic disagrees "
                  "with gold expectations", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
