"""In-memory corpus for the PoC retrieval tools.

Loads every tagged Gen-2 JSON under `CORPUS_PATH` (the slide_tagging hand-labels),
strips the `_legend` aid, and exposes simple structured + keyword queries. No
Postgres, no embeddings — enough to demo retrieving slides by their basic labels.
Swap this for the pgvector path once the corpus is large enough to need it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_TOKEN = re.compile(r"[a-z0-9]+")


def _tokens(text: str | None) -> set[str]:
    return {w for w in _TOKEN.findall((text or "").lower()) if len(w) > 2}


def _ieq(a: Any, b: str | None) -> bool:
    return b is None or (a is not None and str(a).lower() == b.lower())


class Corpus:
    """All tagged decks held in memory, keyed by deck id (filename stem)."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.decks: dict[str, dict] = {}
        self.load()

    def load(self) -> None:
        self.decks = {}
        for f in sorted(self.path.glob("*.json")):
            data = json.loads(f.read_text(encoding="utf-8"))
            data.pop("_legend", None)  # tagging aid, not schema
            name = f.name
            deck_id = name[: -len(".tagged.json")] if name.endswith(".tagged.json") else f.stem
            self.decks[deck_id] = data

    # --- views -------------------------------------------------------------

    def list_decks(self) -> list[dict]:
        return [
            {
                "deck": did,
                "client_industry": d.get("client_industry"),
                "client_type": d.get("client_type"),
                "engagement_stage": d.get("engagement_stage"),
                "content_area": d.get("content_area"),
                "audience_level": d.get("audience_level"),
                "geography": d.get("geography"),
                "deck_summary": d.get("deck_summary_one_sentence"),
                "slide_count": len(d.get("slides", [])),
            }
            for did, d in self.decks.items()
        ]

    def _slide_view(self, deck_id: str, deck: dict, slide: dict) -> dict:
        return {
            "deck": deck_id,
            "index": slide.get("index"),
            "title_text": slide.get("title_text"),
            "main_message": slide.get("main_message"),
            "slide_purpose": slide.get("slide_purpose"),
            "message_type": slide.get("message_type"),
            "dominant_visual_element": slide.get("dominant_visual_element"),
            "chart_type": slide.get("chart_type"),
            "audience_level_slide": slide.get("audience_level_slide"),
            # deck context (handy for the agent)
            "client_industry": deck.get("client_industry"),
            "content_area": deck.get("content_area"),
        }

    # --- queries -----------------------------------------------------------

    def search_slides(
        self,
        *,
        slide_purpose: str | None = None,
        message_type: str | None = None,
        dominant_visual_element: str | None = None,
        client_industry: str | None = None,
        content_area: str | None = None,
        audience_level: str | None = None,
        text: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """Hybrid filter: deck-level constraints narrow the decks, slide-level
        constraints + a keyword scan over title/main_message pick the slides."""
        results: list[dict] = []
        needle = text.lower() if text else None
        for did, d in self.decks.items():
            if not _ieq(d.get("client_industry"), client_industry):
                continue
            if not _ieq(d.get("audience_level"), audience_level):
                continue
            if content_area is not None:
                areas = [str(c).lower() for c in (d.get("content_area") or [])]
                if content_area.lower() not in areas:
                    continue
            for s in d.get("slides", []):
                if not _ieq(s.get("slide_purpose"), slide_purpose):
                    continue
                if not _ieq(s.get("message_type"), message_type):
                    continue
                if not _ieq(s.get("dominant_visual_element"), dominant_visual_element):
                    continue
                if needle:
                    hay = f"{s.get('main_message') or ''} {s.get('title_text') or ''}".lower()
                    if needle not in hay:
                        continue
                results.append(self._slide_view(did, d, s))
        return results[:limit]

    def get_deck(self, deck: str) -> dict | None:
        """Everything needed to model a new deck on this one: deck-level tags, the
        Pipeline A `design_system` (fonts, colors, palette, grid, recurring
        elements), the observed `inferred_rules`, and a one-line outline of every
        slide. This is the design source-of-truth for generation."""
        d = self.decks.get(deck)
        if d is None:
            return None
        return {
            "deck": deck,
            "client_industry": d.get("client_industry"),
            "client_type": d.get("client_type"),
            "engagement_stage": d.get("engagement_stage"),
            "content_area": d.get("content_area"),
            "audience_level": d.get("audience_level"),
            "geography": d.get("geography"),
            "confidentiality_tier": d.get("confidentiality_tier"),
            "deck_summary": d.get("deck_summary_one_sentence"),
            "design_system": d.get("design_system"),
            "inferred_rules": d.get("inferred_rules"),
            "slides": [
                {
                    "index": s.get("index"),
                    "slide_purpose": s.get("slide_purpose"),
                    "message_type": s.get("message_type"),
                    "dominant_visual_element": s.get("dominant_visual_element"),
                    "title_text": s.get("title_text"),
                    "main_message": s.get("main_message"),
                }
                for s in d.get("slides", [])
            ],
        }

    def get_slide(self, deck: str, index: int) -> dict | None:
        d = self.decks.get(deck)
        if d is None:
            return None
        for s in d.get("slides", []):
            if s.get("index") == index:
                return {
                    "deck": deck,
                    "deck_context": {
                        "client_industry": d.get("client_industry"),
                        "client_type": d.get("client_type"),
                        "audience_level": d.get("audience_level"),
                        "content_area": d.get("content_area"),
                        "deck_summary": d.get("deck_summary_one_sentence"),
                    },
                    "slide": s,  # full tag set for this slide
                }
        return None

    def find_similar_slides(self, text: str, limit: int = 10) -> list[dict]:
        """Rank slides by keyword overlap of `text` with their main_message/title
        (a cheap, dependency-free stand-in for embedding search)."""
        q = _tokens(text)
        scored: list[tuple[float, dict]] = []
        for did, d in self.decks.items():
            for s in d.get("slides", []):
                t = _tokens(f"{s.get('main_message') or ''} {s.get('title_text') or ''}")
                if not t:
                    continue
                overlap = len(q & t) / (len(q) or 1)
                if overlap > 0:
                    scored.append((overlap, self._slide_view(did, d, s)))
        scored.sort(key=lambda x: -x[0])
        return [{**v, "score": round(sc, 3)} for sc, v in scored[:limit]]
