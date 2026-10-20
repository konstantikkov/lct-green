import json
import math
from types import SimpleNamespace
from pathlib import Path

from fastapi.testclient import TestClient
from shapely.geometry import LineString, Point, Polygon


def rect(x0, y0, x1, y1):
    return [[(x0, y0), (x1, y0), (x1, y1)], [(x0, y0), (x1, y1), (x0, y1)]]


def site():
    """120×80 m: a 3 m path along the south edge, lawns, one building."""
    return {'extents': {'min': [0, 0], 'max': [120, 80]}, 'seed': 42,
            'surfaces': [{'category': 'SIDEWALK', 'layer': 'P', 'triangles': rect(0, 5, 120, 8)},
                         {'category': 'GREEN_AREA', 'layer': 'L', 'triangles': rect(0, 8, 120, 80)},
                         {'category': 'BUILDING', 'layer': 'B', 'triangles': rect(40, 30, 80, 60), 'height': 12}],
            'placements': [{'id': 't1', 'kind': 'TREE', 'position': (20, 40), 'crownRadiusM': 3},
                           {'id': 's1', 'kind': 'SHRUB', 'position': (100, 40), 'crownRadiusM': 1.5}]}


def test_tour_points_stand_on_paths_and_lawns_with_line_of_sight_links():
    from app.services.panorama_tour import tour_points
    points = tour_points(site(), max_points=8)
    assert 4 <= len(points) <= 8
    building = Polygon([(40, 30), (80, 30), (80, 60), (40, 60)])
    path = Polygon([(0, 5), (120, 5), (120, 8), (0, 8)])
    xy = [(p['x'], p['y']) for p in points]
    assert any(path.contains(Point(p)) for p in xy)
    for p in xy:
        assert building.buffer(1.4).disjoint(Point(p))
        assert math.dist(p, (20, 40)) > .8 and math.dist(p, (100, 40)) > 1.7
    for i, p in enumerate(points):
        assert 0 <= p['heading'] < 360
        for j in p['links']:
            assert i in points[j]['links']
            assert not LineString([xy[i], xy[j]]).intersects(building)
    # Every point can be reached: the link graph is connected.
    seen, stack = {0}, [0]
    while stack:
        for j in points[stack.pop()]['links']:
            if j not in seen:
                seen.add(j); stack.append(j)
    assert seen == set(range(len(points)))


def test_tour_without_walkable_surfaces_uses_the_site_minus_roads():
    from app.services.panorama_tour import tour_points
    scene = {'extents': {'min': [0, 0], 'max': [60, 40]}, 'placements': [],
             'surfaces': [{'category': 'ROAD', 'layer': 'R', 'triangles': rect(0, 0, 60, 10)}]}
    points = tour_points(scene, max_points=4)
    assert points and all(p['y'] > 10 for p in points)


def _patch(monkeypatch, submitted):
    from app import main
    from app.services import local_render, render_jobs
    extents = SimpleNamespace(min=(0, 0), max=(120, 80))
    monkeypatch.setattr(main, '_get_rt', lambda pid: SimpleNamespace(doc=SimpleNamespace(extents=extents)))
    monkeypatch.setattr(main, '_selected_iteration', lambda pid: {'iterationId': 'it-1', 'placements': []})
    monkeypatch.setattr(local_render, 'scene_data', lambda doc, rec, options=None: {**site(), 'options': options})
    monkeypatch.setattr(render_jobs, 'submit', lambda pid, data, settings, **kw: submitted.append((data, settings, kw)) or 'f'*32)
    return main


