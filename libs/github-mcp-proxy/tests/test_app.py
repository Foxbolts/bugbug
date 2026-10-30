"""Standalone service wiring without a GitHub App or agent configuration."""

from contextlib import asynccontextmanager

from github_mcp_proxy import __main__ as service
from starlette.testclient import TestClient


def test_service_starts_server_and_rejects_missing_tokens(monkeypatch):
    running = []

    @asynccontextmanager
    async def run(self):
        running.append(self.command)
        yield
        running.pop()

    monkeypatch.setattr(service.GitHubMCPServer, "run", run)
    with TestClient(service.build_app()) as client:
        assert "--read-only" in running[0]
        assert client.post("/mcp/", json={}).status_code == 401
    assert running == []
