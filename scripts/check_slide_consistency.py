"""Pre-render consistency gate for a generated slide (the v4 QA check).

Extracts a style+geometry signature for every text shape on a slide and, given one or
more 'sibling sets' (repeated elements that must look identical), asserts each set
collapses to a single signature. Exits non-zero if any set is inconsistent, so it can
gate the generate -> render loop.

Usage:
    # list every text shape's signature (find the shape_idx of your repeated set):
    uv run python -m scripts.check_slide_consistency deck.pptx --slide 3

    # assert the four tile labels (shapes 2-5) and the four bullets (8-11) are uniform:
    uv run python -m scripts.check_slide_consistency deck.pptx --slide 3 \
        --group 2,3,4,5 --group 8,9,10,11
"""

from __future__ import annotations

import argparse
import sys

from src.slide_inspect import consistency_report, slide_signatures


def _fmt(v: object) -> str:
    return "-" if v is None else str(v)


def main() -> None:
    # Slide text can contain characters outside the console codepage (e.g. ₦ on a
    # Windows cp932 console); never let printing the report crash the gate.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="Sibling-consistency gate for a slide.")
    ap.add_argument("pptx", help="path to the generated .pptx")
    ap.add_argument("--slide", type=int, default=0, help="0-based slide index")
    ap.add_argument(
        "--group",
        action="append",
        default=[],
        metavar="i,j,k",
        help="comma-separated shape_idx that should look identical (repeatable)",
    )
    args = ap.parse_args()

    sigs = slide_signatures(args.pptx, args.slide)
    print(f"# {len(sigs)} text shapes on slide {args.slide}:")
    for s in sigs:
        print(
            f"  [{s['shape_idx']:>2}] {s['text'][:32]:<32} "
            f"fill={_fmt(s['fill']):<10} bold={_fmt(s['bold']):<5} "
            f"size={_fmt(s['size_pt']):<5} font={_fmt(s['font']):<10} "
            f"algn={_fmt(s['algn']):<7} anchor={_fmt(s['anchor'])}"
        )

    if not args.group:
        print("\n# no --group given; nothing to assert. Pass --group i,j,k to gate a set.")
        return

    groups = [[int(x) for x in g.split(",")] for g in args.group]
    report = consistency_report(sigs, groups)
    print()
    for g in report["groups"]:
        if g["uniform"]:
            print(f"# OK  shapes {g['shapes']} share one signature")
        else:
            print(f"# DRIFT shapes {g['shapes']} differ on: {', '.join(g['differing_fields'])}")
            for field, vals in g["values"].items():
                print(f"      {field}: {[_fmt(v) for v in vals]}")

    if not report["ok"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
