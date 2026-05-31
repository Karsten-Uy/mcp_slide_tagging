"""Generated-slide consistency checker — the v4 QA gate.

Clone-and-edit drifts on three independent axes: run character style (fill/bold/
font/size), paragraph alignment, and box geometry (width/height/anchor). A "looks
fine" check on one axis says nothing about the others, and a bare "run has no rPr"
grep misses the nastiest case: a run *with* an rPr that sets none of fill/bold/font,
so it silently inherits the theme (the black-label bug).

This module extracts a per-shape signature on all three axes, then asserts a
"sibling set" (repeated elements meant to look identical — e.g. the four tile labels)
collapses to a single signature. Missing fill reads as None (= inherits theme),
missing bold as None (= not bold), etc., so an explicit-white sibling and an
inherited one compare unequal — which is exactly the drift we want to flag.

Pure, dependency-light (python-pptx only); runs client-side on the agent's *output*,
never on the read-only corpus server.
"""

from __future__ import annotations

import io
from typing import Any

from pptx import Presentation

# Axes a repeated set must agree on. `left`/`top` are intentionally excluded:
# siblings sit at different positions by design, but should share these.
STYLE_FIELDS = ("fill", "bold", "size_pt", "font", "algn", "anchor", "width", "height")


def _enum_name(value: Any) -> str | None:
    if value is None:
        return None
    return getattr(value, "name", str(value))


def _run_color(run: Any) -> str | None:
    """The run's explicit color: an RGB hex string, 'theme:<NAME>', or None when the
    run sets no color (so it inherits the placeholder/theme — the black-label trap)."""
    color = run.font.color
    if color is None or color.type is None:
        return None
    try:
        return str(color.rgb)
    except Exception:
        return f"theme:{_enum_name(getattr(color, 'theme_color', None))}"


def _first_run(shape: Any) -> tuple[Any | None, Any | None]:
    """The (paragraph, run) of the first non-empty paragraph — so the representative
    run and the alignment we report come from the *same* paragraph (a blank leading
    paragraph can carry a different alignment than the one that holds the text)."""
    for para in shape.text_frame.paragraphs:
        if para.runs:
            return para, para.runs[0]
    return None, None


def slide_signatures(source: bytes | str, slide_index: int = 0) -> list[dict[str, Any]]:
    """Return a style+geometry signature for every text shape on the slide, in shape
    order. `source` is a .pptx path or its bytes. The representative run/paragraph is
    the first run of the first non-empty paragraph (falling back to paragraph 0)."""
    prs = Presentation(io.BytesIO(source) if isinstance(source, bytes) else source)
    slide = list(prs.slides)[slide_index]
    out: list[dict[str, Any]] = []
    for idx, shape in enumerate(slide.shapes):
        if not shape.has_text_frame:
            continue
        tf = shape.text_frame
        rep_para, run = _first_run(shape)
        if rep_para is None:
            rep_para = tf.paragraphs[0] if tf.paragraphs else None
        size = run.font.size if run is not None else None
        out.append(
            {
                "shape_idx": idx,
                "text": (tf.text or "").strip()[:60],
                "fill": _run_color(run) if run is not None else None,
                "bold": run.font.bold if run is not None else None,
                "size_pt": (size.pt if size is not None else None),
                "font": run.font.name if run is not None else None,
                "algn": _enum_name(rep_para.alignment) if rep_para is not None else None,
                "anchor": _enum_name(tf.vertical_anchor),
                "left": shape.left,
                "top": shape.top,
                "width": shape.width,
                "height": shape.height,
            }
        )
    return out


def consistency_report(
    signatures: list[dict[str, Any]], groups: list[list[int]]
) -> dict[str, Any]:
    """For each group (a list of shape_idx that should be identical), report whether
    the set collapses to one signature across STYLE_FIELDS, and which fields differ.

    `ok` is True only if every group is uniform. Geometry is compared with a 1pt
    (12700 EMU) tolerance so sub-perceptible rounding doesn't trip the gate."""
    by_idx = {s["shape_idx"]: s for s in signatures}
    geom_tol = 12700  # 1pt in EMU
    group_reports: list[dict[str, Any]] = []
    for shapes in groups:
        present = [by_idx[i] for i in shapes if i in by_idx]
        differing: list[str] = []
        values: dict[str, list[Any]] = {}
        for field in STYLE_FIELDS:
            vals = [s.get(field) for s in present]
            if field in ("width", "height"):
                nums = [v for v in vals if isinstance(v, int)]
                # An empty/all-absent group is uniform (nothing to compare) — matching
                # the non-geometry branch — instead of false "drift" with empty values.
                uniform = (not vals) or (
                    len(nums) == len(vals) and (max(nums) - min(nums) <= geom_tol)
                )
            else:
                uniform = len(set(vals)) <= 1
            if not uniform:
                differing.append(field)
                values[field] = vals
        group_reports.append(
            {
                "shapes": shapes,
                "uniform": not differing,
                "differing_fields": differing,
                "values": values,
            }
        )
    return {"ok": all(g["uniform"] for g in group_reports), "groups": group_reports}
