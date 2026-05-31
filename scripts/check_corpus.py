"""Pre-deploy corpus preflight: which decks will actually be served, and why not.

The server's admission gate drops any deck whose source .pptx is missing or whose slide
count doesn't align with the tags — silently (one stderr line). That once shipped an
*empty* deploy when the .pptx weren't committed. Run this before committing/redeploying
to see the verdict per deck instead of finding out from a confusingly empty connector.

Usage:
    uv run python -m scripts.check_corpus                 # uses CORPUS_PATH / SOURCE_PPTX_PATH
    CORPUS_PATH=corpus SOURCE_PPTX_PATH=corpus/source uv run python -m scripts.check_corpus
    uv run python -m scripts.check_corpus --strict        # exit 1 if any deck is excluded
"""

from __future__ import annotations

import argparse
import sys

from src.config import settings
from src.poc_corpus import Corpus


def main() -> None:
    ap = argparse.ArgumentParser(description="Report deploy-ready vs excluded corpus decks.")
    ap.add_argument(
        "--strict", action="store_true", help="exit non-zero if any deck is excluded"
    )
    args = ap.parse_args()
    # Reconfigure BOTH streams before constructing Corpus — load() logs exclusions to
    # stderr, which on a Windows cp932 console can't encode non-ASCII filenames/reasons.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:
            pass

    corpus = Corpus(settings.corpus_path, settings.assets_path, settings.source_pptx_path)
    audit = corpus.audit()
    loaded, excluded = audit["loaded"], audit["excluded"]

    print(f"corpus:  {settings.corpus_path}")
    print(f"source:  {settings.source_pptx_path}")
    print(f"\n{len(loaded)} deploy-ready (tagged + aligned .pptx):")
    for d in loaded:
        print(f"  OK       {d}")
    if excluded:
        print(f"\n{len(excluded)} EXCLUDED (won't appear in ANY endpoint):")
        for e in excluded:
            print(f"  EXCLUDED {e['deck']}  ->  {e['reason']}")
        print("\nFix: bundle the deck's .pptx into the source folder and align its slide "
              "count (see docs/HANDOFF-slide_tagging.md), then regenerate the manifest.")
    elif loaded:
        print("\nAll decks are deploy-ready.")
    else:
        print("\nWARNING: no decks loaded — the corpus is EMPTY (wrong CORPUS_PATH, or "
              "nothing committed/bundled). The server would come up serving 0 decks.")

    # Fail strict on exclusions OR an empty corpus (an empty deploy is the very thing
    # this preflight exists to catch).
    if args.strict and (excluded or not loaded):
        sys.exit(1)


if __name__ == "__main__":
    main()
