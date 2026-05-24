# Architecture: the slide-corpus MCP server and the tagging service

This document describes the `mcp-slide-corpus` MCP server (MCP = Model Context
Protocol, the tool/resource protocol agents like claude.ai speak), the
`slide_tagging` service that produces its data, and the contract that connects
them. It is written for engineers working on either repo. For a runnable local
setup, follow the [server README quickstart](../README.md); this doc explains the
*why* and the *how it fits together*.

Two facts up front, because they shape everything below:

1. **The two projects are decoupled siblings.** They share no code, no database,
   and make no calls to each other. Their only coupling is a directory of tagged
   JSON files on disk. `mcp-slide-corpus` reads that directory **read-only** and
   never touches `slide_tagging` or its output.
2. **Much of the system is designed but not yet built.** `slide_tagging`'s
   Pipeline A is real; its Pipeline B is not. `mcp-slide-corpus` is a Phase-1
   skeleton: schema, config, logging, and `/health` exist; ingestion and the
   retrieval tools do not. Sections below are tagged **[built]** or **[planned]**
   so you can tell what runs today from what is on paper.

---

## 1. What the system is for

The goal is to make an AI slide generator produce decks that respect a *real*
design system instead of generic defaults. To do that you need a corpus of
existing decks broken down into machine-actionable design and semantic metadata,
plus a way for an agent to retrieve the right slices of that corpus at generation
time.

That splits cleanly into two responsibilities, one per project:

| Project | Role | Responsibility |
|---|---|---|
| [`slide_tagging`](../../slide_tagging/) | **Producer** | Turn `.pptx` decks into tagged JSON records (structural + semantic metadata). |
| [`mcp-slide-corpus`](../) | **Consumer / server** | Serve the tagged corpus to AI agents over MCP, with structured + vector retrieval. |

The handoff between them is a folder of JSON files. Everything else is internal
to one side or the other.

---

## 2. End-to-end data flow

```
  .pptx deck
      │
      ▼
┌─────────────────────────────────────────────────────────┐
│ slide_tagging  (producer — separate repo)                │
│                                                          │
│  Pipeline A  [built]            Pipeline B  [planned]    │
│  deterministic structural  ──▶  VLM / hand-label         │
│  extraction (python-pptx,       semantic enrichment      │
│  pHash)                         (grounded by Pipeline A)  │
│        └──────────────┬──────────────┘                   │
│                       ▼                                   │
│         one tagged JSON file per deck                     │
│         → reference_data/hand_labels/*.json              │
└───────────────────────┬─────────────────────────────────┘
                        │   filesystem handoff
                        │   (read-only; CORPUS_PATH)
                        ▼
┌─────────────────────────────────────────────────────────┐
│ mcp-slide-corpus  (consumer / server)                    │
│                                                          │
│  ingest  [planned] ──▶ Postgres + pgvector  [schema only]│
│   JSON → decks / slides rows                              │
│   + OpenAI text embeddings  + (opt.) CLIP visual embeds   │
│                       │                                   │
│                       ▼                                   │
│  FastMCP server  [skeleton built]                        │
│   Streamable HTTP  /mcp   ·   GET /health                 │
│   6 retrieval tools  [planned]                           │
└───────────────────────┬─────────────────────────────────┘
                        │   MCP over HTTPS
                        ▼
        AI agent generating a new deck
        (e.g. claude.ai custom connector)
```

The arrow that matters for "how the two projects integrate" is the single
filesystem handoff in the middle. The rest of this document expands the boxes on
either side of it. Note `[schema only]`: the Postgres tables are defined by the
migration, but the database is **empty** until ingest is built and run.

---

## 3. The integration contract

This is the heart of the relationship. Get this right and the two repos can
evolve independently.

### 3.1 The corpus is a directory of JSON files

`slide_tagging` writes one JSON file per tagged deck. The canonical location for
ground-truth, hand-labeled records is
[`slide_tagging/reference_data/hand_labels/`](../../slide_tagging/reference_data/hand_labels/);
the design also reserves `data/tagged/` for automated output. Each file is a
serialized deck record (see §5 for its shape).

### 3.2 `CORPUS_PATH` is the wire

`mcp-slide-corpus` finds the corpus through one setting,
[`corpus_path`](../src/config.py), which defaults to the sibling repo's
hand-labels folder:

```
CORPUS_PATH=../slide_tagging/reference_data/hand_labels
```

