"""One local diffusion subprocess at a time, separate from the API and CAD files."""
import base64
import io
import json
import os
from pathlib import Path
import subprocess
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from PIL import Image
from .. import config

_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='local-photo')
_slots = threading.BoundedSemaphore(2)
_active = set()


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
        write_status(folder, {'state': 'QUEUED'})
        _active.add(jid)
        _pool.submit(_run, folder, jid)
    except Exception:
        _active.discard(jid); _slots.release(); raise
    return jid


def write_status(folder, value):
    tmp = folder/'status.tmp'; tmp.write_text(json.dumps(value)); tmp.replace(folder/'status.json')


def _run(folder, jid):
    try:
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
        _active.discard(jid); _slots.release()


def status(pid, jid):
    data = json.loads((root(pid, jid)/'status.json').read_text())
    if data['state'] in ('QUEUED', 'GENERATING') and jid not in _active:
        return {'state': 'FAILED', 'error': 'Генерация прервана перезапуском сервиса. Создайте фото повторно.'}
    return data


def list_jobs(pid):
    parent=config.OUTPUT_DIR/'photo_jobs'/pid
    if not parent.is_dir():return []
    result=[]
    for folder in sorted(parent.iterdir(),key=lambda p:p.stat().st_mtime,reverse=True):
        try:
            data=status(pid,folder.name)
        except (ValueError,FileNotFoundError):
            continue
        base=f'/api/projects/{pid}/photos/{folder.name}'
        result.append({'jobId':folder.name,'state':data['state'],'statusUrl':base,
                       'imageUrl':base+'/image','sourceUrl':base+'/source','manifestUrl':base+'/manifest'})
        if len(result)>=20:break
    return result
