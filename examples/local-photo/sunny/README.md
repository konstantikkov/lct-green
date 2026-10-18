# Sunny presentation with illustrative distant greenery

Actual local CPU output, using the same camera/source frame as [the original canary](../README.md). `photo.png` is generated output, not a supplied reference photograph. The source CAD and planting coordinates are unchanged.

The sunny preset and background conditioning replace the empty horizon with distant greenery. That context is illustrative and is not a survey of the neighborhood. `context_mask.png` records input-guide changes; `guide_validation.json` verifies that applying the final camera/bounds guard produces byte-identical guides for this real case. The original inference metadata is in `manifest.json`.
