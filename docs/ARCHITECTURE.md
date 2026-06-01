# Architecture: the slide-corpus MCP server and the tagging service

This document describes the `mcp_slide_tagging` MCP server (MCP = Model Context
Protocol, the tool protocol agents like claude.ai speak), the `slide_tagging`
service that produces its data, and the contract that connects them. For a runnable
setup, follow the [server README](../README.md) and [`docs/POC.md`](POC.md); this
doc explains the *why* and *how it fits together*.

Two facts up front:

1. **The two projects are decoupled siblings.** They share no code, no database, and
   make no calls to each other. Their only coupling is a folder of tagged JSON files
   (+ extracted image assets) on disk. `mcp_slide_tagging` reads that folder
   **read-only** and never touches `slide_tagging` or its output.
2. **There are two server paths, and the lean one is built.** A **lean PoC server**
   (in-memory, no database, 15 retrieval tools incl. logo serving, raw single-slide
   `.pptx` export (`get_slide_pptx`), and the storyboard composites `suggest_outline` /
   `match_slide`) is built, deployed, and is what claude.ai connects to today. A **production pgvector server** (Postgres +
   embeddings) is scaffolded but its ingestion/tools/embeddings are deferred.
   Sections below are tagged **[built]**, **[skeleton]**, or **[planned]**.

---

## 1. What the system is for

Make an AI slide generator produce decks that respect a *real* design system instead
of generic defaults. That needs (a) a corpus of existing decks broken into
machine-actionable design + semantic metadata, and (b) a way for an agent to retrieve
the right slices at generation time.

| Project | Role | Responsibility |
|---|---|---|
| [`slide_tagging`](../../slide_tagging/) | **Producer** | Turn `.pptx` decks into tagged JSON (structural + semantic metadata) + extract branding images. |
| [`mcp_slide_tagging`](../) | **Consumer / server** | Serve the tagged corpus (tags, design system, logo images) to AI agents over MCP. |

The handoff is a folder of JSON + image assets. Everything else is internal to one side.

---

## 2. End-to-end data flow

```
  .pptx deck
      │
      ▼
┌──────────────────────────────────────────────────────────┐
│ slide_tagging  (producer — separate repo)                 │
│  Pipeline A [built]            Pipeline B [API + manual]   │
│  structural extraction   ──▶   semantic enrichment via the │
│  (python-pptx, pHash,          `bench` Anthropic API path  │
│  normalized fonts) +           or paste-into-claude.ai;    │
│  logo extraction               `merge` re-imposes Pipeline │
│  (`extract-assets`)            A fields; `eval`/`score`     │
│        └───────────┬──────────────┘                        │
│                    ▼                                        │
│   one tagged JSON per deck  +  extracted logo PNGs          │
│   → reference_data/hand_labels/*.tagged.json               │
│   → reference_data/assets/<deck>/*.png                     │
└──────────────────────┬─────────────────────────────────────┘
                       │  filesystem handoff (read-only)
                       │  CORPUS_PATH + ASSETS_PATH (or a bundled corpus/ snapshot)
                       ▼
┌──────────────────────────────────────────────────────────┐
│ mcp_slide_tagging  (consumer / server)                    │
│  Lean PoC server [built]          Production server [skeleton] │
│  src/poc_server.py + poc_corpus   src/server.py + Postgres │
│  in-memory; 15 MCP tools;         + pgvector + OpenAI/CLIP │
│  logos + raw slides as base64;    embeddings + ingestion   │
│  /mcp + /health (no DB/keys)      [planned]                │
└──────────────────────┬─────────────────────────────────────┘
                       │  MCP over HTTPS (Streamable HTTP /mcp)
                       ▼
        AI agent generating a new deck (claude.ai custom connector + the
        corpus-pptx skill)
```

---

## 3. The integration contract

### 3.1 The corpus is a directory of JSON (+ assets + source .pptx)
`slide_tagging` writes one JSON per tagged deck under
[`reference_data/hand_labels/`](../../slide_tagging/reference_data/hand_labels/),
extracted branding PNGs under `reference_data/assets/<deck-slug>/`, and the **source
`.pptx`** the deck was tagged from (under `data/source/`). Each JSON is a serialized
`DeckTag` (see §5). The `.pptx` is now part of the contract: `get_slide_pptx` slices a
single reference slide from it for high-fidelity cloning, and the admission gate
(§3.4) refuses any deck whose `.pptx` is missing or misaligned.

