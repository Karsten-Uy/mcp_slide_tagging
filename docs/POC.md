# PoC: query the slide corpus from claude.ai (no infra)

A minimal, zero-dependency way to use the tagged corpus from the **claude.ai web
GUI** as a custom connector. It skips Postgres/pgvector/OpenAI entirely: the
server loads the tagged Gen-2 JSON from `CORPUS_PATH` into memory and exposes
read-only retrieval tools over the basic labels. Graduate to the production
pgvector path ([README](../README.md) + [`src/server.py`](../src/server.py)) once
the corpus is large enough to need vector search.

> Built because the production path needs Postgres + an OpenAI key **and** a
> Gen-1→Gen-2 schema reconciliation — all overkill for a proof of concept.

## What it serves

`src/poc_server.py` — FastMCP over Streamable HTTP at `/mcp`, plus `GET /health`.
Fifteen tools over the corpus. The corpus is filtered by an **admission gate** — a
deck is served only if its source `.pptx` is present and its slide count matches the
tags — so of the four tagged decks, two currently pass (nigeria + digital-auto =
2 decks / 69 slides); the other two lack an index-aligned `.pptx` (see
[`HANDOFF-slide_tagging.md`](HANDOFF-slide_tagging.md)). Tools:

| Tool | What it does |
|---|---|
| `list_decks()` | Every deck + its deck-level tags (industry, type, content_area, audience, summary, slide_count). |
| `get_deck(deck)` | Design source-of-truth: deck tags + `design_system` (normalized fonts, colors, palette, grid, recurring elements) + `inferred_rules` + a one-line outline of every slide + `recurring_assets_available`. Use it to match a deck's look when generating. |
| `get_deck_assets(deck)` | The deck's recurring **branding images (logos)** as base64 PNGs (type/source/position/image_path/base64), so a generated deck can carry real branding. Returns `[]` when a deck's logo is vector-only (no extractable raster). |
| `search_slides(...)` | Filter slides by `slide_purpose` / `message_type` / `dominant_visual_element` + deck-level `client_industry` / `content_area` / `audience_level`, and/or a `text` keyword over title + main_message. |
| `get_slide(deck, index)` | Full tag set for one slide. |
| **`get_slide_pptx(deck, index)`** | **One reference slide as a standalone, self-contained `.pptx`** (base64) + a per-shape map (shape_idx/text/style/geometry). The highest-fidelity clone source: open it with python-pptx and overwrite only the text. Non-text shapes (charts/tables/groups) aren't in the map — use python-pptx on the bytes for those. |
| `find_similar_slides(text)` | Keyword-overlap ranking over main_message (cheap stand-in for embeddings). |
| `list_vocabulary()` | The valid filter values actually present in the corpus, per field — so `search_slides` strings hit instead of silently returning nothing. |
| `get_deck_outline(deck)` | The deck's narrative flow: each slide's `slide_position_role` + purpose + title, in order. |
| **`suggest_outline(...)`** | **Storyboard skeleton from brief attributes** (industry/content/audience/engagement/slide_count/key_sections). Picks the closest reference deck by weighted tag overlap (threshold `min_deck_score=3.0`) and adapts its outline; each planned slide carries a candidate `reference {deck, index}`. Returns `low_confidence=true` with a generic spine when no deck clears the threshold — never silently locks in a weak deck. |
| `find_slide_templates(...)` | Reusable layout skeletons for a slide kind, ranked by reusability, with their `zones` / `slot_types_present`. |
| **`match_slide(...)`** | **Per-slide clone kit** for one storyboard point: tags + `zones` + `slot_types_present` + reusability/tier + the source deck's `design_system`. Combines find_similar_slides + get_slide + design lookup in one call; threshold `min_score=0.15` filters noise; `prefer_deck` biases toward the storyboard's chosen reference deck for design coherence; surfaces `best_below_threshold` when nothing clears. |
| `get_house_style()` | The firm's style aggregated across all decks (dominant fonts/sizes, common palette, all logos). |
| `start_deck(deck)` | One-call kit to model a new deck: design_system + inferred_rules + logos (base64) + outline + reference slides. |
| `corpus_stats()` | Coverage: deck/slide counts, decks-with-logos, and counts by industry / content_area / slide_purpose / message_type / dominant_visual_element (shows which slide kinds lack a clone precedent). |

### Generating decks with the corpus

Skills are client-owned and live in `skills/`; load **one** alongside the connector.
The latest is:

