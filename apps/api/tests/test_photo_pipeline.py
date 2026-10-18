import base64
import io
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from PIL import Image
from fastapi.testclient import TestClient
from app import config
from app.main import app
from app.services import photo_jobs


def png(size=(128,96)):
    out=io.BytesIO();Image.new('RGB',size,(120,150,90)).save(out,format='PNG')
    return 'data:image/png;base64,'+base64.b64encode(out.getvalue()).decode()


def payload():
    return dict(image=png(),depth=png(),semantic=png(),camera=dict(position=[1,2,3],target=[0,0,0],fov=50,aspect=4/3),lighting='evening')


@pytest.fixture
def photo_api(tmp_path,monkeypatch):
    import app.main as main
    monkeypatch.setattr(main,'_get_rt',lambda pid:SimpleNamespace())
    monkeypatch.setattr(config,'OUTPUT_DIR',tmp_path)
    return TestClient(app)


def test_capture_validation():
    assert photo_jobs.decode_png(png()).size==(128,96)
    for bad in ['https://example.com/a.png','data:image/png;base64,broken!',png((32,32)),png((1601,128))]:
        with pytest.raises(ValueError):photo_jobs.decode_png(bad)


def test_photo_input_validation_before_worker(photo_api,monkeypatch):
    calls=[]
    monkeypatch.setattr(photo_jobs,'submit',lambda *a:calls.append(a))
    for changes in [dict(camera=dict(position=[1,2,3],target=[0,0,0],fov=0,aspect=1)),
                    dict(lighting='anything'),dict(strength=.99),dict(seed=-1),dict(resolution=4096)]:
        assert photo_api.post('/api/projects/test/photos',json={**payload(),**changes}).status_code==422
    assert not calls


def test_job_submission_and_restart_detection(tmp_path,monkeypatch):
    monkeypatch.setattr(config,'OUTPUT_DIR',tmp_path)
    monkeypatch.setattr(photo_jobs,'capabilities',lambda:{'available':True})
    scheduled=[]
    monkeypatch.setattr(photo_jobs._pool,'submit',lambda *args:scheduled.append(args))
    jid=photo_jobs.submit('test',payload())
    try:
        folder=photo_jobs.root('test',jid)
        assert Image.open(folder/'source.png').size==(128,96)
        assert Image.open(folder/'depth.png').size==(128,96)
        settings=json.loads((folder/'settings.json').read_text())
        assert 'image' not in settings and settings['camera']==payload()['camera']
        assert scheduled and photo_jobs.status('test',jid)['state']=='QUEUED'
        photo_jobs._active.discard(jid)
        assert photo_jobs.status('test',jid)['state']=='FAILED'
    finally:
        photo_jobs._active.discard(jid);photo_jobs._slots.release()


@pytest.mark.parametrize("field",["depth","semantic"])
def test_dimensions_must_match(monkeypatch,field):
    monkeypatch.setattr(photo_jobs,'capabilities',lambda:{'available':True})
    with pytest.raises(ValueError,match='identical'):
        photo_jobs.submit('test',{**payload(),field:png((128,128))})


def test_missing_model_is_explicit(photo_api,monkeypatch):
    monkeypatch.setattr(photo_jobs,'capabilities',lambda:{'available':False})
    r=photo_api.post('/api/projects/test/photos',json=payload())
    assert r.status_code==503 and r.json()['detail']['code']=='PHOTO_MODEL_UNAVAILABLE'


def test_assets_are_not_arbitrary_paths_and_require_completion(photo_api,tmp_path):
    jid='a'*32;folder=photo_jobs.root('test',jid);folder.mkdir(parents=True)
    photo_jobs.write_status(folder,{'state':'FAILED'})
    Image.new('RGB',(128,96)).save(folder/'source.png')
    assert photo_api.get(f'/api/projects/test/photos/{jid}/source').status_code==200
    assert photo_api.get(f'/api/projects/test/photos/{jid}/image').status_code==409
    assert photo_api.get('/api/projects/test/photos/not-a-job/source').status_code==404
    assert photo_api.get(f'/api/projects/test/photos/{jid}/inference.log').status_code==422


def test_worker_is_offline_and_records_failures(tmp_path,monkeypatch):
    folder=tmp_path/'job';folder.mkdir();jid='b'*32
    photo_jobs._slots.acquire();photo_jobs._active.add(jid)
    calls=[]
    monkeypatch.setattr(photo_jobs,'configuration',lambda:(Path('/python'),Path('/model'),Path('/control')))
    def run(args,**kwargs):
        calls.append((args,kwargs));return SimpleNamespace(returncode=1)
    monkeypatch.setattr(photo_jobs.subprocess,'run',run)
    photo_jobs._run(folder,jid)
    assert json.loads((folder/'status.json').read_text())['state']=='FAILED'
    assert calls[0][1]['env']['HF_HUB_OFFLINE']=='1'
    assert calls[0][1]['env']['TRANSFORMERS_OFFLINE']=='1'
    assert jid not in photo_jobs._active