### 3.2 `CORPUS_PATH` / `ASSETS_PATH` / `SOURCE_PPTX_PATH` are the wire
[`src/config.py`](../src/config.py): `corpus_path` (default
`../slide_tagging/reference_data/hand_labels`) points at the JSON; `assets_path`
(default `corpus/assets`) resolves a recurring element's relative `image_path`
(`assets/<slug>/x.png`) to bytes; `source_pptx_path` (default `corpus/source`)
resolves the deck's source `.pptx` (`<deck>.pptx`) and reads a `manifest.json` of
precomputed slide counts. For deployment, a **bundled snapshot** under
[`corpus/`](../corpus/) (JSON + `corpus/assets/` + `corpus/source/*.pptx` +
`corpus/source/manifest.json`) ships in the Docker image, with `CORPUS_PATH=corpus`,
`SOURCE_PPTX_PATH=corpus/source`. `thumbnail_base_path` remains reserved for renders.

### 3.3 The relationship is read-only and one-directional
Data flows producer → consumer only. The server never writes back, tags, or renders.
Neither repo imports the other; you can run the tagger with no server and the server
against any folder of conformant JSON + assets.

### 3.4 The admission gate (a deck must be tagged **and** cloneable)
At load, [`poc_corpus.py`](../src/poc_corpus.py) keeps a deck only if its source
`.pptx` resolves **and** the `.pptx` slide count equals `len(slides[])` in the tags —
otherwise the deck is dropped from the **whole** corpus (every endpoint), with the
reason logged. The count comes from `corpus/source/manifest.json` (a dict lookup) so
boot doesn't parse every `.pptx`; absent manifest entries fall back to parsing (so keep
the manifest current — a stale count could admit a misaligned deck). The
producer-side contract that keeps decks passing this gate — tag from the `.pptx`, keep
counts/indices aligned, ship the `.pptx`, emit the manifest — is
[`docs/HANDOFF-slide_tagging.md`](HANDOFF-slide_tagging.md); run
`scripts/check_corpus.py` to see which decks pass before deploying.

---

## 4. `slide_tagging` — the producer

See its [`README`](../../slide_tagging/README.md), [`init.md`](../../slide_tagging/docs/init.md)
(historical design), and [`vlm_prompt_test.md`](../../slide_tagging/docs/vlm_prompt_test.md).

### 4.1 Pipeline A — deterministic structural extraction **[built]**
Reads the `.pptx` with `python-pptx`; no AI. Code in
[`extractors/structural/`](../../slide_tagging/src/slide_tagger/extractors/structural/).
- **Per-slide** (`SlideStructural`): index, title text + position, image/chart/table
  presence, density block.
- **Deck-level** (`DesignSystem`): modal title/body text styles — **font names are
  normalized to installable families** (`"Arial MT"`→`"Arial"`) and the title family
  falls back to the deck's dominant (body) font when the title placeholder has none
  ([`design_system.py`](../../slide_tagging/src/slide_tagger/extractors/structural/design_system.py)
  `_normalize_font`, `build_design_system`) — color palette, default alignment, and
  pHash recurring-element detection.

### 4.2 Logo / branding-image extraction **[built]**
[`extractors/structural/recurring_images.py`](../../slide_tagging/src/slide_tagger/extractors/structural/recurring_images.py)
+ the `extract-assets` CLI command find recurring branding images (scanning slides
**plus masters/layouts, recursing into group shapes**, at a logo-tuned recurrence
threshold), save a PNG per group, and record `image_path`/`source`/auto-`type` on
`recurring_elements`. Vector/grouped logos (no raster blob) use a manual drop-in.

### 4.3 Pipeline B — semantic enrichment **[API + manual]**
The semantic fields (deck-/slide-level enums + `inferred_rules`) are produced either
by **pasting the enrichment prompt + inputs into claude.ai**, or **automatically via
the `bench` command** which calls the Anthropic API N times/deck and scores the
output (averaging out run-to-run variance). The prompt lives in
[`deck_tagging_prompt.md`](../../slide_tagging/docs/deck_tagging_prompt.md).

