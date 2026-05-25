"""No-LLM smoke demo of the PoC corpus tools.

Prints what the MCP tools would return, so you can sanity-check retrieval without
claude.ai or any tokens. Run:  uv run python scripts/poc_demo.py
"""

from __future__ import annotations

import json
import sys

sys.stdout.reconfigure(encoding="utf-8")

import base64
from pathlib import Path

from src.config import settings
from src.poc_corpus import Corpus

c = Corpus(settings.corpus_path, settings.assets_path)


def show(title, obj):
    print(f"\n=== {title} ===")
    print(json.dumps(obj, indent=2, ensure_ascii=False)[:1500])


show("list_decks()", c.list_decks())
show("corpus_stats()", c.corpus_stats())
show("list_vocabulary()", c.list_vocabulary())
show(
    "search_slides(slide_purpose='Finding', content_area='Market analysis', limit=3)",
    c.search_slides(slide_purpose="Finding", content_area="Market analysis", limit=3),
)
show(
    "find_similar_slides('foreign exchange policy recommendation', limit=3)",
    c.find_similar_slides("foreign exchange policy recommendation", limit=3),
)
show("get_house_style()", c.get_house_style())
show(
    "find_slide_templates(slide_purpose='Finding', limit=3)",
    c.find_slide_templates(slide_purpose="Finding", limit=3),
)
_first = c.list_decks()[0]["deck"]
show(f"get_deck_outline({_first!r})", c.get_deck_outline(_first))
show(f"start_deck({_first!r}) [logos base64 elided]", {
    **{k: v for k, v in (c.start_deck(_first) or {}).items() if k != "logos"},
    "logos": [
        {kk: (f"<{len(vv)} b64 chars>" if kk == "base64" else vv) for kk, vv in lg.items()}
        for lg in (c.start_deck(_first) or {}).get("logos", [])
    ],
})

# Asset serving: list each deck's recurring images (base64 elided) and decode one logo.
print("\n=== get_deck_assets() per deck ===")
for d in c.list_decks():
    assets = c.get_deck_assets(d["deck"]) or []
    print(f"  {d['deck']}: {[ (a['type'], a['filename'], len(a['base64'])) for a in assets ]}")
    for a in assets:
        if a["type"] == "logo":
            out = Path("tmp_logo_demo.png")
            out.write_bytes(base64.b64decode(a["base64"]))
            print(f"    -> decoded a logo to {out} ({out.stat().st_size} bytes): {a['image_path']}")
            break
