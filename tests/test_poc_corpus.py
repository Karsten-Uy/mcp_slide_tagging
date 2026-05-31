"""Tests for the in-memory PoC corpus (no DB)."""

from __future__ import annotations

from pathlib import Path

from src.poc_corpus import _REUSE_RANK, Corpus

# Enforcement: only decks with a matching source .pptx (present + count-aligned) load.
# The bundled snapshot ships two such decks (nigeria, digital-auto); the other two
# tagged JSONs are intentionally excluded (no/misaligned .pptx).
_CORPUS = Path("corpus")
_SOURCE = Path("corpus/source")


def _corpus() -> Corpus:
    return Corpus(_CORPUS, _CORPUS / "assets", _SOURCE)


def test_loads_only_decks_with_matching_pptx():
    c = _corpus()
    assert len(c.decks) == 2  # nigeria + digital-auto (the bundled, aligned decks)
    assert "nigeria-economic-outlook-october-2023-v1" in c.decks
    # decks whose .pptx is absent/misaligned are dropped from the whole corpus
    assert "electric-vehicle-sales-review-q4-2022" not in c.decks
    assert "ereadiness-study-2023" not in c.decks
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


def test_corpus_stats_reports_slide_kind_coverage():
    # coverage by message_type + dominant_visual_element shows where the corpus is thin
    # (which slide kinds the clone workflow can't yet find a precedent for).
    stats = _corpus().corpus_stats()
    assert isinstance(stats["by_message_type"], dict)
    assert isinstance(stats["by_dominant_visual_element"], dict)


# --- match_slide ----------------------------------------------------------


def test_match_slide_returns_clone_kit_above_threshold():
    c = _corpus()
    # "foreign exchange" hits the nigeria deck strongly.
    res = c.match_slide("foreign exchange policy recommendation", limit=5)
    assert isinstance(res, dict)
    assert res["min_score"] == 0.15
    assert res["candidates_evaluated"] > 0
    assert res["matches"], "expected at least one clone-kit match"
    top = res["matches"][0]
    # Clone-kit shape: tags + structural recipe + source design_system.
    for key in (
        "deck", "index", "score", "base_score", "boosts", "title_text",
        "main_message", "slide_purpose", "dominant_visual_element",
        "zones", "slot_types_present", "reusability_score_qualitative",
        "tier_match_difficulty", "design_system",
    ):
        assert key in top, f"clone kit missing {key}"
    assert top["score"] >= 0.15
    # Sorted descending.
    scores = [m["score"] for m in res["matches"]]
    assert scores == sorted(scores, reverse=True)


def test_match_slide_threshold_filters_noise_and_surfaces_best_below():
    c = _corpus()
    # Gibberish text → nothing should clear the threshold.
    res = c.match_slide("zzzqqq xyzzy plover unrelated", min_score=0.5)
    assert res["matches"] == []
    assert res["above_threshold_count"] == 0
    # Even with no matches, the agent gets a `best_below_threshold` near-miss
    # (or null if literally nothing scored) — never silently nothing.
    assert "best_below_threshold" in res


def test_match_slide_tag_filter_narrows_candidates():
    c = _corpus()
    everything = c.match_slide("the", min_score=0.0)
    findings = c.match_slide("the", slide_purpose="Finding", min_score=0.0)
    assert findings["candidates_evaluated"] <= everything["candidates_evaluated"]
    assert all(m["slide_purpose"] == "Finding" for m in findings["matches"])


def test_match_slide_prefer_deck_boost():
    c = _corpus()
    # Pick any deck and bias toward it; matched-deck hits should carry the +0.15 boost.
    target = next(iter(c.decks))
    res = c.match_slide("the", prefer_deck=target, limit=20, min_score=0.0)
    for m in res["matches"]:
        if m["deck"] == target:
            assert m["boosts"]["prefer_deck"] == 0.15
        else:
            assert m["boosts"]["prefer_deck"] == 0.0


# --- suggest_outline ------------------------------------------------------


def test_suggest_outline_picks_close_deck_and_adapts_outline():
    c = _corpus()
    # The nigeria deck is Cross-industry / Market analysis / C-suite — this
    # should clear the deck-level threshold easily.
    res = c.suggest_outline(
        client_industry="Cross-industry",
        content_area="Market analysis",
        audience_level="C-suite / board",
        slide_count=8,
    )
    assert res["low_confidence"] is False
    assert res["chosen_reference_deck"] is not None
    assert res["design_system"], "snapshotted design_system expected when above threshold"
    assert res["inferred_rules"] is not None
    # candidate scoring is exposed
    top = res["candidate_reference_decks"][0]
    assert top["above_threshold"] is True
    assert 0.0 <= top["match_score_normalized"] <= 1.0
    # adapted to 8 slides, each bound to a donor reference, none low_confidence.
    assert len(res["slides"]) == 8
    assert all(s["reference"] is not None for s in res["slides"])
    assert all(s["low_confidence"] is False for s in res["slides"])
    # indices are 0..n-1, sequential.
    assert [s["index"] for s in res["slides"]] == list(range(8))


def test_suggest_outline_low_confidence_when_no_deck_clears():
    c = _corpus()
    # Filters no deck satisfies → low_confidence, chosen=null, generic spine.
    res = c.suggest_outline(
        client_industry="Healthcare",
        content_area="ERP",
        audience_level="Working team",
        engagement_stage="Final delivery",
        min_deck_score=9.0,  # require a perfect 4-way hit no deck can hit
    )
    assert res["low_confidence"] is True
    assert res["chosen_reference_deck"] is None
    assert res["design_system"] is None
    assert res["slides"], "generic spine should still be returned"
    assert all(s["low_confidence"] is True for s in res["slides"])
    # candidate_reference_decks still lists the closest decks with their scores.
    assert res["candidate_reference_decks"]
    assert all("match_score" in c_ for c_ in res["candidate_reference_decks"])


def test_suggest_outline_respects_slide_count_padding_and_trimming():
    c = _corpus()
    # Trim: ask for 3 slides; should always keep Title-like first + Closing-like last.
    res_trim = c.suggest_outline(
        client_industry="Cross-industry",
        content_area="Market analysis",
        audience_level="C-suite / board",
        slide_count=3,
    )
    assert len(res_trim["slides"]) == 3
    assert [s["index"] for s in res_trim["slides"]] == [0, 1, 2]
