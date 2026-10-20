> Для нового сценария «минимальный макет → локальная AI-фотография» см. [LOCAL_PHOTO.md](LOCAL_PHOTO.md). Этот документ описывает технический Blender/Cycles-рендер и экспорт геометрии.

# Source-only CAD rendering

The editor, local scene bundle and Cycles export render source CAD objects and
the selected planting plan. There is no generated courtyard, surrounding housing,
pergola, furniture, additional paving or automatic perennial bed. The former
presentation-context generator and its UI toggle were removed. Legacy
`detail=presentation` requests remain accepted but cannot add scenery.

## Instant human-height walkthrough

Drag **🚶 Вид от человека** onto the CAD map or 3D viewport to enter the
existing scene at 1.6 m above the ground. Clicking the button and then the ground
also works. Drag to look around; WASD, arrow keys, the wheel or on-screen buttons
move the viewpoint. Escape or **Вернуться к обзору** restores the overview.
Camera-photo generation uses the current human viewpoint and viewing direction.
This WebGL view opens immediately and does not run a panorama render.

Cycles panorama tours remain a separate offline feature. A 12-point high-quality
tour can take many hours on this CPU server. New tours default to six preview
points; this does not change already running jobs.

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

The 3D editor's **360° панорамы · прогулка по участку** panel renders
equirectangular Cycles panoramas of the same scene and links them into a
walkable tour, like street-level imagery.

**Создать тур автоматически** places 6, 12 or 20 standing points
(`panorama_tour.py`): source paths (`SIDEWALK`) first, then lawns and parking,
spread evenly (farthest-point seeding, then Lloyd relaxation so points move from
site corners to the middle of their zone). Points keep 0.6 m from surface edges,
1.5 m from buildings, and stay clear of tree trunks and shrub crowns. Without
paths or lawns, the site minus roads is used. Neighbours are linked when the
line between them does not cross a building, at most one per 45° sector, and
links are symmetric so every move has a way back. Each point initially faces
the next point of the walk.

One Blender process renders all points from one scene build. Each finished
image appears atomically, so the first points can be viewed while the rest
render; progress and remaining time cover the whole tour. **Добавить точку
вручную** adds single points (click on the ground or the screen centre); they
link to tour points within 40 m. Blue pins in the 3D editor open finished
panoramas; grey pins are still rendering.

The viewer: drag to look around, wheel or pinch to zoom, chevrons on the ground
or a click on the ground move to a neighbour, W/↑ and S/↓ step forward/back
along the view direction, A/D turn, F toggles full screen. Moves zoom and fade
into the next point; neighbour images are preloaded. A mini-map shows points,
links and the view cone. Eye height (1.6 m pedestrian … 40 m drone), quality
(2048/4096/8192 px wide, 64/192/256 samples) and lawn blades are selectable.

The camera stands at eye height above the ground under the point; plantings,
lawn blades and fence/light rods are ignored when finding the ground, and a point
inside a building footprint is reported as `insideBuilding` in the manifest.
Beyond the site, the neutral ground plane is extended in a dark matte tone so
the horizon reads as distant ground; no context is added.

Lawn blades are instanced only inside source `GREEN_AREA` surfaces within 30 m
of the point, never over roads, paving or buildings, with density fading to 10 %
at the edge (`blender_grass.py`). They are a material-level detail of an
existing lawn, not new scenery.

Panoramas use the GPU automatically (OptiX → CUDA → HIP → Metal → oneAPI, CPU
fallback); set `BLENDER_DEVICE=CPU` to force the CPU. On an RTX 4060 Ti a 4K
point takes about a minute and a 6-point 2K tour about three and a half minutes;
a CPU is much slower. The API needs Blender: set `BLENDER_EXECUTABLE`.

