# Security

## What toolbloat runs on your machine

`toolbloat run` and `toolbloat tokens` **start the MCP servers listed in your config** (or passed
with `--server`) as local processes, exactly as your agent (Claude Code, Claude Desktop, Cursor)
would start them. That means third-party code you already run with your agent runs again here,
with your user permissions. toolbloat:

- only sends `initialize` and `tools/list`; it **never calls a tool** (`tools/call` is not used),
- passes each server the `env` from your config but never prints it or writes it to a report,
- redacts arguments that look like secrets (`--api-key X`, `token=...`, `sk-...`) in output,
- does not execute any code produced by a model, so no sandbox is involved.

If you do not trust a server, do not include it: use `--config` with a file that lists only the
servers you want, or `--server` one by one. Note that with no flags toolbloat reads every config it
finds (`./.mcp.json`, `./.cursor/mcp.json`, `~/.cursor/mcp.json`, the Claude Desktop config).

## What leaves your machine

With a cloud provider (`mistral:`, `openrouter:`, `openai:`), the tool definitions of your servers
and the test requests are sent to that provider. With `ollama:` nothing leaves your machine. There is
no telemetry. API keys are read only from environment variables.

## Reporting a vulnerability

Please open a private security advisory on GitHub
(<https://github.com/diegoperezg7/toolbloat/security/advisories/new>) instead of a public issue.
You will get an answer within a week.
