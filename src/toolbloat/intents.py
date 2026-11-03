"""User requests ("intents") that should map to one tool. LLM-generated, cached as YAML, editable."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import yaml

from .discover import ToolDef
from .errors import ToolbloatError
from .llm import LLM

HEADER = """\
# toolbloat intents: realistic user requests and the tool that should answer each.
# Edit freely. `tool` is server/tool. `also_ok` lists other acceptable tools.
# Delete an entry to have it regenerated on the next run; set `skip: true` to ignore it.
"""

PROMPT = """You write test prompts for an AI assistant that is connected to tools.

Tool under test: {name}
Description: {description}
Input schema: {schema}

Other tools on the same server: {siblings}

Write {n} different, realistic requests a user might type where THIS tool is clearly
the right one to call (not one of the other tools). Rules:
- Natural language, one or two sentences, as a real user would write.
- Do not mention the tool name or the word "tool".
- Include the concrete details the tool's required arguments need (paths, names, values).
- Vary the wording.

Answer with JSON only, a list of plain strings: {{"requests": ["...", "..."]}}"""


@dataclass
class Intent:
    id: str
    tool: str  # server/tool
    text: str
    also_ok: list[str] = field(default_factory=list)
    skip: bool = False

    @property
    def server(self) -> str:
        return self.tool.split("/", 1)[0]

    def accepts(self, key: str | None) -> bool:
        return key is not None and (key == self.tool or key in self.also_ok)


def load(path: Path) -> tuple[list[Intent], dict]:
    if not path.exists():
        return [], {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise ToolbloatError(f"{path} is not valid YAML: {e}") from e
    items = []
    for raw in data.get("intents") or []:
        if not isinstance(raw, dict) or "tool" not in raw or "text" not in raw:
            raise ToolbloatError(f"{path}: every intent needs `tool` and `text` (got {raw!r})")
        items.append(
            Intent(
                id=str(raw.get("id") or f"{raw['tool'].replace('/', '-')}-{len(items) + 1}"),
                tool=str(raw["tool"]),
                text=str(raw["text"]),
                also_ok=[str(x) for x in raw.get("also_ok") or []],
                skip=bool(raw.get("skip", False)),
            )
        )
    meta = {k: v for k, v in data.items() if k != "intents"}
    return items, meta


def save(path: Path, intents: list[Intent], meta: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = dict(meta)
    body["intents"] = [
        {k: v for k, v in asdict(i).items() if not (k == "also_ok" and not v) and not (k == "skip" and not v)}
        for i in intents
    ]
    text = yaml.safe_dump(body, sort_keys=False, allow_unicode=True, width=100)
    path.write_text(HEADER + text, encoding="utf-8")


def parse_requests(text: str) -> list[str]:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    m = re.search(r"\{.*\}", text, flags=re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    reqs = data.get("requests") if isinstance(data, dict) else None
    if not isinstance(reqs, list):
        return []
    out = []
    for r in reqs:
        if isinstance(r, dict):
            # Some models wrap each request in an object; keep its first string field.
            r = next((v for v in r.values() if isinstance(v, str)), None)
        if isinstance(r, str) and r.strip():
            out.append(r.strip())
    return out


def generate_for_tool(llm: LLM, tool: ToolDef, siblings: list[str], n: int) -> list[str]:
    prompt = PROMPT.format(
        name=tool.name,
        description=tool.description.strip()[:2000] or "(no description)",
        schema=json.dumps(tool.input_schema, ensure_ascii=False)[:2000],
        siblings=", ".join(siblings) or "(none)",
        n=n,
    )
    for _ in range(2):
        out = llm.chat(
            [{"role": "user", "content": prompt}],
            max_tokens=1500,
            temperature=0.7,
            est_prompt_tokens=len(prompt) // 3,
            json_mode=llm.spec.provider != "ollama",
        )
        reqs = parse_requests(out.text)
        if reqs:
            return reqs[:n]
    return []


def ensure(
    path: Path,
    tools: list[ToolDef],
    llm: LLM | None,
    per_tool: int,
    on_progress=None,
) -> list[Intent]:
    """Load cached intents and generate the missing ones."""
    intents, meta = load(path)
    have = {i.tool for i in intents}
    missing = [t for t in tools if t.key not in have]
    if missing and llm is None:
        raise ToolbloatError(
            f"{len(missing)} tool(s) have no intents in {path} and no model is available to write them."
        )
    by_server: dict[str, list[str]] = {}
    for t in tools:
        by_server.setdefault(t.server, []).append(t.name)
    for n, t in enumerate(missing, 1):
        if on_progress:
            on_progress(n, len(missing), t.key)
        reqs = generate_for_tool(llm, t, [s for s in by_server[t.server] if s != t.name], per_tool)
        for k, r in enumerate(reqs, 1):
            intents.append(Intent(id=f"{t.server}-{t.name}-{k}", tool=t.key, text=r))
        # Save after every tool so an interrupted run keeps its work.
        if llm is not None:
            meta.setdefault("generated_by", llm.spec.id)
            meta["generated_at"] = datetime.now(UTC).strftime("%Y-%m-%d")
        save(path, intents, meta)
    if not missing and not path.exists():
        save(path, intents, meta)
    return intents
