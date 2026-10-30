# GitHub read tools

`github-mcp-proxy` runs the official `github-mcp-server` as a standalone read-only
service. It is independent of the Bugzilla bug-fix agent. The PR-review workflow
will connect to this service with a repository-scoped installation token minted
by hackbot-api's existing GitHub client. This branch provides the service; it does
not yet create a PR-review agent or wire token delivery into run startup.

The service accepts a bearer token on each `/mcp/` request and forwards it to the
official server on loopback. It strips `X-MCP-*` server overrides and rejects
missing bearer tokens. The official server runs with `--read-only` and the
`pull_requests,repos,issues` toolsets, with an empty environment. Neither the
service nor its child needs the App private key.

## Run the service

Install the official `github-mcp-server` v2.0.3 binary, then from the workspace root:

```bash
uv sync --locked --package github-mcp-proxy
.venv/bin/python -m github_mcp_proxy \
  --binary /path/to/github-mcp-server \
  --host 127.0.0.1 --port 8765
```

The MCP URL is `http://127.0.0.1:8765/mcp/`. Each client supplies its own token in
`Authorization: Bearer <installation-token>`. Tokens should be restricted to one
repository and `contents: read`, `pull_requests: read`, and `issues: read`. The
service cannot downscope a supplied token itself. Expired-token errors are returned
to the caller; renewal and the production deployment belong to the review workflow
integration, which remains follow-up work.

## Manual check

This helper uses hackbot-api's existing client to mint a read-only token, starts
the official server and proxy locally, and reads a test PR. No running API,
Bugzilla agent, database, or GCP deployment is needed.

```bash
uv sync --locked --package hackbot-api --package github-mcp-proxy --extra dev
.venv/bin/python tools/check-github-mcp.py \
  --app-id 123456 \
  --key-file /path/to/your-test-app.pem \
  --repo owner/test-repository \
  --pr 1 \
  --binary /path/to/github-mcp-server
```

Use a personal test App installed on that repository with Contents, Pull requests,
and Issues read access. The helper prints three `PASS` lines for the PR read,
read-only tool list, and unauthenticated-request rejection. It posts no comments.
This validates the MCP infrastructure, not the future PR-review workflow.
