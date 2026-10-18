#!/usr/bin/env bash
# First-time download only. Inference uses these local files in offline mode.
set -euo pipefail
photo_root="${1:-/opt/greenplan}"
repo_root="$(cd "$(dirname "$0")/.." && pwd)"
python3 -m venv "$photo_root/photo-venv"
"$photo_root/photo-venv/bin/pip" install torch --index-url https://download.pytorch.org/whl/cpu
"$photo_root/photo-venv/bin/pip" install -r "$repo_root/apps/api/requirements-photo.txt"
HF_HUB_DISABLE_XET=1 "$photo_root/photo-venv/bin/python" - "$photo_root/photo-models" <<'PY'
import sys,json
from pathlib import Path
from huggingface_hub import snapshot_download,HfApi
base=Path(sys.argv[1])
models=[('SG161222/Realistic_Vision_V5.1_noVAE','realistic-vision',[
 'model_index.json','scheduler/*.json','tokenizer/*','text_encoder/config.json','text_encoder/model.safetensors',
 'unet/config.json','unet/diffusion_pytorch_model.safetensors','vae/config.json','vae/diffusion_pytorch_model.safetensors','README.md','LICENSE*']),
 ('lllyasviel/control_v11p_sd15_seg','controlnet-seg',['config.json','diffusion_pytorch_model.safetensors','README.md','LICENSE*'])]
for repo,directory,patterns in models:
 revision=HfApi().model_info(repo).sha
 snapshot_download(repo,revision=revision,local_dir=base/directory,allow_patterns=patterns,max_workers=2)
 (base/directory/'provenance.json').write_text(json.dumps({'repository':repo,'revision':revision}))
PY
# Compact learned upscaler; downloaded only during installation, verified again at inference.
curl -fL --retry 3 -o "$photo_root/photo-models/realesr-general-x4v3.pth" https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesr-general-x4v3.pth
"$photo_root/photo-venv/bin/python" - "$photo_root/photo-models/realesr-general-x4v3.pth" <<'PYVERIFY'
import hashlib,sys
from pathlib import Path
assert hashlib.sha256(Path(sys.argv[1]).read_bytes()).hexdigest() == '8dc7edb9ac80ccdc30c3a5dca6616509367f05fbc184ad95b731f05bece96292', 'Real-ESRGAN checksum mismatch'
PYVERIFY
printf 'PHOTO_PYTHON=%s/photo-venv/bin/python\nPHOTO_MODEL_DIR=%s/photo-models/realistic-vision\nPHOTO_CONTROL_DIR=%s/photo-models/controlnet-seg\n' "$photo_root" "$photo_root" "$photo_root"
