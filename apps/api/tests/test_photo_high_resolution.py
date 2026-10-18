import json
import time
import pytest
from app.services import photo_jobs
from app.services.photo_quality import quality_plan, dimensions, workload
from .test_photo_pipeline import payload


@pytest.mark.parametrize('resolution',[1024,1536,2048])
def test_high_resolution_api_keeps_plan_and_settings(photo_api,monkeypatch,resolution):
    captured=[]
    monkeypatch.setattr(photo_jobs,'submit',lambda pid,s:(captured.append(s) or 'e'*32))
    response=photo_api.post('/api/projects/test/photos',json={**payload(),'resolution':resolution})
    assert response.status_code==202
    assert captured[0]['resolution']==resolution
    assert json.loads(json.dumps(captured[0]['camera']))==payload()['camera']


# Reuse the API fixture without importing a duplicate set of tests.
from .test_photo_pipeline import photo_api


def test_high_resolution_budget_is_not_native_2k_diffusion():
    p=quality_plan({'resolution':2048})
    assert p['baseResolution']<=768 and p['detailResolution']==768
    assert p['detailStrength']<=.25
    assert dimensions((1280,720),2048)==(2048,1152)
    assert dimensions((720,1280),1536)==(864,1536)
    assert workload({'resolution':2048})==workload({'resolution':1024})


def test_hd_requires_local_upscaler_before_allocating_queue(monkeypatch,tmp_path):
    monkeypatch.setattr(photo_jobs,'capabilities',lambda:{'available':True})
    monkeypatch.setattr(photo_jobs,'configuration',lambda:(tmp_path/'python',tmp_path/'model',tmp_path/'control'))
    with pytest.raises(FileNotFoundError,match='Real-ESRGAN'):
        photo_jobs.submit('test',{**payload(),'resolution':2048})


def test_multi_stage_progress_does_not_reset_to_zero(tmp_path):
    (tmp_path/'job.json').write_text(json.dumps({'estimatedSeconds':1100,'startedAt':time.time()-700}))
    result=photo_jobs.progress_details(tmp_path,{'state':'GENERATING','step':2,'totalSteps':4,
        'generationStartedAt':time.time()-60,'progressStart':70,'progressEnd':88,'remainingStageSeconds':30})
    assert result['progressPercent']==79
    assert 90<=result['remainingSeconds']<=92
    assert photo_jobs.progress_details(tmp_path,{'state':'GENERATING','progressStart':88})['progressPercent']==88


def test_hd_estimate_is_budgeted_work_not_output_pixel_area(tmp_path,monkeypatch):
    from app import config
    monkeypatch.setattr(config,'OUTPUT_DIR',tmp_path)
    folder=tmp_path/'photo_jobs'/'test'/'job';folder.mkdir(parents=True)
    (folder/'manifest.json').write_text(json.dumps({'elapsedSeconds':400,'strength':.85,'width':512,'height':352}))
    hd=photo_jobs.estimate_seconds('test',{'resolution':2048})
    assert 800<hd<1200
    assert hd==photo_jobs.estimate_seconds('test',{'resolution':1024})


@pytest.mark.parametrize('resolution',[512,2048])
def test_worker_executes_detail_and_upscale_only_for_hd(tmp_path,monkeypatch,resolution):
    """Exercise worker orchestration without loading diffusion weights in unit tests."""
    import contextlib
    import sys
    from types import SimpleNamespace
    from PIL import Image
    from app.services import photo_inference,photo_quality,photo_styles
    folder=tmp_path/'job';folder.mkdir()
    model=tmp_path/'model';model.mkdir()
    settings={**payload(),'resolution':resolution}
    (folder/'settings.json').write_text(json.dumps(settings))
    for name in ['source.png','depth.png','semantic.png']:
        Image.new('RGB',(1280,720),(120,120,70)).save(folder/name)
    stages=[];calls=[]
    class Generator:
        def __init__(self,**kw):pass
        def manual_seed(self,seed):return self
    torch=SimpleNamespace(set_num_threads=lambda n:None,cuda=SimpleNamespace(is_available=lambda:False),
        float16='fp16',float32='fp32',Generator=Generator,inference_mode=contextlib.nullcontext)
    class Pipeline:
        scheduler=SimpleNamespace(config={})
        vae=SimpleNamespace(enable_slicing=lambda:None,enable_tiling=lambda:None)
        def to(self,device):pass
        def __call__(self,**kw):
            calls.append(kw)
            stages.append(json.loads((folder/'status.json').read_text())['progressStart'])
            self.num_timesteps=max(1,int(kw['num_inference_steps']*kw['strength']))
            kw['callback_on_step_end'](self,self.num_timesteps-1,0,{})
            return SimpleNamespace(images=[kw['image']])
    pipe=Pipeline()
    diffusion=SimpleNamespace(ControlNetModel=SimpleNamespace(from_pretrained=lambda *a,**k:object()),
        StableDiffusionControlNetImg2ImgPipeline=SimpleNamespace(from_pretrained=lambda *a,**k:pipe),
        UniPCMultistepScheduler=SimpleNamespace(from_config=lambda config:None))
    def upscale(image,size,*args):
        stages.append(json.loads((folder/'status.json').read_text())['progressStart'])
        args[-1](1,1)
        return image.resize(size)
    modules={'torch':torch,'diffusers':diffusion,'photo_quality':photo_quality,'photo_styles':photo_styles,
        'photo_upscale':SimpleNamespace(upscale=upscale,model_path=lambda p:tmp_path/'weights',MODEL_SHA256='tested')}
    for key,value in modules.items():monkeypatch.setitem(sys.modules,key,value)
    monkeypatch.setattr(sys,'argv',['photo_inference','--job',str(folder),'--model',str(model),'--control',str(tmp_path/'control')])
    photo_inference.main()
    manifest=json.loads((folder/'manifest.json').read_text())
    assert manifest['width']==resolution and manifest['height']==round(resolution*9/16/8)*8
    assert stages==([5,70,88] if resolution>=1024 else [5])
    assert len(calls)==(2 if resolution>=1024 else 1)
    assert calls[0]['control_image'].size==calls[0]['image'].size
    if resolution>=1024:
        assert calls[1]['strength']==.2 and calls[1]['control_image'].size==calls[1]['image'].size
        assert manifest['upscaler']['model']=='Real-ESRGAN general-x4v3'
