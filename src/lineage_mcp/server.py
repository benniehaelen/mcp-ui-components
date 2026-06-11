"""Real MCP server over stdio.

Run it directly (``python -m lineage_mcp.server`` or the ``lineage-mcp`` script)
and point a host such as Claude Desktop or VS Code at it. It exposes exactly the
tools and resource defined in :mod:`lineage_mcp.tools`, so a production host
renders the same widget and proxies the same calls the local demo does.

Requires the official MCP Python SDK (``pip install mcp``). The browser demo in
``demo/`` does **not** need this dependency.
"""

from __future__ import annotations

import asyncio
import json

from . import WIDGET_URI
from . import tools as toolset


def _build_server():
    # Imported lazily so the demo host (stdlib-only) never needs the SDK.
    from mcp.server import Server
    import mcp.types as types

    server = Server("lineage-mcp")

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        # Our TOOLS dicts already match the Tool schema (name, description,
        # inputSchema, _meta), so validate them straight through. This is what
        # carries `_meta.ui.resourceUri` to the host.
        return [types.Tool.model_validate(spec) for spec in toolset.TOOLS]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict | None):
        result = toolset.call_tool(name, arguments)
        structured = result.get("structuredContent", result)
        content = [types.TextContent(type="text", text=json.dumps(structured))]
        # Modern SDK (>=1.2) accepts (content, structuredContent); the text
        # block keeps it working on hosts that ignore structured output.
        return content, structured

    @server.list_resources()
    async def list_resources() -> list[types.Resource]:
        return [types.Resource.model_validate(r) for r in toolset.RESOURCES]

    @server.read_resource()
    async def read_resource(uri):
        from mcp.server.lowlevel.helper_types import ReadResourceContents

        html = toolset.read_resource(str(uri))
        # The MCP Apps MIME type is mandatory: hosts (VS Code, Claude Desktop)
        # reject a UI resource whose content mimeType is anything else.
        return [ReadResourceContents(
            content=html, mime_type=toolset.RESOURCE_MIME_TYPE,
        )]

    return server


async def _run_stdio() -> None:
    from mcp.server.stdio import stdio_server

    server = _build_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def _build_http_app(path: str = "/mcp"):
    """A Starlette app exposing the server over streamable HTTP at ``path``.

    This is what a config like
        { "type": "http", "url": "http://localhost:3001/mcp" }
    connects to. The tool definitions — including `_meta.ui.resourceUri` — are
    identical to the stdio server; only the transport differs.
    """
    import contextlib

    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
    from starlette.applications import Starlette
    from starlette.middleware import Middleware
    from starlette.middleware.cors import CORSMiddleware
    from starlette.routing import Mount

    server = _build_server()
    manager = StreamableHTTPSessionManager(app=server, stateless=True)

    async def handle(scope, receive, send):
        await manager.handle_request(scope, receive, send)

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        async with manager.run():
            yield

    # Browser-based hosts connect cross-origin; allow it and expose the
    # session header the streamable-HTTP transport uses.
    cors = Middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["Mcp-Session-Id"],
    )
    return Starlette(routes=[Mount(path, app=handle)],
                     middleware=[cors], lifespan=lifespan)


def _run_http(host: str, port: int) -> None:
    import uvicorn

    app = _build_http_app("/mcp")
    print(f"  lineage MCP server (streamable HTTP) -> http://{host}:{port}/mcp")
    uvicorn.run(app, host=host, port=port, log_level="info")


def main() -> None:
    import argparse

    try:
        import mcp  # noqa: F401
    except ImportError:  # pragma: no cover - guidance path
        raise SystemExit(
            "The MCP SDK is required to run the server.\n"
            "  pip install mcp\n"
            "(The browser demo `python demo/host.py` does not need it.)"
        )

    parser = argparse.ArgumentParser(description="Lineage MCP server.")
    parser.add_argument("--http", action="store_true",
                        help="Serve over streamable HTTP instead of stdio.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=3001)
    args = parser.parse_args()

    if args.http:
        _run_http(args.host, args.port)
    else:
        asyncio.run(_run_stdio())


if __name__ == "__main__":
    main()
