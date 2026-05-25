"""Tests for serving recurring-image assets (Corpus.get_deck_assets)."""

from __future__ import annotations

import base64
import json

from src.poc_corpus import Corpus


def _corpus(tmp_path):
    corpus = tmp_path / "corpus"
    (corpus / "assets" / "demo-deck").mkdir(parents=True)
    (corpus / "assets" / "demo-deck" / "logo.png").write_bytes(b"PNGBYTES")
    deck = {
        "source_filename": "demo.pptx",
        "slide_count": 1,
        "design_system": {
            "recurring_elements": [
                {"type": "logo", "value": "ACME", "source": "slide",
                 "position": "top-right", "appears_on_slides": [0, 1],
                 "image_path": "assets/demo-deck/logo.png"},
                {"type": "footer", "value": "X"},  # no image_path -> excluded
                {"type": "logo", "image_path": "assets/demo-deck/missing.png"},  # missing file -> skipped
            ]
        },
        "slides": [{"index": 0}],
    }
    (corpus / "demo-deck.tagged.json").write_text(json.dumps(deck), encoding="utf-8")
    return Corpus(corpus, corpus / "assets")


def test_get_deck_assets_returns_decodable_base64(tmp_path):
    assets = _corpus(tmp_path).get_deck_assets("demo-deck")
    assert len(assets) == 1  # footer (no image_path) and the missing file both excluded
    a = assets[0]
    assert a["type"] == "logo" and a["filename"] == "logo.png" and a["mime_type"] == "image/png"
    assert a["source"] == "slide" and a["position"] == "top-right"
    assert base64.b64decode(a["base64"]) == b"PNGBYTES"


def test_get_deck_assets_unknown_deck_is_none(tmp_path):
    assert _corpus(tmp_path).get_deck_assets("nope") is None


def test_get_deck_exposes_recurring_assets_available(tmp_path):
    assert _corpus(tmp_path).get_deck("demo-deck")["recurring_assets_available"] is True
