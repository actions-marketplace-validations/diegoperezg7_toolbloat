"""Approximate token cost of tool definitions.

Each tool is serialised the way OpenAI-compatible APIs receive it
({"type": "function", "function": {name, description, parameters}}) and counted
with tiktoken's o200k_base. Every provider wraps tools in its own template and
tokenizer, so this is an approximation; the report also shows the prompt tokens
each provider actually billed.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache

from .discover import ToolDef

TOKENIZER = "o200k_base"


@lru_cache(maxsize=1)
def _encoding():
    import tiktoken

    return tiktoken.get_encoding(TOKENIZER)


def count(text: str) -> int:
    return len(_encoding().encode(text, disallowed_special=()))


def function_name(server: str, tool: str) -> str:
    """Name a tool the way agents namespace MCP tools: server__tool, max 64 chars."""
    name = re.sub(r"[^A-Za-z0-9_-]", "_", f"{server}__{tool}")
    return name[:64]


def openai_tool(t: ToolDef) -> dict:
    return {
        "type": "function",
        "function": {
            "name": function_name(t.server, t.name),
            "description": t.description,
            "parameters": t.input_schema,
        },
    }


def tool_tokens(t: ToolDef) -> int:
    return count(json.dumps(openai_tool(t), separators=(",", ":"), ensure_ascii=False))


def annotate(tools: list[ToolDef]) -> None:
    for t in tools:
        t.tokens = tool_tokens(t)
