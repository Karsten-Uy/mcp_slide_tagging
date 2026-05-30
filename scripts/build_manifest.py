"""Precompute the source-.pptx slide-count manifest.

Run this whenever you add or change source decks under the source .pptx folder, so
the server's corpus-admission gate (slide-count alignment) is a cheap dict lookup
instead of parsing every .pptx on each boot / cold start.

Usage:
    uv run python -m scripts.build_manifest                 # uses SOURCE_PPTX_PATH
    uv run python -m scripts.build_manifest path/to/source  # explicit folder

Writes <source>/manifest.json (where the server looks by default).
"""

from __future__ import annotations

import sys
from pathlib import Path

from src.config import settings
from src.manifest import write_manifest


def main() -> None:
    source = Path(sys.argv[1]) if len(sys.argv) > 1 else settings.source_pptx_path
    if not source.is_dir():
        sys.exit(f"source .pptx folder not found: {source}")
    out = source / "manifest.json"
    n = write_manifest(source, out)
    print(f"wrote {out} ({n} deck{'s' if n != 1 else ''})")


if __name__ == "__main__":
    main()