That relative default assumes the two repos sit side by side under the same
parent (`slide_mcp/`). In any other layout — CI, a container, a server — set
`CORPUS_PATH` explicitly to wherever the tagged JSON lives.

A second, optional path, `THUMBNAIL_BASE_PATH`, resolves the relative
`thumbnail_path` values inside the JSON to actual PNG files on disk. Leave it
unset until renders exist; image-dependent data (visual embeddings) is simply
skipped when a thumbnail file is missing.

### 3.3 The relationship is read-only and one-directional

- Data flows **only** producer → consumer. The server reads JSON; it never
  writes back, never tags, never ingests new decks, never renders.
- The server's own writable state (the Postgres database) is **derived** from the
  corpus. It can be dropped and rebuilt from the JSON at any time; the JSON is the
  source of truth.
- Because the coupling is a file format and a path, neither repo imports the
  other. You can run the tagger with no database and run the server against any
  folder of conformant JSON.

---

## 4. `slide_tagging` — the producer

A two-pipeline tagging service. See its own [`README`](../../slide_tagging/README.md)
and design notes in [`docs/init.md`](../../slide_tagging/docs/init.md).

### 4.1 Pipeline A — deterministic structural extraction **[built]**

Reads the `.pptx`'s own data with `python-pptx`; no AI, so these fields are
near-100% accurate. These are exactly the fields a VLM (vision-language model)
must *not* guess — they're fed to Pipeline B as grounding instead.

- **Per-slide** ([`schema/models.py`](../../slide_tagging/src/slide_tagger/schema/models.py)
  `SlideStructural`): index, title text + position, image count, chart/table
  presence, and a `Density` block (word count, text blocks, visual elements,
  whitespace-ratio estimate, density bucket).
- **Deck-level** (`DeckStructural`, `DesignSystem`): modal title/body text styles
  (font, size, weight, color, alignment), color palette, default alignment, and
  recurring-element detection via perceptual hash (pHash).
- **Code:** [`extractors/structural/`](../../slide_tagging/src/slide_tagger/extractors/structural/)
  — `pptx_parser.py`, `aggregator.py`, `density.py`, `design_system.py`.

### 4.2 Pipeline B — semantic enrichment **[planned]**

Adds the fields Pipeline A can't read off the file: deck purpose/industry, slide
roles and messages, element-level style rules. Intended to run a VLM over slide
renders, grounded by Pipeline A's structural blocks. **Today this repo only
*grounds* Pipeline B** — it emits paste-ready `STRUCTURAL DATA` / `DECK SUMMARY`
blocks and ships the enrichment prompt
([`docs/deck_tagging_prompt.md`](../../slide_tagging/docs/deck_tagging_prompt.md)),
but does not make the VLM calls. The same fields can be filled by hand.

### 4.3 The schema is the deliverable

The enrichment schema is locked in
[`schema/tagged.py`](../../slide_tagging/src/slide_tagger/schema/tagged.py)
(`DeckTag` / `SlideTag`) and [`schema/enums.py`](../../slide_tagging/src/slide_tagger/schema/enums.py),
organized in three levels: deck-level, slide-level, and an element-level
`inferred_rules` block, plus a `provenance` block recording who/what tagged it.
Enrichment fields are optional — `null` until filled — so a freshly templated deck
validates and `validate` can report what's still untagged.

### 4.4 CLI

The [`slide-tagger`](../../slide_tagging/src/slide_tagger/cli.py) command exposes
Pipeline A and the hand-labeling workflow:

| Command | Purpose |
|---|---|
| `tag <deck.pptx>` | Paste-ready `STRUCTURAL DATA` blocks (or `--json` for full structural JSON). |
| `deck-summary <deck.pptx>` | Whole-deck `DECK SUMMARY` grounding block. |
| `template <deck-or-json>` | Blank hand-tagging template: structural filled, enrichment `null`, with an `_legend` of allowed values. |
| `validate <labels.json>` | Validate a tagged file against the schema and report completeness. |

### 4.5 Known limits (deferred)

PDF parsing (`.pptx` only); slide → PNG rendering (needs LibreOffice); recurring
elements on the master/layout and vector images (EMF/WMF) aren't hashed;
`consistency_score` and `deviations_from_system` aren't computed; Pipeline B's
VLM calls.

---

## 5. The data contract — JSON shape and its drift

This section is where most integration bugs will come from, so it's explicit.

### 5.1 What a record looks like

