"""Find MCP servers in agent configs. Only command and args are ever shown."""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .errors import ToolbloatError


@dataclass
class ServerSpec:
    name: str
    command: str
    args: list[str] = field(default_factory=list)
    # Passed to the server process, never printed or written to a report.
    env: dict[str, str] = field(default_factory=dict, repr=False)
    cwd: str | None = None
    source: str = "--server"

    def display(self) -> str:
        return " ".join([self.command, *redact_args(self.args)])


_SECRET_FLAG = re.compile(r"(key|token|secret|password|passwd|auth|bearer|credential)", re.I)
_SECRET_VALUE = re.compile(r"^(sk-|ghp_|gho_|github_pat_|xox[abp]-|AKIA|AIza|eyJ)")


def redact_args(args: list[str]) -> list[str]:
    """Hide values that look like secrets (e.g. --api-key X, token=X, sk-...)."""
    out: list[str] = []
    hide_next = False
    for a in args:
        if hide_next:
            out.append("***")
            hide_next = False
            continue
        if a.startswith("-") and "=" in a and _SECRET_FLAG.search(a.split("=", 1)[0]):
            out.append(a.split("=", 1)[0] + "=***")
        elif a.startswith("-") and _SECRET_FLAG.search(a):
            out.append(a)
            hide_next = True
        elif _SECRET_VALUE.search(a) or ("=" in a and _SECRET_FLAG.search(a.split("=", 1)[0])):
            out.append("***" if "=" not in a else a.split("=", 1)[0] + "=***")
        else:
            out.append(a)
    return out


def default_config_paths(cwd: Path | None = None) -> list[Path]:
    cwd = cwd or Path.cwd()
    home = Path.home()
    paths = [cwd / ".mcp.json", cwd / ".cursor" / "mcp.json", home / ".cursor" / "mcp.json"]
    if sys.platform == "darwin":
        paths.append(home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json")
    elif sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            paths.append(Path(appdata) / "Claude" / "claude_desktop_config.json")
    else:
        paths.append(home / ".config" / "Claude" / "claude_desktop_config.json")
    return paths


def load_config_file(path: Path) -> list[ServerSpec]:
    """Read the mcpServers block of .mcp.json, Claude Desktop or Cursor config."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ToolbloatError(f"{path} is not valid JSON (line {e.lineno}): {e.msg}") from e
    except OSError as e:
        raise ToolbloatError(f"Cannot read {path}: {e.strerror}") from e
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    if not isinstance(servers, dict):
        return []
    out: list[ServerSpec] = []
    for name, cfg in servers.items():
        if not isinstance(cfg, dict):
            continue
        if cfg.get("disabled") is True:
            continue
        command = cfg.get("command")
        transport = cfg.get("type") or cfg.get("transport") or "stdio"
        if not command or transport not in ("stdio",):
            # HTTP/SSE servers are on the roadmap; skip them for now.
            continue
        args = [str(a) for a in cfg.get("args") or []]
        env = {str(k): str(v) for k, v in (cfg.get("env") or {}).items()}
        out.append(
            ServerSpec(
                name=safe_name(name),
                command=str(command),
                args=args,
                env=env,
                cwd=cfg.get("cwd"),
                source=str(path),
            )
        )
    return out


def skipped_remote(path: Path) -> list[str]:
    """Names of servers in a config that use a transport we do not support yet."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    if not isinstance(servers, dict):
        return []
    return [
        n
        for n, c in servers.items()
        if isinstance(c, dict) and not c.get("command") and (c.get("url") or c.get("type") in ("http", "sse"))
    ]


def parse_server_flag(value: str, index: int) -> ServerSpec:
    """--server "name=cmd args" or --server "cmd args"."""
    name = None
    m = re.match(r"^([A-Za-z0-9_.-]+)=(.+)$", value.strip())
    if m and " " not in m.group(1):
        name, value = m.group(1), m.group(2)
    try:
        parts = shlex.split(value)
    except ValueError as e:
        raise ToolbloatError(f"Cannot parse --server '{value}': {e}") from e
    if not parts:
        raise ToolbloatError('--server needs a command, e.g. --server "uvx mcp-server-time"')
    if name is None:
        name = guess_name(parts) or f"server{index + 1}"
    return ServerSpec(name=safe_name(name), command=parts[0], args=parts[1:])


RUNNERS = {"npx", "uvx", "node", "python", "python3", "uv", "run", "bunx", "pnpm", "dlx", "deno", "docker"}


def guess_name(parts: list[str]) -> str | None:
    """Pick a short server name from a command line, e.g. "uvx mcp-server-time" -> "time"."""
    cands = [p for p in parts[1:] if not p.startswith("-") and p not in RUNNERS]
    preferred = [p for p in cands if "mcp" in p.lower() or "server" in p.lower()]
    for p in preferred + cands:
        base = re.split(r"[@=<>~!]", p.rstrip("/").split("/")[-1])[0] or p.split("/")[-1]
        if p.startswith("@") and "/" in p:
            base = re.split(r"[@=<>~!]", p.split("/", 1)[1])[0]
        base = re.sub(r"\.(py|js|mjs|ts)$", "", base)
        base = re.sub(r"^(mcp[-_]server[-_]|server[-_]|mcp[-_])", "", base)
        base = re.sub(r"([-_]mcp|[-_]server)$", "", base)
        if base:
            return base
    return None


def safe_name(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_-]+", "-", name).strip("-_")
    return s[:24] or "server"


def dedupe(specs: list[ServerSpec]) -> list[ServerSpec]:
    seen: dict[str, int] = {}
    out = []
    for s in specs:
        key = s.name
        if key in seen:
            seen[key] += 1
            s.name = f"{key}{seen[key]}"
        else:
            seen[key] = 1
        out.append(s)
    return out
