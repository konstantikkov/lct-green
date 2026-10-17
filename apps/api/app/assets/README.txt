Botanical render meshes

Four generic trees authored for GreenPlan with Blender's Sapling generator:
small_maple, white_birch and japanese_maple presets, seeds 42–45,
with denser summer foliage and normalized crown radius.
Sapling / Blender Foundation: https://www.blender.org/
Sapling implements procedural tree generation based on Weber & Penn (1995).

These are generated mesh outputs, not copies of the add-on's Python code.
The generator is only needed to rebuild assets; runtime rendering loads meshes
from trees.blend. Authoring script: apps/api/scripts/create_render_assets.py.
Blender 4.0 or later is recommended for this library.

Mesh origins are ground level; reference crown radius is 1.45 m. Planting crown
radii from the selected plan scale the linked meshes. Forms are illustrative
and are not an exact botanical depiction of each species in the plan.

Photographic environment and ground maps (CC0, Poly Haven):
https://polyhaven.com/a/kloofendal_48d_partly_cloudy
https://polyhaven.com/a/grass_ground
License: https://polyhaven.com/license (CC0)

environment.hdr: 1K Kloofendal 48d Partly Cloudy HDRI.
ground_diff.jpg / ground_normal.jpg / ground_rough.jpg: 1K Grass Ground maps.
Downloaded from https://dl.polyhaven.org/ and bundled for offline rendering.
The environment and surface appearance are illustrative, not a site survey.

Source-surface photographic PBR maps (CC0, Poly Haven; 1K JPEG):
facade_*.jpg: https://polyhaven.com/a/brick_wall_001
asphalt_*.jpg: https://polyhaven.com/a/asphalt_02
paving_*.jpg: https://polyhaven.com/a/concrete_pavers
Each set contains diffuse, roughness and OpenGL normal maps. These are material
choices applied only to source CAD surfaces, not surveyed building finishes.
