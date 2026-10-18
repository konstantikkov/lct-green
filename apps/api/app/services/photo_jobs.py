"""One local diffusion subprocess at a time, separate from the API and CAD files."""
import base64
import io
import json
import os
from pathlib import Path
import subprocess
import threading
import uuid
import time
import statistics
import math
from concurrent.futures import ThreadPoolExecutor
from PIL import Image
from .. import config

_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='local-photo')
_slots = threading.BoundedSemaphore(2)
_active = set()
_locations = {}


def configuration():
    return (Path(os.environ.get('PHOTO_PYTHON', '/opt/greenplan/photo-venv/bin/python')),
            Path(os.environ.get('PHOTO_MODEL_DIR', '/opt/greenplan/photo-models/realistic-vision')),
            Path(os.environ.get('PHOTO_CONTROL_DIR', '/opt/greenplan/photo-models/controlnet-seg')))


def capabilities():
    python, model, control = configuration()
    required = [python, model/'model_index.json', model/'unet/diffusion_pytorch_model.safetensors',
                model/'text_encoder/model.safetensors', model/'vae/diffusion_pytorch_model.safetensors',
                control/'config.json', control/'diffusion_pytorch_model.safetensors']
    ready = all(path.is_file() for path in required)
    return {'available': ready, 'engine': 'Realistic Vision 5.1 + ControlNet segmentation',
            'execution': 'local', 'message': 'Локальная модель готова' if ready else 'Локальная фотомодель ещё не установлена'}


def root(pid, jid):
    if len(jid) != 32 or any(c not in '0123456789abcdef' for c in jid):
        raise ValueError('Invalid photo job')
    return config.OUTPUT_DIR/'photo_jobs'/pid/jid


def decode_png(value):
    if not value.startswith('data:image/png;base64,'):
        raise ValueError('Expected a PNG capture')
    try:
        raw = base64.b64decode(value.split(',', 1)[1], validate=True)
        if len(raw) > 5 * 1024 * 1024:
            raise ValueError('Capture exceeds 5 MB')
        with Image.open(io.BytesIO(raw)) as image:
            if image.format != 'PNG' or not (64 <= image.width <= 1600 and 64 <= image.height <= 1600):
                raise ValueError('Capture dimensions must be between 64 and 1600 pixels')
            return image.convert('RGB').copy()
    except (OSError, Image.DecompressionBombError) as exc:
        raise ValueError('Invalid PNG capture') from exc


def submit(pid, settings):
    if not capabilities()['available']:
        raise FileNotFoundError('Install the local photo model first; no remote API is used.')
    source = decode_png(settings.pop('image'))
    depth = decode_png(settings.pop('depth'))
    semantic = decode_png(settings.pop('semantic'))
    if source.size != depth.size or source.size != semantic.size:
        raise ValueError('Color, depth and semantic captures must have identical dimensions')
    if not _slots.acquire(blocking=False):
        raise RuntimeError('Local photo queue is full')
    jid = uuid.uuid4().hex
    folder = root(pid, jid)
    try:
        folder.mkdir(parents=True)
        source.save(folder/'source.png'); depth.save(folder/'depth.png'); semantic.save(folder/'semantic.png')
        (folder/'settings.json').write_text(json.dumps(settings), encoding='utf-8')
        metadata={'createdAt':time.time(),'estimatedSeconds':estimate_seconds(pid,settings)}
        (folder/'job.json').write_text(json.dumps(metadata))
        write_status(folder, {'state': 'QUEUED'})
        _active.add(jid);_locations[jid]=folder
        _pool.submit(_run, folder, jid)
    except Exception:
        _active.discard(jid);_locations.pop(jid,None); _slots.release(); raise
    return jid


def write_status(folder, value):
    tmp = folder/'status.tmp'; tmp.write_text(json.dumps(value)); tmp.replace(folder/'status.json')