### 4.4 The schema is the deliverable
Locked in [`schema/tagged.py`](../../slide_tagging/src/slide_tagger/schema/tagged.py)
(`DeckTag`/`SlideTag`/`DesignSystem`/`RecurringElement`) and
[`enums.py`](../../slide_tagging/src/slide_tagger/schema/enums.py): deck-, slide-, and
element-level (`inferred_rules`) enrichment + `provenance`. `RecurringElement` carries
`type`, `value`, `phash` (optional), `position`, `appears_on_slides`, and (from
`extract-assets`) `image_path` + `source`. Enrichment fields are optional/`null` until filled.

### 4.5 CLI
| Command | Purpose |
|---|---|
| `tag` / `deck-summary` | Paste-ready `STRUCTURAL DATA` / `DECK SUMMARY` grounding blocks (`--json` for full structural JSON). |
| `template` | Blank hand-tagging template (structural filled, enrichment `null`, `_legend`). |
| `validate` | Schema + completeness check, plus the **structural-alignment guard** the MCP gate needs (`slide_count == deck_length == len(slides)`, contiguous indices; `--pptx` also asserts the real `.pptx` slide count). |
| `render` | `.pptx` → per-slide PNGs (full + thumbnail) via LibreOffice + poppler. |
| `extract-assets` | Extract recurring logo/branding PNGs + merge into `recurring_elements`. |
| `manifest` | Write `manifest.json` (`filename → slide_count`) for a folder of source `.pptx` — the precomputed counts the MCP gate reads. |
| `bundle` | Assemble an MCP deploy snapshot for one deck (tagged JSON + assets + `.pptx` + manifest), alignment-checked, confidentiality-filtered. |
| `merge` | Re-impose Pipeline A structural fields from the template onto a VLM output (guard). |
| `score` / `eval` | Score enriched output vs hand-labels (per-field accuracy, confusions). |
| `bench` | Run the enrichment prompt via the Anthropic API N×/deck; report mean ± std. |

### 4.6 Known limits (deferred)
PDF parsing (`.pptx` only); `consistency_score` / `deviations_from_system` not computed;
vector/EMF logos not auto-extracted (manual drop-in). Rendering and master/layout logo
extraction are now built.

---

## 5. The data contract — JSON shape

One JSON file = one `DeckTag` (Generation-2 enrichment schema). Abbreviated:

```jsonc
{
  "source_filename": "...pptx", "source_format": "pptx", "slide_count": 29,
  "client_industry": "...", "client_type": "...", "engagement_stage": "...",
  "content_area": ["..."], "audience_level": "...", "geography": "...",
  "deck_summary_one_sentence": "...",
  "design_system": {
    "title_style": {"font_family": "Arial", "size_pt": ..., "weight": "...", "color_hex": "#..."},
    "body_style":  {"font_family": "Arial", ...},
    "color_palette": {"primary": "#...", "accent": "#...", "neutrals": ["#..."]},
    "default_text_alignment": "...", "grid": "...",
    "recurring_elements": [
      {"type": "footer", "value": "Strategy&"},
      {"type": "logo", "value": "Strategy&", "source": "layout",
       "image_path": "assets/<slug>/recurring_03.png", "phash": "..."},
      {"type": "page_number"}
    ]
  },
  "inferred_rules": { "title": {...}, "body_text": {...}, "color_palette": {...},
                      "chart_styling": {...}, "layout_conventions": {...} },
  "slides": [ {"index": 0, "title_text": "...", "title_position": "...",
               "image_count": 1, "has_chart": false, "density": {...},
               "slide_purpose": "...", "message_type": "...", "main_message": "...",
               "dominant_visual_element": "...", "chart_type": "...",
               "audience_level_slide": "...", "slide_position_role": "...",
               "placeholder_compliance": "...", "embedded_data_present": false,
               "zones": [...], "slot_types_present": ["..."],
               "reusability_score_qualitative": "...", "tier_match_difficulty": "..."} ],
  "provenance": {...}
}
```
Templates carry a leading `_legend` of allowed enum values; the server strips it on load.

> **Schema generation note.** The current corpus is entirely **Generation 2** (the
> three-level enrichment schema above). The earlier Gen-1 format (`deck_type`, `role`,
> `core_message`, `emphasis_techniques`) is gone from the corpus. The **lean PoC server
> reads Gen-2 directly.** The production pgvector migration (§6.2) is still Gen-1-shaped
> — reconciling it to Gen-2 is part of the deferred production work (§8).

