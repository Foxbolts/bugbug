"""Read a test PR through the official GitHub MCP server and the standalone read-only proxy."""

import argparse
import asyncio
import os
import socket
from contextlib import asynccontextmanager
from pathlib import Path


def _free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


@asynccontextmanager
async def _proxy_server(app):
    import uvicorn

    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(
            app, host="127.0.0.1", port=port, log_level="warning", access_log=False
        )
    )
    task = asyncio.create_task(server.serve())
    try:
        async with asyncio.timeout(10):
            while not server.started:
                if task.done():
                    await task
                    raise RuntimeError("Proxy failed to start")
                await asyncio.sleep(0.05)
        yield f"http://127.0.0.1:{port}/mcp/"
    finally:
        server.should_exit = True
        await task


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-id", required=True)
    parser.add_argument("--key-file", type=Path, required=True)
    parser.add_argument("--repo", required=True, help="owner/repository")
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--binary", default="github-mcp-server")
    args = parser.parse_args()

    # Only GitHub is contacted; these satisfy unrelated API settings.
    os.environ.update(
        {
            "PHABRICATOR_API_KEY": "api-" + "a" * 28,
            "WEBHOOK_SECRET": "manual-test",
            "WEBHOOK_AUTHORIZED_GROUP": "manual-test",
            "BUGZILLA_WEBHOOK_SECRET": "manual-test",
            "BUGZILLA_WEBHOOK_AUTHORIZED_GROUP": "manual-test",
            "BUGZILLA_API_KEY": "manual-test",
            "SLACK_SIGNING_SECRET": "manual-test",
            "SLACK_BOT_TOKEN": "manual-test",
            "SENDGRID_API_KEY": "manual-test",
            "NOTIFICATION_SENDER": "manual-test@example.invalid",
            "AGENTS_SENTRY_DSN": "https://key@sentry.example/1",
            "GITHUB_APP_ID": args.app_id,
            "GITHUB_PRIVATE_KEY": args.key_file.expanduser().read_text(),
        }
    )
    os.environ.pop("GITHUB_INSTALLATION_ID", None)

    import httpx
    from app.config import GitHubAppSettings
    from app.github import GitHubApp
    from github_mcp_proxy.__main__ import build_app
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async def run():
        app = GitHubApp(
            GitHubAppSettings(
                app_id=args.app_id, private_key=args.key_file.expanduser().read_text()
            )
        )
        owner, repo = args.repo.split("/")
        github = app.as_app()
        installation = await github.rest.apps.async_get_repo_installation(owner, repo)
        minted = await app.installation_token(
            installation.json()["id"],
            permissions={"contents": "read", "pull_requests": "read", "issues": "read"},
            repositories=[repo],
        )
        token = minted.token
        async with _proxy_server(build_app(args.binary, _free_port())) as url:
            async with httpx.AsyncClient() as unauthenticated:
                response = await unauthenticated.post(url, json={})
                if response.status_code != 401:
                    raise RuntimeError("Proxy accepted a request without a token")
            async with (
                streamablehttp_client(
                    url, headers={"Authorization": f"Bearer {token}"}
                ) as (read, write, _),
                ClientSession(read, write) as session,
            ):
                await session.initialize()
                names = {tool.name for tool in (await session.list_tools()).tools}
                write_tools = {
                    "add_issue_comment",
                    "create_pull_request_review",
                    "merge_pull_request",
                    "create_or_update_file",
                }
                if names & write_tools:
                    raise RuntimeError(
                        f"Write tools exposed: {sorted(names & write_tools)}"
                    )
                if "pull_request_read" not in names:
                    raise RuntimeError("pull_request_read is missing")
                owner, repo = args.repo.split("/")
                result = await session.call_tool(
                    "pull_request_read",
                    {
                        "method": "get",
                        "owner": owner,
                        "repo": repo,
                        "pullNumber": args.pr,
                    },
                )
                if result.isError:
                    raise RuntimeError(f"PR read failed: {result.content}")
                print(f"PASS: read {args.repo}#{args.pr} through GitHub MCP")
                print(f"PASS: {len(names)} read tools available; write tools absent")
                print("PASS: proxy rejects requests without a token")

    asyncio.run(run())


if __name__ == "__main__":
    main()
