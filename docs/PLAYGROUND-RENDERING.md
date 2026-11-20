# Playground rendering

Qwen 2511 uses its own reference-conditioned workflow in `job_playground`.
Do not pass its result through `_render_images`: that dispatcher's generic fallback
is Z-Image, and it would replace the identity-conditioned result with a second,
unconditioned generation. Keep the original output items for saving.

Deduplicate selected character IDs, describe the exact cast count, and distinguish
reference portraits from people in the final scene. Camera azimuth is a fixed
viewpoint for a still, not an instruction to render an orbit or multiple views.

Regression coverage: `tests/test_playground_regression.py` asserts one model call,
preservation of the conditioned output, unique cast and fixed-viewpoint language.