- **[`skills/skill_v5.md`](../skills/skill_v5.md)** (`corpus-pptx-v5`) — the 3-stage
  flow (brief → storyboard → generate), where Stage 3 **clones the bound reference
  slide's raw `.pptx`** via `get_slide_pptx` and overwrites only the visible text
  (never restyling), gated by a three-axis consistency check
  ([`scripts/check_slide_consistency.py`](../scripts/check_slide_consistency.py))
  before render, plus cross-deck canvas handling. Cloning is the only generation path —
  no from-scratch building — which is what keeps output firm-authentic instead of
  generic-AI.

Earlier iterations remain for reference: `skill_v4.md` (clone + consistency gate),
`skill_v3.md` (raw-clone, pre-gate), `skill_v2.md` (reconstruct-from-tags storyboard),
`skill_v1.md` (Stage-3-only).

Both drive the same retrieval primitives — `list_decks` → `start_deck` (or
`get_deck` / `get_deck_outline` / `get_deck_assets`) → `search_slides` /
`find_slide_templates` / `find_similar_slides` — then build the `.pptx` in the
firm's real fonts/palette/structure.

Sanity-check the data with no server/tokens: `uv run python scripts/poc_demo.py`.

## 1. Run the server

```bash
uv sync
uv run python -m src.poc_server      # serves http://localhost:8000  (/mcp + /health)
```
Verify: `curl http://localhost:8000/health` → `{"status":"ok","decks":2,"slides":69}`
(the count reflects decks that pass the admission gate, not all tagged JSON).

`CORPUS_PATH` defaults to `../slide_tagging/reference_data/hand_labels`;
`SOURCE_PPTX_PATH` defaults to `corpus/source` (the bundled snapshot — which is why
nigeria + digital-auto pass even when `CORPUS_PATH` points at the sibling). To serve
raw slices straight from the producer's source decks during local dev, set
`SOURCE_PPTX_PATH=../slide_tagging/data/source`. Override in `.env` if your layout
differs. (No `OPENAI_API_KEY` or `DATABASE_URL` needed for the PoC.) Run
`uv run python -m scripts.check_corpus` to see which decks pass the gate and why the
rest don't.

## 2. Expose it over HTTPS

claude.ai can't reach `localhost`, so tunnel it to a public HTTPS URL. Easiest is a
Cloudflare quick tunnel (no account):

```bash
# install once: winget install Cloudflare.cloudflared    (or download cloudflared)
cloudflared tunnel --url http://localhost:8000
```
It prints a URL like `https://random-words.trycloudflare.com`. (ngrok works too:
`ngrok http 8000`.) Leave both the server and the tunnel running.

## 3. Add it to claude.ai

claude.ai → **Settings → Connectors → Add custom connector** →
- **URL:** `https://<your-tunnel>.trycloudflare.com/mcp`  ← note the `/mcp` path
- **Auth:** None

claude.ai will connect and list the fifteen tools.

## 4. Try it

In a new chat (with the connector enabled), ask things like:
- *"List the decks in the slide corpus."*
- *"Find Finding slides about market analysis for a C-suite audience."*
- *"Show me framework-graphic slides, and give me the full tags for one of them."*
- *"Find slides about foreign-exchange policy recommendations."*

Claude will call `list_decks` / `search_slides` / `get_slide` / `find_similar_slides`
and answer from the tags.

## Deploy an always-on public endpoint (to hand someone else)

The tunnel above is tied to your machine. To give a tester a durable URL, deploy the
`Dockerfile` (lean PoC server + bundled corpus, no Postgres/OpenAI). It binds to
`$PORT` and serves `/mcp` + `/health`.

