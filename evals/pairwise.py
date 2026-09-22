"""Order-swapped pairwise comparison (position-bias control).

The pair judge sees blinded labels 'Plan A' / 'Plan B'. We run it twice with
the operand order swapped; if the two runs disagree about the underlying
winner, the comparison is INCONCLUSIVE — never resolved by coin flip.
"""
from __future__ import annotations

from typing import Callable

BASELINE = "BASELINE"
CANDIDATE = "CANDIDATE"
TIE = "TIE"
INCONCLUSIVE = "INCONCLUSIVE"


def compare(baseline_packet: dict, candidate_packet: dict,
            judge_pair: Callable[[dict, dict], object]) -> dict:
    """judge_pair(packet_a, packet_b) must return an object with .winner in
    {"A","B","TIE"} (see evals.evaluator.PairVerdict). Returns:
      {"winner": BASELINE|CANDIDATE|TIE|INCONCLUSIVE, "runs": [...]}
    """
    # run 1: A=baseline, B=candidate
    r1 = judge_pair(baseline_packet, candidate_packet)
    # run 2 (order swapped): A=candidate, B=baseline
    r2 = judge_pair(candidate_packet, baseline_packet)

    map1 = {"A": BASELINE, "B": CANDIDATE, "TIE": TIE}
    map2 = {"A": CANDIDATE, "B": BASELINE, "TIE": TIE}
    w1 = map1[r1.winner]
    w2 = map2[r2.winner]

    winner = w1 if w1 == w2 else INCONCLUSIVE
    return {
        "winner": winner,
        "runs": [
            {"order": "baseline-first", "rawWinner": r1.winner, "mapped": w1,
             "reasons": list(getattr(r1, "reasons", []))},
            {"order": "candidate-first", "rawWinner": r2.winner, "mapped": w2,
             "reasons": list(getattr(r2, "reasons", []))},
        ],
    }
