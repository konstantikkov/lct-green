"""Standalone offline diffusion worker; also runnable on a local CUDA workstation."""
import argparse
import json
import os
from pathlib import Path
import time


def prepare_backdrop(image, semantic, depth, settings):
    """Context affects original sky and distant unclassified ground only.

    The source files remain untouched. CAD/building/vegetation label pixels
    never enter the context mask. This is illustrative scenery, not a survey.
    """
    from PIL import Image
    import math
    import numpy as np
    rgb = np.array(image).copy()
    labels = np.array(semantic).copy()
    z = np.array(depth.convert('L'))
    h, w = labels.shape[:2]
    sky = np.max(np.abs(labels.astype(int)-[6,230,230]),axis=2)<12
    earth = np.max(np.abs(labels.astype(int)-[120,120,70]),axis=2)<12
    mask = np.zeros((h,w),dtype=bool)
    if settings.get('background','park') == 'park':
        # The extended neutral floor is far outside the source scene at these
        # depths. Never convert known source surfaces to invented lawn.
        offsite=np.zeros((h,w),dtype=bool)
        bounds=settings.get('siteBounds');camera=settings.get('camera')
        if bounds and camera:
            eye=np.array(camera['position'],dtype=float)
            forward=np.array(camera['target'],dtype=float)-eye
            length=np.linalg.norm(forward)
            if length>1e-8:
                forward/=length;right=np.cross(forward,[0.,0.,1.]);rl=np.linalg.norm(right)
                if rl>1e-8:
                    right/=rl;up=np.cross(right,forward)
                    yy,xx=np.mgrid[0:h,0:w]
                    tan=math.tan(math.radians(camera['fov'])/2)
                    rays=forward+(2*(xx[...,None]+.5)/w-1)*tan*camera['aspect']*right+(1-2*(yy[...,None]+.5)/h)*tan*up
                    with np.errstate(divide='ignore',invalid='ignore'):
                        distance=(-.07-eye[2])/rays[...,2]
                        world=eye+distance[...,None]*rays
                    lo,hi=bounds['min'],bounds['max']
                    offsite=(distance>0)&((world[...,0]<lo[0]-2)|(world[...,0]>hi[0]+2)|(world[...,1]<lo[1]-2)|(world[...,1]>hi[1]+2))
        far = earth & (z<28) & offsite
        labels[far] = [4,250,7]
        rgb[far] = [125,155,82]
        mask |= far
        # Derive the ground horizon from sky/earth transitions; existing
        # canopies interrupt it but must never become context themselves.
        transitions=[]
        for x in range(w):
            ys=np.flatnonzero(sky[:-1,x] & earth[1:,x])
            if len(ys):transitions.append(int(ys[0]))
        if transitions:
            horizon=int(np.median(transitions))
            for x in range(w):
                height=int(h*(.045+.019*math.sin(x*.051)+.012*math.sin(x*.119+1.3)))
                top=max(0,horizon-height)
                rows=np.arange(top,min(h,horizon+2))
                rows=rows[sky[rows,x]]
                labels[rows,x]=[4,200,3]
                rgb[rows,x]=[83,125,68]
                mask[rows,x]=True
    if settings.get('lighting','sunny')=='sunny':
        # A bright input sky helps the model avoid the previous grey-sky prior.
        for y in range(h):
            selected=sky[y] & ~mask[y]
            t=y/max(h-1,1)
            rgb[y,selected]=[int(105+65*t),int(175+40*t),int(230+15*t)]
        mask |= sky
    allowed = sky | (earth & (z<28) & offsite) if settings.get('background','park')=='park' else sky
    assert not np.any(mask & ~allowed)
    return Image.fromarray(rgb), Image.fromarray(labels), Image.fromarray((mask*255).astype('uint8'))


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--job', type=Path, required=True)
    p.add_argument('--model', required=True)
    p.add_argument('--control', required=True)
    args = p.parse_args()
    import fcntl
    lock_file = (Path(args.model).parent/'.inference.lock').open('w')
    fcntl.flock(lock_file, fcntl.LOCK_EX)  # also serializes separate API processes
    import torch
    from PIL import Image
    from diffusers import ControlNetModel, StableDiffusionControlNetImg2ImgPipeline, UniPCMultistepScheduler
    torch.set_num_threads(int(os.environ.get('OMP_NUM_THREADS', '2')))
    folder = args.job
    settings = json.loads((folder/'settings.json').read_text())
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    dtype = torch.float16 if device == 'cuda' else torch.float32
    control = ControlNetModel.from_pretrained(args.control, torch_dtype=dtype, local_files_only=True, use_safetensors=True)
    pipe = StableDiffusionControlNetImg2ImgPipeline.from_pretrained(
        args.model, controlnet=control, torch_dtype=dtype, local_files_only=True, use_safetensors=True,
        safety_checker=None, requires_safety_checker=False)
    pipe.scheduler = UniPCMultistepScheduler.from_config(pipe.scheduler.config)
    pipe.to(device); pipe.vae.enable_slicing(); pipe.vae.enable_tiling()
    # PyTorch scaled-dot-product attention is faster and avoids the many
    # tiny CPU operations introduced by maximum attention slicing.
    image = Image.open(folder/'source.png').convert('RGB')
    limit = settings.get('resolution', 512)
    scale = limit/max(image.size)
    size = tuple(max(64, round(x*scale/8)*8) for x in image.size)
    image = image.resize(size, Image.Resampling.LANCZOS)
    semantic = Image.open(folder/'semantic.png').convert('RGB').resize(size, Image.Resampling.NEAREST)
    depth = Image.open(folder/'depth.png').convert('RGB').resize(size, Image.Resampling.BILINEAR)
    image,semantic,context_mask=prepare_backdrop(image,semantic,depth,settings)
    image.save(folder/'conditioning_source.png');semantic.save(folder/'conditioning_semantic.png');context_mask.save(folder/'context_mask.png')
    light = {'sunny': 'bright cheerful summer morning sunshine, clear vivid blue sky, small soft white clouds, warm directional sunlight, luminous fresh green foliage, inviting premium landscape photography, balanced rich natural colors, welcoming sunny atmosphere',
             'daylight': 'soft natural daylight, pale blue sky with soft clouds, balanced photographic exposure',
             'evening': 'warm low evening sunlight, long soft shadows, delicate blue sky, realistic golden hour photography',
             'overcast': 'bright overcast sky, soft diffuse light, subtle shadows, natural restrained colors'}[settings['lighting']]
    architecture = ('realistic existing building surfaces, buildings only at the input silhouettes, ' if settings.get('buildingCount', 0) else 'open landscape with no buildings, no urban skyline, ')
    prompt = ('RAW architectural landscape photograph, professionally photographed real urban landscaping, '
              'deciduous trees with visible woody trunks and irregular leafy crowns, dense natural leaf detail, low leafy shrubs, realistic grass, physical paving materials, '
              'same camera perspective and object positions as input, ' + architecture + light +
              ('; distant leafy park vegetation at the horizon, naturally connected landscape continuing beyond the site, background greenery without buildings' if settings.get('background','park')=='park' else ''))
    negative = ('cartoon, illustration, videogame, unity, low poly, plastic, CGI, oversaturated, fake HDR, '
                'new buildings, extra buildings, skyscrapers, additional paths, extra objects, furniture, '
                'people, rocks, boulders, topiary, clipped spherical trees, green balls, sculpture, text, watermark, distorted architecture, blurry, fantasy')
    negative += ', isolated display model, floating diorama, miniature model, blank studio background'
    if settings.get('background','park')=='park':negative += ', empty horizon'
    if settings['lighting']=='sunny':negative += ', overcast, storm clouds, grey sky, gloomy, desaturated, winter, dull lighting, harsh HDR'
    if not settings.get('buildingCount', 0):negative += ', buildings, houses, urban skyline'
    started = time.monotonic()
    def progress(pipeline, step, timestep, values):
        tmp = folder/'status.tmp'
        tmp.write_text(json.dumps({'state': 'GENERATING', 'step': step+1, 'totalSteps': pipeline.num_timesteps,
                                  'message': 'Локальная генерация фотографии…'}))
        tmp.replace(folder/'status.json')
        return values
    seed = settings.get('seed', 42)
    with torch.inference_mode():
        result = pipe(prompt=prompt, negative_prompt=negative, image=image, control_image=semantic,
                      strength=settings.get('strength', .85), num_inference_steps=24,
                      guidance_scale=6, controlnet_conditioning_scale=1.1, control_guidance_end=.95,
                      generator=torch.Generator(device=device).manual_seed(seed),
                      callback_on_step_end=progress).images[0]
    if settings['lighting']=='sunny':
        from PIL import ImageEnhance
        result=ImageEnhance.Color(result).enhance(1.12)
        result=ImageEnhance.Contrast(result).enhance(1.04)
        result=ImageEnhance.Brightness(result).enhance(1.03)
    result.save(folder/'photo.png')
    (folder/'manifest.json').write_text(json.dumps({
        'engine': 'Realistic Vision 5.1 + ControlNet segmentation', 'execution': 'local', 'device': device,
        'seed': seed, 'width': result.width, 'height': result.height, 'elapsedSeconds': round(time.monotonic()-started, 1),
        'lighting': settings['lighting'], 'background': settings.get('background','park'),
        'backgroundIllustrative': settings.get('background','park')=='park', 'siteBounds': settings.get('siteBounds'),
        'camera': settings.get('camera'), 'sourceBuildingCount': settings.get('buildingCount', 0), 'prompt': prompt, 'negativePrompt': negative,
        'strength': settings.get('strength', .85), 'controlScale': 1.1,
        'modelRevision': json.loads((Path(args.model)/'provenance.json').read_text()) if (Path(args.model)/'provenance.json').is_file() else None,
        'controlRevision': json.loads((Path(args.control)/'provenance.json').read_text()) if (Path(args.control)/'provenance.json').is_file() else None,
        'notice': 'Generated visualization. Semantic masks guide composition; CAD and planting coordinates are unchanged, but pixels are not CAD-certified.'
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
