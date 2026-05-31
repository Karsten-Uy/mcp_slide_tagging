# Scaling the slide-corpus MCP server

When and how to grow the server as the corpus grows — keyed to the number of decks
and slides you're serving. This is a planning doc: it maps corpus size to concrete
infrastructure moves, and ties each move to what's **already built** vs **deferred**
in this repo (see [`ARCHITECTURE.md`](ARCHITECTURE.md) for the full picture).

> **Calibrate, don't trust the numbers blindly.** The thresholds below are
> order-of-magnitude estimates extrapolated from a small PoC corpus. They're decision
> *triggers*, not guarantees — measure the real signals (§4) and let those drive the
> cutover. Counts are a proxy; latency / quality / boot time are the truth.

---

## 1. TL;DR — the decision table

| Corpus size | Total slides | Tier | What you run | Trigger to move up |
|---|---|---|---|---|
| **0–~50 decks** | ~0–2,000 | **0 — In-memory PoC** | `poc_server.py`, JSON in RAM, pptx bundled in image | Docker image > ~750 MB, or cold start > ~5 s, or keyword retrieval starts missing obvious matches |
| **~50–~300 decks** | ~2k–10k | **1 — In-memory + object storage** | PoC server, but pptx/assets in R2 (signed URLs) | Retrieval quality/latency degrades, or boot > ~10 s, or metadata RAM > a few hundred MB |
| **~300–~few k decks** | ~10k–100k | **2 — pgvector server** | `server.py` + Postgres + embeddings (scaffolded) | Single Postgres latency/recall degrades, or QPS saturates one node |
| **few k+ decks** | 100k–1M+ | **3 — Scale-out** | replicas, hnsw, read replicas, CDN, pgbouncer | SLO-driven (p95 latency, availability) |

**The single most important idea:** *deck count*, *slide count*, *file size*, and
*traffic* stress **different** parts of the system and hit walls at **different**
times (§2). You migrate when the **first** wall is hit, not when all of them are.

---

## 2. What actually drives scaling (and what each stresses)

| Dimension | What it stresses | First component to hurt |
|---|---|---|
| **# decks** | Boot time (one JSON read + manifest lookup per deck), metadata RAM | Cold start, then RAM |
| **# slides (total)** | Per-query retrieval cost (linear token-overlap scan in Python), retrieval *quality* | Retrieval quality first, then latency |
| **Raw .pptx file size × #decks** | Docker image size (decks bundled in image), response payload (base64) | Image size / deploy, then payload limits |
| **Traffic (QPS)** | CPU on the single in-memory process; no horizontal scaling while stateful | Tail latency under load |

Why this matters: a corpus of **30 large decks** can hit the *image-size* wall (Tier 1)
while its keyword retrieval is still perfectly fine. A corpus of **500 tiny decks** can
hit the *retrieval-quality* wall (Tier 2) while its image stays small. Watch all four.

---

## 3. The tiers

### Tier 0 — In-memory PoC *(what runs today)*

[`src/poc_server.py`](../src/poc_server.py) + [`src/poc_corpus.py`](../src/poc_corpus.py):
all tagged JSON loaded into RAM, keyword/tag retrieval, logos + single reference
slides served as base64, the `corpus/` snapshot (JSON + assets + `.pptx`) bundled into
the Docker image.

- **Good for:** ~0–50 decks / ~2,000 slides, low QPS.
- **Already optimized:** the slide-count [`manifest`](../src/manifest.py) means boot is
  O(decks) dict lookups, not a full-corpus python-pptx parse. Regenerate it with
  `uv run python -m scripts.build_manifest corpus/source` whenever decks change.
- **Watch for:** Docker image bloat (you already have a 20 MB deck — bundling dozens of
  those is the first likely wall), and keyword retrieval missing semantically-relevant
  slides as the slide count climbs.

### Tier 1 — In-memory + object storage offload

Keep the in-memory server, but stop shipping raw bytes in the image.

- **Move `.pptx` + assets to object storage.** Reuse the **Cloudflare R2** setup already
  in the sibling `cortyze_product` (`services/storage/r2.py`). Have `get_slide_pptx` /
  `get_deck_assets` return a **short-lived signed URL** (or stream via an MCP Resource)
  instead of base64. Image stays small; payloads stay small.
- **Keep only metadata in RAM**, fetch deck bytes on demand. RAM then scales with tags,
  not file sizes.
- **Good for:** ~50–300 decks / ~2k–10k slides.
- **Trigger to Tier 2 (whichever first):** retrieval quality drops (keyword overlap
  misses relevant slides — often noticeable in the **low hundreds** of slides), or boot
  exceeds ~10 s, or metadata RAM exceeds a few hundred MB.

### Tier 2 — pgvector production server *(scaffolded, not finished)*

Cut over to the Postgres + embeddings server that's already skeletoned:
[`src/server.py`](../src/server.py), [`src/retrieval/db.py`](../src/retrieval/db.py),
[`migrations/001_initial.sql`](../migrations/001_initial.sql),
[`docker-compose.yml`](../docker-compose.yml).

