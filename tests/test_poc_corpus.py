"""Tests for the in-memory PoC corpus (no DB)."""

from __future__ import annotations

from src.config import settings
from src.poc_corpus import _REUSE_RANK, Corpus


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


def test_list_vocabulary_only_present_values():
    c = _corpus()
    vocab = c.list_vocabulary()
    assert "slide_purpose" in vocab and vocab["slide_purpose"]
    # values are the real strings search_slides matches, sorted, de-duped
    assert vocab["slide_purpose"] == sorted(set(vocab["slide_purpose"]))
    # every advertised value actually returns slides (no dead filters)
    a_value = vocab["slide_purpose"][0]
    assert c.search_slides(slide_purpose=a_value)


def test_get_deck_outline_ordered_with_position_role():
    c = _corpus()
    deck = next(iter(c.decks))
    outline = c.get_deck_outline(deck)
    assert outline and outline["deck"] == deck
    idxs = [s["index"] for s in outline["slides"]]
    assert idxs == sorted(idxs)
    # slide_position_role is surfaced here (it's in no other tool)
    assert all("slide_position_role" in s for s in outline["slides"])
    assert c.get_deck_outline("nonexistent-deck") is None


def test_find_slide_templates_ranked_by_reusability():
    c = _corpus()
    templates = c.find_slide_templates(limit=50)
    assert templates
    ranks = [_REUSE_RANK.get(t["reusability_score_qualitative"], 0) for t in templates]
    assert ranks == sorted(ranks, reverse=True)  # High → Low
    assert all("zones" in t and "slot_types_present" in t for t in templates)
    # filter is respected
    findings = c.find_slide_templates(slide_purpose="Finding")
    assert all(t["slide_purpose"] == "Finding" for t in findings)


def test_get_house_style_aggregates():
    c = _corpus()
    hs = c.get_house_style()
    assert hs["decks_analyzed"] == len(c.decks)
    assert hs["title_font"] and hs["body_font"]  # fonts are populated post-normalization
    assert "primary" in hs["palette"] and "neutrals" in hs["palette"]
    assert isinstance(hs["logos"], list)


def test_start_deck_bundles_everything():
    c = _corpus()
    deck = next(iter(c.decks))
    kit = c.start_deck(deck)
    assert kit and kit["deck"] == deck
    assert kit["design_system"] and "outline" in kit
    assert isinstance(kit["logos"], list)  # base64 PNGs (may be empty)
    assert kit["outline"] == c.get_deck_outline(deck)["slides"]
    assert c.start_deck("nonexistent-deck") is None


def test_corpus_stats_counts():
    c = _corpus()
    stats = c.corpus_stats()
    assert stats["decks"] == len(c.decks)
    assert stats["slides"] == sum(len(d.get("slides", [])) for d in c.decks.values())
    assert isinstance(stats["by_slide_purpose"], dict) and stats["by_slide_purpose"]
