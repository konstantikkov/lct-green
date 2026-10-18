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
