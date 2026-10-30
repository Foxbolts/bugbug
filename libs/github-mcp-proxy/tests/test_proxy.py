"""Tests for forwarding scoped tokens to the read-only GitHub MCP server."""

import json

import httpx
import pytest
from github_mcp_proxy import create_proxy

UPSTREAM = "http://127.0.0.1:8082/"
INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}


class _FakeServer:
    """Stands in for github-mcp-server, answering with an event stream."""

    def __init__(self):
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)

        # Streamed like a real response; one built from bytes is already read.
        async def events():
            yield b"event: message\n"
            yield b'data: {"jsonrpc":"2.0","id":1,"result":{}}\n\n'

        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream", "mcp-session-id": "s-1"},
            content=events(),
        )


def _agent(proxy, authorization="Bearer ghs_scoped") -> httpx.AsyncClient:
    """An MCP client talking to the mounted proxy, as the agent would."""
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=proxy),
        base_url="http://broker",
        headers={"Authorization": authorization} if authorization is not None else {},
    )


def _proxy(server: _FakeServer):
    return create_proxy(
        UPSTREAM, httpx.AsyncClient(transport=httpx.MockTransport(server))
    )


async def test_relays_a_request_with_the_agents_token():
    server = _FakeServer()

    async with _agent(_proxy(server)) as agent:
        resp = await agent.post(
            "/",
            json=INIT,
            headers={
                "Accept": "application/json, text/event-stream",
                "Mcp-Protocol-Version": "2026-07-28",
                "Mcp-Session-Id": "s-1",
                "Mcp-Param-Owner": "mozilla-mobile",
                "Authorization": "Bearer agent-supplied",
            },
        )

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "text/event-stream"
    assert resp.headers["mcp-session-id"] == "s-1"
    assert resp.text.startswith("event: message\ndata: ")
    [relayed] = server.requests
    assert relayed.method == "POST"
    assert str(relayed.url) == UPSTREAM
    assert json.loads(relayed.content) == INIT
    assert relayed.headers["authorization"] == "Bearer agent-supplied"
    assert relayed.headers["accept"] == "application/json, text/event-stream"
    assert relayed.headers["mcp-protocol-version"] == "2026-07-28"
    assert relayed.headers["mcp-session-id"] == "s-1"
    assert relayed.headers["mcp-param-owner"] == "mozilla-mobile"


async def test_callers_cannot_change_the_servers_configuration():
    server = _FakeServer()

    async with _agent(_proxy(server)) as agent:
        await agent.post(
            "/",
            json=INIT,
            headers={
                "X-MCP-Readonly": "false",
                "X-MCP-Toolsets": "all",
                "X-MCP-Tools": "merge_pull_request",
                "X-MCP-Lockdown": "false",
            },
        )

    [relayed] = server.requests
    assert not [h for h in relayed.headers if h.startswith("x-mcp-")]


@pytest.mark.parametrize("authorization", [None, "", "Basic abc", "Bearer "])
async def test_a_missing_or_invalid_bearer_token_never_reaches_the_server(
    authorization,
):
    server = _FakeServer()
    async with _agent(_proxy(server), authorization) as agent:
        resp = await agent.post("/", json=INIT)
    assert resp.status_code == 401
    assert server.requests == []


async def test_an_expired_token_is_reported_by_the_upstream():
    async def body():
        yield b'{"error":"Token expired"}'

    def unauthorized(request):
        return httpx.Response(
            401, headers={"content-type": "application/json"}, content=body()
        )

    async with _agent(_proxy(unauthorized), "Bearer expired") as agent:
        resp = await agent.post("/", json=INIT)
    assert resp.status_code == 401
    assert resp.json() == {"error": "Token expired"}


async def test_an_unreachable_server_fails_with_a_bad_gateway():
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    proxy = create_proxy(
        UPSTREAM, httpx.AsyncClient(transport=httpx.MockTransport(refuse))
    )
    async with _agent(proxy) as agent:
        resp = await agent.post("/", json=INIT)

    assert resp.status_code == 502
