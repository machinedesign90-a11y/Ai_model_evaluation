"""Run the catalog MCP server: `python -m mcp_server --transport stdio|http`."""

from __future__ import annotations

import argparse
from urllib.parse import urlparse

from config import get_settings
from mcp_server.server import create_server


def main() -> None:
    settings = get_settings()
    url = urlparse(settings.mcp_server_url)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    parser.add_argument("--host", default=url.hostname or "127.0.0.1")
    parser.add_argument("--port", type=int, default=url.port or 8001)
    parser.add_argument("--path", default=url.path or "/mcp")
    args = parser.parse_args()

    server = create_server(settings.database_dsn())
    if args.transport == "stdio":
        server.run("stdio")
    else:
        server.run("streamable-http", host=args.host, port=args.port, streamable_http_path=args.path)


if __name__ == "__main__":
    main()
