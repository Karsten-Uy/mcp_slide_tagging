# mcp_slide_tagging

A read-only MCP server over the tagged slide-deck corpus produced by the
`slide_tagging` service. It serves the existing corpus (it does not tag, ingest
new decks, or render) through retrieval tools that AI agents call when generating
new decks — deck tags, the design system (normalized fonts, palette, grid),
per-slide metadata, narrative outlines, reusable layout templates, the firm's
aggregated house style, and branding **logo images** as base64 PNGs.

There are **two server paths**, and the lean one is what runs today:

- **Lean PoC server [built].** In-memory, no database, no API keys — loads the
  Gen-2 tagged JSON from disk and exposes the tools below over Streamable HTTP. This
  is what claude.ai connects to as a custom connector. Start here.
- **Production pgvector server [skeleton].** Postgres + pgvector + OpenAI/CLIP
  embeddings for semantic retrieval at scale. Schema, config, and `/health` exist;
  ingestion, embeddings, and the production tools are deferred until the corpus and
  traffic outgrow the in-memory PoC.

This project is a standalone sibling of `slide_tagging`. It reads the corpus
read-only via `CORPUS_PATH` (+ `ASSETS_PATH` for images, `SOURCE_PPTX_PATH` for raw
slides) and never modifies the tagging service or its output. See
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for how the two fit together,
[`docs/POC.md`](docs/POC.md) for the connect-from-claude.ai walkthrough,
[`docs/SCALING.md`](docs/SCALING.md) for the growth path, and
[`docs/HANDOFF-slide_tagging.md`](docs/HANDOFF-slide_tagging.md) for the producer
contract the corpus must satisfy (every served deck needs a tagged JSON **and** an
index-aligned source `.pptx`).

## Lean PoC server (what runs today)

Use the corpus from the **claude.ai web GUI** with no Postgres, no OpenAI, no keys:

```bash
uv sync                                  # install dependencies
uv run python -m src.poc_server          # in-memory tools over CORPUS_PATH, :8000 (/mcp + /health)
uv run python scripts/poc_demo.py        # optional: see the tools' output, no server/tokens
```

It binds `$PORT` if set (Cloud Run/Render) else `8000`. Health check:
`curl http://localhost:8000/health` → `{status, decks, slides}` once the corpus
loads (no external deps).

### The tools

| Tool | Returns |
|---|---|
| `list_decks()` | every deck + deck-level tags + slide_count |
| `get_deck(deck)` | deck tags + full `design_system` (normalized fonts, palette, grid, recurring elements) + `inferred_rules` + slide outline + `recurring_assets_available` |
| `get_deck_assets(deck)` | recurring branding images (logos) as **base64 PNGs** (type/source/position/image_path/base64) |
| `search_slides(...)` | slides by tag filters (slide_purpose/message_type/dominant_visual_element + deck-level industry/content_area/audience) and/or a keyword |
| `get_slide(deck, index)` | full tag set for one slide |
| `get_slide_pptx(deck, index)` | one reference slide as a standalone, self-contained `.pptx` (base64) + a per-shape map — the highest-fidelity source for clone-and-edit generation |
| `find_similar_slides(text)` | slides ranked by keyword overlap on `main_message` (embedding stand-in) |
| `list_vocabulary()` | the valid filter values actually present in the corpus, per field (so `search_slides` strings hit) |
| `get_deck_outline(deck)` | the deck's narrative flow — `slide_position_role` + purpose + title, in order |
| `find_slide_templates(...)` | reusable layout skeletons for a slide kind, ranked by reusability, with `zones`/`slot_types_present` |
| `suggest_outline(...)` | storyboard skeleton from brief attributes — picks the closest reference deck and adapts its outline, each planned slide bound to a candidate reference |
| `match_slide(...)` | per-slide clone kit for one storyboard point: tags + `zones` + `slot_types_present` + the source deck's `design_system` |
| `get_house_style()` | the firm's style aggregated across all decks (dominant fonts/sizes, common palette, all logos) |
| `start_deck(deck)` | one-call kit to model a new deck: design_system + inferred_rules + logos (base64) + outline + reference slides |
| `corpus_stats()` | coverage: deck/slide counts, decks-with-logos, and counts by industry/content_area/slide_purpose/message_type/dominant_visual_element (where the corpus is thin) |

### Connect it to claude.ai

Expose the local server over HTTPS and add it as a custom connector:

```bash
cloudflared tunnel --url http://localhost:8000      # or: ngrok http 8000
```