def test_single_point_and_tour_requests_become_one_cycles_job(monkeypatch, tmp_path):
    from app import config
    monkeypatch.setattr(config, 'OUTPUT_DIR', tmp_path)
    submitted = []
    main = _patch(monkeypatch, submitted)
    with TestClient(main.app) as c:
        r = c.post('/api/projects/p/panoramas', json={'x': 32, 'y': 20, 'heading': -90,
                                                      'reconstruction': {'buildingHeightM': 12, 'showFences': True}})
        assert r.status_code == 202, r.text
        [item] = r.json()
        assert item['id'] == 'f'*32+'-0' and item['state'] == 'QUEUED' and item['heading'] == 270
        assert item['imageUrl'] == f'/api/projects/p/panoramas/{"f"*32}/0/image'
        data, settings, kw = submitted[-1]
        assert settings['view'] == 'panorama' and (settings['width'], settings['height']) == (4096, 2048)
        assert settings['grass_radius'] == 30 and kw['points'] == [{'x': 32, 'y': 20, 'heading': 270}]
        assert kw['meta']['tour'] is False and data['options']['buildingHeightM'] == 12

        r = c.post('/api/projects/p/panorama-tour', json={'maxPoints': 6, 'quality': 'ultra', 'grass': False})
        assert r.status_code == 202, r.text
        items = r.json()
        settings, kw = submitted[-1][1], submitted[-1][2]
        assert (settings['width'], settings['grass_radius']) == (8192, 0)
        assert len(items) == len(kw['points']) <= 6 and kw['meta']['tour']
        assert all(link.startswith('f'*32+'-') for i in items for link in i['links'])

        r = c.post('/api/projects/p/panoramas', json={'x': 5000, 'y': 20})
        assert r.status_code == 422 and r.json()['detail']['code'] == 'PANORAMA_OUTSIDE_SITE'


def test_tour_progress_listing_images_and_delete(tmp_path, monkeypatch):
    from app import config, main
    from app.services import render_jobs
    monkeypatch.setattr(config, 'OUTPUT_DIR', tmp_path)
    monkeypatch.setattr(main, '_get_rt', lambda pid: SimpleNamespace())
    tour, photo = 'a'*32, 'c'*32
    points = [{'x': i*10, 'y': 0, 'heading': 90, 'links': [j for j in (i-1, i+1) if 0 <= j < 3]} for i in range(3)]
    for jid, meta in ((tour, {'kind': 'panorama', 'tour': True, 'points': points, 'eyeHeight': 1.6, 'quality': 'high', 'createdAt': 2}),
                      (photo, {'kind': 'photo', 'createdAt': 3})):
        root = render_jobs.job_dir('p', jid); root.mkdir(parents=True)
        (root/'job.json').write_text(json.dumps(meta))
    root = render_jobs.job_dir('p', tour)
    (root/'points.json').write_text('[]')
    (root/'status.json').write_text(json.dumps({'state': 'RENDERING'}))
    (root/'tour.progress').write_text('1/3')
    (root/'pano_000.jpg').write_bytes(b'JPEG')
    (root/'pano_000.manifest.json').write_text('{"panorama": {"eye": [0, 0, 1.6]}}')
    (root/'render.log').write_text('Sample 192/192\nPANORAMA 2/3\nFra:1 | Remaining:00:30.00 | Sample 96/192\n')
    monkeypatch.setattr(render_jobs, '_active', {tour})
    with TestClient(main.app) as c:
        items = c.get('/api/projects/p/panoramas').json()
        assert [i['state'] for i in items] == ['COMPLETED', 'RENDERING', 'QUEUED']
        assert items[1]['progressPercent'] == 50 and items[0]['links'] == [tour+'-1']
        # Remaining: this point's 30 s plus one more point at a similar pace.
        assert items[1]['remainingSeconds'] >= 30
        assert c.get(f'/api/projects/p/panoramas/{tour}/0/image').content == b'JPEG'
        assert c.get(f'/api/projects/p/panoramas/{tour}/0/manifest').json()['panorama']['eye'][2] == 1.6
        assert c.get(f'/api/projects/p/panoramas/{tour}/1/image').status_code == 404
        assert c.delete(f'/api/projects/p/panoramas/{tour}').status_code == 409
        (root/'status.json').write_text(json.dumps({'state': 'COMPLETED'}))
        monkeypatch.setattr(render_jobs, '_active', set())
        assert c.delete(f'/api/projects/p/panoramas/{tour}').status_code == 204
        assert c.get('/api/projects/p/panoramas').json() == []


def test_portable_bundle_ships_the_lawn_module():
    from pathlib import Path
    from app.services import local_render
    assert '"blender_grass.py"' in Path(local_render.__file__).read_text(encoding='utf-8')
    assert Path(local_render.__file__).with_name('blender_grass.py').is_file()


