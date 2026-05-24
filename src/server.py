"""FastMCP server entry point.

Exposes the corpus retrieval tools over Streamable HTTP (mounted at /mcp) plus a
plain /health endpoint for deploy probes. Run with: python -m src.server
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from src.config import settings
from src.logging import get_logger, setup_logging
from src.retrieval import db

logger = get_logger(__name__)

mcp = FastMCP(
    "slide-corpus",
    host=settings.mcp_server_host,
    port=settings.mcp_server_port,
)


@mcp.custom_route("/health", methods=["GET"])  # type: ignore[untyped-decorator]
async def health(request: Request) -> JSONResponse:
    """Return 200 when Postgres is reachable and an OpenAI key is configured."""
    postgres_ok = await db.ping()
    openai_key_ok = bool(settings.openai_api_key)
    checks = {"postgres": postgres_ok, "openai_api_key": openai_key_ok}
    healthy = postgres_ok and openai_key_ok
    return JSONResponse(
        {"status": "ok" if healthy else "unhealthy", "checks": checks},
        status_code=200 if healthy else 503,
    )


def main() -> None:
    setup_logging(settings.log_level)
    logger.info(
        "server_starting",
        host=settings.mcp_server_host,
        port=settings.mcp_server_port,
        transport="streamable-http",
    )
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
