import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace
import pytest
from app import config
from app.services import photo_jobs,photo_chat
from .test_photo_pipeline import payload


def complete_job(tmp_path,monkeypatch,settings=None):
    monkeypatch.setattr(config,'OUTPUT_DIR',tmp_path)
    folder=photo_jobs.root('test','a'*32);folder.mkdir(parents=True)
    data={'camera':payload()['camera'],'iterationId':'chosen-plan','planDigest':'frozen','lighting':'sunny','background':'park','seed':42,'planSnapshot':{'placements':[{'id':'tree','position':[12,34]}]},**(settings or {})}
    (folder/'settings.json').write_text(json.dumps(data))
    photo_jobs.write_status(folder,{'state':'COMPLETED'})
    for name in ['source.png','depth.png','semantic.png']:
        photo_jobs.decode_png(payload()['image']).save(folder/name)
    return folder,data


def test_refinement_preserves_original_plan_and_camera(tmp_path,monkeypatch):
    folder,original=complete_job(tmp_path,monkeypatch)
    class LLM:
        def __init__(self,**kwargs):pass
        async def is_reachable(self):return True
        async def structured_generate(self,*args,**kwargs):return photo_chat.AppearanceIntent(geometryChange=False,lighting='evening',background='keep',palette='vibrant',temperature='keep',finish='advertising',surfaceStyle='keep')
    monkeypatch.setattr(photo_chat,'LocalLLM',LLM)
    captured=[]
    def submit(pid,settings):captured.append(settings);return 'b'*32
    monkeypatch.setattr(photo_jobs,'submit',submit)
    result=asyncio.run(photo_chat.refine('test','a'*32,'Сделай свет теплее'))
    child=captured[0]
    for k in ['camera','iterationId','planDigest','planSnapshot','seed']:assert child[k]==original[k]
    assert child['palette']=='vibrant' and child['lighting']=='evening'
    assert child['image']==payload()['image']
    assert result['job']['jobId']=='b'*32
    assert photo_chat.history('test','a'*32)[0]['content']=='Сделай свет теплее'
    assert json.loads((folder/'settings.json').read_text())==original


def test_layout_request_gets_reply_without_generation(tmp_path,monkeypatch):
    complete_job(tmp_path,monkeypatch)
    class LLM:
        def __init__(self,**kwargs):pass
        async def is_reachable(self):return True
        async def structured_generate(self,*args,**kwargs):return photo_chat.AppearanceIntent(geometryChange=True,lighting='keep',background='keep',palette='keep',temperature='keep',finish='keep',surfaceStyle='keep')
    monkeypatch.setattr(photo_chat,'LocalLLM',LLM)
    monkeypatch.setattr(photo_jobs,'submit',lambda *a:pytest.fail('Must not regenerate'))
    result=asyncio.run(photo_chat.refine('test','a'*32,'Перенеси дерево'))
    assert result['job'] is None


def test_eta_uses_progress_and_completion_is_not_early(tmp_path,monkeypatch):
    folder,_=complete_job(tmp_path,monkeypatch)
    (folder/'job.json').write_text(json.dumps({'estimatedSeconds':420,'startedAt':time.time()-100}))
    data=photo_jobs.progress_details(folder,{'state':'GENERATING','step':5,'totalSteps':20,'generationStartedAt':time.time()-100})
    assert 20<=data['progressPercent']<=40
    assert 300<=data['remainingSeconds']<=330
    final=photo_jobs.progress_details(folder,{'state':'GENERATING','step':20,'totalSteps':20})
    assert final['progressPercent']<100 and final['remainingSeconds']>0
    assert photo_jobs.progress_details(folder,{'state':'COMPLETED'})['progressPercent']==100


def test_estimate_learns_measured_runtime(tmp_path,monkeypatch):
    folder,_=complete_job(tmp_path,monkeypatch)
    (folder/'manifest.json').write_text(json.dumps({'elapsedSeconds':400,'strength':.85,'width':512,'height':352}))
    assert photo_jobs.estimate_seconds('test',{'strength':.85,'resolution':512})==400


def test_requested_plan_is_bound_to_capture(tmp_path,monkeypatch):
    import app.main as main
    from fastapi.testclient import TestClient
    monkeypatch.setattr(main,'_get_rt',lambda pid:SimpleNamespace())
    monkeypatch.setattr(main.store,'list_iterations',lambda pid:[{'iterationId':'wanted','placements':[{'id':'a','position':[1,2]}]},{'iterationId':'other','placements':[]}])
    captured=[]
    monkeypatch.setattr(photo_jobs,'submit',lambda pid,s:(captured.append(s) or 'd'*32))
    monkeypatch.setattr(config,'OUTPUT_DIR',tmp_path)
    c=TestClient(main.app)
    assert c.post('/api/projects/test/photos',json={**payload(),'iterationId':'missing'}).status_code==404
    assert not captured
    assert c.post('/api/projects/test/photos',json={**payload(),'iterationId':'wanted'}).status_code==202
    assert captured[0]['planSnapshot']['placements']==[{'id':'a','position':[1,2]}]
    assert len(captured[0]['planDigest'])==64


def test_explicit_sunshine_is_preserved_despite_model_error():
    intent=photo_chat.AppearanceIntent(geometryChange=False,lighting='overcast',background='keep',palette='vibrant',temperature='warm',finish='keep',surfaceStyle='keep')
    changes,reply=photo_chat.apply_intent({'lighting':'sunny'},intent,'Теплее и насыщеннее, сохрани солнечный день')
    assert 'lighting' not in changes
    assert changes['temperature']=='warm' and changes['palette']=='vibrant'
    assert 'тёплый' in reply and 'пасмур' not in reply
