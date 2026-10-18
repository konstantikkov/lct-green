"""Standalone offline diffusion worker; also runnable on a local CUDA workstation."""
import argparse
import json
import os
from pathlib import Path
import time


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
    semantic = Image.open(folder/'semantic.png').convert('RGB').resize(size, Image.Resampling.BILINEAR)
    light = {'daylight': 'soft natural daylight, pale blue sky with soft clouds, balanced photographic exposure',
             'evening': 'warm low evening sunlight, long soft shadows, delicate blue sky, realistic golden hour photography',
             'overcast': 'bright overcast sky, soft diffuse light, subtle shadows, natural restrained colors'}[settings['lighting']]
    architecture = ('realistic existing building surfaces, buildings only at the input silhouettes, ' if settings.get('buildingCount', 0) else 'open landscape with no buildings, no urban skyline, ')
    prompt = ('RAW architectural landscape photograph, professionally photographed real urban landscaping, '
              'deciduous trees with visible woody trunks and irregular leafy crowns, dense natural leaf detail, low leafy shrubs, realistic grass, physical paving materials, '
              'same camera perspective and object positions as input, ' + architecture + light)
    negative = ('cartoon, illustration, videogame, unity, low poly, plastic, CGI, oversaturated, fake HDR, '
                'new buildings, extra buildings, skyscrapers, additional paths, extra objects, furniture, '
                'people, rocks, boulders, topiary, clipped spherical trees, green balls, sculpture, text, watermark, distorted architecture, blurry, fantasy')
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
    result.save(folder/'photo.png')
    (folder/'manifest.json').write_text(json.dumps({
        'engine': 'Realistic Vision 5.1 + ControlNet segmentation', 'execution': 'local', 'device': device,
        'seed': seed, 'width': result.width, 'height': result.height, 'elapsedSeconds': round(time.monotonic()-started, 1),
        'camera': settings.get('camera'), 'sourceBuildingCount': settings.get('buildingCount', 0), 'prompt': prompt, 'negativePrompt': negative,
        'strength': settings.get('strength', .85), 'controlScale': 1.1,
        'modelRevision': json.loads((Path(args.model)/'provenance.json').read_text()) if (Path(args.model)/'provenance.json').is_file() else None,
        'controlRevision': json.loads((Path(args.control)/'provenance.json').read_text()) if (Path(args.control)/'provenance.json').is_file() else None,
        'notice': 'Generated visualization. Semantic masks guide composition; CAD and planting coordinates are unchanged, but pixels are not CAD-certified.'
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