Add the resulting `https://…/mcp` URL (the path **must** end in `/mcp`) as a
claude.ai custom connector with **Auth: None**. Full steps — including deploying to
a stable HTTPS URL — in [`docs/POC.md`](docs/POC.md).

## Deploy

The PoC server ships as a [`Dockerfile`](Dockerfile) that bundles a point-in-time
`corpus/` snapshot — tagged JSON + `corpus/assets/` (logos) + `corpus/source/`
(the raw `.pptx` + a `manifest.json` of slide counts) — so the container is
self-contained (`CORPUS_PATH=corpus`, `SOURCE_PPTX_PATH=corpus/source`). Config is
provided for:

- **Render** — [`render.yaml`](render.yaml) (Blueprint).
- **Railway** — [`railway.json`](railway.json) (+ [`.railwayignore`](.railwayignore)).
- **Cloud Run** — [`.gcloudignore`](.gcloudignore); `$PORT` is honored.
- **Fly.io** — standard Dockerfile deploy.

No-auth over HTTPS is a deliberate PoC choice (read-only data). **Admission gate:** a
deck is served only if its source `.pptx` is present **and** its slide count matches
the tags — otherwise it's dropped from the whole corpus. So when labels change,
re-bundle **all three** parts: copy `*.tagged.json` + `assets/` **and** the source
`.pptx` into `corpus/source/`, then rebuild the manifest
(`uv run python -m scripts.build_manifest corpus/source`). The producer's
`slide-tagger bundle` does this in one step. Run
`uv run python -m scripts.check_corpus` to see exactly which decks will be served
before you redeploy.

## Production pgvector server (skeleton)

```bash
cp .env.example .env                     # set DATABASE_URL + OPENAI_API_KEY
docker compose up -d                     # Postgres + pgvector (migrations auto-apply on first init)
uv run python -m src.server              # serve at /mcp and /health on :8000
```

In this path `/health` is 200 only when Postgres is reachable and
`OPENAI_API_KEY` is set (503 otherwise). Schema
([`migrations/001_initial.sql`](migrations/001_initial.sql)), `asyncpg` pool, and
config are in place; **ingestion, the OpenAI/CLIP embeddings, and the production
retrieval tools are not yet implemented.** Its migration is still Gen-1-shaped and
must be reconciled to the Gen-2 corpus before use (see ARCHITECTURE §8).

## Configuration

See [`.env.example`](.env.example). The PoC server needs **none** of these to run
locally.

| Var | Role |
|---|---|
| `CORPUS_PATH` | Folder of tagged JSON (default: sibling `slide_tagging`; deploy: `corpus`). |
| `ASSETS_PATH` | Resolves `recurring_elements[].image_path` to PNGs (default: `corpus/assets`). |
| `SOURCE_PPTX_PATH` | Folder of source `.pptx` + `manifest.json` for `get_slide_pptx` and the admission gate (default: `corpus/source`; local dev: `../slide_tagging/data/source`). |
| `MCP_SERVER_HOST` / `MCP_SERVER_PORT` / `$PORT` | Bind address (`$PORT` injected by Cloud Run/Render). |
| `DATABASE_URL` / `OPENAI_API_KEY` | Production pgvector server only (not the PoC). |
| `THUMBNAIL_BASE_PATH` / `EMBEDDING_MODEL` / `CLIP_MODEL` | Reserved for the production path. |
| `LOG_LEVEL` | structlog level. |

## Status

| Area | Status |
|---|---|
| Lean PoC server + 15 MCP tools + logo serving | ✅ built |
| Raw single-slide `.pptx` export (`get_slide_pptx`) + shape map | ✅ built |
| Admission gate (`.pptx` present + slide-count aligned) + slide-count manifest | ✅ built |
| Deploy (Docker, Render/Railway/Cloud Run, claude.ai connector) | ✅ built |
| Production pgvector: schema / config / `/health` | ✅ skeleton |
| Production pgvector: ingestion + embeddings + tools | ⛔ planned |
| Auth, object-storage offload, pgvector cutover (scale path) | ⛔ deferred — see [`docs/SCALING.md`](docs/SCALING.md) |

The `corpus-pptx` skill that consumes these tools is **client-owned**; the latest is
[`skills/skill_v5.md`](skills/skill_v5.md) (clone the bound reference slide's raw
`.pptx`, overwrite only the text, three-axis consistency gate, plus cross-deck canvas
handling). Earlier iterations (`skill_v1`–`skill_v4`) remain in `skills/` for reference.
This server only makes the served data correct.
