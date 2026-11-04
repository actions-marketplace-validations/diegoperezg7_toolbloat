# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [0.2.0] - 2026-11-04

### Added
- GitHub Action (`uses: diegoperezg7/toolbloat@v0.2.0`): counts the tokens of the MCP tool
  definitions in a config, writes the table to the job summary and fails over `max-tokens`.
  No model calls, no keys.
- `toolbloat tokens --max-tokens N`: exit code 2 when the total is over the budget, 1 when a server
  could not be measured.
- `toolbloat tokens --markdown FILE`: append a Markdown summary (for `$GITHUB_STEP_SUMMARY`).

## [0.1.1] - 2026-11-03

### Changed
- The privacy prompt before sending anything to a cloud provider now lists every server that will be
  sent, the config file it came from (or `--server`), its command and the provider, and refuses
  without `--yes` when there is no interactive terminal. `toolbloat intents` asks too.
- The HTML report formats numbers, percentages and amounts for the active language
  (English `6,064` / `$2.73`, Spanish `6.064` / `2,73 US$`).

## [0.1.0] - 2026-11-03

### Added
- `toolbloat tokens`: start MCP servers over stdio (official MCP Python SDK), list their tools and
  count definition tokens per tool, per server and in total (tiktoken o200k, approximate).
- `toolbloat intents`: write one or more realistic requests per tool with a model, cached in an
  editable YAML file.
- `toolbloat run`: ask each model which tool it would call, with only the tool's own server and with
  the whole stack. Reports selection accuracy, argument validity against the JSON Schema, a
  cross-server confusion matrix, tools no model chose, tokens saved by removing them and a monthly
  token and cost projection. Tools are never executed.
- `toolbloat report`: rebuild the HTML report from `results.json`.
- `toolbloat demo`: a recorded real run on six MCP reference servers, no keys needed.
- Before sending tool definitions to a cloud provider, lists the servers and the provider and asks
  for confirmation (`--yes` to skip, required without a terminal).
- Reads `.mcp.json`, Cursor and Claude Desktop configs (command and args only, `env` is never shown).
- Providers: Ollama, Mistral, OpenRouter, OpenAI through one OpenAI-compatible client, with
  `--max-cost`, backoff on rate limits and a resumable call cache.
- Static HTML report in English and Spanish, light and dark, mobile friendly.

[0.2.0]: https://github.com/diegoperezg7/toolbloat/releases/tag/v0.2.0
[0.1.1]: https://github.com/diegoperezg7/toolbloat/releases/tag/v0.1.1
[0.1.0]: https://github.com/diegoperezg7/toolbloat/releases/tag/v0.1.0
