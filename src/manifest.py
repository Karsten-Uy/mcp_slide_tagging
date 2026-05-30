"""Precomputed slide-count manifest for the source .pptx decks.

The corpus admission gate (Corpus.load) only keeps decks whose source .pptx slide
count matches the tagged JSON. Computing that count means opening every .pptx with
python-pptx — fine for a handful of decks, but it turns every server boot / cold
start into a full-corpus parse as the corpus grows.

This manifest precomputes `filename -> slide_count` once (at ingest / build time, via
scripts/build_manifest.py) so the gate is a cheap dict lookup. It's keyed by .pptx
filename — no deck/corpus knowledge — so the producer (slide_tagging) can emit it by
scanning its outputs. The server falls back to parsing any deck the manifest omits.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pptx import Presentation

MANIFEST_VERSION = 1


def _slide_count(path: Path) -> int:
    return len(Presentation(str(path)).slides)


def build_manifest(source_pptx_path: Path) -> dict[str, Any]:
    """Scan every *.pptx under `source_pptx_path` and return a manifest dict
    {"version": N, "slide_counts": {filename: count}}."""
    counts = {p.name: _slide_count(p) for p in sorted(Path(source_pptx_path).glob("*.pptx"))}
    return {"version": MANIFEST_VERSION, "slide_counts": counts}


def write_manifest(source_pptx_path: Path, out_path: Path) -> int:
    """Build the manifest for `source_pptx_path` and write it to `out_path`.
    Returns the number of decks recorded."""
    manifest = build_manifest(source_pptx_path)
    Path(out_path).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return len(manifest["slide_counts"])


def load_manifest(path: Path) -> dict[str, int]:
    """Read a manifest file and return its {filename: slide_count} map. Returns an
    empty dict if the file is absent (the server then falls back to parsing)."""
    p = Path(path)
    if not p.exists():
        return {}
    data = json.loads(p.read_text(encoding="utf-8"))
    return {str(k): int(v) for k, v in (data.get("slide_counts") or {}).items()}
