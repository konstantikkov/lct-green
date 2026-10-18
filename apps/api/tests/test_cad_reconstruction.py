import json
import ezdxf
from app.models.ir import LayerClassification
from app.services.dxf_parser import parse_dxf
from app.services.local_render import scene_data


def drawing(tmp_path):
    cad=ezdxf.new();cad.units=6;cad.layers.new('arbitrary-1');return cad


def parse(cad,tmp_path):
    path=tmp_path/'scene.dxf';cad.saveas(path);doc=parse_dxf(path,'test','scene.dxf')
    for layer in doc.layers:
        if layer.name=='arbitrary-1':layer.classification=LayerClassification(category='BUILDING',source='user',requiresReview=False)
    return doc


def test_height_from_cad_thickness_and_courtyard(tmp_path):
    cad=drawing(tmp_path)
    hatch=cad.modelspace().add_hatch(dxfattribs={'layer':'arbitrary-1'})
    hatch.paths.add_polyline_path([(0,0),(20,0),(20,20),(0,20)],is_closed=True)
    hatch.paths.add_polyline_path([(5,5),(10,5),(10,10),(5,10)],is_closed=True)
    cad.modelspace().add_text('H=18',dxfattribs={'insert':(1,1),'layer':'arbitrary-1'})
    doc=parse(cad,tmp_path);before=doc.model_dump();data=scene_data(doc,{'placements':[]})
    building=data['surfaces'][0]
    assert building['height']==18 and building['heightSource']=='CAD height label'
    assert len(building['outlines'])==2
    assert doc.model_dump()==before


def test_actual_thickness_beats_fallback_and_floor_count(tmp_path):
    cad=drawing(tmp_path)
    cad.modelspace().add_lwpolyline([(0,0),(10,0),(10,10),(0,10)],close=True,dxfattribs={'layer':'arbitrary-1','thickness':7})
    cad.modelspace().add_text('5КЖ',dxfattribs={'insert':(2,2),'layer':'arbitrary-1'})
    doc=parse(cad,tmp_path)
    data=scene_data(doc,{'placements':[]},{'buildingHeightM':30})
    assert data['surfaces'][0]['height']==7
    assert data['surfaces'][0]['heightSource']=='CAD thickness'


def test_floor_count_and_explicit_unknown_height(tmp_path):
    cad=drawing(tmp_path)
    for x in [0,20]:cad.modelspace().add_lwpolyline([(x,0),(x+10,0),(x+10,10),(x,10)],close=True,dxfattribs={'layer':'arbitrary-1'})
    cad.modelspace().add_text('5КЖ',dxfattribs={'insert':(2,2),'layer':'arbitrary-1'})
    data=scene_data(parse(cad,tmp_path),{'placements':[]},{'buildingHeightM':6})
    assert [s['height'] for s in data['surfaces']]==[15,6]
    assert 'assumed' in data['surfaces'][0]['heightSource']
    assert 'fallback' in data['surfaces'][1]['heightSource']


def test_terrain_faces_keep_z_in_meters(tmp_path):
    cad=drawing(tmp_path);cad.units=4
    cad.modelspace().add_3dface([(0,0,100000),(10000,0,101000),(0,10000,102000)],dxfattribs={'layer':'0'})
    doc=parse(cad,tmp_path);data=scene_data(doc,{'placements':[]})
    face=data['terrainFaces'][0]
    assert data['elevationOriginM']==101
    assert face['vertices']==[(0,0,-1),(10,0,0),(0,10,1)]
    assert not data['surfaces']


def test_preview_settings_are_bounded_and_default_fences_off():
    import pytest
    from pydantic import ValidationError
    from app.main import ReconstructionSettings
    assert not ReconstructionSettings().showFences
    for height in [float('nan'),-1,500]:
        with pytest.raises(ValidationError):ReconstructionSettings(buildingHeightM=height)


