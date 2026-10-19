"""Bounded CPU-only Blender worker. No shell commands or external rendering API."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import re
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


OUTPUTS = ('render.png', 'render.jpg')


def submit(pid, data, settings, output='render.png', timeout=1800, meta=None):
    """Queue a Blender job; ``meta`` is stored as job.json for listings."""
    blender = shutil.which(os.environ.get('BLENDER_EXECUTABLE', 'blender'))
    if not blender:
        raise FileNotFoundError('Install Blender on the API host, or download the portable render bundle.')
    if not _slots.acquire(blocking=False):
        raise RuntimeError('Render queue is full; retry after an existing job completes.')
    jid = uuid.uuid4().hex
    root = job_dir(pid, jid)
    try:
        root.mkdir(parents=True, exist_ok=True)
        assets = Path(__file__).parents[1]/'assets'
        if assets.is_dir():
            for asset in assets.iterdir():
                if asset.suffix in {'.blend','.hdr','.jpg'}:
                    shutil.copyfile(asset,root/asset.name)
        (root/'scene.json').write_text(json.dumps(data), encoding='utf-8')
        if meta is not None:
            (root/'job.json').write_text(json.dumps(meta, ensure_ascii=False), encoding='utf-8')
        _write_status(root/'status.json', {'state':'QUEUED'})
        _active.add(jid)
        _pool.submit(_run, blender, root, jid, settings, output, timeout)
    except Exception:
        _active.discard(jid)
        _slots.release()
        raise
    return jid


def _run(blender, root, jid, settings, output='render.png', timeout=1800):
    status = root/'status.json'
    try:
        _write_status(status, {'state':'RENDERING'})
        args = [blender, '-b', '-t', '4', '--python', str(Path(__file__).with_name('blender_scene.py')),
                '--', '--scene', str(root/'scene.json'), '--output', str(root/output)]
        for key, value in settings.items():
            if value is not None:
                args += ['--'+key.replace('_','-'), str(value)]
        with (root/'render.log').open('w') as log:
            proc = subprocess.run(args, stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
        if proc.returncode != 0 or not (root/output).is_file():
            raise RuntimeError('Blender did not produce an image. Download the local bundle to inspect the scene.')
        _write_status(status, {'state':'COMPLETED'})
    except Exception as exc:
        _write_status(status, {'state':'FAILED', 'error':str(exc)})
    finally:
        _active.discard(jid)
        _slots.release()


def _progress(log):
    """Sample counter and remaining time from the tail of Cycles' log."""
    try:
        with log.open('rb') as f:
            f.seek(max(0, log.stat().st_size-4096))
            tail = f.read().decode('utf-8', 'replace')
    except OSError:
        return {}
    samples = re.findall(r'Sample (\d+)/(\d+)', tail)
    if not samples:
        return {'progressPercent': 2}
    done, total = map(int, samples[-1])
    result = {'progressPercent': max(2, min(99, round(100*done/max(total, 1))))}
    remaining = re.findall(r'Remaining:(?:(\d+):)?(\d+):(\d+(?:\.\d+)?)', tail)
    if remaining:
        h, m, sec = remaining[-1]
        result['remainingSeconds'] = round(int(h or 0)*3600+int(m)*60+float(sec))
    return result


def status(pid, jid):
    root = job_dir(pid,jid)
    data = json.loads((root/'status.json').read_text())
    if data['state'] in ('QUEUED','RENDERING') and jid not in _active:
        return {'state':'FAILED','error':'The service restarted. Start a new render.'}
    if data['state'] == 'RENDERING':
        data = {**data, **_progress(root/'render.log')}
    return data


def image(pid, jid):
    root = job_dir(pid, jid)
    return next((root/name for name in OUTPUTS if (root/name).is_file()), root/OUTPUTS[0])


def manifest(pid, jid):
    path = image(pid, jid).with_suffix('.manifest.json')
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}


def listing(pid, kind):
    """Jobs of one ``kind`` (from job.json), newest first."""
    root = config.OUTPUT_DIR / 'render_jobs' / pid
    rows = []
    for folder in root.iterdir() if root.is_dir() else []:
        try:
            meta = json.loads((folder/'job.json').read_text(encoding='utf-8'))
            if meta.get('kind') != kind:
                continue
            rows.append({**meta, 'jobId': folder.name, **status(pid, folder.name)})
        except (OSError, ValueError):
            continue
    return sorted(rows, key=lambda r: r.get('createdAt', 0), reverse=True)
