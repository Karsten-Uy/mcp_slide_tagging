"""Extract one slide from a deck into a standalone, self-contained .pptx.

The server ships a single planned reference slide to the agent as a real .pptx so
it can clone the actual shapes/geometry/fills with python-pptx — far higher fidelity
than rebuilding from the tag summary. We keep the target slide and drop every other
slide's id + relationship, leaving its layout/master/theme/media intact so the
result opens standalone in PowerPoint.
"""

from __future__ import annotations

import io

from pptx import Presentation


def extract_single_slide(pptx_bytes: bytes, index: int) -> bytes:
    """Return a one-slide .pptx containing only slide `index` (0-based) of the deck.

    The retained slide keeps its layout/master/theme and referenced media, so the
    result is a valid standalone deck. Raises IndexError if `index` is out of range.
    """
    prs = Presentation(io.BytesIO(pptx_bytes))
    sld_id_lst = prs.slides._sldIdLst  # CT_SlideIdList
    sld_ids = list(sld_id_lst)
    n = len(sld_ids)
    if index < 0 or index >= n:
        raise IndexError(f"slide index {index} out of range (deck has {n} slides)")

    for i, sld_id in enumerate(sld_ids):
        if i == index:
            continue
        sld_id_lst.remove(sld_id)
        prs.part.drop_rel(sld_id.rId)

    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()