---

## 6. `mcp_slide_tagging` — the consumer / server

### 6.1 Lean PoC server **[built — what runs today]**
[`src/poc_server.py`](../src/poc_server.py) + [`src/poc_corpus.py`](../src/poc_corpus.py):
a `FastMCP("slide-corpus-poc")` over `transport="streamable-http"` (`/mcp`) plus a
`GET /health`. It loads the Gen-2 JSON from `CORPUS_PATH` **into memory** — no Postgres,
no OpenAI, no embeddings — and binds `$PORT` (cloud) or 8000. It applies the
**admission gate** (§3.4) at load — keeping only decks with an index-aligned source
`.pptx`, using `corpus/source/manifest.json` ([`src/manifest.py`](../src/manifest.py))
to avoid parsing every deck on boot. Fifteen read-only tools:

| Tool | Returns |
|---|---|
| `list_decks()` | every deck + deck-level tags + slide_count |
| `get_deck(deck)` | deck tags + full `design_system` (normalized fonts, palette, grid, recurring elements) + `inferred_rules` + slide outline + `recurring_assets_available` |
| `get_deck_assets(deck)` | recurring branding images (logos) as **base64 PNGs** (type/source/position/image_path/base64) |
| `search_slides(...)` | slides by tag filters (slide_purpose/message_type/dominant_visual_element + deck-level industry/content_area/audience) and/or a keyword |
| `get_slide(deck, index)` | full tag set for one slide |
| **`get_slide_pptx(deck, index)`** | **one reference slide as a standalone, self-contained `.pptx`** (base64; [`pptx_split.py`](../src/pptx_split.py) keeps that slide + drops the rest) **plus a per-shape map** (shape_idx/text/style/geometry via [`slide_inspect.py`](../src/slide_inspect.py)) — the highest-fidelity source for clone-and-edit generation |
| `find_similar_slides(text)` | slides ranked by keyword overlap on `main_message` (embedding stand-in) |
| `list_vocabulary()` | valid filter values actually present in the corpus, per field (so `search_slides` strings hit) |
| `get_deck_outline(deck)` | narrative flow — each slide's `slide_position_role` + purpose + title, in order |
| **`suggest_outline(...)`** | **storyboard skeleton** from brief attributes (industry/content/audience/engagement/slide_count/key_sections): picks the closest reference deck by weighted tag overlap (threshold `min_deck_score=3.0`) and adapts its outline; each planned slide carries a candidate `reference {deck, index}`. Marks `low_confidence=true` + returns a generic spine when no deck clears the threshold |
| `find_slide_templates(...)` | reusable layouts for a slide kind, ranked by reusability, with `zones`/`slot_types_present` |
| **`match_slide(...)`** | **per-slide clone kit** for one storyboard point: tags + `zones` + `slot_types_present` + reusability/tier + the source deck's `design_system`; threshold `min_score=0.15` filters noise; `prefer_deck` boost; surfaces `best_below_threshold` when nothing clears |
| `get_house_style()` | style aggregated across all decks (dominant fonts/sizes, common palette, all logos) |
| `start_deck(deck)` | one-call kit: design_system + inferred_rules + logos (base64) + outline + reference slides |
| `corpus_stats()` | coverage: deck/slide counts, decks-with-logos, counts by industry/content_area/slide_purpose/message_type/dominant_visual_element (where the corpus is thin) |

`suggest_outline` and `match_slide` are read-only composites of the existing helpers
(no new storage, no server writes). Together they power the
[`skill_v2.md`](../skills/skill_v2.md) 3-stage flow (collect → storyboard →
generate); the storyboard itself (`storyboard.json`) is a **client-side, per-project
local artifact** the agent writes in the user's project folder — never uploaded,
never persisted server-side. This preserves the read-only / one-directional
boundary in §3.3.

`/health` returns `{status, decks, slides}` once the corpus loads (no external deps).
A no-LLM smoke check: [`scripts/poc_demo.py`](../scripts/poc_demo.py).

