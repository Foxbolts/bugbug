"""Tests for running github-mcp-server as a child process.

A small Python script stands in for the Go binary: it takes the same flags and
answers 401 like the real server does without a token.
"""

import json
import socket
import sys
from pathlib import Path

import httpx
import pytest
from github_mcp_proxy import GitHubMCPServer

_FAKE_SERVER = """\
#!{python}
import json, os, sys
from http.server import BaseHTTPRequestHandler, HTTPServer

with open({record!r}, "w") as f:
    json.dump({{"argv": sys.argv[1:], "env": dict(os.environ)}}, f)

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(401)
        self.end_headers()

port = int(sys.argv[sys.argv.index("--port") + 1])
HTTPServer(("127.0.0.1", port), Handler).serve_forever()
"""


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _executable(path: Path, source: str) -> str:
    path.write_text(source)
    path.chmod(0o755)
    return str(path)


def test_serves_read_only_toolsets_on_loopback():
    command = GitHubMCPServer(port=9000).command

    assert command[:2] == ["github-mcp-server", "http"]
    assert "--read-only" in command
    assert command[command.index("--listen-host") + 1] == "127.0.0.1"
    assert command[command.index("--port") + 1] == "9000"
    assert command[command.index("--toolsets") + 1] == "pull_requests,repos,issues"


async def test_run_waits_for_the_server_and_stops_it(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_PRIVATE_KEY", "not for the child")
    monkeypatch.setenv("GITHUB_MCP_TOKEN", "not for the child either")
    record = tmp_path / "record.json"
    binary = _executable(
        tmp_path / "github-mcp-server",
        _FAKE_SERVER.format(python=sys.executable, record=str(record)),
    )
    server = GitHubMCPServer(binary, port=_free_port())

    async with httpx.AsyncClient() as client:
        async with server.run():
            resp = await client.get(server.url)
            assert resp.status_code == 401

        with pytest.raises(httpx.ConnectError):
            await client.get(server.url)
    started = json.loads(record.read_text())
    assert started["argv"] == server.command[1:]
    assert "GITHUB_PRIVATE_KEY" not in started["env"]
    assert "GITHUB_MCP_TOKEN" not in started["env"]


async def test_finds_the_binary_on_the_callers_path(tmp_path, monkeypatch):
    # The child gets an empty environment, so the lookup can't be left to it.
    _executable(
        tmp_path / "github-mcp-server",
        _FAKE_SERVER.format(python=sys.executable, record=str(tmp_path / "r.json")),
    )
    monkeypatch.setenv("PATH", str(tmp_path))

    async with GitHubMCPServer(port=_free_port()).run():
        pass


async def test_a_missing_binary_fails_loudly(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path))

    with pytest.raises(FileNotFoundError, match="github-mcp-server"):
        async with GitHubMCPServer().run():
            pass


async def test_a_server_that_exits_at_startup_fails_loudly(tmp_path):
    binary = _executable(
        tmp_path / "github-mcp-server", f"#!{sys.executable}\nraise SystemExit(3)\n"
    )

    with pytest.raises(RuntimeError, match="exited with status 3"):
        async with GitHubMCPServer(binary, port=_free_port()).run():
            pass


async def test_a_server_that_never_listens_times_out(tmp_path):
    binary = _executable(
        tmp_path / "github-mcp-server",
        f"#!{sys.executable}\nimport time\ntime.sleep(60)\n",
    )

    with pytest.raises(TimeoutError):
        async with GitHubMCPServer(
            binary, port=_free_port(), startup_timeout=0.5
        ).run():
            pass
