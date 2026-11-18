"""Run the catalog MCP server: `python -m mcp_server --transport stdio|http`.

Phase one: the HTTP transport has no authentication, so it only binds to a loopback
address. The agent calls the catalog in-process and does not need this server; it is
for local use and testing. Exposing it to outside agents needs an API key in front first.
"""

from __future__ import annotations

import argparse
import ipaddress
import sys
from urllib.parse import urlparse

from config import get_settings
from mcp_server.server import create_server


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

    if args.transport == "http" and not is_loopback(args.host):
        sys.exit(
            f"refusing to serve MCP over HTTP on {args.host!r}: it has no authentication in phase one. "
            "Use 127.0.0.1 or localhost."
        )

    server = create_server(settings.database_dsn())
    if args.transport == "stdio":
        server.run("stdio")
    else:
        server.run("streamable-http", host=args.host, port=args.port, streamable_http_path=args.path)


if __name__ == "__main__":
    main()
