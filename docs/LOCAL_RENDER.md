> Для нового сценария «минимальный макет → локальная AI-фотография» см. [LOCAL_PHOTO.md](LOCAL_PHOTO.md). Этот документ описывает технический Blender/Cycles-рендер и экспорт геометрии.

# Source-only CAD rendering

The editor, local scene bundle and Cycles export render source CAD objects and
the selected planting plan. There is no generated courtyard, surrounding housing,
pergola, furniture, additional paving or automatic perennial bed. The former
presentation-context generator and its UI toggle were removed. Legacy
`detail=presentation` requests remain accepted but cannot add scenery.

## Import and regeneration

A new DXF/DWG/ZIP upload schedules the source 3D scene and material preparation in
the background after parsing/classification. **Обновить импорт** schedules it
again from the refreshed drawing, preserving user layer mappings. Blender being
unavailable does not reject the upload: CAD and planting editing remain usable.

Only closed building polygons or closed cycles of source building lines become
building volumes. Closed POLYLINE entities are supported directly. Open lines
are not converted into guessed footprints or surrounding blocks. Surface holes
and disconnected shells are retained. The source drawing is not overwritten.
Building heights use source `heightM` when available, otherwise an illustrative
12 m; materials are defaults, not surveyed facade finishes. **CAD оригинал** is
available to compare with the original source modelspace.

The preview cache fingerprints geometry, renderer code and bundled material
images. New file uploads and changed input/materials cannot reuse an old scene
from another project. The viewport reports the source building count and material
readiness. Moving proposed plantings reuses the prepared source environment.

## Photographic materials and rendering

Existing building, road, pavement and green-area surfaces receive bundled CC0
Poly Haven diffuse, roughness and OpenGL normal maps. UVs use physical meter
scale; building roofs have a separate material. Solidified building triangles
are welded before extrusion and modifiers are evaluated before wall UV mapping.
No windows or other architectural objects are invented to decorate the source.
See `apps/api/app/assets/README.txt` for asset sources and licenses.

The browser uses these embedded PBR textures with outdoor HDR illumination and
soft shadows for interactive editing. Trees use locally bundled branch/leaf
meshes, textured bark and a separate larch form. Their measured crown radii are
normalized to the planting plan. Tree forms and heights are illustrative.

**Фото · Cycles** produces a ray-traced CPU image with indirect lighting, denoising,
AgX tone mapping, a physical camera and automatic framing of the source objects.
The default photo quality is 1920×1080 / 256 samples. A faster 900×600 preview is
available. This photo pipeline is separate from the interactive editor snapshot.
The wider fitted camera avoids cropping tall source buildings by default.

**Фильтры** provides exposure, contrast, saturation, warmth, vignette and clarity
for the live viewport and **Снимок** PNG. Original 2D CAD colors and DXF exports
are unaffected. Source linework remains visible and can be toggled separately.

## Portable local download

**3D CAD** downloads the prepared source building/surface meshes as a GLB with
embedded PBR textures. It does not add neighboring blocks or contain the
proposed planting plan. The original CAD remains available separately.

**Фото · Cycles → Скачать сцену для Blender** exports the selected DXF and packages
`scene.json`, source render modules, photographic maps, HDR and tree assets.
The bundle contains no unrelated demonstration courtyard. Extract and run:

```sh
blender -b -t 8 --python render.py -- --scene scene.json --output render.png --samples 256 --width 1920 --height 1080 --blend scene.blend
```

Blender 4.0+ is recommended. No external rendering service, paid API or internet
connection is required at runtime. Open `scene.blend` to adjust material defaults,
height assumptions, camera or Cycles device. A render manifest records source
building/surface counts, plant coordinates, material use and assumptions.

## 360° panoramas

The 3D editor's **360° панорамы · Cycles** panel renders equirectangular
panoramas of the same Cycles scene. Pick a standing point with
**Указать точку кликом** or **Центр экрана**; the current view direction becomes
the image centre. Eye height (1.6 m pedestrian … 40 m drone), quality
(2048/4096/8192 px wide, 64/192/256 samples) and close-range lawn blades are
selectable. Panoramas open in a full-screen viewer: drag to look around, wheel or
pinch to zoom, rings on the ground and the mini-map move to neighbouring points.
All points share one deterministic scene, so trees, buildings and lighting stay
consistent between them.

The camera stands at eye height above the ground under the point; plantings and
fence/light rods are ignored when finding the ground, and a point inside a
building footprint is reported as `insideBuilding` in the manifest. Beyond the
site, the neutral ground plane is extended in a dark matte tone so the horizon
reads as distant ground; no context is added.

Lawn blades are instanced only inside source `GREEN_AREA` surfaces within 30 m
of the point, never over roads, paving or buildings, with density fading to 10 %
at the edge (`blender_grass.py`). They are a material-level detail of an
existing lawn, not new scenery.

Panoramas use the GPU automatically (OptiX → CUDA → HIP → Metal → oneAPI, CPU
fallback); set `BLENDER_DEVICE=CPU` to force the CPU. A 4096×2048 panorama
takes about a minute on an RTX 4060 Ti and much longer on a CPU. The API
needs Blender: set `BLENDER_EXECUTABLE` to its binary.

API: `POST /api/projects/{pid}/panoramas` with `x`, `y` (CAD metres), `heading`
(compass bearing of the image centre, 0 = +Y, clockwise), `eyeHeight`,
`quality`, `grass`, optional `iterationId` and `reconstruction`;
`GET /api/projects/{pid}/panoramas` lists points with state and progress;
the image is served by `/api/projects/{pid}/renders/{jobId}/image` (JPEG) and
the manifest by `/api/projects/{pid}/panoramas/{jobId}/manifest`. Locally:

```sh
blender -b -t 8 --python render.py -- --scene scene.json --output pano.jpg --view panorama --target-x 32 --target-y 20 --heading 0 --width 4096 --height 2048 --samples 192 --device AUTO --grass-radius 30
```

## Validation

Regression checks cover arbitrary layers and nested blocks, source preservation,
closed building outlines, absence of demo data in downloads, preview preparation
on upload/re-import, worker mode and planting movement. A real Blender canary
uses a CAD fixture with two building footprints and five classified surfaces;
its scene must contain exactly two buildings and no illustrative scenery.