### 6.2 Production pgvector server **[skeleton]**
[`src/server.py`](../src/server.py) + [`src/retrieval/db.py`](../src/retrieval/db.py) +
[`migrations/001_initial.sql`](../migrations/001_initial.sql) + `docker-compose.yml`:
config, logging, `asyncpg` pool, the `decks`/`slides` schema with `ivfflat` embedding
indexes, and a `/health` that requires Postgres + `OPENAI_API_KEY`. **Ingestion, the
embeddings (OpenAI text / CLIP visual), and the production retrieval tools are planned.**
This is the scale-up path for when the corpus and traffic outgrow the in-memory PoC.

### 6.3 Deployment **[built — PoC]**
The PoC server ships as a [`Dockerfile`](../Dockerfile) bundling the `corpus/` snapshot
— JSON + `corpus/assets/` (logos) + `corpus/source/*.pptx` + `corpus/source/manifest.json`
(all three are load-bearing: the admission gate drops any deck whose `.pptx` is absent,
so a JSON-only bundle deploys empty). Rebuild the manifest
([`scripts/build_manifest.py`](../scripts/build_manifest.py)) when decks change, and
preflight with [`scripts/check_corpus.py`](../scripts/check_corpus.py). Config for
**Render** ([`render.yaml`](../render.yaml)), **Railway** ([`railway.json`](../railway.json)),
**Cloud Run** ([`.gcloudignore`](../.gcloudignore)), and Fly.io — plus a Cloudflare/ngrok
tunnel for local exposure. Full steps + the claude.ai custom-connector flow (URL
`https://…/mcp`, Auth: None) are in [`docs/POC.md`](POC.md). No-auth is a deliberate PoC
choice; for growth (auth, object-storage offload, pgvector cutover) see
[`docs/SCALING.md`](SCALING.md).

---

## 7. Build status at a glance

| Area | Status |
|---|---|
| slide_tagging · Pipeline A (structural, normalized fonts) | ✅ built |
| slide_tagging · logo/branding extraction (`extract-assets`) | ✅ built |
| slide_tagging · rendering (`render`, LibreOffice + poppler) | ✅ built |
| slide_tagging · enrichment via API (`bench`) + `merge`/`score`/`eval` | ✅ built |
| slide_tagging · enrichment schema (`tagged.py`, enums) | ✅ built |
| slide_tagging · PDF parsing, consistency_score, vector-logo extraction | ⛔ deferred |
| slide_tagging · **MCP handoff tooling (`validate` alignment guard, `manifest`, `bundle`)** | ✅ built |
| mcp_slide_tagging · **lean PoC server + 15 MCP tools + logo serving** | ✅ built |
| mcp_slide_tagging · **raw single-slide `.pptx` export (`get_slide_pptx`) + shape map** | ✅ built |
| mcp_slide_tagging · **admission gate (`.pptx` aligned) + slide-count manifest + `check_corpus`** | ✅ built |
| mcp_slide_tagging · **storyboard composites (`suggest_outline`, `match_slide`)** | ✅ built |
| mcp_slide_tagging · deploy (Docker, Render/Railway/Cloud Run, connector) | ✅ built |
| mcp_slide_tagging · production pgvector: schema/config/`/health` | ✅ skeleton |
| mcp_slide_tagging · production pgvector: ingestion + embeddings + tools | ⛔ planned |
| mcp_slide_tagging · auth, object-storage offload, pgvector cutover (scale path) | ⛔ deferred ([SCALING](SCALING.md)) |
| corpus-pptx-v6 skill (clone raw `.pptx`, shape-map-targeted, consistency-gated, cross-deck) | ✅ built |
| corpus-pptx v1–v5 skills (Stage-3-only · storyboard · raw-clone · clone+gate · cross-deck) | ✅ built |

---

## 8. Known seams, risks, and open questions

1. **Corpus size is 4 tagged decks, of which only 2 currently pass the admission gate**
   (§3.4) — electric-vehicle and ereadiness were tagged from PDF exports whose page
   count differs from the `.pptx`, so they're dropped until re-tagged from the `.pptx`
   (see [`HANDOFF-slide_tagging.md`](HANDOFF-slide_tagging.md)). A proof-of-concept set;
   retrieval quality and the value of vector search assume scale that doesn't exist yet.
2. **Logo coverage is partial.** Only raster branding on slides/masters is auto-extracted;
   vector/grouped logos (e.g. nigeria) yield no asset, so `get_deck_assets` returns `[]`
   for those decks — consumers fall back to the footer wordmark text.
