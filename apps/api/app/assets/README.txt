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
