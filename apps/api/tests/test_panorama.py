import json
from types import SimpleNamespace

from fastapi.testclient import TestClient


def _runtime():
    extents = SimpleNamespace(min=(0, 0), max=(70, 60))
    return SimpleNamespace(doc=SimpleNamespace(extents=extents))


def test_panorama_request_becomes_equirectangular_cycles_job(monkeypatch):
    from app import main
    from app.services import local_render, render_jobs
    submitted = []
    monkeypatch.setattr(main, '_get_rt', lambda pid: _runtime())
    monkeypatch.setattr(main, '_selected_iteration', lambda pid: {'iterationId': 'it-1', 'placements': []})
    monkeypatch.setattr(local_render, 'scene_data', lambda doc, rec, options=None: {'options': options})
    monkeypatch.setattr(render_jobs, 'submit', lambda pid, data, settings, **kw: submitted.append((data, settings, kw)) or 'f'*32)
    with TestClient(main.app) as c:
        r = c.post('/api/projects/p/panoramas', json={'x': 32, 'y': 20, 'heading': -90,
                                                      'reconstruction': {'buildingHeightM': 12, 'showFences': True}})
        assert r.status_code == 202, r.text
        body = r.json()
        assert body['imageUrl'] == f'/api/projects/p/renders/{"f"*32}/image'
        assert body['state'] == 'QUEUED' and body['iterationId'] == 'it-1'
        data, settings, kw = submitted[-1]
        assert settings['view'] == 'panorama' and settings['heading'] == 270
        assert (settings['width'], settings['height']) == (4096, 2048)
        assert settings['grass_radius'] == 30 and settings['target_x'] == 32
        assert kw['output'] == 'render.jpg' and kw['meta']['kind'] == 'panorama'
        assert data['options']['buildingHeightM'] == 12

        r = c.post('/api/projects/p/panoramas', json={'x': 1, 'y': 1, 'quality': 'ultra', 'grass': False})
        settings = submitted[-1][1]
        assert (settings['width'], settings['height'], settings['grass_radius']) == (8192, 4096, 0)

        r = c.post('/api/projects/p/panoramas', json={'x': 5000, 'y': 20})
        assert r.status_code == 422
        assert r.json()['detail']['code'] == 'PANORAMA_OUTSIDE_SITE'


def test_render_progress_and_panorama_listing(tmp_path, monkeypatch):
    from app import config
    from app.services import render_jobs
    monkeypatch.setattr(config, 'OUTPUT_DIR', tmp_path)
    older, newer, other = 'a'*32, 'b'*32, 'c'*32
    for jid, kind, created in ((older, 'panorama', 1), (newer, 'panorama', 2), (other, 'photo', 3)):
        root = render_jobs.job_dir('p', jid); root.mkdir(parents=True)
        (root/'job.json').write_text(json.dumps({'kind': kind, 'createdAt': created}))
        (root/'status.json').write_text(json.dumps({'state': 'COMPLETED'}))
    root = render_jobs.job_dir('p', newer)
    (root/'status.json').write_text(json.dumps({'state': 'RENDERING'}))
    (root/'render.log').write_text('Fra:1 | Remaining:01:05.20 | Sample 48/192\nFra:1 | Remaining:00:40.00 | Sample 96/192\n')
    monkeypatch.setattr(render_jobs, '_active', {newer})
    rows = render_jobs.listing('p', 'panorama')
    assert [r['jobId'] for r in rows] == [newer, older]
    assert rows[0]['progressPercent'] == 50 and rows[0]['remainingSeconds'] == 40
    (render_jobs.job_dir('p', older)/'render.jpg').write_bytes(b'JPEG')
    assert render_jobs.image('p', older).name == 'render.jpg'
    assert render_jobs.image('p', other).name == 'render.png'


def test_portable_bundle_ships_the_lawn_module():
    from pathlib import Path
    from app.services import local_render
    source = Path(local_render.__file__).read_text(encoding='utf-8')
    assert '"blender_grass.py"' in source
    assert Path(local_render.__file__).with_name('blender_grass.py').is_file()
