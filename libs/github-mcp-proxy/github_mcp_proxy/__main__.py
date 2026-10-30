"""Run the official read-only GitHub MCP server behind a token-forwarding proxy."""

import argparse
from contextlib import asynccontextmanager

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.routing import Mount

from github_mcp_proxy import GitHubMCPServer, create_proxy


def build_app(binary: str = "github-mcp-server", server_port: int = 8082) -> Starlette:
    server = GitHubMCPServer(binary, port=server_port)
    http = httpx.AsyncClient(timeout=httpx.Timeout(30, read=None))

    @asynccontextmanager
    async def lifespan(app):
        async with http, server.run():
            yield

    return Starlette(
        routes=[Mount("/mcp", app=create_proxy(server.url, http))], lifespan=lifespan
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--server-port", type=int, default=8082)
    parser.add_argument("--binary", default="github-mcp-server")
    args = parser.parse_args()
    uvicorn.run(
        build_app(args.binary, args.server_port),
        host=args.host,
        port=args.port,
        access_log=False,
    )


if __name__ == "__main__":
    main()
