from pathlib import Path
import json
from zipfile import ZipFile
import ezdxf
from app.services.dxf_parser import parse_dxf
from app.services.local_render import write_bundle
from app.main import _render_selection


def test_arbitrary_layers_nested_blocks_and_large_block(tmp_path):
    cad = ezdxf.new()
    cad.units = 6
    cad.layers.new('arbitrary-123', dxfattribs={'color': 1})
    cad.layers.new('empty-layer')
    inner = cad.blocks.new('inner')
    for i in range(450):
        inner.add_line((i, 0), (i, 1), dxfattribs={'color': 0})
    outer = cad.blocks.new('outer')
    outer.add_blockref('inner', (10, 0), dxfattribs={'color': 0})
    cad.modelspace().add_blockref('outer', (100, 50), dxfattribs={'layer': 'arbitrary-123', 'color': 3})
    src = tmp_path / 'nested.dxf'
    cad.saveas(src)
    doc = parse_dxf(src, 'test', src.name)
    lines = [e for e in doc.entities if e.type == 'LINE']
    assert len(lines) == 450
    assert lines[0].geometry['points'][0] == (110.0, 50.0)
    assert {e.layer for e in lines} == {'arbitrary-123'}
    assert {e.attributes['color'] for e in lines} == {'#00ff00'}
    assert 'empty-layer' in {l.name for l in doc.layers}
    assert _render_selection(doc) == doc.entities


def test_bundle_preserves_planting_coordinates_and_offline_script(tmp_path):
    cad = ezdxf.new(); cad.units = 6
    src = tmp_path / 'source.dxf'; cad.saveas(src)
    doc = parse_dxf(src, 'test', src.name)
    placements = [{'id':'t1','kind':'TREE','position':[12,34],'crownRadiusM':2}]
    dst = tmp_path / 'render.zip'
    write_bundle(dst, doc, {'placements': placements}, src)
    with ZipFile(dst) as z:
        data = json.loads(z.read('scene.json'))
        assert data['placements'] == placements
        assert {'plan.dxf','render.py','README.txt','scene.json'} <= set(z.namelist())
        assert b"scene.render.engine = 'CYCLES'" in z.read('render.py')
        assert data['assumptions']


def test_source_preview_and_user_mapping(tmp_path, monkeypatch):
    from app import config, pipeline, store
    from app.main import app
    from fastapi.testclient import TestClient
    monkeypatch.setattr(config, 'DATA_DIR', tmp_path / 'data')
    monkeypatch.setattr(config, 'OUTPUT_DIR', tmp_path / 'outputs')
    config.ensure_dirs()
    cad = ezdxf.new(); cad.units = 6
    cad.layers.new('X-991')
    cad.modelspace().add_text('Original text', dxfattribs={'layer':'X-991','height':1})
    src = tmp_path / 'source.dxf'; cad.saveas(src)
    with TestClient(app) as c:
        with src.open('rb') as f:
            r = c.post('/api/projects', files={'file':('site.dxf',f,'application/dxf')})
        assert r.status_code == 201, r.text
        pid = r.json()['projectId']
        before = src.read_bytes()
        r = c.get(f'/api/projects/{pid}/cad-preview.svg')
        assert r.status_code == 200, r.text
        assert '<svg' in r.text
        r = c.put(f'/api/projects/{pid}/layer-mapping', json={'mappings':{'X-991':'BUILDING','absent':'ROAD'}})
        assert r.status_code == 422
        assert pipeline.get_runtime(pid).doc.layer_map()['X-991'].classification.source != 'user'
        r = c.put(f'/api/projects/{pid}/layer-mapping', json={'mappings':{'X-991':'BUILDING'}})
        assert r.status_code == 200
        assert r.json()['regenerateRequired']
        r = c.post(f'/api/projects/{pid}/refresh-import')
        assert r.status_code == 200, r.text
        assert pipeline.get_runtime(pid).doc.layer_map()['X-991'].classification.source == 'user'
        assert src.read_bytes() == before
        assert c.get(f'/api/projects/{pid}/export/render-bundle').status_code == 409


def test_render_job_worker_success_failure_and_path(tmp_path, monkeypatch):
    import pytest
    from app import config
    from app.services import render_jobs
    monkeypatch.setattr(config, 'OUTPUT_DIR', tmp_path)
    with pytest.raises(ValueError):
        render_jobs.job_dir('p', '../outside')
    jid = 'a'*32
    root = render_jobs.job_dir('p',jid); root.mkdir(parents=True)
    def run(args, **kwargs):
        (root/'render.png').write_bytes(b'PNG')
        return type('Done', (), {'returncode':0})()
    monkeypatch.setattr(render_jobs.subprocess, 'run', run)
    render_jobs._slots.acquire()
    render_jobs._active.add(jid)
    render_jobs._run('blender', root, jid, {'samples':16})
    assert render_jobs.status('p',jid)['state'] == 'COMPLETED'
    (root/'render.png').unlink()
    def fail(*args, **kwargs):
        raise RuntimeError('render failed')
    monkeypatch.setattr(render_jobs.subprocess, 'run', fail)
    render_jobs._slots.acquire()
    render_jobs._active.add(jid)
    render_jobs._run('blender', root, jid, {})
    assert render_jobs.status('p',jid)['state'] == 'FAILED'
    assert jid not in render_jobs._active


def test_render_surfaces_keep_hatch_holes_and_disconnected_shells(tmp_path):
    from app.models.ir import IREntity, LayerInfo, LayerClassification
    from app.services.local_render import scene_data
    from shapely.geometry import Polygon
    cad = ezdxf.new(); src = tmp_path/'empty.dxf';cad.saveas(src)
    doc = parse_dxf(src, 'p','empty.dxf')
    doc.layers.append(LayerInfo(name='custom', classification=LayerClassification(category='GREEN_AREA',source='user')))
    doc.entities=[IREntity(id='h',type='HATCH',layer='custom',kind='polygon', geometry={
        'rings': [[(0,0),(10,0),(10,10),(0,10)],[(2,2),(4,2),(4,4),(2,4)],[(20,0),(22,0),(22,2),(20,2)]]})]
    data=scene_data(doc, {'placements':[]})
    tris=data['surfaces'][0]['triangles']
    assert sum(Polygon(t).area for t in tris) == 100
    hole=Polygon([(2,2),(4,2),(4,4),(2,4)])
    assert all(Polygon(t).intersection(hole).area == 0 for t in tris)


def test_renderer_reconstructs_building_from_line_entities(tmp_path):
    from app.models.ir import IREntity, LayerInfo, LayerClassification
    from app.services.local_render import scene_data
    from shapely.geometry import Polygon
    cad=ezdxf.new();src=tmp_path/'lines.dxf';cad.saveas(src)
    doc=parse_dxf(src,'p','lines.dxf')
    doc.layers=[LayerInfo(name='random-name',classification=LayerClassification(category='BUILDING',source='user'))]
    pts=[(0,0),(4,0),(4,3),(0,3),(0,0)]
    doc.entities=[IREntity(id=str(i),layer='random-name',type='LINE',kind='polyline',geometry={'points':[a,b]}) for i,(a,b) in enumerate(zip(pts,pts[1:]))]
    data=scene_data(doc,{'placements':[]})
    assert sum(Polygon(t).area for s in data['surfaces'] for t in s['triangles']) == 12
