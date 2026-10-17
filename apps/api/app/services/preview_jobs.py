"""Cached Blender-to-glTF editor environments, independent of proposed plants."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from .. import config
from .local_render import scene_data

_pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='blender-preview')
_lock=threading.Lock()
_active=set()


def root(pid,key):
    if len(key)!=32 or any(c not in '0123456789abcdef' for c in key):raise ValueError('Invalid preview')
    return config.OUTPUT_DIR/'editor_previews'/pid/key


def request(pid,doc):
    blender=shutil.which(os.environ.get('BLENDER_EXECUTABLE','blender'))
    if not blender:raise FileNotFoundError('Blender is unavailable on the API host.')
    data=scene_data(doc,{'placements':[]})
    payload=json.dumps(data,sort_keys=True,ensure_ascii=False)
    fingerprint=hashlib.sha256(payload.encode()+b'cad-only-v2')
    for name in ['blender_scene.py','blender_landscape.py','render_geometry.py','preview_materials.py']:
        fingerprint.update(Path(__file__).with_name(name).read_bytes())
    key=fingerprint.hexdigest()[:32];folder=root(pid,key)
    with _lock:
        if not (folder/'environment.glb').is_file() and (pid,key) not in _active:
            # Keep the queue bounded while another project is being prepared.
            if len(_active)>=3:raise RuntimeError('Preview queue full. Retry shortly.')
            folder.mkdir(parents=True,exist_ok=True)
            (folder/'scene.json').write_text(payload,encoding='utf-8')
            assets=Path(__file__).parents[1]/'assets'
            for asset in assets.iterdir():
                if asset.suffix in ('.jpg','.hdr'):shutil.copyfile(asset,folder/asset.name)
            (folder/'status.json').write_text(json.dumps({'state':'QUEUED'}))
            _active.add((pid,key));_pool.submit(_build,blender,pid,key)
    return {'previewId':key,'origin':[(doc.extents.min[i]+doc.extents.max[i])/2 for i in (0,1)],
            'statusUrl':f'/api/projects/{pid}/editor-preview/{key}',
            'modelUrl':f'/api/projects/{pid}/editor-preview/{key}/model'}


def _write(folder,data):
    temp=folder/'status.tmp';temp.write_text(json.dumps(data));temp.replace(folder/'status.json')


def _build(blender,pid,key):
    folder=root(pid,key)
    try:
        _write(folder,{'state':'BUILDING'})
        args=[blender,'-b','-t','2','--python',str(Path(__file__).with_name('blender_scene.py')),'--',
              '--scene',str(folder/'scene.json'),'--gltf',str(folder/'working.glb'),'--preview-only',
              '--output',str(folder/'unused.png'),'--detail','cad']
        with (folder/'build.log').open('w') as log:
            p=subprocess.run(args,stdout=log,stderr=subprocess.STDOUT,timeout=600)
        if p.returncode or not (folder/'working.glb').is_file():raise RuntimeError('Blender could not build the editor scene.')
        (folder/'working.glb').replace(folder/'environment.glb')
        _write(folder,{'state':'COMPLETED'})
    except Exception as exc:_write(folder,{'state':'FAILED','error':str(exc)})
    finally:
        with _lock:_active.discard((pid,key))


def status(pid,key):
    folder=root(pid,key)
    if (folder/'environment.glb').is_file():return {'state':'COMPLETED'}
    data=json.loads((folder/'status.json').read_text())
    if data['state'] in ('BUILDING','QUEUED') and (pid,key) not in _active:
        return {'state':'FAILED','error':'Preview build interrupted. Reopen the project to retry.'}
    return data