**Refresh the corpus snapshot first** (it's a point-in-time copy, gitignored) — copy
the tagged JSON, the logo assets, **and the source `.pptx`**, then rebuild the
manifest. All three are required: the admission gate drops any deck whose `.pptx` is
missing, so a JSON-only snapshot deploys *empty*.

```bash
cp ../slide_tagging/reference_data/hand_labels/*.tagged.json corpus/
cp -r ../slide_tagging/reference_data/assets/* corpus/assets/      # logo PNGs (get_deck_assets)
cp ../slide_tagging/data/source/*.pptx corpus/source/             # raw decks (get_slide_pptx + gate)
uv run python -m scripts.build_manifest corpus/source             # slide-count manifest
uv run python -m scripts.check_corpus                             # verify which decks will serve
```

(The producer's `slide-tagger bundle <tagged.json> <deck.pptx> --out corpus` does the
copy + manifest for one deck in a single, alignment-checked step.)

### Option A — Google Cloud Run (one command, scale-to-zero, public HTTPS)

```bash
gcloud run deploy slide-corpus-poc --source . --region us-central1 --allow-unauthenticated
```
Cloud Run builds the Dockerfile, sets `$PORT`, and returns `https://slide-corpus-poc-…run.app`.
`.gcloudignore` ensures `corpus/` is uploaded; `--allow-unauthenticated` makes it
publicly reachable (platform level — the MCP layer is still no-auth as you asked).
Give your tester: **`https://…run.app/mcp`**.

### Option B — Fly.io (Docker context, no git/GitHub needed)

```bash
fly launch --no-deploy      # detects the Dockerfile; pick an app name
# in the generated fly.toml, set:  [http_service]  internal_port = 8000
fly deploy
```
Returns `https://<app>.fly.dev`; give your tester **`https://<app>.fly.dev/mcp`**.
(Fly serves HTTPS and doesn't set `$PORT`, so the server uses 8000 — match `internal_port`.)

### Option C — Railway (Docker, simple CLI or GitHub)

Railway auto-builds the `Dockerfile`, injects `$PORT`, and gives a public HTTPS
domain. `railway.json` (Dockerfile builder + `/health` check) and `.railwayignore`
(so the gitignored `corpus/` still uploads) are included.

```bash
npm i -g @railway/cli      # or: brew install railway
railway login
railway init               # create a new project
railway up                 # uploads the dir, builds the Dockerfile, deploys
railway domain             # generate https://<app>.up.railway.app
```
Give your tester **`https://<app>.up.railway.app/mcp`**. (GitHub flow also works —
connect the repo in the Railway dashboard — but then commit the **whole** snapshot
first: `git add -f corpus/*.json corpus/assets corpus/source` so the `.pptx` +
manifest reach the build, not just the JSON.)

### Option D — Render (free tier, via Blueprint)

A [`render.yaml`](../render.yaml) Blueprint is included (Docker, free plan,
`/health` check). Render is **git-based**, so the repo must be on GitHub and the
`corpus/` snapshot must be committed (it's gitignored):

```bash
# force-add the WHOLE gitignored snapshot — JSON, assets, AND the source .pptx + manifest.
# JSON-only here is the classic mistake: the admission gate then drops every deck and the
# deploy comes up with an empty corpus.
git add -f corpus/*.json corpus/assets corpus/source render.yaml
git commit -m "Add Render blueprint + corpus snapshot (JSON + assets + .pptx + manifest)"
git remote add origin https://github.com/<you>/<repo>.git   # create the repo on github.com first
git push -u origin master
```

Then in Render: **New → Blueprint** → pick the repo → it reads `render.yaml` and
creates the service. (Or **New → Web Service → Docker** pointed at the repo.)
You get `https://<service>.onrender.com`; give your tester
**`https://<service>.onrender.com/mcp`**, Auth = None.

**Free-tier caveat:** the instance sleeps after ~15 min idle (cold start ~30–60s),
so claude.ai's *first* connect after a nap may time out — just retry, or keep it
warm by pinging `https://<service>.onrender.com/health` every ~10 min (free, e.g.
cron-job.org) during your tester's window.

### Your tester adds it in claude.ai
Settings → Connectors → Add custom connector → URL = **`https://<deploy-host>/mcp`**,
Auth = **None**. They'll see the fifteen tools.

> Docker isn't installed here, so the image wasn't build-tested locally — but the
> exact runtime it runs (`CORPUS_PATH=corpus` + `$PORT`) is verified. If `docker`
> is on your machine: `docker build -t slide-corpus-poc . && docker run -p 8000:8000 slide-corpus-poc`.

## Caveats (it's a PoC)

- **Corpus is 4 tagged decks, but only the 2 that pass the admission gate are served**
  (nigeria + digital-auto have an index-aligned source `.pptx`; electric-vehicle and
  ereadiness don't yet — their tags were made from PDF exports whose page count differs
  from the `.pptx`). See [`HANDOFF-slide_tagging.md`](HANDOFF-slide_tagging.md) for the
  producer contract that fixes this. The deployed snapshot must include
  `corpus/source/*.pptx` + `manifest.json`, not just the JSON.
- **The trycloudflare URL changes every restart** — re-paste it into the connector.
- **No auth** — anyone with the URL can call the tools (read-only over slide *tags*
  from published reports, so low risk, but it's also open compute). A deployed
  endpoint is reachable 24/7, not just while a tunnel is up — **take the service
  down when testing is over**, or add a shared-secret header gate if you want
  minimal protection. The deployed corpus is a **snapshot**; re-copy into `corpus/`
  and redeploy when you update the labels.
- **Logos yes, embeddings no** — `get_deck_assets` serves recurring branding images
  (logo PNGs) as base64, but *retrieval* is still keyword/tag-only; vector/visual
  search over slide thumbnails is the production path's job. Decks whose logo is
  vector-only return no asset.
