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

## Validation

Regression checks cover arbitrary layers and nested blocks, source preservation,
closed building outlines, absence of demo data in downloads, preview preparation
on upload/re-import, worker mode and planting movement. A real Blender canary
uses a CAD fixture with two building footprints and five classified surfaces;
its scene must contain exactly two buildings and no illustrative scenery.
