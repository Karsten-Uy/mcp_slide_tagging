"""In-memory corpus for the PoC retrieval tools.

Loads every tagged Gen-2 JSON under `CORPUS_PATH` (the slide_tagging hand-labels),
strips the `_legend` aid, and exposes simple structured + keyword queries. No
Postgres, no embeddings — enough to demo retrieving slides by their basic labels.
Swap this for the pgvector path once the corpus is large enough to need it.
"""

from __future__ import annotations

import base64
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

_TOKEN = re.compile(r"[a-z0-9]+")

# Deck/slide fields surfaced by list_vocabulary (the ones search_slides filters on,
# plus a few useful extras). Scalar vs. list-valued are handled separately.
_VOCAB_DECK = (
    "client_industry", "client_type", "engagement_stage", "audience_level",
    "geography", "deliverable_format", "confidentiality_tier",
)
_VOCAB_DECK_LIST = ("content_area",)
_VOCAB_SLIDE = (
    "slide_purpose", "message_type", "dominant_visual_element", "chart_type",
    "audience_level_slide", "slide_position_role", "placeholder_compliance",
    "reusability_score_qualitative", "tier_match_difficulty",
)
_VOCAB_SLIDE_LIST = ("slot_types_present",)

# Order for ranking reusable templates (ReusabilityScore enum: High/Medium/Low).
_REUSE_RANK = {"High": 3, "Medium": 2, "Low": 1}


def _tokens(text: str | None) -> set[str]:
    return {w for w in _TOKEN.findall((text or "").lower()) if len(w) > 2}


def _ieq(a: Any, b: str | None) -> bool:
    return b is None or (a is not None and str(a).lower() == b.lower())


def _top1(counter: Counter) -> Any:
    return counter.most_common(1)[0][0] if counter else None


