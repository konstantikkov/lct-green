# Real local CPU generation, not a reference/sample photograph

Project `prj_04f9c0fac6`, selected garden camera. The imported source has no closed building footprints, so this case deliberately contains no generated surrounding buildings.

- `source.png`: the minimal composition frame captured from the editor.
- `semantic.png`: ADE20K object labels from the same camera (trees, shrubs, ground, sky).
- `photo.png`: actual Realistic Vision 5.1 + ControlNet segmentation output.
- `manifest.json`: original inference metadata, camera, seed, prompts and measured runtime.

The CPU run generated 512×352 pixels in 446.4 seconds. It demonstrates the local generative handoff and improved interpretation of tree/shrub proxies. It does **not** demonstrate equivalence to the supplied detailed architectural reference photographs or guarantee exact CAD pixel fidelity. The source frame is included to make discrepancies reviewable.
