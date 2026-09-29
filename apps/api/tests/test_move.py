"""Tests for manual placement editing: the stateless live-position check
(drag preview) and the persisting move endpoint (full revalidation +
manual-edit audit trail). No LLM involved — deterministic geometry only.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import config  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
FIXTURE = REPO / "fixtures" / "synthetic_site.dxf"


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "outputs")
    monkeypatch.setenv("LOCAL_LLM_ENABLED", "false")
    config.ensure_dirs()
    from fastapi.testclient import TestClient

    from app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def generated(client):
    """Upload the synthetic site and run one generation to completion."""
    with FIXTURE.open("rb") as f:
        r = client.post("/api/projects",
                        files={"file": ("synthetic_site.dxf", f,
                                        "application/dxf")})
    assert r.status_code == 201, r.text
    pid = r.json()["projectId"]
    r = client.post(f"/api/projects/{pid}/generate", json={})
    assert r.status_code == 202, r.text
    run_id = r.json()["runId"]
    for _ in range(240):
        st = client.get(f"/api/projects/{pid}/runs/{run_id}").json()
        if st["state"] in ("COMPLETED", "FAILED"):
            break
        time.sleep(0.5)
    assert st["state"] == "COMPLETED", st
    return pid, _selected(client, pid)


def _selected(client, pid):
    """Full record of the selected iteration (list endpoint is summary-only)."""
    its = client.get(f"/api/projects/{pid}/iterations").json()
    if isinstance(its, dict):
        its = its["iterations"]
    sel = next((i for i in its if i.get("selected")), its[-1])
    return client.get(
        f"/api/projects/{pid}/iterations/{sel['iterationId']}").json()


def test_live_check_at_current_position_passes(client, generated):
    pid, sel = generated
    p = sel["placements"][0]
    r = client.get(f"/api/projects/{pid}/placements/check",
                   params={"x": p["position"][0], "y": p["position"][1],
                           "kind": p["kind"], "speciesId": p["speciesId"],
                           "excludeId": p["id"]})
    assert r.status_code == 200, r.text
    body = r.json()
    # a validated plan's own position must not report violations
    assert body["verdict"] in ("OK", "UNVERIFIED")
    assert body["insideAllowed"] is True
    assert body["checks"], "annotation needs per-rule checks"
    c0 = body["checks"][0]
    for key in ("ruleId", "result", "requiredM", "source"):
        assert key in c0, f"check missing {key}"


def test_live_check_far_outside_reports_violation(client, generated):
    pid, sel = generated
    r = client.get(f"/api/projects/{pid}/placements/check",
                   params={"x": 10_000.0, "y": 10_000.0, "kind": "TREE",
                           "speciesId": "tree_linden"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["insideAllowed"] is False
    assert body["verdict"] == "VIOLATIONS"
    fail_ids = {c["ruleId"] for c in body["checks"] if c["result"] == "FAIL"}
    assert "PLANTING_INSIDE_SITE_BOUNDARY" in fail_ids


def test_move_persists_and_revalidates(client, generated):
    pid, sel = generated
    p = sel["placements"][0]
    old = list(p["position"])
    # nudge 0.5 m — far below any clearance, must stay valid
    new_pos = [old[0] + 0.5, old[1]]
    r = client.post(f"/api/projects/{pid}/placements/{p['id']}/move",
                    json={"position": new_pos})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["placement"]["position"] == [round(new_pos[0], 3),
                                             round(new_pos[1], 3)]
    assert body["placement"]["manuallyMoved"] is True
    assert "regulatory" in body and body["regulatory"] is not None

    # persisted: re-read the iteration
    sel2 = _selected(client, pid)
    q = next(x for x in sel2["placements"] if x["id"] == p["id"])
    assert q["position"] == body["placement"]["position"]
    assert q.get("manuallyMoved") is True
    edits = sel2.get("manualEdits", [])
    assert edits and edits[-1]["placementId"] == p["id"]
    assert edits[-1]["from"] == old


def test_move_into_violation_is_reported_not_hidden(client, generated):
    pid, sel = generated
    p = sel["placements"][0]
    # far outside the boundary — validation must FAIL loudly, move still saved
    r = client.post(f"/api/projects/{pid}/placements/{p['id']}/move",
                    json={"position": [10_000.0, 10_000.0]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["validation"]["valid"] is False
    assert body["validation"]["hardViolations"]
    assert any(v.get("plantingId") == p["id"]
               for v in body["validation"]["hardViolations"])


def test_move_unknown_placement_404(client, generated):
    pid, _ = generated
    r = client.post(f"/api/projects/{pid}/placements/nope/move",
                    json={"position": [0, 0]})
    assert r.status_code == 404