class Corpus:
    """All tagged decks held in memory, keyed by deck id (filename stem)."""

    def __init__(self, path: Path, assets_path: Path | None = None) -> None:
        self.path = Path(path)
        # Where recurring-element image_paths ("assets/<slug>/x.png") resolve to disk.
        self.assets_path = Path(assets_path) if assets_path else self.path / "assets"
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
            # True if any recurring element has an extracted image; call get_deck_assets to fetch bytes.
            "recurring_assets_available": any(
                r.get("image_path")
                for r in (d.get("design_system", {}) or {}).get("recurring_elements", [])
            ),
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

    def get_deck_assets(self, deck: str) -> list[dict] | None:
        """A deck's extracted recurring branding images (logos/watermarks) as base64
        PNGs. None if the deck is unknown; [] if none extracted. Missing files are
        skipped (a stale snapshot degrades gracefully)."""
        d = self.decks.get(deck)
        if d is None:
            return None
        out: list[dict] = []
        for r in (d.get("design_system", {}) or {}).get("recurring_elements", []):
            ip = r.get("image_path")
            if not ip:
                continue
            rel = ip[len("assets/"):] if ip.startswith("assets/") else ip
            fpath = self.assets_path / rel
            if not fpath.exists():
                print(f"# get_deck_assets: missing asset {fpath}", file=sys.stderr)
                continue
            out.append(
                {
                    "type": r.get("type"),
                    "value": r.get("value"),
                    "source": r.get("source"),
                    "position": r.get("position"),
                    "appears_on_slides": r.get("appears_on_slides", []),
                    "image_path": ip,
                    "filename": fpath.name,
                    "mime_type": "image/png",
                    "base64": base64.b64encode(fpath.read_bytes()).decode(),
                }
            )
        return out

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

    # --- aggregate / structure-aware views -------------------------------------

    def list_vocabulary(self) -> dict[str, list[str]]:
        """The distinct values *present in the corpus* for each filterable field, so
        an agent uses real `search_slides` filter strings (exact-match) instead of
        guessing values that silently return nothing. Empty fields are omitted."""
        vocab: dict[str, set[str]] = {
            f: set() for f in (*_VOCAB_DECK, *_VOCAB_DECK_LIST, *_VOCAB_SLIDE, *_VOCAB_SLIDE_LIST)
        }
        for d in self.decks.values():
            for f in _VOCAB_DECK:
                if d.get(f) not in (None, ""):
                    vocab[f].add(str(d[f]))
            for f in _VOCAB_DECK_LIST:
                vocab[f].update(str(v) for v in (d.get(f) or []))
            for s in d.get("slides", []):
                for f in _VOCAB_SLIDE:
                    if s.get(f) not in (None, ""):
                        vocab[f].add(str(s[f]))
                for f in _VOCAB_SLIDE_LIST:
                    vocab[f].update(str(v) for v in (s.get(f) or []))
        return {f: sorted(vals) for f, vals in vocab.items() if vals}

    def get_deck_outline(self, deck: str) -> dict | None:
        """The deck's narrative sequence — each slide's `slide_position_role` +
        `slide_purpose` + title, ordered by index. Shows how a real deck flows
        (opening → context → findings → recommendation → close) for generating a
        coherent whole deck. None if the deck id is unknown."""
        d = self.decks.get(deck)
        if d is None:
            return None
        slides = sorted(d.get("slides", []), key=lambda s: s.get("index") or 0)
        return {
            "deck": deck,
            "deck_summary": d.get("deck_summary_one_sentence"),
            "slides": [
                {
                    "index": s.get("index"),
                    "slide_position_role": s.get("slide_position_role"),
                    "slide_purpose": s.get("slide_purpose"),
                    "title_text": s.get("title_text"),
                    "main_message": s.get("main_message"),
                }
                for s in slides
            ],
        }

    def suggest_outline(
        self,
        *,
        client_industry: str | None = None,
        content_area: str | None = None,
        audience_level: str | None = None,
        engagement_stage: str | None = None,
        slide_count: int | None = None,
        key_sections: list[str] | None = None,
        max_reference_decks: int = 2,
        min_deck_score: float = 3.0,
    ) -> dict:
        """Propose a storyboard skeleton for a new deck by picking the closest
        reference deck(s) from the corpus and adapting their outline. Operationalizes
        the 'reuse existing deck outlines' principle: deck-pick is a deterministic
        weighted tag-overlap (industry 3 + content_area 3 + audience 2 + engagement 1,
        max 9) instead of the agent eyeballing list_decks; each planned slide carries
        a candidate `reference {deck, index}` cloned from a donor slide.

        Threshold (`min_deck_score`, default 3.0): if no deck clears it,
        `chosen_reference_deck` is null and `low_confidence` is true; `slides[]`
        carries a generic spine (Title / Agenda / Context / Finding / Recommendation
        / Closing) built from the strongest available per-purpose slides, each marked
        `low_confidence: true`. The skill then asks the user to proceed with the weak
        best-match, fall back to get_house_style(), or refine the brief — never
        silently locks in noise.

        `slide_count` trims/pads the donor outline (Title stays first, Closing last).
        `key_sections` hints inject/annotate planned slides via find_similar_slides;
        only hits clearing `min_score=0.15` annotate."""
        # Score every deck on weighted tag overlap; report raw + normalized.
        candidates: list[dict] = []
        wanted_areas = (
            {content_area.lower()} if isinstance(content_area, str) else
            {c.lower() for c in (content_area or [])}
        )
        for did, d in self.decks.items():
            ind_hit = bool(client_industry) and _ieq(d.get("client_industry"), client_industry)
            d_areas = {str(c).lower() for c in (d.get("content_area") or [])}
            area_hit = bool(wanted_areas) and bool(wanted_areas & d_areas)
            aud_hit = bool(audience_level) and _ieq(d.get("audience_level"), audience_level)
            eng_hit = bool(engagement_stage) and _ieq(d.get("engagement_stage"), engagement_stage)
            score = 3 * ind_hit + 3 * area_hit + 2 * aud_hit + 1 * eng_hit
            candidates.append({
                "deck": did,
                "match_score": float(score),
                "match_score_normalized": round(score / 9.0, 3),
                "above_threshold": score >= min_deck_score,
                "why": {
                    "client_industry_hit": bool(ind_hit),
                    "content_area_hit": bool(area_hit),
                    "audience_level_hit": bool(aud_hit),
                    "engagement_stage_hit": bool(eng_hit),
                },
                "deck_summary": d.get("deck_summary_one_sentence"),
            })
        candidates.sort(key=lambda c: -c["match_score"])
        top = candidates[:max_reference_decks]

        # Pick the donor outline. If the top deck clears the threshold, snapshot its
        # design system; otherwise fall back to whatever deck is closest (for the
        # generic spine) but mark low_confidence and leave chosen_reference_deck null.
        donor_id = top[0]["deck"] if top else None
        donor = self.decks.get(donor_id) if donor_id else None
        cleared = bool(top and top[0]["above_threshold"])
        chosen = donor_id if cleared else None
        low_confidence = not cleared

        donor_design = (donor or {}).get("design_system") if donor else None
        donor_rules = (donor or {}).get("inferred_rules") if donor else None
        recurring_available = bool(
            donor and any(
                r.get("image_path") for r in (donor.get("design_system") or {}).get("recurring_elements", [])
            )
        )

        # Build the storyboard slides[].
        slides: list[dict] = []
        notes: list[str] = []
        if cleared and donor:
            # Adapt donor's outline directly: each donor slide -> a planned slide
            # with a candidate reference pointing back at that donor slide.
            donor_slides = sorted(donor.get("slides", []), key=lambda s: s.get("index") or 0)
            outline = list(donor_slides)
            if slide_count and slide_count != len(outline):
                # Keep Title (first) and Closing (last); trim/pad the middle.
                if slide_count < len(outline):
                    head = outline[:1]
                    tail = outline[-1:] if len(outline) > 1 else []
                    middle = outline[1:-1] if len(outline) > 1 else []
                    # Prefer trimming "Evidence / backup" slides from the back.
                    evidence_mask = [s.get("slide_position_role") == "Evidence / backup" for s in middle]
                    drop = len(outline) - slide_count
                    keep_middle: list[dict] = []
                    for s, is_ev in zip(reversed(middle), reversed(evidence_mask)):
                        if drop > 0 and is_ev:
                            drop -= 1
                            continue
                        keep_middle.append(s)
                    keep_middle.reverse()
                    # If we still need to drop, lop off from the back of middle.
                    if drop > 0:
                        keep_middle = keep_middle[: max(0, len(keep_middle) - drop)]
                    outline = head + keep_middle + tail
                    notes.append(f"Adapted {donor_id} {len(donor_slides)}→{len(outline)}; trimmed Evidence-backup run.")
                else:
                    # Pad by repeating the most-reusable Finding/Data slide.
                    pad_candidates = self.find_slide_templates(
                        slide_purpose="Finding", limit=1
                    ) or self.find_slide_templates(
                        slide_purpose="Data presentation", limit=1
                    )
                    while pad_candidates and len(outline) < slide_count:
                        # Pull the actual slide dict from the donor (or any deck the pad came from).
                        pad_did = pad_candidates[0]["deck"]
                        pad_idx = pad_candidates[0]["index"]
                        pad_deck = self.decks.get(pad_did) or donor
                        pad_slide = next(
                            (s for s in pad_deck.get("slides", []) if s.get("index") == pad_idx),
                            None,
                        )
                        if pad_slide is None:
                            break
                        outline = outline[:-1] + [pad_slide] + outline[-1:] if len(outline) > 1 else outline + [pad_slide]
                    notes.append(f"Adapted {donor_id} {len(donor_slides)}→{len(outline)}; padded with reusable Finding slides.")
            for new_idx, s in enumerate(outline):
                slides.append({
                    "index": new_idx,
                    "slide_purpose": s.get("slide_purpose"),
                    "message_type": s.get("message_type"),
                    "slide_position_role": s.get("slide_position_role"),
                    "dominant_visual_element": s.get("dominant_visual_element"),
                    "intended_main_message": s.get("main_message"),
                    "reference": {
                        "deck": donor_id,
                        "index": s.get("index"),
                        "why": f"Donor outline slide {s.get('index')}; {s.get('slide_position_role') or 'role unset'}.",
                        "reusability_score_qualitative": s.get("reusability_score_qualitative"),
                        "tier_match_difficulty": s.get("tier_match_difficulty"),
                    },
                    "low_confidence": False,
                })
        else:
            # No deck cleared the threshold — build a generic spine from the strongest
            # per-purpose templates available, regardless of source deck. Mark every
            # planned slide low_confidence so the skill surfaces it.
            spine = ["Title", "Agenda / Contents", "Context-setting", "Finding", "Recommendation", "Closing / contacts"]
            target = slide_count or len(spine)
            # If target > len(spine), repeat Finding in the middle; if target < len(spine),
            # drop from the back, keeping Title and Closing.
            if target > len(spine):
                spine = spine[:-1] + ["Finding"] * (target - len(spine)) + spine[-1:]
            elif target < len(spine):
                spine = spine[:1] + spine[1 : 1 + max(0, target - 2)] + spine[-1:]
            for new_idx, purpose in enumerate(spine):
                templates = self.find_slide_templates(slide_purpose=purpose, limit=1)
                ref = None
                if templates:
                    t = templates[0]
                    ref = {
                        "deck": t["deck"],
                        "index": t["index"],
                        "why": f"No deck cleared min_deck_score={min_deck_score}; using best available {purpose} template.",
                        "reusability_score_qualitative": t.get("reusability_score_qualitative"),
                        "tier_match_difficulty": t.get("tier_match_difficulty"),
                    }
                slides.append({
                    "index": new_idx,
                    "slide_purpose": purpose,
                    "message_type": None,
                    "slide_position_role": None,
                    "dominant_visual_element": (templates[0].get("dominant_visual_element") if templates else None),
                    "intended_main_message": None,
                    "reference": ref,
                    "low_confidence": True,
                })
            top_score = top[0]["match_score"] if top else 0.0
            notes.append(
                f"No deck cleared min_deck_score={min_deck_score} (top={top_score}); generic spine built from per-purpose templates."
            )

        # `key_sections` hints: only annotate when a hit clears the slide-level
        # threshold (uses match_slide's default min_score=0.15).
        if key_sections:
            for hint in key_sections:
                hit = self.match_slide(hint, limit=1)
                top_hit = (hit.get("matches") or [None])[0]
                if not top_hit:
                    continue
                # Annotate the first slide whose intended_main_message is empty/null.
                for s in slides:
                    if not s.get("intended_main_message"):
                        s["intended_main_message"] = hint
                        s["reference"] = {
                            "deck": top_hit["deck"],
                            "index": top_hit["index"],
                            "why": f"key_section '{hint}' matched with score {top_hit['score']}.",
                            "reusability_score_qualitative": top_hit.get("reusability_score_qualitative"),
                            "tier_match_difficulty": top_hit.get("tier_match_difficulty"),
                        }
                        break

        return {
            "candidate_reference_decks": top,
            "chosen_reference_deck": chosen,
            "low_confidence": low_confidence,
            "min_deck_score": min_deck_score,
            "design_system": donor_design if cleared else None,
            "inferred_rules": donor_rules if cleared else None,
            "recurring_assets_available": recurring_available if cleared else False,
            "slides": slides,
            "notes": notes,
        }

    def find_slide_templates(
        self,
        *,
        slide_purpose: str | None = None,
        dominant_visual_element: str | None = None,
        message_type: str | None = None,
        limit: int = 10,
    ) -> list[dict]:
        """Reusable layout skeletons for a kind of slide, ranked by
        `reusability_score_qualitative` (High→Low). Returns each match's `zones` and
        `slot_types_present` — the structural recipe to fill in — plus
        `tier_match_difficulty`. Use it to scaffold a new slide of a given purpose."""
        out: list[dict] = []
        for did, d in self.decks.items():
            for s in d.get("slides", []):
                if not _ieq(s.get("slide_purpose"), slide_purpose):
                    continue
                if not _ieq(s.get("dominant_visual_element"), dominant_visual_element):
                    continue
                if not _ieq(s.get("message_type"), message_type):
                    continue
                out.append(
                    {
                        "deck": did,
                        "index": s.get("index"),
                        "title_text": s.get("title_text"),
                        "slide_purpose": s.get("slide_purpose"),
                        "dominant_visual_element": s.get("dominant_visual_element"),
                        "reusability_score_qualitative": s.get("reusability_score_qualitative"),
                        "tier_match_difficulty": s.get("tier_match_difficulty"),
                        "zones": s.get("zones", []),
                        "slot_types_present": s.get("slot_types_present", []),
                    }
                )
        out.sort(key=lambda t: _REUSE_RANK.get(t.get("reusability_score_qualitative"), 0), reverse=True)
        return out[:limit]

    def match_slide(
        self,
        text: str,
        *,
        slide_purpose: str | None = None,
        dominant_visual_element: str | None = None,
        message_type: str | None = None,
        prefer_deck: str | None = None,
        limit: int = 5,
        min_score: float = 0.15,
    ) -> dict:
        """For one planned storyboard slide, return ranked reference slides with the
        full clone kit (tags + zones + slot_types_present + reusability/tier + the
        source deck's design_system) needed to clone-and-edit. Combines the
        find_similar_slides token-overlap formula with find_slide_templates' tag
        filters, plus boosts for `prefer_deck` (design coherence) and High reusability.

        Threshold (`min_score`, default 0.15 on the boosted [0, 1] scale): filters
        pure-noise matches. When nothing clears it, `matches` is empty and
        `best_below_threshold` surfaces the single best near-miss so the agent can
        decide (widen filters, lower threshold, fall back) — never binds a storyboard
        slide to noise. Use `prefer_deck` to bias matches toward the storyboard's
        chosen reference deck for design coherence."""
        q = _tokens(text)
        scored: list[tuple[float, float, dict, dict, str]] = []  # final, base, slide, deck, did
        evaluated = 0
        for did, d in self.decks.items():
            for s in d.get("slides", []):
                if not _ieq(s.get("slide_purpose"), slide_purpose):
                    continue
                if not _ieq(s.get("dominant_visual_element"), dominant_visual_element):
                    continue
                if not _ieq(s.get("message_type"), message_type):
                    continue
                evaluated += 1
                t = _tokens(f"{s.get('main_message') or ''} {s.get('title_text') or ''}")
                base = (len(q & t) / (len(q) or 1)) if t else 0.0
                prefer_boost = 0.15 if prefer_deck and did == prefer_deck else 0.0
                reuse_boost = 0.10 * (_REUSE_RANK.get(s.get("reusability_score_qualitative"), 0) / 3)
                final = min(1.0, base + prefer_boost + reuse_boost)
                scored.append((final, base, s, d, did))
        scored.sort(key=lambda x: -x[0])

        def _kit(final: float, base: float, s: dict, d: dict, did: str) -> dict:
            return {
                "deck": did,
                "index": s.get("index"),
                "score": round(final, 3),
                "base_score": round(base, 3),
                "boosts": {
                    "prefer_deck": 0.15 if prefer_deck and did == prefer_deck else 0.0,
                    "reusability": round(0.10 * (_REUSE_RANK.get(s.get("reusability_score_qualitative"), 0) / 3), 3),
                },
                "title_text": s.get("title_text"),
                "main_message": s.get("main_message"),
                "slide_purpose": s.get("slide_purpose"),
                "message_type": s.get("message_type"),
                "slide_position_role": s.get("slide_position_role"),
                "dominant_visual_element": s.get("dominant_visual_element"),
                "chart_type": s.get("chart_type"),
                "zones": s.get("zones", []),
                "slot_types_present": s.get("slot_types_present", []),
                "reusability_score_qualitative": s.get("reusability_score_qualitative"),
                "tier_match_difficulty": s.get("tier_match_difficulty"),
                "design_system": d.get("design_system"),
            }

        above = [(f, b, s, d, did) for (f, b, s, d, did) in scored if f >= min_score]
        matches = [_kit(*row) for row in above[:limit]]
        best_below = None
        if not matches and scored:
            best_below = _kit(*scored[0])
        return {
            "matches": matches,
            "candidates_evaluated": evaluated,
            "above_threshold_count": len(above),
            "min_score": min_score,
            "best_below_threshold": best_below,
        }

    def get_house_style(self) -> dict:
        """The firm's house style aggregated across every deck: the dominant
        title/body fonts + sizes, default alignment, the most-common palette colors,
        and references to all logos. Use it when generating a deck not modeled on one
        specific reference. Fetch logo bytes with get_deck_assets(deck)."""
        title_fonts, body_fonts = Counter(), Counter()
        title_sizes, body_sizes, alignments = Counter(), Counter(), Counter()
        primaries, accents, neutrals = Counter(), Counter(), Counter()
        logos: list[dict] = []
        for did, d in self.decks.items():
            ds = d.get("design_system") or {}
            ts, bs = ds.get("title_style") or {}, ds.get("body_style") or {}
            if ts.get("font_family"):
                title_fonts[ts["font_family"]] += 1
            if bs.get("font_family"):
                body_fonts[bs["font_family"]] += 1
            if ts.get("size_pt") is not None:
                title_sizes[ts["size_pt"]] += 1
            if bs.get("size_pt") is not None:
                body_sizes[bs["size_pt"]] += 1
            if ds.get("default_text_alignment"):
                alignments[ds["default_text_alignment"]] += 1
            pal = ds.get("color_palette") or {}
            if pal.get("primary"):
                primaries[pal["primary"]] += 1
            if pal.get("accent"):
                accents[pal["accent"]] += 1
            neutrals.update(pal.get("neutrals") or [])
            for r in ds.get("recurring_elements", []):
                if r.get("type") == "logo":
                    logos.append(
                        {"deck": did, "value": r.get("value"),
                         "image_path": r.get("image_path"), "source": r.get("source")}
                    )
        return {
            "decks_analyzed": len(self.decks),
            "title_font": _top1(title_fonts),
            "body_font": _top1(body_fonts),
            "title_size_pt": _top1(title_sizes),
            "body_size_pt": _top1(body_sizes),
            "default_text_alignment": _top1(alignments),
            "palette": {
                "primary": [v for v, _ in primaries.most_common(3)],
                "accent": [v for v, _ in accents.most_common(3)],
                "neutrals": [v for v, _ in neutrals.most_common(5)],
            },
            "logos": logos,
        }

    def start_deck(self, deck: str, *, max_reference_slides: int = 5) -> dict | None:
        """Everything needed to start generating a deck modeled on `deck`, in ONE
        call: design_system, inferred_rules, the logos as base64, the full narrative
        outline, and a few reference slides. Saves the agent chaining get_deck +
        get_deck_outline + get_deck_assets. None if the deck id is unknown."""
        base = self.get_deck(deck)
        if base is None:
            return None
        outline = self.get_deck_outline(deck) or {}
        return {
            "deck": deck,
            "deck_summary": base.get("deck_summary"),
            "design_system": base.get("design_system"),
            "inferred_rules": base.get("inferred_rules"),
            "logos": self.get_deck_assets(deck) or [],
            "outline": outline.get("slides", []),
            "reference_slides": base.get("slides", [])[:max_reference_slides],
        }

    def corpus_stats(self) -> dict:
        """Coverage at a glance: total decks/slides, how many decks have a logo, and
        counts by client_industry / content_area / slide_purpose. Helps pick a
        reference deck and shows where the corpus is thin as it grows."""
        by_industry, by_content, by_purpose = Counter(), Counter(), Counter()
        n_slides = decks_with_logos = 0
        for d in self.decks.values():
            if d.get("client_industry"):
                by_industry[d["client_industry"]] += 1
            by_content.update(d.get("content_area") or [])
            slides = d.get("slides", [])
            n_slides += len(slides)
            for s in slides:
                if s.get("slide_purpose"):
                    by_purpose[s["slide_purpose"]] += 1
            ds = d.get("design_system") or {}
            if any(r.get("type") == "logo" for r in ds.get("recurring_elements", [])):
                decks_with_logos += 1
        return {
            "decks": len(self.decks),
            "slides": n_slides,
            "decks_with_logos": decks_with_logos,
            "by_client_industry": dict(by_industry.most_common()),
            "by_content_area": dict(by_content.most_common()),
            "by_slide_purpose": dict(by_purpose.most_common()),
        }
