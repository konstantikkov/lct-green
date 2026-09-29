# case_007 — Impossible site (fully blocked)

Building footprint + 5 m buffer cover the whole parcel. The correct output is an EMPTY plan: 0 placements, valid validation report, export still succeeds. Crash = failure.

Layers: ГРАНИЦА_УЧАСТКА, ЗДАНИЯ

Known edge cases: no_placements_expected, empty_allowed_area