One JSON file = one deck. Abbreviated shape of the live corpus file
([`pwc-global-top-100-companies-2023.json`](../../slide_tagging/reference_data/hand_labels/pwc-global-top-100-companies-2023.json)),
showing the fields the server's columns map to:

```jsonc
{
  "source_filename": "pwc-global-top-100-companies-2023.pptx",
  "source_format": "pptx",
  "slide_count": 22,
  "deck_type": "report",            // ── deck-level semantic fields
  "style_archetype": "...",         //    (Generation 1; see §5.2)
  "narrative_structure": "...",
  "dominant_visual_mode": "...",
  "design_system": { "title_style": {...}, "color_palette": {...},
                     "recurring_elements": [...] },   // Pipeline A
  "slides": [
    {
      "index": 0,
      "title_text": "...",          // ── per-slide structural (Pipeline A)
      "title_position": "top-left",
      "image_count": 1, "has_chart": false, "has_table": false,
      "density": { "word_count": 47, "text_blocks": 3,
                   "visual_elements": 1, "whitespace_ratio_est": 0.65,
                   "bucket": "sparse" },
      "role": "title",              // ── per-slide semantic (Generation 1)
      "layout_archetype": "...",
      "core_message": "...",
      "emphasis_techniques": ["hierarchy_by_size", "contrast"]
    }
  ]
}
```

Templates also carry a leading `_legend` of allowed enum values; it's a tagging
aid, not part of the schema, and the server's `validate`/ingest strip it.

### 5.2 Two schema generations exist

The corpus format has evolved, and the two repos are currently aligned to
*different generations* of it:

- **Generation 1 (the live corpus + the DB).** The one existing corpus file,
  [`pwc-global-top-100-companies-2023.json`](../../slide_tagging/reference_data/hand_labels/pwc-global-top-100-companies-2023.json),
  carries deck-level `deck_type`, `style_archetype`, `narrative_structure`,
  `dominant_visual_mode`, and per-slide `role`, `layout_archetype`,
  `core_message`, `emphasis_techniques`. The server's migration
  ([`001_initial.sql`](../migrations/001_initial.sql)) has columns for exactly
  these — it was, per its own header comment, "adapted from the design spec to
  match the real corpus shape."
