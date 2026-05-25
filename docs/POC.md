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
Twelve tools over the corpus (currently 4 decks / 127 slides):

| Tool | What it does |
|---|---|
| `list_decks()` | Every deck + its deck-level tags (industry, type, content_area, audience, summary, slide_count). |
| `get_deck(deck)` | Design source-of-truth: deck tags + `design_system` (normalized fonts, colors, palette, grid, recurring elements) + `inferred_rules` + a one-line outline of every slide + `recurring_assets_available`. Use it to match a deck's look when generating. |
| `get_deck_assets(deck)` | The deck's recurring **branding images (logos)** as base64 PNGs (type/source/position/image_path/base64), so a generated deck can carry real branding. Returns `[]` when a deck's logo is vector-only (no extractable raster). |
| `search_slides(...)` | Filter slides by `slide_purpose` / `message_type` / `dominant_visual_element` + deck-level `client_industry` / `content_area` / `audience_level`, and/or a `text` keyword over title + main_message. |
| `get_slide(deck, index)` | Full tag set for one slide. |
| `find_similar_slides(text)` | Keyword-overlap ranking over main_message (cheap stand-in for embeddings). |
| `list_vocabulary()` | The valid filter values actually present in the corpus, per field — so `search_slides` strings hit instead of silently returning nothing. |
| `get_deck_outline(deck)` | The deck's narrative flow: each slide's `slide_position_role` + purpose + title, in order. |
| `find_slide_templates(...)` | Reusable layout skeletons for a slide kind, ranked by reusability, with their `zones` / `slot_types_present`. |
| `get_house_style()` | The firm's style aggregated across all decks (dominant fonts/sizes, common palette, all logos). |
| `start_deck(deck)` | One-call kit to model a new deck: design_system + inferred_rules + logos (base64) + outline + reference slides. |
| `corpus_stats()` | Coverage: deck/slide counts, decks-with-logos, and counts by industry / content_area / slide_purpose. |

### Generating decks with the corpus

To have claude.ai *build* decks grounded in this corpus (not just query it), load
the [`skills/skill_v1.md`](../skills/skill_v1.md) skill alongside the connector
(the skill is **client-owned** and being rewritten). It drives the tools above —
`list_decks` → `start_deck` (design system + outline + logos in one call, or the
`get_deck`/`get_deck_outline`/`get_deck_assets` trio) → `search_slides` /
`find_slide_templates` / `find_similar_slides` (reference slides + layouts) — then
builds the `.pptx` in the firm's real fonts/palette/structure.

Sanity-check the data with no server/tokens: `uv run python scripts/poc_demo.py`.

## 1. Run the server

```bash
uv sync
uv run python -m src.poc_server      # serves http://localhost:8000  (/mcp + /health)
```
Verify: `curl http://localhost:8000/health` → `{"status":"ok","decks":4,"slides":127}`.

`CORPUS_PATH` defaults to `../slide_tagging/reference_data/hand_labels`; override in
`.env` if your layout differs. (No `OPENAI_API_KEY` or `DATABASE_URL` needed for the PoC.)

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

claude.ai will connect and list the twelve tools.

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

**Refresh the corpus snapshot first** (it's a point-in-time copy, gitignored) —
copy both the tagged JSON **and** the extracted logo assets:

```bash
cp ../slide_tagging/reference_data/hand_labels/*.tagged.json corpus/
cp -r ../slide_tagging/reference_data/assets/* corpus/assets/   # logo PNGs served by get_deck_assets
```

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
connect the repo in the Railway dashboard — but then commit the snapshot first:
`git add -f corpus/`.)

### Option D — Render (free tier, via Blueprint)

A [`render.yaml`](../render.yaml) Blueprint is included (Docker, free plan,
`/health` check). Render is **git-based**, so the repo must be on GitHub and the
`corpus/` snapshot must be committed (it's gitignored):

```bash
git add -f corpus/*.json render.yaml          # force-add the gitignored snapshot
git commit -m "Add Render blueprint + corpus snapshot"
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
Auth = **None**. They'll see the twelve tools.

> Docker isn't installed here, so the image wasn't build-tested locally — but the
> exact runtime it runs (`CORPUS_PATH=corpus` + `$PORT`) is verified. If `docker`
> is on your machine: `docker build -t slide-corpus-poc . && docker run -p 8000:8000 slide-corpus-poc`.

## Caveats (it's a PoC)

- **Corpus is 4 decks** (nigeria is fully clean; digital-auto's labels were partly
  corrected — see `../slide_tagging/reference_data/hand_labels/digital-auto-label-review.md`).
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
