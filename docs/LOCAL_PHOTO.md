# Local photographic generation from the CAD composition

The 3D editor is a composition tool: simple building masses and tree crowns, CAD coordinates, draggable planting circles, orbit/pan and garden/aerial cameras. It deliberately does not imitate a finished photograph. Only recognized source building footprints appear; no surrounding buildings are generated in the editor.

Choose the planting plan in the right sidebar, position the camera by orbit/pan or the viewpoint selector, then press **Сгенерировать 3D-рендер**. CAD remains the default view; the CAD/3D tabs remain available. Settings panels are resizable by dragging their edges. The selected iteration ID, placement snapshot/digest and camera are saved with the job. The generated image and refinement chat appear in the same sidebar.

The result uses **Realistic Vision 5.1** with **ControlNet segmentation** and image-to-image conditioning. Color, depth, ADE20K semantic labels and camera come from the same frame. CAD annotations and editing circles are excluded. Original captures remain available through the API. Generating a photo does not change the plan.

Semantic labels guide perspective, massing, tree and shrub positions; it does **not** guarantee exact pixel or object-count fidelity. The generative model can change or add details, particularly with high strength. The output is labeled as an AI visualization, and the source frame remains available for comparison. It is not a substitute for the DXF. No fabricated benchmark result or sample photograph is shown as the generated output.

## Installation on Linux

```bash
bash scripts/install_local_photo.sh /opt/greenplan
```

Requires internet only for the first installation (~5.4 GB model weights plus PyTorch). The script prints `PHOTO_PYTHON`, `PHOTO_MODEL_DIR`, `PHOTO_CONTROL_DIR`; set these in the API environment if installed elsewhere. Restart the API. Do not commit weights or a virtualenv to Git. Model downloads record the exact repository revision in `provenance.json`.

Models and licenses:

- https://huggingface.co/SG161222/Realistic_Vision_V5.1_noVAE — CreativeML Open RAIL-M.
- https://huggingface.co/lllyasviel/control_v11p_sd15_seg — model card OpenRAIL terms.
- https://github.com/xinntao/Real-ESRGAN — general-x4v3 upscaler; architecture license in [licenses/Real-ESRGAN.txt](licenses/Real-ESRGAN.txt). The installer verifies the model SHA-256.
- These are locally runnable, open-weight models with license conditions; they are not claimed to be unrestricted OSI software.

The API environment only needs its existing dependencies. Heavy inference runs in `photo-venv` as a subprocess, with `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`, two CPU threads and a 60-minute timeout. A model-directory file lock serializes subprocesses across API processes and restarts. One generation runs at a time, with one waiting slot; no remote image service is called. Standard modes use 512/768 pixels on the long edge and 24 scheduled steps. HD modes output 1024/1536/2048 pixels: a 28-step composition pass, a low-denoise (.2) detail pass guided by the original semantic labels, and tiled Real-ESRGAN general-x4v3 neural upscaling. This is not native 2K diffusion. Nearly square compositions use a smaller internal size to control CPU cost. Target CPU time is approximately 10–20 minutes, excluding queue wait; measured stage timing updates the estimate. The server keeps a 60-minute failure timeout for stalled jobs, rather than discarding an almost complete result at 20 minutes. Generative fidelity to reference photographs is not guaranteed.

For an NVIDIA workstation, install CUDA-compatible PyTorch in the photo environment instead. The same inference script detects CUDA automatically and uses float16; the UI defaults to 1536 px when local upscaler weights are available and falls back to 512 px otherwise. CPU uses float32.

## API and reproducibility

`GET /api/projects/{pid}/photos` restores recent jobs/results after a browser reload. `GET /api/photo-capabilities` reports local model availability, `highResolutionAvailable`, and supported `resolutions`. `POST /api/projects/{pid}/photos` accepts PNG data URLs `image`/`depth`/`semantic`, camera position/target/FOV/aspect, lighting (`sunny`, `daylight`, `evening`, `overcast`), strength .55–.95, seed and resolution. It returns job/status/image/source/manifest URLs. `GET /api/projects/{pid}/photos/{jid}` reports queue, generation step, completion or failure. Swagger exposes the schemas.

Files under `outputs/photo_jobs/{pid}/{jobId}`:

- `source.png`, `depth.png`, `semantic.png`: exact submitted camera captures;
- `settings.json`: seed, camera and requested settings;
- `composition.png`, `detail.png` (HD only): intermediate stages;
- `photo.png`: real model output at the selected resolution, never a fallback image;
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

## Sunny presentation and distant context

