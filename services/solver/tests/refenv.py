"""Reference-environment detection (PRD §9 golden policy).

Byte-stable goldens and exact solve-count baselines are asserted ONLY on the
pinned reference environment recorded in tests/golden/README.md and
BASELINES.md: macOS arm64, CPython 3.14, PuLP 3.3.2 (bundled CBC). Every
other platform asserts properties and tolerance bands instead — CBC binaries
differ across OS/arch and may legally pick different optima of equal cost,
so byte-claims off the reference environment would be red by construction
(the exact v1 failure mode PRD §9 retired).
"""

import platform
import sys

REFERENCE = dict(os="darwin", machine="arm64", python=(3, 14),
                 pulp="3.3.2")


def is_reference_env() -> bool:
    try:
        import pulp
        pulp_version = pulp.__version__
    except Exception:                        # pragma: no cover
        pulp_version = None
    return (sys.platform == REFERENCE["os"]
            and platform.machine() == REFERENCE["machine"]
            and sys.version_info[:2] == REFERENCE["python"]
            and pulp_version == REFERENCE["pulp"])


def off_reference_reason() -> str:
    return ("byte-stable goldens / exact solve counts are asserted only on "
            f"the pinned reference environment {REFERENCE} "
            "(tests/golden/README.md); this platform asserts properties and "
            "tolerance bands instead")