- **Generation 2 (the tagger's current code).** `slide_tagging`'s present
  `tagged.py` emits the three-level enrichment schema instead
  (`client_industry`, `slide_purpose`, `message_type`, `main_message`,
  `inferred_rules`, …) and does **not** include the Gen-1 semantic fields.

**Implication:** a corpus file produced by today's `slide-tagger template` would
*not* line up field-for-field with today's migration columns. The existing
hand-labeled file does, because it predates the schema change.

### 5.3 How the server absorbs the mismatch

The DB schema is deliberately forgiving so this drift doesn't block ingest:

- **Semantic columns are nullable.** `deck_type`, `role`, `summary_text`, etc. can
  be null — the corpus is hand-labeled incrementally and is often partial.
- **`raw_json` is preserved on both tables.** Every row keeps the full original
  JSON, so a later schema reconciliation can backfill new columns without
  re-reading source files.
- **Some columns are derived at ingest, not read from JSON:** `deck_id` /
  `slide_id` (UUIDv5, deterministic from content — the source JSON carries no
  ids), and `has_image` (from `image_count > 0`).

### 5.4 Reconciling the two generations is open work

When ingest is implemented, decide whether to (a) map Gen-2 enrichment fields
onto the existing columns, (b) add columns for them, or (c) keep them only in
`raw_json` and project at query time. Until then, treat the Gen-1 fields as the
columns the server actually understands. See §8.

---

## 6. `mcp-slide-corpus` — the consumer / server

A read-only MCP server over the tagged corpus, backed by Postgres + pgvector,
reachable over Streamable HTTP and deployable to public HTTPS as a claude.ai
custom connector. Python ≥ 3.11; key deps: `mcp[cli]`, `asyncpg`, `pgvector`,
`openai`, `pydantic-settings`, `structlog`, `starlette`.

### 6.1 Server and transport **[skeleton built]**

[`src/server.py`](../src/server.py) builds a `FastMCP("slide-corpus")` instance
and runs it with `transport="streamable-http"`. MCP traffic is served at `/mcp`;
a plain `GET /health` is added as a custom route. Entry point: `python -m
src.server`.

`/health` returns **200** only when Postgres is reachable **and**
`OPENAI_API_KEY` is set, otherwise **503** — both are hard requirements for
serving (the OpenAI key is needed to embed query text at retrieval time). This is
the deploy/readiness probe.

### 6.2 Database layer **[built]**

- **Engine:** Postgres 16 with the `pgvector` extension
  ([`docker-compose.yml`](../docker-compose.yml) uses `pgvector/pgvector:pg16`).
  Migrations in [`migrations/`](../migrations/) auto-apply on first init of an
  empty data volume (mounted into `docker-entrypoint-initdb.d`).
- **Pool:** [`src/retrieval/db.py`](../src/retrieval/db.py) holds a single lazily
  created `asyncpg` pool (min 1 / max 5) shared across tool calls. Each new
  connection registers the pgvector codec so `VECTOR` columns round-trip as
  Python lists. `ping()` backs `/health`.

### 6.3 Schema and indexes **[built]**

Two tables ([`001_initial.sql`](../migrations/001_initial.sql)):

- **`decks`** — `deck_id` (UUID PK), source filename, the Gen-1 semantic fields
  (nullable), `design_system` JSONB, `consistency_score`, `summary_text`,
  `summary_embedding VECTOR(1536)`, `slide_count`, `raw_json`, `tagged_at`.
- **`slides`** — `slide_id` (UUID PK), `deck_id` FK (cascade), `index`, `role`,
  `layout_archetype_id`, density/structural fields, `has_chart` / `has_image`,
  `core_message`, `emphasis_techniques TEXT[]`, `structural_data` JSONB,
  `thumbnail_path`, `core_message_embedding VECTOR(1536)`, `visual_embedding
  VECTOR(768)`, `raw_json`, `tagged_at`.

Indexes support both retrieval styles: a B-tree on `(role, density_bucket)` and a
per-deck index for **structured filters**, and `ivfflat` cosine indexes on all
three embedding columns for **vector search**.

### 6.4 Embeddings — created by the server, not the tagger **[planned ingest]**

Embeddings live on the server side and are produced at ingest time:

- **Text:** OpenAI `text-embedding-3-small` (1536-dim) for deck `summary_text` and
  slide `core_message`. The same model also embeds the agent's **query text at
  retrieval time**, so the key is needed for ingest *and* serving (and `/health`
  checks for it) — see §6.1. This is why the server needs `OPENAI_API_KEY` and the
  tagger does not.
- **Visual:** CLIP `ViT-L/14` (768-dim) for slide thumbnails — populated only when
  a thumbnail file is resolvable via `THUMBNAIL_BASE_PATH`, left null otherwise.

### 6.5 Ingestion **[planned]**

Not yet implemented. By design it reads each JSON file under `CORPUS_PATH`,
derives `deck_id`/`slide_id` (UUIDv5) and `has_image`, computes embeddings, and
upserts `decks` / `slides` rows while preserving `raw_json`. It is a read-only
consumer of the corpus: it reads the files, writes only to its own database.

### 6.6 Retrieval tools **[planned — not yet specified in code]**

The README states the server will expose **six read-only retrieval tools** that
agents call while generating a deck. **Their exact names and signatures are not
defined anywhere in the repo yet** — and the *six* don't map one-to-one onto the
four capability categories below. This section describes the *capability surface*
the schema and indexes are built to support, not a committed API; deciding how
that surface is sliced into six concrete tools is part of the open work:

- **Structured filtering** over deck/slide fields — e.g. "sparse data slides from
  report-style decks" — served by the `(role, density_bucket)` and per-deck
  indexes.
- **Text-semantic search** over `summary_embedding` (find similar decks) and
  `core_message_embedding` (find slides expressing a similar idea), via the
  `ivfflat` cosine indexes.
- **Visual-similarity search** over `visual_embedding`, once thumbnails and CLIP
  embeddings exist.
- **Hybrid retrieval** — structured filters narrow the candidate set, vector
  similarity ranks within it (the standard RAG — retrieval-augmented generation —
  pattern called out in [`init.md`](../../slide_tagging/docs/init.md)
  §"Storage and retrieval").

When implementing these, pin down the six tool contracts first and document them
here.

### 6.7 Deployment **[planned]**

Intended to run behind public HTTPS and be registered as a **claude.ai custom
connector**, with `/health` as the readiness probe. Not yet wired up.

---

## 7. Build status at a glance

| Area | Status |
|---|---|
| slide_tagging · Pipeline A (structural extraction) | ✅ built |
| slide_tagging · CLI (`tag`/`deck-summary`/`template`/`validate`) | ✅ built |
| slide_tagging · enrichment schema (`tagged.py`, enums) | ✅ built |
| slide_tagging · Pipeline B VLM calls | ⛔ planned (grounding only today) |
| slide_tagging · PDF, rendering, consistency_score, deviations | ⛔ deferred |
| mcp-slide-corpus · config, logging, `/health` | ✅ built |
| mcp-slide-corpus · DB schema + migration + pool | ✅ built |
| mcp-slide-corpus · ingestion JSON → Postgres | ⛔ planned |
| mcp-slide-corpus · 6 retrieval tools | ⛔ planned (unspecified) |
| mcp-slide-corpus · embeddings (OpenAI text / CLIP visual) | ⛔ planned |
| mcp-slide-corpus · public deployment / claude.ai connector | ⛔ planned |

---

## 8. Known seams, risks, and open questions

1. **Schema-generation drift (the big one).** The DB/migration match the Gen-1
   corpus format; the tagger now emits Gen-2 (§5). Reconcile before ingest is
   written, or ingest will silently drop the tagger's current semantic fields
   into `raw_json` and nowhere else. *Owner: whoever writes ingest.*
2. **The six tools are an unwritten contract.** They're promised in the README and
   the schema is shaped for them, but no signatures exist. Define and document
   them (here) before building, so the agent-facing API is deliberate.
3. **`CORPUS_PATH` relative default is environment-fragile.** It only resolves
   when the repos are siblings under `slide_mcp/`. Always set it explicitly off
   the developer's machine.
4. **Embeddings depend on a live OpenAI key at ingest *and* query time.** Text
   queries are embedded on the fly, so a missing/invalid key fails `/health` and
   blocks retrieval, not just ingest.
5. **Visual search is gated on rendering, which is gated on LibreOffice.** No
   thumbnails → no CLIP embeddings → no visual-similarity retrieval. This cuts
   across both repos (rendering lives on the tagger side, embedding on the server
   side).
6. **Corpus size is one deck.** Everything downstream (ivfflat list counts,
   retrieval quality, the value of the tools) assumes a corpus that doesn't exist
   at scale yet.
7. **No auth model for the public endpoint.** The server is meant to run on public
   HTTPS as a claude.ai connector, but authentication, authorization, and rate
   limiting for `/mcp` are undefined. Decide before exposing it.
8. **No defined re-ingest trigger.** The database is derived and rebuildable from
   the corpus, but *when* re-ingest runs (manual, on deploy, on file change) and
   whether it's a full rebuild or incremental upsert is unspecified.

---

## Appendix A: layout and key files

```
slide_mcp/                              # parent dir (not a git repo)
├── slide_tagging/                      # PRODUCER
│   ├── src/slide_tagger/
│   │   ├── cli.py                      # tag / deck-summary / template / validate
│   │   ├── schema/{models,tagged,enums}.py   # structural + enrichment contract
│   │   └── extractors/structural/      # Pipeline A
│   ├── reference_data/hand_labels/     # ← the corpus (ground truth JSON)
│   ├── data/{source,renders,tagged}/   # source decks, PNG renders, auto output
│   └── docs/{init,deck_tagging_prompt,manual_tagging,vlm_prompt_test}.md
└── mcp-slide-corpus/                   # CONSUMER / SERVER
    ├── src/
    │   ├── server.py                   # FastMCP app + /health
    │   ├── config.py                   # settings (incl. CORPUS_PATH)
    │   └── retrieval/db.py             # asyncpg pool + pgvector codec
    ├── migrations/001_initial.sql      # decks + slides schema
    ├── docker-compose.yml              # Postgres + pgvector
    └── docs/ARCHITECTURE.md            # this file
```

## Appendix B: key environment variables (`mcp-slide-corpus`)

See [`.env.example`](../.env.example) for the full list.

| Var | Role |
|---|---|
| `DATABASE_URL` | Postgres (pgvector) DSN. |
| `OPENAI_API_KEY` | Text embeddings — required for ingest, query, and `/health`. |
| `CORPUS_PATH` | Folder of tagged JSON; the integration seam. Defaults to the sibling tagging repo. |
| `THUMBNAIL_BASE_PATH` | Resolves relative `thumbnail_path`s to PNGs; gates visual embeddings. |
| `MCP_SERVER_HOST` / `MCP_SERVER_PORT` | Bind address (default `0.0.0.0:8000`). |
| `EMBEDDING_MODEL` / `CLIP_MODEL` | `text-embedding-3-small` / `ViT-L/14`. |
| `LOG_LEVEL` | structlog level. |
