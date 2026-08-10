# Golden files

`solo_lifter_pipeline.json` — byte-stable serialization of the full pipeline
(load `tests/fixtures/solo_lifter` -> choose_menu -> build_week ->
session_plan -> purchase) at seed 0, produced by
`golden_payload()` in `tests/test_capabilities.py`.

**Regeneration policy (PRD §9):** goldens are byte-stable only on the pinned
reference environment (this repo's pinned Python + PuLP 3.3.2 / bundled CBC,
one OS/arch, enforced in CI); other platforms assert properties and tolerance
bands, not bytes. **Mechanism** (`tests/refenv.py`): `is_reference_env()`
gates the byte compare (`test_determinism_golden_full_pipeline_byte_stable`)
and the exact solve-count baseline (`test_perf_budget`), which SKIP /
band-relax off-reference; `test_golden_pipeline_properties_all_platforms`
asserts the frozen pipeline's properties — feasibility, day counts,
purchase coverage, same-machine re-run determinism — everywhere. Never
auto-regenerate: when an intended engine change diffs the pipeline output,
regenerate deliberately in a reviewed commit that explains the behavior
change, via:

    python tests/test_capabilities.py regen

Recorded on: macOS arm64, Python 3.14, PuLP 3.3.2 (CBC), 2026-08-09.

Regenerated 2026-08-09 (M1.10): `session_plan` sessions gained `feeds` —
the day-level attribution rows (component, day, grams) the portioning
matrix reshapes (M19_SPEC §1). Purely additive: batches, minutes,
portions, and purchasing are byte-identical to the previous golden.
