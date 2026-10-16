"""Bounded CPU-only Blender worker. No shell commands or external rendering API."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil
import subprocess
import threading
import uuid

from .. import config

_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='cycles')
_slots = threading.BoundedSemaphore(3)
_active = set()


def _write_status(path, data):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data))
    temporary.replace(path)


def job_dir(pid, jid):
    if not jid or any(c not in '0123456789abcdef' for c in jid) or len(jid) != 32:
        raise ValueError('Invalid render job')
    return config.OUTPUT_DIR / 'render_jobs' / pid / jid


def submit(pid, data, settings):
    blender = shutil.which('blender')
    if not blender:
        raise FileNotFoundError('Install Blender on the API host, or download the portable render bundle.')
    if not _slots.acquire(blocking=False):
        raise RuntimeError('Render queue is full; retry after an existing job completes.')
    jid = uuid.uuid4().hex
    root = job_dir(pid, jid)
    try:
        root.mkdir(parents=True, exist_ok=True)
        trees = Path(__file__).parents[1]/'assets'/'trees.blend'
        if trees.is_file():
            shutil.copyfile(trees, root/'trees.blend')
        (root/'scene.json').write_text(json.dumps(data), encoding='utf-8')
        _write_status(root/'status.json', {'state':'QUEUED'})
        _active.add(jid)
        _pool.submit(_run, blender, root, jid, settings)
    except Exception:
        _active.discard(jid)
        _slots.release()
        raise
    return jid


def _run(blender, root, jid, settings):
    status = root/'status.json'
    try:
        _write_status(status, {'state':'RENDERING'})
        args = [blender, '-b', '-t', '4', '--python', str(Path(__file__).with_name('blender_scene.py')),
                '--', '--scene', str(root/'scene.json'), '--output', str(root/'render.png')]
        for key, value in settings.items():
            if value is not None:
                args += ['--'+key.replace('_','-'), str(value)]
        with (root/'render.log').open('w') as log:
            proc = subprocess.run(args, stdout=log, stderr=subprocess.STDOUT, timeout=1800)
        if proc.returncode != 0 or not (root/'render.png').is_file():
            raise RuntimeError('Blender did not produce an image. Download the local bundle to inspect the scene.')
        _write_status(status, {'state':'COMPLETED'})
    except Exception as exc:
        _write_status(status, {'state':'FAILED', 'error':str(exc)})
    finally:
        _active.discard(jid)
        _slots.release()


def status(pid, jid):
    root = job_dir(pid,jid)
    data = json.loads((root/'status.json').read_text())
    if data['state'] in ('QUEUED','RENDERING') and jid not in _active:
        return {'state':'FAILED','error':'The service restarted. Start a new render.'}
    return data