def test_capabilities_require_complete_local_weights(tmp_path,monkeypatch):
    python=tmp_path/'python';model=tmp_path/'model';control=tmp_path/'control'
    monkeypatch.setattr(photo_jobs,'configuration',lambda:(python,model,control))
    for path in [python,model/'model_index.json',control/'config.json']:
        path.parent.mkdir(parents=True,exist_ok=True);path.touch()
    assert not photo_jobs.capabilities()['available']
    for path in [model/'unet/diffusion_pytorch_model.safetensors',model/'text_encoder/model.safetensors',model/'vae/diffusion_pytorch_model.safetensors',control/'diffusion_pytorch_model.safetensors']:
        path.parent.mkdir(parents=True,exist_ok=True);path.touch()
    assert photo_jobs.capabilities()['available']


def test_photo_history_survives_api_restart(photo_api):
    jid='d'*32;folder=photo_jobs.root('test',jid);folder.mkdir(parents=True)
    photo_jobs.write_status(folder,{'state':'COMPLETED'})
    (folder.parent/'unrelated').mkdir()
    rows=photo_api.get('/api/projects/test/photos').json()
    assert len(rows)==1 and rows[0]['jobId']==jid and rows[0]['state']=='COMPLETED'
    assert rows[0]['sourceUrl'].endswith('/source')


def test_backdrop_preserves_source_objects_and_inside_plan():
    import numpy as np
    from app.services.photo_inference import prepare_backdrop
    rgb=np.full((96,128,3),160,dtype=np.uint8)
    labels=np.full_like(rgb,[120,120,70]);labels[:30]=[6,230,230]
    labels[30:65,10:30]=[4,200,3];labels[55:85,60:90]=[180,120,120]
    labels[65:90,95:120]=[140,140,140]
    depth=Image.new('L',(128,96),0)
    settings=dict(background='park',lighting='sunny',camera=dict(position=[0,-10,10],target=[0,0,0],fov=50,aspect=128/96),siteBounds=dict(min=[-10000,-10000],max=[10000,10000]))
    image,semantic,mask=prepare_backdrop(Image.fromarray(rgb),Image.fromarray(labels),depth,settings)
    protected=np.any(labels!=[6,230,230],axis=2)
    assert np.array_equal(np.array(image)[protected],rgb[protected])
    assert np.array_equal(np.array(semantic)[protected],labels[protected])
    assert not np.array(mask)[protected].any()
    assert np.array(mask)[:30].any()
    assert np.any(np.all(np.array(semantic)==[4,200,3],axis=2)[:30])


def test_backdrop_only_changes_offsite_unclassified_floor():
    import numpy as np
    from app.services.photo_inference import prepare_backdrop
    rgb=np.full((96,128,3),160,dtype=np.uint8)
    labels=np.full_like(rgb,[120,120,70]);labels[:30]=[6,230,230];labels[65:90,95:120]=[140,140,140]
    settings=dict(background='park',lighting='daylight',camera=dict(position=[0,-10,10],target=[0,0,0],fov=50,aspect=128/96),siteBounds=dict(min=[-1,-1],max=[1,1]))
    image,semantic,mask=prepare_backdrop(Image.fromarray(rgb),Image.fromarray(labels),Image.new('L',(128,96),0),settings)
    road=np.all(labels==[140,140,140],axis=2)
    assert np.array_equal(np.array(semantic)[road],labels[road])
    assert not np.array(mask)[road].any()
    assert np.any(np.all(np.array(semantic)==[4,250,7],axis=2))


def test_source_background_mode_can_be_unchanged():
    import numpy as np
    from app.services.photo_inference import prepare_backdrop
    rgb=np.full((96,128,3),160,dtype=np.uint8)
    labels=np.full_like(rgb,[120,120,70]);labels[:30]=[6,230,230]
    image,semantic,mask=prepare_backdrop(Image.fromarray(rgb),Image.fromarray(labels),Image.new('L',(128,96),0),dict(background='source',lighting='daylight'))
    assert np.array_equal(np.array(image),rgb)
    assert np.array_equal(np.array(semantic),labels)
    assert not np.array(mask).any()


def test_sunny_presentation_preserves_camera_and_bounds_in_job(photo_api,monkeypatch):
    captured=[]
    def submit(pid,settings):
        captured.append(settings);return 'e'*32
    monkeypatch.setattr(photo_jobs,'submit',submit)
    data={**payload(),'lighting':'sunny','background':'park','siteBounds':{'min':[-20,-30],'max':[40,50]}}
    assert photo_api.post('/api/projects/test/photos',json=data).status_code==202
    assert json.loads(json.dumps(captured[0]['camera']))==data['camera']
    assert captured[0]['siteBounds']['min']==(-20.,-30.)
    assert captured[0]['background']=='park'
