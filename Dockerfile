# Lean PoC MCP server (src/poc_server.py) over the bundled corpus snapshot.
# No Postgres / OpenAI — pure in-memory retrieval. Binds to $PORT (cloud) or 8000.
#
#   docker build -t slide-corpus-poc .
#   docker run -p 8000:8000 slide-corpus-poc        # then http://localhost:8000/mcp
FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim

WORKDIR /app

# Install dependencies first (cached layer); the project itself is run from src/.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# App code + the corpus snapshot (a point-in-time copy of the tagged JSON).
COPY src ./src
COPY corpus ./corpus

ENV CORPUS_PATH=corpus \
    MCP_SERVER_HOST=0.0.0.0 \
    PYTHONUNBUFFERED=1

# Informational; the server actually binds to $PORT if the platform sets it.
EXPOSE 8000

CMD ["uv", "run", "--no-sync", "python", "-m", "src.poc_server"]