3. **No auth on the public endpoint.** The PoC connector runs no-auth over HTTPS; fine
   for non-sensitive read-only data, but add a gate before anything sensitive.
4. **Production pgvector path is Gen-1-shaped.** Its migration predates Gen-2; ingestion
   must reconcile (map onto columns, add columns, or keep in `raw_json`) before it's used.
5. **Deployed corpus is a snapshot.** `corpus/` is a point-in-time copy; re-bundle (copy
   JSON + assets + the source `.pptx` into `corpus/source/`, then rebuild `manifest.json`)
   and redeploy when labels change. `slide-tagger bundle` does this per deck.
6. **`CORPUS_PATH`/`ASSETS_PATH`/`SOURCE_PPTX_PATH` defaults assume the sibling layout**
   under `slide_mcp/`; set them explicitly off the dev machine / in the container.

---

## Appendix A: layout and key files

```
slide_mcp/                              # parent dir (not a git repo)
├── slide_tagging/                      # PRODUCER
│   ├── src/slide_tagger/
│   │   ├── cli.py                      # tag/deck-summary/template/validate/render/extract-assets/
│   │   │                               #   merge/score/eval/bench + manifest/bundle (MCP handoff)
│   │   ├── schema/{models,tagged,enums}.py
│   │   ├── enrich.py · merge.py        # API enrichment client · structural merge guard
│   │   └── extractors/structural/      # Pipeline A + recurring_images.py (logos)
│   ├── reference_data/hand_labels/*.tagged.json   # the corpus (ground truth)
│   ├── reference_data/assets/<slug>/*.png         # extracted logo images
│   ├── data/source/*.pptx              # source decks (the .pptx half of the contract)
│   └── docs/{init,deck_tagging_prompt,manual_tagging,vlm_prompt_test}.md
└── mcp_slide_tagging/                  # CONSUMER / SERVER
    ├── src/
    │   ├── poc_server.py · poc_corpus.py   # ← lean PoC server (built)
    │   ├── pptx_split.py · slide_inspect.py · manifest.py  # raw-slide export · shape sigs · gate manifest
    │   ├── server.py · retrieval/db.py     # production pgvector server (skeleton)
    │   └── config.py                       # CORPUS_PATH / ASSETS_PATH / SOURCE_PPTX_PATH / …
    ├── corpus/{*.tagged.json, assets/, source/*.pptx, source/manifest.json}  # bundled deploy snapshot
    ├── scripts/{poc_demo, build_manifest, check_corpus, check_slide_consistency}.py
    ├── skills/skill_v6.md              # corpus-pptx-v6 (clone raw .pptx, shape-map-targeted, consistency-gated, cross-deck) ← latest
    ├── skills/skill_v{1,2,3,4,5}.md    # earlier iterations (legacy reference)
    ├── migrations/001_initial.sql · docker-compose.yml
    ├── Dockerfile · render.yaml · railway.json · .gcloudignore · .railwayignore
    └── docs/{ARCHITECTURE,POC,SCALING,HANDOFF-slide_tagging}.md
```

## Appendix B: key environment variables (`mcp_slide_tagging`)

See [`.env.example`](../.env.example). PoC server needs **none** of these to run locally.

| Var | Role |
|---|---|
| `CORPUS_PATH` | Folder of tagged JSON (default: sibling `slide_tagging`; deploy: `corpus`). |
| `ASSETS_PATH` | Resolves `recurring_elements[].image_path` to PNGs (default: `corpus/assets`). |
| `SOURCE_PPTX_PATH` | Folder of source `.pptx` + `manifest.json` for `get_slide_pptx` and the admission gate (default: `corpus/source`; local dev: `../slide_tagging/data/source`). |
| `MCP_SERVER_HOST` / `MCP_SERVER_PORT` / `$PORT` | Bind address (`$PORT` injected by Cloud Run/Render). |
| `DATABASE_URL` / `OPENAI_API_KEY` | Production pgvector server only (not the PoC). |
| `THUMBNAIL_BASE_PATH` / `EMBEDDING_MODEL` / `CLIP_MODEL` | Reserved for the production path. |
| `LOG_LEVEL` | structlog level. |
```
