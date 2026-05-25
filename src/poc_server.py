"""Lean PoC MCP server over the tagged slide corpus.

Minimal, zero-infra version of the slide-corpus server: it loads the tagged Gen-2
JSON from CORPUS_PATH into memory and exposes read-only retrieval tools over the
basic labels — no Postgres, no OpenAI, no embeddings. Serves MCP over Streamable
HTTP at /mcp (plus GET /health), so it can be tunnelled to HTTPS and added to
claude.ai as a custom connector.

Run with:  uv run python -m src.poc_server

For the production pgvector path see src/server.py (separate, untouched).
"""

from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from src.config import settings
from src.logging import get_logger, setup_logging
from src.poc_corpus import Corpus

logger = get_logger(__name__)

corpus = Corpus(settings.corpus_path, settings.assets_path)

# Cloud hosts (Cloud Run, Render, …) inject the port to bind via $PORT; fall back
# to the configured port for local runs.
_port = int(os.environ.get("PORT", settings.mcp_server_port))

mcp = FastMCP(
    "slide-corpus-poc",
    host=settings.mcp_server_host,
    port=_port,
)


@mcp.tool()
def list_decks() -> list[dict]:
    """List every tagged deck in the corpus with its deck-level tags
    (client_industry, client_type, engagement_stage, content_area, audience_level,
    geography, one-sentence summary, slide_count). Call this first to see what's
    available and to learn the deck ids used by other tools."""
    return corpus.list_decks()


