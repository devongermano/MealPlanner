# Golden files

`solo_lifter_pipeline.json` — byte-stable serialization of the full pipeline
(load `tests/fixtures/solo_lifter` -> choose_menu -> build_week ->
session_plan -> purchase) at seed 0, produced by
`golden_payload()` in `tests/test_capabilities.py`.

**Regeneration policy (PRD §9):** goldens are byte-stable only on the pinned
reference environment (this repo's pinned Python + PuLP 3.3.2 / bundled CBC,
one OS/arch, enforced in CI); other platforms assert properties and tolerance
bands, not bytes. Never auto-regenerate: when an intended engine change diffs
the pipeline output, regenerate deliberately in a reviewed commit that
explains the behavior change, via:

    python tests/test_capabilities.py regen

Recorded on: macOS arm64, Python 3.14, PuLP 3.3.2 (CBC), 2026-08-09.
