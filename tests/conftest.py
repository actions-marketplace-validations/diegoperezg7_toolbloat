from dataclasses import dataclass, field

import pytest

from toolbloat.discover import ToolDef
from toolbloat.intents import Intent
from toolbloat.llm import Completion, ModelSpec


@dataclass
class FakeLLM:
    """Stands in for toolbloat.llm.LLM. `answers` maps (user text, n_tools) or user text to a tool name."""

    spec: ModelSpec
    answers: dict = field(default_factory=dict)
    text: str = ""
    calls: list = field(default_factory=list)
    price: float = 0.0

    def chat(self, messages, *, tools=None, max_tokens=0, temperature=0, est_prompt_tokens=0, json_mode=False):
        user = messages[-1]["content"]
        self.calls.append((user, len(tools or [])))
        name = self.answers.get((user, len(tools or [])), self.answers.get(user))
        args = '{"a": 1, "b": 2}' if name and name.endswith("add") else '{"text": "hi"}'
        return Completion(name, args if name else None, self.text, 100 * len(tools or [1]), 5, self.price)


@pytest.fixture
def tools():
    add = ToolDef(
        "math",
        "add",
        "Add two integers.",
        {"type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}}, "required": ["a", "b"]},
    )
    shout = ToolDef(
        "text",
        "shout",
        "Upper-case text.",
        {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
    )
    echo = ToolDef(
        "text",
        "echo",
        "Echo text back.",
        {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
    )
    out = [add, shout, echo]
    for t in out:
        t.tokens = 50
    return out


@pytest.fixture
def intents():
    return [
        Intent("i-add", "math/add", "What is 2 plus 3?"),
        Intent("i-shout", "text/shout", "Make HELLO loud"),
        Intent("i-echo", "text/echo", "Repeat after me: hi"),
    ]