**Deferred work to do at this cutover** (see [ARCHITECTURE §6.2 / §8](ARCHITECTURE.md)):
1. **Ingestion script:** folder/manifest → `decks`/`slides` rows.
2. **Embeddings:** OpenAI text on `main_message`/summaries (+ optional CLIP visual on
   thumbnails). ivfflat indexes are already defined in the migration.
3. **Port the 15 tools to SQL:** `match_slide`/`find_similar_slides` become vector
   queries; tag filters become `WHERE` clauses (`idx_slides_role_density` exists).
4. **⚠️ Reconcile the schema to Gen-2.** The migration is still **Gen-1-shaped**
   (`deck_type`, `role`, `core_message`). `raw_json` columns exist for backfill — map
   Gen-2 fields onto columns or add columns before relying on it.

**The big win:** the server becomes **stateless** — many replicas behind a load
balancer share one Postgres + R2, so horizontal scaling becomes trivial and cold starts
no longer touch the corpus.

- **Good for:** ~300–few-thousand decks / 10k–100k+ slides.

### Tier 3 — Scale-out tuning

Once DB-backed, scaling is mostly knobs:

- **Vector index:** ivfflat → **hnsw** for better recall/latency; tune `lists ≈ sqrt(rows)`,
  build after data lands.
- **Postgres:** managed (e.g. **Supabase**, already used in `cortyze_product`); add
  **read replicas** for retrieval-heavy load; put **pgbouncer** in front (the pool is
  `max_size=5` today in [`db.py`](../src/retrieval/db.py)).
- **Embeddings:** batch + cache at ingest; CLIP needs a GPU — reuse the RunPod pattern
  from `cortyze_product` or run it offline in the tagging pipeline.
- **Serving:** R2 + CDN for `.pptx`/thumbnails; autoscaling stateless containers
  (Cloud Run / Render / Fly — already configured).
- **Trigger:** SLO-driven — vector recall/latency degrades, QPS saturates a node, or
  p95 latency misses target.

---

## 4. Signals to watch (better than raw counts)

Counts are a convenient proxy, but migrate on these measured signals:

| Signal | Healthy | Migrate when | Points to |
|---|---|---|---|
| **Retrieval quality** | Top match is the slide a human would pick | Keyword search regularly misses semantically-relevant slides | Tier 2 (embeddings) |
| **Retrieval p95 latency** | < ~100 ms | > ~300–500 ms per `match_slide`/`search_slides` | Tier 2, then index tuning |
| **Cold-start / boot** | < ~3 s | > ~10 s | Tier 1 (offload), Tier 2 (stateless) |
| **Docker image size** | < ~750 MB | approaching ~1 GB | Tier 1 (R2 offload) |
| **Metadata RAM** | comfortably within instance | > a few hundred MB | Tier 2 (DB-backed) |
| **QPS / CPU** | single process keeps up | sustained CPU saturation | Tier 2/3 (stateless replicas) |

A rough rule of thumb: **retrieval quality** is usually the first thing to push you off
the PoC (it degrades in the hundreds of slides), and **image size** is the first thing
to push you off bundling (it degrades in the dozens of large decks). Whichever you hit
first decides your next move.

---

## 5. Cross-cutting concerns (plan for these before you need them)

- **Mature the handoff into a manifest/bundle.** Today the producer→server handoff is a
  folder copy ([ARCHITECTURE §3](ARCHITECTURE.md)). Have `slide_tagging` emit a versioned
  bundle (JSON + asset/pptx pointers + **precomputed slide counts** + content hashes).
  That one change feeds the boot-time count gate, deterministic Tier-2 ingestion, and
  incremental re-ingest (only changed decks). The [`manifest`](../src/manifest.py) is
  step one of this; moving its generation into `slide_tagging` is the natural next step.
- **Embedding cost (Tier 2+).** Text embeddings are cheap (fractions of a cent per 1k
  slides) and one-time at ingest; CLIP visual embeddings need a GPU. Batch and cache;
  never embed on the request path.
- **Auth + confidentiality.** The connector is **no-auth public** today
  ([ARCHITECTURE §8 item 3](ARCHITECTURE.md)). As the corpus grows and you serve real client
  decks' raw `.pptx`, add an auth gate **before** scaling exposure — this is a
  prerequisite for Tier 1+ with sensitive data, not an afterthought.
- **The `.pptx` admission gate.** Every endpoint only serves decks whose `.pptx` exists
  and whose slide count aligns with the tags. As you add decks, keep the manifest fresh
  (or emit it from `slide_tagging`) so this stays a cheap check at any scale.

---

## 6. "Do this next" checklist

- **Still in Tier 0, adding decks:** copy `.pptx` → `corpus/source/`, JSON → `corpus/`,
  run `scripts/build_manifest.py`, redeploy. Watch image size.
- **Approaching Tier 1:** move pptx/assets to R2, switch `get_slide_pptx` /
  `get_deck_assets` to signed URLs, lazy-load metadata. Add auth if data is sensitive.
- **Approaching Tier 2:** write the ingestion script + embeddings, port tools to SQL,
  reconcile the schema to Gen-2, stand up managed Postgres, deploy stateless replicas.
- **In Tier 3:** swap to hnsw, add read replicas + pgbouncer + CDN, set autoscaling and
  latency SLOs.
