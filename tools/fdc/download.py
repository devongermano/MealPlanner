#!/usr/bin/env python3
"""Download USDA FoodData Central bulk CSVs (SR Legacy + Foundation Foods).

Raw dumps are cached OUTSIDE the repo (default /tmp/fdc-cache) and are never
committed. What IS committed: data/fdc/MANIFEST.yaml recording source URLs,
retrieval date, and sha256 of each archive — so the committed extract.json is
reproducible and auditable.

Usage:
    python tools/fdc/download.py [--cache-dir /tmp/fdc-cache] [--manifest data/fdc/MANIFEST.yaml]

USDA FoodData Central data is public domain (CC0 / U.S. government work).
https://fdc.nal.usda.gov/download-datasets.html
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import sys
import urllib.request
import zipfile
from pathlib import Path

# Dataset releases pinned by URL. SR Legacy is frozen (final release 2018-04);
# Foundation Foods gets new releases — bump the URL here, rerun download +
# transform, review the extract diff, commit (see tools/fdc/README.md).
DATASETS = {
    "sr_legacy": {
        "url": "https://fdc.nal.usda.gov/fdc-datasets/FoodData_Central_sr_legacy_food_csv_2018-04.zip",
        "release": "2018-04",
    },
    "foundation": {
        "url": "https://fdc.nal.usda.gov/fdc-datasets/FoodData_Central_foundation_food_csv_2025-12-18.zip",
        "release": "2025-12-18",
    },
}


def snapshot_label() -> str:
    """Stable label identifying this pair of source releases.

    Used as the <snapshot> part of provenance strings: usda_fdc:<fdc_id>@<snapshot>.
    """
    return "sr{}+fnd{}".format(
        DATASETS["sr_legacy"]["release"], DATASETS["foundation"]["release"]
    )


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch(url: str, dest: Path) -> None:
    print(f"  fetching {url}")
    tmp = dest.with_suffix(".part")
    with urllib.request.urlopen(url, timeout=120) as r, open(tmp, "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
    tmp.rename(dest)


def download_all(cache_dir: Path) -> list[dict]:
    """Download + extract each dataset into cache_dir/<name>/. Returns manifest rows."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for name, spec in DATASETS.items():
        zip_path = cache_dir / Path(spec["url"]).name
        if not zip_path.exists():
            fetch(spec["url"], zip_path)
        else:
            print(f"  cached: {zip_path}")
        digest = sha256_file(zip_path)
        extract_dir = cache_dir / name
        if not extract_dir.exists():
            print(f"  extracting -> {extract_dir}")
            with zipfile.ZipFile(zip_path) as z:
                z.extractall(extract_dir)
        rows.append(
            {
                "name": name,
                "release": spec["release"],
                "url": spec["url"],
                "sha256": digest,
                "size_bytes": zip_path.stat().st_size,
                "retrieved": datetime.date.today().isoformat(),
            }
        )
    return rows


def write_manifest(rows: list[dict], manifest_path: Path) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# USDA FoodData Central source manifest — written by tools/fdc/download.py.",
        "# Raw archives are cached outside the repo and never committed; this file",
        "# plus tools/fdc/transform.py reproduce data/fdc/extract.json exactly.",
        "# License: USDA FDC data is public domain (U.S. government work / CC0).",
        f"snapshot: {snapshot_label()}",
        "sources:",
    ]
    for r in rows:
        lines.append(f"  - name: {r['name']}")
        lines.append(f"    release: '{r['release']}'")
        lines.append(f"    url: {r['url']}")
        lines.append(f"    sha256: {r['sha256']}")
        lines.append(f"    size_bytes: {r['size_bytes']}")
        lines.append(f"    retrieved: '{r['retrieved']}'")
    manifest_path.write_text("\n".join(lines) + "\n")
    print(f"wrote {manifest_path}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cache-dir", type=Path, default=Path("/tmp/fdc-cache"))
    ap.add_argument(
        "--manifest",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "data" / "fdc" / "MANIFEST.yaml",
    )
    args = ap.parse_args(argv)
    rows = download_all(args.cache_dir)
    write_manifest(rows, args.manifest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
