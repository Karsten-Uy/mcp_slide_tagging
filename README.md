# mcp-slide-corpus

A read-only MCP server over the tagged slide-deck corpus produced by the
`slide_tagging` service. It serves the existing corpus (it does not tag, ingest
new decks, or render) through six retrieval tools that AI agents call when
generating new decks. Backed by Postgres + pgvector; reachable over Streamable
HTTP and deployable to public HTTPS for use as a claude.ai custom connector.

This project is a standalone sibling of `slide_tagging`. It reads the corpus
read-only via `CORPUS_PATH` and never modifies the tagging service or its output.

## Local dev quickstart

```bash
uv sync                                  # install dependencies
cp .env.example .env                     # then set OPENAI_API_KEY
docker compose up -d                     # Postgres + pgvector (migrations auto-apply on first init)
uv run python -m src.server              # serve MCP at /mcp and /health on :8000
```

Health check: `curl http://localhost:8000/health` (200 when Postgres is reachable
and `OPENAI_API_KEY` is set, 503 otherwise).

## Proof-of-concept: query from claude.ai with no infra

To use the corpus from the **claude.ai web GUI** without Postgres/OpenAI, run the
lean PoC server instead and tunnel it to HTTPS:

```bash
uv run python -m src.poc_server          # in-memory tools over CORPUS_PATH, :8000 (/mcp + /health)
uv run python scripts/poc_demo.py        # optional: see the tools' output, no server/tokens
```

Then expose it (e.g. `cloudflared tunnel --url http://localhost:8000`) and add the
resulting `https://…/mcp` URL as a claude.ai custom connector. Full steps in
[`docs/POC.md`](docs/POC.md). This path is separate from and leaves untouched the
production pgvector server below.

## Configuration

See `.env.example` for the full list. Key variables: `DATABASE_URL`,
`OPENAI_API_KEY`, `CORPUS_PATH`, `THUMBNAIL_BASE_PATH`, `MCP_SERVER_HOST`,
`MCP_SERVER_PORT`, `LOG_LEVEL`, `EMBEDDING_MODEL`, `CLIP_MODEL`.

## Status

Built in phases. Phase 1 (skeleton: schema, config, logging, `/health`) is in
place. Ingestion, tools, tests, and deployment follow.
