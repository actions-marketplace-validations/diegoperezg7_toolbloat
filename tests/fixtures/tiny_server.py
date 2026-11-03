"""A minimal stdio MCP server used by the tests. Its tools must never be called."""

import sys

from mcp.server.mcpserver import MCPServer

server = MCPServer("tiny")


@server.tool()
def add(a: int, b: int) -> int:
    """Add two integers and return the sum."""
    sys.exit("add must never be called by toolbloat")


@server.tool()
def shout(text: str) -> str:
    """Return the text in upper case."""
    sys.exit("shout must never be called by toolbloat")


if __name__ == "__main__":
    server.run("stdio")
