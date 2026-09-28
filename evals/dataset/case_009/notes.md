# case_009 — Invalid geometry (self-intersecting boundary)

Bow-tie boundary polyline (self-intersecting). The geometry layer must repair it (buffer(0)) or fall back to extents; the repair log should mention it; pipeline must complete.

Layers: ГРАНИЦА_УЧАСТКА, ГАЗОНЫ

Known edge cases: self_intersecting_boundary_repaired_or_fallback
