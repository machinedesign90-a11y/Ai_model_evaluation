"""Connect to a running catalog MCP server, list its tools and call search_models once.

python scripts/mcp_smoke.py [URL]     (default: MCP_SERVER_URL or http://localhost:8001/mcp)
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

from mcp import Client


async def main(url: str) -> None:
    async with Client(url) as client:
        tools = (await client.list_tools()).tools
        print("tools:", ", ".join(t.name for t in tools))
        result = await client.call_tool("search_models", {"limit": 3})
        print(json.dumps(result.structured_content, indent=2, default=str)[:2000])


if __name__ == "__main__":
    asyncio.run(
        main(sys.argv[1] if len(sys.argv) > 1 else os.environ.get("MCP_SERVER_URL", "http://localhost:8001/mcp"))
    )
