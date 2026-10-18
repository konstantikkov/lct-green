# Local photographic generation from the CAD composition

The 3D editor is a composition tool: simple building masses and tree crowns, CAD coordinates, draggable planting circles, orbit/pan and garden/aerial cameras. It deliberately does not imitate a finished photograph. Only recognized source building footprints appear; no surrounding buildings are generated in the editor.

Choose a camera, press **AI-фото**, review the captured frame, choose light and generate. The result uses **Realistic Vision 5.1** (Stable Diffusion 1.5 family) with **ControlNet segmentation** and image-to-image conditioning. Color, depth, ADE20K semantic labels and camera come from the same frame. The photo pass conditions on semantic labels, because depth-only guidance misread simplified shrubs as stones in the first real canary. Depth is retained as an audit/extension artifact. CAD lines, selection and editing circles are excluded. A comparison of the input and generated photo is displayed. Neither generation nor changing seed modifies CAD or placements.

Semantic labels guide perspective, massing, tree and shrub positions; it does **not** guarantee exact pixel or object-count fidelity. The generative model can change or add details, particularly with high strength. The output is labeled as an AI visualization, and the source frame remains available for comparison. It is not a substitute for the DXF. No fabricated benchmark result or sample photograph is shown as the generated output.

## Installation on Linux

```bash
bash scripts/install_local_photo.sh /opt/greenplan
```

Requires internet only for the first installation (~5.4 GB model weights plus PyTorch). The script prints `PHOTO_PYTHON`, `PHOTO_MODEL_DIR`, `PHOTO_CONTROL_DIR`; set these in the API environment if installed elsewhere. Restart the API. Do not commit weights or a virtualenv to Git. Model downloads record the exact repository revision in `provenance.json`.

Models and licenses:

- https://huggingface.co/SG161222/Realistic_Vision_V5.1_noVAE — CreativeML Open RAIL-M.
- https://huggingface.co/lllyasviel/control_v11p_sd15_seg — model card OpenRAIL terms.
- These are locally runnable, open-weight models with license conditions; they are not claimed to be unrestricted OSI software.

The API environment only needs its existing dependencies. Heavy inference runs in `photo-venv` as a subprocess, with `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`, two CPU threads and a 60-minute timeout. A model-directory file lock serializes subprocesses across API processes and restarts. One generation runs at a time, with one waiting slot; no remote image service is called. On the current CPU host, the UI uses 512 pixels on the long side, 24 scheduled steps (effective count depends on strength). Larger output and fast iteration need a GPU. A CPU render is not advertised as high-resolution reference-quality photography.

For an NVIDIA workstation, install CUDA-compatible PyTorch in the photo environment instead. The same inference script detects CUDA automatically and uses float16; the current UI still uses the conservative 512-pixel default. API resolution also accepts 768. CPU uses float32.

## API and reproducibility

`GET /api/projects/{pid}/photos` restores recent jobs/results after a browser reload. `GET /api/photo-capabilities` reports whether local model configuration exists. `POST /api/projects/{pid}/photos` accepts PNG data URLs `image`/`depth`/`semantic`, camera position/target/FOV/aspect, lighting (`daylight`, `evening`, `overcast`), strength .55–.95, seed and resolution. It returns job/status/image/source/manifest URLs. `GET /api/projects/{pid}/photos/{jid}` reports queue, generation step, completion or failure. Swagger exposes the schemas.

Files under `outputs/photo_jobs/{pid}/{jobId}`:

- `source.png`, `depth.png`, `semantic.png`: exact submitted camera captures;
- `settings.json`: seed, camera and requested settings;
- `photo.png`: real model output, never a fallback image;
- `manifest.json`: model, device, size, elapsed inference time, prompt and camera;
- `inference.log`, `status.json`: diagnosis and progress.

To reproduce on a local workstation, copy the job's source/depth/semantic/settings files and run:

```bash
PHOTO_PYTHON=/opt/greenplan/photo-venv/bin/python
"$PHOTO_PYTHON" apps/api/app/services/photo_inference.py \
  --job /path/to/copied/job \
  --model /opt/greenplan/photo-models/realistic-vision \
  --control /opt/greenplan/photo-models/controlnet-seg
```

Matching weights and seed make iterations reproducible on the same runtime/device; exact pixel identity across CPU/CUDA is not promised. The earlier Blender/Cycles export remains available through `/renders` and `/export/render-bundle` for deterministic geometry rendering; the primary UI uses the separate generative workflow.

A real CPU canary (source frame, semantic labels, generated result and original manifest) is committed in [examples/local-photo](../examples/local-photo/). This is a diagnostic example from an actual pilot import with no closed building footprints, not a claim of matching the architectural reference quality.
