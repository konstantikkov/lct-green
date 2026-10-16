# Local rendering and CAD fidelity update

The interactive Three.js view remains a fast planning view. Final images can now
be rendered offline with Blender/Cycles on CPU. No external image service or
paid API is required.

## Render a selected plan

1. Open a project and generate/select a plan.
2. Open **Фоторендер** → **Скачать сцену для Blender**. The API first exports the final DXF, then packages
   `plan.dxf`, `scene.json`, `render.py`, and instructions into a ZIP.
3. Extract it, install Blender 4.0 or later, and run from the extracted directory:

```sh
blender -b -t 8 --python render.py -- --scene scene.json --output render.png --samples 128 --width 1920 --height 1080 --blend scene.blend
```

For final delivery use 512 samples and 3840×2160. Render time depends on the
machine and scene. CPU is the portable default. Open `scene.blend` to adjust the
camera, materials, and Cycles device in Blender. Camera options include
`--azimuth 225 --elevation 30 --distance 1.0`.

The scene uses physical sky and sunlight, ray-traced shadows and indirect light,
procedural surface textures, and four bundled Sapling-generated tree meshes with branches and
individual leaves. Denoising is enabled when supported; Linux builds without
OpenImageDenoise automatically render without it.

For higher botanical detail, supply your own locally licensed Blender asset
library with mesh objects named `TREE` and `SHRUB` using `--assets trees.blend`.
Objects should use a ground-level origin and the generator's reference crown
radius of 1.45 m. Their mesh data and materials are instanced at plan coordinates.

### Accuracy and assumptions

Planting coordinates and crown radii come directly from the selected plan.
Only classified building, road, sidewalk, parking and green-area polygons become
surfaces. Unknown objects are retained in the CAD drawing. Building heights
are illustrative (12 m unless an entity supplies `heightM`); regular facade windows are generic. Real facades, terrain,
and a surveyed 3D environment cannot be recovered from a flat drawing. This is
an architectural visualization, not a surveyed digital twin. Scene assumptions
are included in `scene.json`. Production quality requires camera composition,
reviewed semantic mappings, and suitable vegetation/context assets.

## CAD import and display

* Import accepts arbitrary layer names. Empty declared layers are retained.
* Nested block references expand recursively with transformed coordinates and
  inherited layer-0/BYBLOCK properties. The former 400-object block cutoff is removed.
* The interactive document API returns all imported entities, with no category
  priority filtering or 20,000-entity cutoff. Large drawings use more memory.
* Source entity/layer colors replace category colors for CAD linework.
* **CAD оригинал** opens the source modelspace through ezdxf's drawing frontend:
  text, hatches, dimensions and block depiction follow CAD properties rather
  than semantic labels. CAD visibility settings are respected.
* Proprietary proxy objects, external raster references, missing xrefs, custom
  fonts and unsupported CAD entities can still differ from AutoCAD. Import
  warnings identify unsupported preview entities; the source DXF is retained.
* **Обновить импорт** reparses an existing project's saved DXF with the new
  importer while retaining user/package mappings. Regenerate the plan afterwards;
  historical results are retained and were validated against the previous import.

Classification rules suggest categories for planning; they never decide whether
a layer is imported or displayed. Existing per-layer editing remains available.
Arbitrary conventions can also be mapped in one API request:

```http
PUT /api/projects/{projectId}/layer-mapping
Content-Type: application/json

{"mappings":{"A-123":"BUILDING","CUSTOM_NETWORK":"UTILITY_WATER"}}
```

Mapping validates every supplied layer/category before changing anything. It
returns `regenerateRequired: true`; regenerate after semantic changes.

Additional endpoints:

* `GET /api/projects/{id}/cad-preview.svg`
* `GET /api/projects/{id}/export/render-bundle`
* `POST /api/projects/{id}/refresh-import`

Re-upload the original package to re-run package assembly; refresh-import reads
the already merged DXF, retaining its existing clipping and missing-xref limitations.

## Render directly in the application

Open **Фоторендер**, set the camera direction/elevation/distance and click
**Создать изображение**. The API runs CPU Blender locally, then offers PNG
preview/download. The server queue permits one active job and two waiting jobs;
other requests receive HTTP 429. Each job has a 30 minute timeout. The API host
needs `blender` in PATH (`sudo apt-get install blender` on Ubuntu). Deployment
installs it; Docker includes it by default (`--build-arg WITH_BLENDER=0` omits it).

`POST /api/projects/{id}/renders` accepts width (320–3840), height (240–2160),
samples (16–512), azimuth, elevation, distance, exposure and optional target_x /
target_y in source meters. Poll the returned statusUrl and retrieve imageUrl
when state becomes COMPLETED. A service restart marks unfinished jobs failed;
completed images remain on disk. Remove old `outputs/render_jobs` directories
when no longer required. Full offline operation needs no LLM or internet.

The bundled tree library is generated offline by the authoring script in
`apps/api/scripts/create_render_assets.py`; Blender Sapling is not a runtime dependency.

Some distro Blender packages omit CPU denoising. To install an official local
Blender build with OpenImageDenoise, run:

```sh
bash scripts/install_local_blender.sh /opt/greenplan
# Add to the API environment and restart:
# BLENDER_EXECUTABLE=/opt/greenplan/blender-4.5.3-linux-x64/blender
```

The installer verifies the archive against Blender's published SHA-256 list.
Downloading happens once at installation; rendering works offline. The host can
use any compatible installed Blender by setting `BLENDER_EXECUTABLE`.

## Photographic materials

The package includes Poly Haven CC0 photographic ground color/normal/roughness
maps and an HDR environment. They improve lighting, reflections, and surface
detail without any runtime downloads. Asset links and license credits ship as
`ASSET_CREDITS.txt` in each portable package. The photographed HDR environment
is illustrative, not the actual surroundings of the project. Replace
`environment.hdr` with a site-matched environment when available; omit it to use
the procedural sky. Facade windows are generic visual detail. Exported editable
Blender scenes pack their image resources for portability.
