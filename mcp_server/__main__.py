"""Run the catalog MCP server: `python -m mcp_server --transport stdio|http`.

Phase one (MCP_PUBLIC=false, the default): HTTP binds to a loopback address only. The agent
calls the catalog in-process and does not need this server; it is for local use and tests.
With MCP_PUBLIC=true, any host is allowed and every request must carry MCP_API_KEY as a
bearer token.
"""

from __future__ import annotations

import argparse
import ipaddress
import sys
from urllib.parse import urlparse

import uvicorn

from config import get_settings
from mcp_server.server import create_server, http_app


def is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def main() -> None:
    settings = get_settings()
    url = urlparse(settings.mcp_server_url)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    parser.add_argument("--host", default=url.hostname or "127.0.0.1")
    parser.add_argument("--port", type=int, default=url.port or 8001)
    parser.add_argument("--path", default=url.path or "/mcp")
    args = parser.parse_args()

    if args.transport == "http" and not settings.mcp_public and not is_loopback(args.host):
        sys.exit(
            f"refusing to serve MCP over HTTP on {args.host!r}: MCP_PUBLIC is false (no authentication). "
            "Use 127.0.0.1 or localhost, or set MCP_PUBLIC=true with MCP_API_KEY."
        )

    server = create_server(settings.database_dsn())
    if args.transport == "stdio":
        server.run("stdio")
        return
    key = settings.mcp_api_key.get_secret_value() if settings.mcp_api_key else None
    app = http_app(server, public=settings.mcp_public, api_key=key, host=args.host, path=args.path)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
