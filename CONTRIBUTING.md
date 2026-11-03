# Contributing

Thanks for helping. Bug reports with a minimal command line are the most useful thing you can send.

## Setup

```bash
git clone https://github.com/diegoperezg7/toolbloat
cd toolbloat
uv sync
uv run pytest
uv run ruff check .
```

Tests never call a paid model or the network: model answers are faked and MCP discovery runs
against a tiny local server in `tests/fixtures/tiny_server.py`.

## Ground rules

- Keep it small. Every new option needs a reason that a user ran into.
- Tools are listed, never executed. Do not add code paths that call `tools/call`.
- Never print or store the `env` block of an MCP config.
- Pin new dependencies exactly (`==`) and commit `uv.lock`.
- Numbers in docs must come from a real run (`toolbloat run`) and say which models and date.

## Releasing

1. Bump `version` in `pyproject.toml` and `src/toolbloat/__init__.py`, update `CHANGELOG.md`.
2. Tag `vX.Y.Z` and create a GitHub release; attach `uv build` artifacts.
3. Run the `publish` workflow by hand to upload to PyPI.