def test_age_reaches_blender_without_changing_the_plan(monkeypatch, tmp_path):
    from app import config
    monkeypatch.setattr(config, 'OUTPUT_DIR', tmp_path)
    submitted = []
    main = _patch(monkeypatch, submitted)
    with TestClient(main.app) as c:
        r = c.post('/api/projects/p/panoramas', json={'x': 32, 'y': 20, 'ageYears': 15})
        assert r.status_code == 202, r.text
        assert r.json()[0]['ageYears'] == 15
        data, settings, kw = submitted[-1]
        assert settings['age_years'] == 15 and kw['meta']['ageYears'] == 15
        r = c.post('/api/projects/p/panorama-tour', json={'maxPoints': 4})
        assert submitted[-1][1]['age_years'] is None and r.json()[0]['ageYears'] is None
        assert c.post('/api/projects/p/panoramas', json={'x': 32, 'y': 20, 'ageYears': -1}).status_code == 422


def test_scene_carries_mature_placements_and_growth_parameters():
    from app.services.local_render import scene_data
    doc = SimpleNamespace(layer_map=lambda: {}, entities=[],
                          extents=SimpleNamespace(model_dump=lambda: {'min': [0, 0], 'max': [10, 10]}))
    import app.services.source_reconstruction as sr
    rec = {'placements': [{'id': 't', 'kind': 'TREE', 'speciesId': 'tree_linden', 'position': [5, 5], 'crownRadiusM': 3.5}]}
    orig = (sr.excavation_buildings, sr.enrich_surfaces)
    try:
        sr.excavation_buildings, sr.enrich_surfaces = (lambda *a: []), (lambda *a: {})
        data = scene_data(doc, rec)
    finally:
        sr.excavation_buildings, sr.enrich_surfaces = orig
    assert data['placements'][0]['crownRadiusM'] == 3.5
    assert data['species']['tree_linden']['growth']['yearsToMature'] > 0


def test_season_reaches_blender_and_winter_has_no_lawn_blades(monkeypatch, tmp_path):
    from app import config
    monkeypatch.setattr(config, 'OUTPUT_DIR', tmp_path)
    submitted = []
    main = _patch(monkeypatch, submitted)
    with TestClient(main.app) as c:
        r = c.post('/api/projects/p/panoramas', json={'x': 32, 'y': 20, 'season': 'winter', 'grass': True})
        assert r.status_code == 202 and r.json()[0]['season'] == 'winter'
        settings = submitted[-1][1]
        assert settings['season'] == 'winter' and settings['grass_radius'] == 0
        c.post('/api/projects/p/panorama-tour', json={'maxPoints': 4, 'season': 'autumn'})
        assert submitted[-1][1]['season'] == 'autumn' and submitted[-1][1]['grass_radius'] == 30
        c.post('/api/projects/p/panoramas', json={'x': 32, 'y': 20})
        assert submitted[-1][1]['season'] == 'summer'
        assert c.post('/api/projects/p/panoramas', json={'x': 32, 'y': 20, 'season': 'monsoon'}).status_code == 422


def test_winter_drops_only_deciduous_leaves(monkeypatch):
    import importlib
    import sys as _sys
    monkeypatch.setitem(_sys.modules, 'bpy', SimpleNamespace())
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'app' / 'services'))
    season = importlib.import_module('blender_season')
    from app.services.rules import load_species
    catalog = load_species()
    # the whole catalog is deciduous — larch included
    assert {s['id'] for s in catalog if s['foliage'] == 'deciduous'} == {s['id'] for s in catalog}
    larch = next(s for s in catalog if s['id'] == 'tree_larch')
    assert season.is_leafless(larch, 'winter') and not season.is_leafless(larch, 'autumn')
    assert not season.is_leafless({'foliage': 'evergreen'}, 'winter')
    assert not season.is_leafless(None, 'winter')
    assert set(season.BLOOM) == {'Syringa', 'Philadelphus'}
    assert {season.genus(s) for s in catalog} <= set(season.AUTUMN_TONE)


def test_bundle_ships_growth_and_season_modules(tmp_path):
    import zipfile
    from app.services import local_render
    doc = SimpleNamespace()
    dxf = tmp_path / 'plan.dxf'; dxf.write_text('0\nEOF\n')
    orig = local_render.scene_data
    local_render.scene_data = lambda d, i, o=None: {}
    try:
        local_render.write_bundle(tmp_path / 'b.zip', doc, {}, dxf)
    finally:
        local_render.scene_data = orig
    names = zipfile.ZipFile(tmp_path / 'b.zip').namelist()
    assert {'growth.py', 'blender_season.py', 'render.py'} <= set(names)