def test_excavation_dashes_become_building_only_when_labelled(tmp_path):
    cad=drawing(tmp_path)
    for y in [0,10]:
        for x in range(10):cad.modelspace().add_line((x,y),(x+.6,y),dxfattribs={'layer':'arbitrary-1'})
    for x in [0,9.6]:cad.modelspace().add_line((x,0),(x,10),dxfattribs={'layer':'arbitrary-1'})
    cad.modelspace().add_text('КОТЛОВАН',dxfattribs={'insert':(2,2),'layer':'arbitrary-1'})
    doc=parse(cad,tmp_path);before=doc.model_dump()
    scene=scene_data(doc,{'placements':[]})
    assert len(scene['surfaces'])==1
    assert scene['surfaces'][0]['category']=='BUILDING'
    assert scene['surfaces'][0]['interpretation'].startswith('excavation-as-building')
    assert scene['surfaces'][0]['height']==9
    assert not scene_data(doc,{'placements':[]},{'excavationsAsBuildings':False})['surfaces']
    assert doc.model_dump()==before


def test_proposed_lawn_surfaces_match_stored_polygon_holes(tmp_path):
    cad=drawing(tmp_path);doc=parse(cad,tmp_path)
    scene=scene_data(doc,{'placements':[],'lawn':{'polygons':[{'shell':[0,0,10,0,10,10,0,10],'holes':[[2,2,4,2,4,4,2,4]]}]}})
    from shapely.geometry import Polygon
    lawns=[s for s in scene['surfaces'] if s.get('proposed')]
    assert len(lawns)==1
    assert sum(Polygon(t).area for t in lawns[0]['triangles'])==96


def test_excavation_dash_repair_does_not_cut_across_corner(tmp_path):
    cad=drawing(tmp_path)
    corners=[(0,0),(10,0),(10,10),(0,10),(0,0)]
    spans=[(0,.3),(.8,1.5),(2,2.7),(3.2,3.9),(4.4,5.1),(5.6,6.3),(6.8,7.5),(8,8.7),(9.2,9.5)]
    # Last dash reaches the corner; two nearby dash ends on adjacent sides
    # must connect forward along their own side rather than to each other.
    spans[-1]=(9.2,10)
    for a,b in zip(corners,corners[1:]):
        dx,dy=(b[0]-a[0])/10,(b[1]-a[1])/10
        for lo,hi in spans:cad.modelspace().add_line((a[0]+dx*lo,a[1]+dy*lo),(a[0]+dx*hi,a[1]+dy*hi),dxfattribs={'layer':'arbitrary-1'})
    cad.modelspace().add_text('КОТЛОВАН',dxfattribs={'insert':(5,5),'layer':'arbitrary-1'})
    scene=scene_data(parse(cad,tmp_path),{'placements':[]})
    from shapely.geometry import Polygon
    assert len(scene['surfaces'])==1
    assert abs(sum(Polygon(t).area for t in scene['surfaces'][0]['triangles'])-100)<1e-6


def test_legacy_iteration_lawn_uses_api_placement_coordinates(monkeypatch):
    import app.main as main
    from app.services import lawn
    record={'iterationId':'old','placements':[{'id':'t','kind':'TREE','speciesId':'tree_linden','position':[12,34],'crownRadiusM':2}]}
    monkeypatch.setattr(main,'_get_rt',lambda pid:object())
    monkeypatch.setattr(main,'_find_iteration',lambda pid,i:record)
    monkeypatch.setattr(main.pipeline,'ensure_constraints',lambda rt:object())
    monkeypatch.setattr(main.pipeline,'ensure_geometry',lambda rt:object())
    def recommend(constraints,index,placements):
        assert placements[0].x==12 and placements[0].y==34
        return None,{'areaM2':10,'polygons':[]}
    monkeypatch.setattr(lawn,'recommend_lawn',recommend)
    result=main.get_iteration('p','old')
    assert result['lawn']['areaM2']==10
    assert 'lawn' not in record
