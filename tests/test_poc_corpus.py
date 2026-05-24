"""Tests for the in-memory PoC corpus (no DB)."""

from __future__ import annotations

from src.config import settings
from src.poc_corpus import Corpus


def _corpus() -> Corpus:
    return Corpus(settings.corpus_path)


def test_loads_the_tagged_decks():
    c = _corpus()
    assert len(c.decks) >= 4  # the 4 Gen-2 hand-labels
    decks = c.list_decks()
    assert all(d["deck"] and d["slide_count"] > 0 for d in decks)
    # _legend is stripped, not surfaced as a deck
    assert "_legend" not in c.decks


def test_search_by_slide_purpose_and_deck_filter():
    c = _corpus()
    findings = c.search_slides(slide_purpose="Finding")
    assert findings, "expected some Finding slides"
    assert all(s["slide_purpose"] == "Finding" for s in findings)

    # deck-level + slide-level filter combine (case-insensitive)
    combo = c.search_slides(slide_purpose="finding", client_industry="cross-industry")
    assert all(s["slide_purpose"] == "Finding" for s in combo)
    assert len(combo) <= len(findings)


def test_search_text_keyword():
    c = _corpus()
    hits = c.search_slides(text="FX")
    assert hits
    assert all("fx" in f"{s['main_message']} {s['title_text']}".lower() for s in hits)


def test_limit_respected():
    c = _corpus()
    assert len(c.search_slides(limit=3)) == 3


def test_get_slide_returns_full_tags():
    c = _corpus()
    any_deck = next(iter(c.decks))
    got = c.get_slide(any_deck, 0)
    assert got and got["deck"] == any_deck
    assert got["slide"]["index"] == 0
    assert "deck_context" in got
    assert c.get_slide("nonexistent-deck", 0) is None


def test_get_deck_exposes_design_system():
    c = _corpus()
    any_deck = next(iter(c.decks))
    deck = c.get_deck(any_deck)
    assert deck and deck["deck"] == any_deck
    assert "design_system" in deck and deck["design_system"]  # fonts/colors/grid
    assert "inferred_rules" in deck
    assert deck["slides"] and "main_message" in deck["slides"][0]
    assert c.get_deck("nonexistent-deck") is None


def test_find_similar_ranks_by_overlap():
    c = _corpus()
    ranked = c.find_similar_slides("foreign exchange market policy", limit=5)
    assert ranked
    scores = [r["score"] for r in ranked]
    assert scores == sorted(scores, reverse=True)
    assert all(0 < s <= 1 for s in scores)
