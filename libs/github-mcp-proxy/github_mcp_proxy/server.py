"""The official github-mcp-server, run read-only as a child process.

Its ``http`` mode holds no credential: every request has to bring its own
``Authorization`` header, which is what :mod:`github_mcp_proxy.proxy` adds. So
the server can be reached by anything on loopback without exposing a token.
"""

import asyncio
import logging
import shutil
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

import httpx

log = logging.getLogger(__name__)

# A pull request, the repository's files and history, and the issues it links
# to. Write tools in these are dropped by --read-only.
READ_ONLY_TOOLSETS = ("pull_requests", "repos", "issues")


class GitHubMCPServer:
    def __init__(
        self,
        binary: str = "github-mcp-server",
        *,
        port: int = 8082,
        toolsets: Sequence[str] = READ_ONLY_TOOLSETS,
        startup_timeout: float = 10.0,
    ):
        self.binary = binary
        self.port = port
        self.toolsets = tuple(toolsets)
        self.startup_timeout = startup_timeout

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    @property
    def command(self) -> list[str]:
        return [
            self.binary,
            "http",
            "--listen-host",
            "127.0.0.1",
            "--port",
            str(self.port),
            "--read-only",
            "--toolsets",
            ",".join(self.toolsets),
        ]

    @asynccontextmanager
    async def run(self) -> AsyncIterator[GitHubMCPServer]:
        """Start the server, wait until it accepts connections, stop it on exit."""
        # Looked up here, as the child's empty environment has no PATH to search.
        binary = shutil.which(self.binary)
        if binary is None:
            raise FileNotFoundError(f"{self.binary} not found on PATH")
        # An empty environment: the server reads settings such as GITHUB_TOOLSETS
        # from it ahead of its flags, and must not see the caller's credentials.
        process = await asyncio.create_subprocess_exec(
            binary, *self.command[1:], env={}
        )
        try:
            await self._wait_until_listening(process)
            log.info("github-mcp-server listening on %s", self.url)
            yield self
        finally:
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=5)
                except TimeoutError:
                    process.kill()
                    await process.wait()

    async def _wait_until_listening(self, process: asyncio.subprocess.Process) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.startup_timeout
        async with httpx.AsyncClient() as client:
            while True:
                if process.returncode is not None:
                    raise RuntimeError(
                        f"github-mcp-server exited with status {process.returncode} "
                        "before it was ready"
                    )
                try:
                    # Any answer will do; without a token it is a 401.
                    await client.get(self.url)
                    return
                except httpx.TransportError:
                    pass
                if loop.time() > deadline:
                    raise TimeoutError(
                        f"github-mcp-server did not listen on {self.url} within "
                        f"{self.startup_timeout}s"
                    )
                await asyncio.sleep(0.1)