@mcp.tool()
def search_slides(
    slide_purpose: str | None = None,
    message_type: str | None = None,
    dominant_visual_element: str | None = None,
    client_industry: str | None = None,
    content_area: str | None = None,
    audience_level: str | None = None,
    text: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """Find slides matching structured tag filters and/or a keyword.

    Deck-level filters (narrow which decks): `client_industry`
    (Financial Services | Tech | Healthcare | Public Sector | Industrials |
    Consumer | Energy | Education | Cross-industry), `content_area` (e.g. Strategy,
    Market analysis, Risk, Digital transformation, ESG, …), `audience_level`
    (C-suite / board | Senior executives | Operating committee | Working team |
    External / public).

    Slide-level filters: `slide_purpose` (e.g. Title, Agenda / Contents, Finding,
    Insight, Recommendation, Framework, Comparison, Data presentation, Closing /
    contacts, …), `message_type` (e.g. Assertion, Comparison, Causation, Trend over
    time, Trade-off, Listing / enumeration, Single statistic / hero number, …),
    `dominant_visual_element` (Chart | Diagram | Table | Image | Icon-based |
    Framework graphic | Pure text | Mixed).

    `text` is a case-insensitive keyword matched against each slide's main_message
    and title. All filters are ANDed; omit any you don't care about. Returns up to
    `limit` slides with their deck, index, title, main_message, and key tags."""
    return corpus.search_slides(
        slide_purpose=slide_purpose,
        message_type=message_type,
        dominant_visual_element=dominant_visual_element,
        client_industry=client_industry,
        content_area=content_area,
        audience_level=audience_level,
        text=text,
        limit=limit,
    )


@mcp.tool()
def get_deck(deck: str) -> dict | None:
    """Return the design source-of-truth for one deck (id from list_decks): its
    deck-level tags, the `design_system` (title/body fonts + sizes + weights +
    colors, color_palette primary/accent/neutrals, default alignment, grid, and
    recurring_elements like logos/footers), the observed `inferred_rules`, and a
    one-line outline of every slide. Call this to match a reference deck's look and
    structure when generating a new deck. Null if not found."""
    return corpus.get_deck(deck)


@mcp.tool()
def get_deck_assets(deck: str) -> list[dict] | None:
    """Return a deck's recurring branding images (logos, watermarks) as base64 PNGs.
    Each item: {type, value, source, position, appears_on_slides, image_path,
    filename, mime_type, base64}. To embed the firm's logo when building a deck:
    pick the item with type=="logo", decode its base64, write it to a file, and
    insert it as a picture (e.g. python-pptx add_picture) at the reported position.
    Returns [] if none were extracted; null if the deck id is unknown. (Use
    get_deck first to see which decks have recurring_assets_available.)"""
    return corpus.get_deck_assets(deck)


@mcp.tool()
def get_slide(deck: str, index: int) -> dict | None:
    """Return the full tag set for one slide, given a deck id (from list_decks)
    and the 0-based slide index. Includes deck-level context. Null if not found."""
    return corpus.get_slide(deck, index)


@mcp.tool()
def find_similar_slides(text: str, limit: int = 10) -> list[dict]:
    """Rank slides by keyword overlap between `text` and their main_message/title —
    a lightweight 'find slides about this idea' search. Returns slides with a
    relevance `score` (0–1). Use search_slides when you want exact tag filters."""
    return corpus.find_similar_slides(text, limit)


@mcp.tool()
def list_vocabulary() -> dict:
    """List the valid filter values actually present in the corpus, per field
    (slide_purpose, message_type, dominant_visual_element, client_industry,
    content_area, audience_level, and more). search_slides matches values exactly,
    so call this first to use real strings instead of guessing (a near-miss returns
    nothing). Empty fields are omitted."""
    return corpus.list_vocabulary()


@mcp.tool()
def get_deck_outline(deck: str) -> dict | None:
    """Return a deck's narrative flow: every slide's slide_position_role +
    slide_purpose + title, in order. Use it to learn how a real deck is sequenced
    (opening → context → findings → recommendation → close) before generating a
    whole deck. Null if the deck id is unknown."""
    return corpus.get_deck_outline(deck)


@mcp.tool()
def find_slide_templates(
    slide_purpose: str | None = None,
    dominant_visual_element: str | None = None,
    message_type: str | None = None,
    limit: int = 10,
) -> list[dict]:
    """Find reusable layout skeletons for a kind of slide, ranked by reusability
    (High→Low). Each result includes its `zones` and `slot_types_present` — the
    structural recipe to fill in — plus `tier_match_difficulty`. Use it to scaffold
    a new slide of a given purpose/visual style. All filters are optional and ANDed."""
    return corpus.find_slide_templates(
        slide_purpose=slide_purpose,
        dominant_visual_element=dominant_visual_element,
        message_type=message_type,
        limit=limit,
    )


@mcp.tool()
def get_house_style() -> dict:
    """Return the firm's house style aggregated across all decks: dominant title/body
    fonts + sizes, default alignment, most-common palette colors, and references to
    all logos. Use it when generating a deck not modeled on one specific reference.
    Fetch logo bytes with get_deck_assets(deck)."""
    return corpus.get_house_style()


@mcp.tool()
def start_deck(deck: str, max_reference_slides: int = 5) -> dict | None:
    """One-call kit to start generating a deck modeled on `deck`: its design_system,
    inferred_rules, logos (base64 PNGs), the full narrative outline, and a few
    reference slides — instead of chaining get_deck + get_deck_outline +
    get_deck_assets. Null if the deck id is unknown."""
    return corpus.start_deck(deck, max_reference_slides=max_reference_slides)


@mcp.tool()
def corpus_stats() -> dict:
    """Corpus coverage at a glance: total decks/slides, how many decks have a logo,
    and counts by client_industry / content_area / slide_purpose. Use it to pick a
    reference deck and to see what the corpus does and doesn't cover."""
    return corpus.corpus_stats()


@mcp.custom_route("/health", methods=["GET"])  # type: ignore[untyped-decorator]
async def health(request: Request) -> JSONResponse:
    """Liveness for the PoC server: ok once the in-memory corpus is loaded."""
    n_slides = sum(len(d.get("slides", [])) for d in corpus.decks.values())
    return JSONResponse(
        {"status": "ok", "decks": len(corpus.decks), "slides": n_slides}
    )


def main() -> None:
    setup_logging(settings.log_level)
    n_slides = sum(len(d.get("slides", [])) for d in corpus.decks.values())
    logger.info(
        "poc_server_starting",
        corpus_path=str(settings.corpus_path),
        decks=len(corpus.decks),
        slides=n_slides,
        host=settings.mcp_server_host,
        port=_port,
        transport="streamable-http",
    )
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
