import json

import pytest

from toolbloat.config import (
    ServerSpec,
    dedupe,
    guess_name,
    load_config_file,
    parse_server_flag,
    redact_args,
    skipped_remote,
)
from toolbloat.errors import ToolbloatError


def test_load_mcp_json_reads_command_and_args_only(tmp_path):
    p = tmp_path / ".mcp.json"
    p.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "fs": {
                        "command": "npx",
                        "args": ["-y", "@modelcontextprotocol/server-filesystem", "."],
                        "env": {"SECRET_TOKEN": "super-secret-value"},
                    },
                    "remote": {"type": "http", "url": "https://example.com/mcp"},
                    "off": {"command": "x", "disabled": True},
                }
            }
        )
    )
    specs = load_config_file(p)
    assert [s.name for s in specs] == ["fs"]
    assert specs[0].env == {"SECRET_TOKEN": "super-secret-value"}
    assert "super-secret-value" not in specs[0].display()
    assert "super-secret-value" not in repr(specs[0])
    assert skipped_remote(p) == ["remote"]


def test_claude_desktop_shape(tmp_path):
    p = tmp_path / "claude_desktop_config.json"
    p.write_text(
        json.dumps({"globalShortcut": "", "mcpServers": {"time": {"command": "uvx", "args": ["mcp-server-time"]}}})
    )
    assert load_config_file(p)[0].command == "uvx"


def test_bad_json_is_a_clear_error(tmp_path):
    p = tmp_path / "x.json"
    p.write_text("{nope")
    with pytest.raises(ToolbloatError, match="not valid JSON"):
        load_config_file(p)


def test_redact_args():
    assert redact_args(["--api-key", "abc", "--token=xyz", "sk-123456", "path"]) == [
        "--api-key",
        "***",
        "--token=***",
        "***",
        "path",
    ]


def test_parse_server_flag():
    s = parse_server_flag("uvx mcp-server-time==2026.10.28 --local-timezone UTC", 0)
    assert (s.name, s.command, s.args[0]) == ("time", "uvx", "mcp-server-time==2026.10.28")
    s = parse_server_flag("clock=uvx mcp-server-time", 0)
    assert s.name == "clock"
    with pytest.raises(ToolbloatError):
        parse_server_flag("", 0)


def test_guess_name():
    assert guess_name(["npx", "-y", "@modelcontextprotocol/server-memory@2.0.1"]) == "memory"
    assert guess_name(["python", "/srv/weather_mcp.py"]) == "weather"
    assert guess_name(["uvx", "mcp-server-fetch==2026.10.28"]) == "fetch"


def test_dedupe():
    out = dedupe([ServerSpec("a", "x"), ServerSpec("a", "y")])
    assert [s.name for s in out] == ["a", "a2"]