The default `sunny` preset asks for clear blue sky, warm summer sunlight, fresh greens and a welcoming presentation. A restrained final color/brightness adjustment applies only to this preset. Daylight, evening and overcast remain selectable.

`background: park` adds an **illustrative**, distant green backdrop to the conditioning guide. Existing sky pixels can receive distant treeline silhouettes. Neutral ground changes to distant lawn only where the captured depth is far away **and** a ground-plane camera-ray test places it outside the source drawing bounds, with a 2 m margin. Known object labels (building, tree, shrub, road, sidewalk, etc.) are protected. With no bounds supplied, neutral ground remains unchanged. `background: source` disables the added backdrop.

The source CAD scene and original captures remain unchanged. `conditioning_source.png`, `conditioning_semantic.png` and `context_mask.png` record precisely which guide pixels were changed; lighting/background choices and drawing bounds are saved in the manifest. These guide protections do not make diffusion pixel-perfect. Added green surroundings are not claimed to be a surveyed description of the neighborhood, and no surrounding buildings are added to the guide.


## Progress and local LLM refinements

A real progress bar follows composition steps, detail steps and completed upscaling tiles, stays below 100% during final decoding, and completes only after the output file is ready. ETA uses recent measured runtimes, current step timing and queue position; it is approximate. Queue/run timestamps and estimates are stored in `job.json`. Polling and job recovery continue after reloading the page.

After a result completes, chat sends the request to the existing local LLM. The model translates it into constrained appearance controls: lighting, palette, color temperature, presentation finish, existing paving materials and optional illustrative background. A small-model prompt uses explicit `keep` values for unchanged settings and a worked example; explicit instructions to preserve sunshine override a contradictory lighting suggestion. Replies describe the controls actually applied. If the LLM is unavailable, the UI reports that rather than pretending to answer.

Accepted edits enqueue a new diffusion job from the **original color/depth/semantic captures**. They never use the previous AI image as a new geometric source. Camera, plan ID/digest/snapshot and planting positions are preserved; `parentJobId` and the conversation link versions. Geometry-change requests direct the user back to editing the plan. Unknown/uninterpretable appearance requests do not launch an unnecessary image generation.

- `GET /api/projects/{pid}/photos/{jid}/chat`: persisted messages.
- `POST /api/projects/{pid}/photos/{jid}/refine`: `{ "message": "Сделай свет теплее" }`; returns an explanation, conversation and optional new job.

The LLM never emits arbitrary code or direct geometry edits. Numeric placement compliance remains a property of the CAD pipeline, not generated pixels.

## CAD reconstruction and artifact control

The right sidebar's **Геометрия из CAD** section rebuilds the environment before a
new photo capture. Closed footprints become watertight walls and roofs, including
courtyard voids. CAD thickness and height labels take precedence; recognized storey
labels use an explicitly assumed 3 m per storey. Missing heights use the adjustable
9 m fallback. Open and dashed outlines are not silently closed into new buildings.

Fences are **off by default**: classified CAD strokes can be symbols or temporary
boundaries, and are not enough evidence for a realistic railing design. The opt-in
checkbox reconstructs those source lines at an illustrative 1.3 m height. New photo
captures record a fence count; without fences the negative prompt also excludes
railings, wire mesh and barriers. Diffusion remains illustrative, not exact CAD.

Raised source sidewalks and road surfaces have boundary edges. Native CAD 3DFACE
vertices retain their metric Z coordinates, normalized to a shared elevation datum;
terrain remains flat when no 3D evidence exists. Plants follow the source ground in
the editor, including after moves. Refresh older imports to extract newly supported
Z/thickness metadata. CAD coordinates and regulatory calculations remain 2D and
unchanged. Existing photographs must be regenerated from the updated scene.

The source-scene background remains available. The current default is illustrative
green context, as requested, rather than empty distant terrain.

### Excavation-as-building and surface presentation

At the user's request, labelled excavation outlines now represent buildings in
visualizations. Mutual-nearest gaps up to 0.65 m are joined, and only closed cycles
containing the excavation label are accepted. No convex hull or bounding box is
substituted. The interpretation is recorded in scene assumptions and can be disabled
with `excavationsAsBuildings: false` in the preview API. CAD and regulatory geometry
remain unchanged.

The editor retains imported PBR grass/asphalt/paving/facade maps. Recommended lawn
polygons, including holes, are displayed as textured proposed grass; older iterations
obtain the same lawn recommendation on read and during selected-plan export. The
photo background defaults to a dense, illustrative green tree belt and lawn, with
negative prompts against desert and agricultural fields. Background guide changes
remain restricted to sky and unclassified ground outside the CAD bounds.
