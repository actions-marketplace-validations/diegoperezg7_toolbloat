"""Start each MCP server over stdio and list its tools. Tools are never called."""

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass, field

import anyio

from .config import ServerSpec
from .errors import ServerStartError


@dataclass
class ToolDef:
    server: str
    name: str
    description: str
    input_schema: dict
    tokens: int = 0

    @property
    def key(self) -> str:
        return f"{self.server}/{self.name}"


@dataclass
class ServerResult:
    name: str
    command: str  # display form, secrets redacted
    tools: list[ToolDef] = field(default_factory=list)
    error: str | None = None
    server_version: str | None = None

    @property
    def tokens(self) -> int:
        return sum(t.tokens for t in self.tools)


async def _list_one(spec: ServerSpec, timeout: float) -> ServerResult:
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    result = ServerResult(name=spec.name, command=spec.display())
    if shutil.which(spec.command) is None and not os.path.isfile(spec.command):
        result.error = f"command not found: {spec.command}"
        return result
    params = StdioServerParameters(command=spec.command, args=spec.args, env=spec.env or None, cwd=spec.cwd)
    with tempfile.TemporaryFile("w+", encoding="utf-8") as errlog:
        try:
            with anyio.fail_after(timeout):
                async with stdio_client(params, errlog=errlog) as (read, write):
                    async with ClientSession(read, write) as session:
                        init = await session.initialize()
                        info = getattr(init, "server_info", None)
                        if info is not None:
                            result.server_version = f"{info.name} {info.version}"
                        cursor = None
                        while True:
                            from mcp.types import PaginatedRequestParams

                            page = await session.list_tools(
                                params=PaginatedRequestParams(cursor=cursor) if cursor else None
                            )
                            for t in page.tools:
                                result.tools.append(
                                    ToolDef(
                                        server=spec.name,
                                        name=t.name,
                                        description=t.description or "",
                                        input_schema=t.input_schema or {"type": "object", "properties": {}},
                                    )
                                )
                            cursor = page.next_cursor
                            if not cursor:
                                break
        except TimeoutError:
            result.error = f"no answer to tools/list within {timeout:.0f}s"
        except Exception as e:  # server crashed, bad protocol, ...
            errlog.seek(0)
            tail = errlog.read()[-400:].strip()
            result.error = f"{type(e).__name__}: {e}"[:300] + (f" | stderr: {tail}" if tail else "")
    return result


def list_tools(specs: list[ServerSpec], timeout: float = 90.0) -> list[ServerResult]:
    """List tools of every server, one after another (keeps memory use low)."""

    async def run() -> list[ServerResult]:
        return [await _list_one(s, timeout) for s in specs]

    results = anyio.run(run)
    if results and all(r.error for r in results):
        raise ServerStartError(
            "No MCP server could be started:\n" + "\n".join(f"  - {r.name} ({r.command}): {r.error}" for r in results)
        )
    return results
