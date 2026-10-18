"""Offline tiled Real-ESRGAN general-x4v3 (SRVGGNetCompact) inference.

Architecture adapted from xinntao/Real-ESRGAN, BSD-3-Clause;
see docs/licenses/Real-ESRGAN.txt. No BasicSR/torchvision dependency required.
"""
import os
from pathlib import Path

MODEL_SHA256 = '8dc7edb9ac80ccdc30c3a5dca6616509367f05fbc184ad95b731f05bece96292'


def model_path(model_dir):
    return Path(os.environ.get('PHOTO_UPSCALE_MODEL', str(Path(model_dir).parent/'realesr-general-x4v3.pth')))


def build_model():
    import torch
    from torch import nn
    class SRVGGNetCompact(nn.Module):
        def __init__(self):
            super().__init__()
            self.body = nn.ModuleList([nn.Conv2d(3,64,3,1,1),nn.PReLU(64)])
            for _ in range(32):
                self.body.extend([nn.Conv2d(64,64,3,1,1),nn.PReLU(64)])
            self.body.append(nn.Conv2d(64,48,3,1,1))
            self.upsampler = nn.PixelShuffle(4)
        def forward(self, value):
            out = value
            for layer in self.body:
                out = layer(out)
            return self.upsampler(out) + torch.nn.functional.interpolate(value,scale_factor=4,mode='nearest')
    return SRVGGNetCompact()


def upscale(image, output_size, weights, device='cpu', on_progress=lambda done,total:None, tile=192):
    import hashlib
    import math
    import numpy as np
    import torch
    from PIL import Image
    path=Path(weights)
    if hashlib.sha256(path.read_bytes()).hexdigest()!=MODEL_SHA256:
        raise ValueError('Real-ESRGAN model checksum mismatch; reinstall local photo models.')
    model=build_model()
    state=torch.load(path,map_location='cpu',weights_only=True)
    model.load_state_dict(state.get('params_ema',state.get('params',state)),strict=True)
    model.to(device).eval()
    # Full receptive-field padding prevents seams at tile boundaries.
    pad=36
    source=np.asarray(image.convert('RGB'),dtype=np.float32)/255
    height,width=source.shape[:2]
    output=Image.new('RGB',(width*4,height*4))
    total=math.ceil(width/tile)*math.ceil(height/tile)
    done=0
    with torch.inference_mode():
        for y in range(0,height,tile):
            for x in range(0,width,tile):
                right,bottom=min(width,x+tile),min(height,y+tile)
                left0,top0=max(0,x-pad),max(0,y-pad)
                right0,bottom0=min(width,right+pad),min(height,bottom+pad)
                tensor=torch.from_numpy(source[top0:bottom0,left0:right0].copy()).permute(2,0,1).unsqueeze(0).to(device)
                prediction=model(tensor)[0,:, (y-top0)*4:(bottom-top0)*4, (x-left0)*4:(right-left0)*4]
                pixels=prediction.clamp(0,1).mul(255).round().byte().permute(1,2,0).cpu().numpy()
                output.paste(Image.fromarray(pixels),(x*4,y*4))
                done+=1;on_progress(done,total)
    return output.resize(output_size,Image.Resampling.LANCZOS)
