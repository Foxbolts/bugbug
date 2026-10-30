"""Relay MCP requests using the caller's scoped GitHub installation token."""

import logging

import httpx
from starlette.requests import Request
from starlette.responses import PlainTextResponse, StreamingResponse
from starlette.types import ASGIApp, Receive, Scope, Send

log = logging.getLogger(__name__)

# Forward MCP protocol headers; X-MCP-* server overrides are dropped.
_FORWARDED = frozenset({"accept", "content-type", "last-event-id"})
# Hop-by-hop headers describe one connection, not the message relayed over it.
_HOP_BY_HOP = frozenset({"connection", "keep-alive", "transfer-encoding"})


def _forwarded(header: str) -> bool:
    return header in _FORWARDED or header.startswith("mcp-")


def create_proxy(upstream: str, http: httpx.AsyncClient) -> ASGIApp:
    """Build the proxy app, relaying to the MCP server at ``upstream``.

    ``http`` sends the relayed requests; give it no read timeout, as an event
    stream can stay quiet for a long time.
    """

    async def proxy(scope: Scope, receive: Receive, send: Send) -> None:
        request = Request(scope, receive)
        headers = {k: v for k, v in request.headers.items() if _forwarded(k)}
        authorization = request.headers.get("authorization", "")
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            await PlainTextResponse(
                "A scoped GitHub bearer token is required", status_code=401
            )(scope, receive, send)
            return
        headers["authorization"] = authorization
        try:
            response = await http.send(
                http.build_request(
                    request.method,
                    upstream,
                    params=request.query_params,
                    headers=headers,
                    content=await request.body(),
                ),
                stream=True,
            )
        except httpx.HTTPError:
            log.exception("GitHub MCP request failed before reaching the server")
            await PlainTextResponse("GitHub MCP proxy request failed", status_code=502)(
                scope, receive, send
            )
            return

        try:
            await StreamingResponse(
                response.aiter_raw(),
                status_code=response.status_code,
                headers={
                    k: v for k, v in response.headers.items() if k not in _HOP_BY_HOP
                },
            )(scope, receive, send)
        finally:
            await response.aclose()

    return proxy