API: `POST /api/projects/{pid}/panorama-tour` (`maxPoints`, `eyeHeight`,
`quality`, `grass`, optional `iterationId`, `reconstruction`, `ageYears`, `season`) and
`POST /api/projects/{pid}/panoramas` (`x`, `y` in CAD metres, `heading` = compass
bearing of the image centre, 0 = +Y, clockwise) return one item per point;
`GET /api/projects/{pid}/panoramas` lists points with state, progress and links;
`/api/projects/{pid}/panoramas/{jobId}/{index}/image` (JPEG) and `/manifest`
serve results; `DELETE /api/projects/{pid}/panoramas/{jobId}` removes a finished
tour or point. Locally (`points.json`: `[{x, y, heading, output}]`):

```sh
blender -b -t 8 --python render.py -- --scene scene.json --output pano_000.jpg --view panorama --points points.json --width 4096 --height 2048 --samples 192 --device AUTO --grass-radius 30 --season autumn --age-years 15
```

## Plant age

Plants can be shown younger than mature: **при посадке**, **через 5 / 15 / 30
лет**, or **зрелые** (default). The 3D editor has an «Возраст посадок» slider;
the panorama panel has the same choice for tours and single points
(`ageYears` in `POST /panoramas` and `POST /panorama-tour`, passed to Blender
as `--age-years`; omitted = mature). The viewer header shows the age
(«через 15 лет»).

Size follows an **illustrative** growth model, not survey or nursery data:
`rules/species.yaml` gives each species a planting stock (`planting_stock`:
height range and crown radius at planting) and `growth.years_to_mature`, both
`status: illustrative`. Size grows from the stock to the mature parameters
along u·(1.5 − 0.5·u), u = min(years / years_to_mature, 1). The same function
exists in `app/services/growth.py` (imported by `blender_scene.py` and shipped
in the portable bundle) and `apps/web/src/viewer/growth.ts`;
`tests/test_growth.py` checks both against `tests/golden/growth_points.json`.

Each plant instance is scaled by the ratio of its age size to its mature size:
crown radius in plan (X, Y), height vertically. The mature look of a plant
stays as before. Age affects **only the picture**: the plan, the validator,
the planting schedule and the DXF export always use mature crowns. The
editor's planting circles keep showing the mature crown footprint.

## Seasons

`season` = `spring` | `summer` (default) | `autumn` | `winter` in
`POST /panoramas`, `POST /panorama-tour` and `POST /renders`, passed to Blender
as `--season`; the panorama panel has a «Сезон» choice and the viewer header
names the season. All changes are materials, light and leafless copies of the
existing plant meshes (`blender_season.py`); no geometry is added.

| Season | Foliage | Ground and light |
|---|---|---|
| summer | unchanged | unchanged |
| spring | light young green; lilac (*Syringa*) and mock orange (*Philadelphus*) flower through a noise-masked blossom colour in the leaf material | unchanged |
| autumn | the «Season palette» ramp on every leaf material, toned per genus (linden and larch yellow, maple orange, rowan and cotoneaster red, lilac drops its leaves green) with a little per-plant spread | warmer sun at ~25°, slightly desaturated HDR |
| winter | deciduous species (`foliage: deciduous` in `species.yaml`; all catalog species, Siberian larch included) render only their branches — leaf faces are deleted, so nothing floats | lawns, open ground and the horizon plane turn to matte snow; no lawn blades are scattered; cooler sun at ~20°, greyer HDR |

The «Season palette» node is now driven by `--season`; the legacy
`--detail presentation` no longer switches it on. Autumn tones and blossom
colours are illustrative appearance choices, not phenology data. The 3D
editor tints crowns lightly to match (winter: faint bare-wood crowns) — only
Cycles renders show leaf loss and snow.

Paved surfaces stay clear in winter. Planting beds that sit slightly below a
lawn keep that step under the snow, which shows as a thin shadow line.

## Validation

Regression checks cover arbitrary layers and nested blocks, source preservation,
closed building outlines, absence of demo data in downloads, preview preparation
on upload/re-import, worker mode and planting movement. A real Blender canary
uses a CAD fixture with two building footprints and five classified surfaces;
its scene must contain exactly two buildings and no illustrative scenery.