def _run(folder, jid):
    try:
        metadata=json.loads((folder/'job.json').read_text()) if (folder/'job.json').is_file() else {}
        metadata['startedAt']=time.time();(folder/'job.json').write_text(json.dumps(metadata))
        write_status(folder, {'state': 'GENERATING', 'message': 'Загрузка локальной модели…'})
        python, model, control = configuration()
        env = {**os.environ, 'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', 'OMP_NUM_THREADS': '2', 'MKL_NUM_THREADS': '2'}
        args = [str(python), str(Path(__file__).with_name('photo_inference.py')), '--job', str(folder),
                '--model', str(model), '--control', str(control)]
        with (folder/'inference.log').open('w') as log:
            result = subprocess.run(args, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=3600)
        if result.returncode or not (folder/'photo.png').is_file():
            raise RuntimeError('Локальная фотомодель не создала изображение. Исходный кадр сохранён; повторите генерацию.')
        write_status(folder, {'state': 'COMPLETED'})
    except Exception as exc:
        write_status(folder, {'state': 'FAILED', 'error': str(exc)})
    finally:
        _active.discard(jid);_locations.pop(jid,None); _slots.release()


def status(pid, jid):
    data = json.loads((root(pid, jid)/'status.json').read_text())
    if data['state'] in ('QUEUED', 'GENERATING') and jid not in _active:
        return {'state': 'FAILED', 'error': 'Генерация прервана перезапуском сервиса. Создайте фото повторно.'}
    data.update(progress_details(root(pid,jid),data))
    if data['state']=='QUEUED':
        ahead=[]
        for other,folder in list(_locations.items()):
            if other==jid:continue
            try:
                raw=json.loads((folder/'status.json').read_text())
                if raw['state']=='GENERATING':ahead.append(progress_details(folder,raw)['remainingSeconds'])
            except (OSError,ValueError):pass
        data['queuePosition']=len(ahead)+1
        data['remainingSeconds']+=sum(ahead)
    return data


def estimate_seconds(pid,settings):
    estimates=[]
    parent=config.OUTPUT_DIR/'photo_jobs'/pid
    folders=sorted(parent.glob('*/manifest.json'),key=lambda f:f.stat().st_mtime,reverse=True)[:10]
    for path in folders:
        try:
            m=json.loads(path.read_text())
            duration=float(m['elapsedSeconds'])
            factor=settings.get('strength',.85)/max(m.get('strength',.85),.1)
            factor*=(settings.get('resolution',512)/max(m.get('width',512),m.get('height',512)))**2
            if duration>0:estimates.append(duration*factor)
        except (OSError,ValueError,KeyError,TypeError):pass
    return round(statistics.median(estimates)) if estimates else 420


def progress_details(folder,data):
    try:meta=json.loads((folder/'job.json').read_text())
    except (OSError,ValueError):meta={}
    estimate=meta.get('estimatedSeconds',420)
    elapsed=max(0,time.time()-meta.get('startedAt',time.time()))
    if data['state']=='COMPLETED':return {'progressPercent':100,'remainingSeconds':0,'estimatedSeconds':estimate}
    if data['state']=='FAILED':return {'progressPercent':0,'remainingSeconds':0,'estimatedSeconds':estimate}
    step,total=data.get('step',0),max(data.get('totalSteps',1),1)
    if step:
        since=max(1,time.time()-data.get('generationStartedAt',meta.get('startedAt',time.time())))
        remaining=max(5,math.ceil(since/step*max(0,total-step)+25))
        progress=min(95,10+85*step/total)
    else:
        remaining=max(30,math.ceil(estimate-elapsed))
        progress=2 if data['state']=='QUEUED' else 5
    return {'progressPercent':round(progress),'remainingSeconds':remaining,'estimatedSeconds':estimate}


def urls(pid,jid):
    base=f'/api/projects/{pid}/photos/{jid}'
    try:settings=json.loads((root(pid,jid)/'settings.json').read_text())
    except (OSError,ValueError):settings={}
    return {'jobId':jid,'statusUrl':base,'imageUrl':base+'/image','sourceUrl':base+'/source',
            'manifestUrl':base+'/manifest','iterationId':settings.get('iterationId')}


def list_jobs(pid):
    parent=config.OUTPUT_DIR/'photo_jobs'/pid
    if not parent.is_dir():return []
    result=[]
    for folder in sorted(parent.iterdir(),key=lambda p:p.stat().st_mtime,reverse=True):
        try:
            data=status(pid,folder.name)
        except (ValueError,FileNotFoundError):
            continue
        result.append({**urls(pid,folder.name),'state':data['state']})
        if len(result)>=20:break
    return result
