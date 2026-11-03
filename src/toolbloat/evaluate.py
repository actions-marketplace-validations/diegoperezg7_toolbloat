"""Ask each model which tool it would call, with one server and with the whole stack.

Tools are never executed: we only read the tool call the model proposes.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from .discover import ToolDef
from .errors import BudgetExceeded, MissingApiKey, ModelNotFound, ProviderUnreachable, ToolbloatError
from .intents import Intent
from .llm import LLM
from .tokens import function_name, openai_tool

SYSTEM = (
    "You are an assistant connected to tools. Handle the user's request by calling exactly"
    " one tool, the one that fits best, with complete arguments. Do not answer in text."
)

CONDITIONS = ("solo", "stack")


@dataclass
class Call:
    model: str
    intent_id: str
    condition: str  # solo | stack
    chosen: str | None  # server/tool, "?:<name>" for an unknown name, None for no call
    correct: bool
    args_valid: bool | None
    prompt_tokens: int
    completion_tokens: int
    cost: float
    seconds: float
    error: str | None = None


def validate_args(arguments: str | None, schema: dict) -> bool:
    import jsonschema

    try:
        args = json.loads(arguments or "{}")
    except json.JSONDecodeError:
        return False
    if not isinstance(args, dict):
        return False
    try:
        cls = jsonschema.validators.validator_for(schema)
        cls(schema).validate(args)
    except jsonschema.ValidationError:
        return False
    except (jsonschema.SchemaError, jsonschema.exceptions.UnknownType, RecursionError):
        # The server ships an invalid schema; only check that we got an object.
        return True
    return True


def _key(model: str, intent: Intent, condition: str, stack_hash: str) -> str:
    raw = json.dumps([model, intent.tool, intent.text, sorted(intent.also_ok), condition, stack_hash])
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


def stack_hash(tools: list[ToolDef]) -> str:
    raw = json.dumps([openai_tool(t) for t in tools], sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


class Cache:
    """Append-only JSONL of finished calls, so reruns and interrupted runs reuse work."""

    def __init__(self, path: Path | None):
        self.path = path
        self.data: dict[str, dict] = {}
        self.lock = threading.Lock()
        if path and path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    row = json.loads(line)
                    self.data[row["key"]] = row["call"]
                except (json.JSONDecodeError, KeyError):
                    continue

    def get(self, key: str) -> Call | None:
        row = self.data.get(key)
        return Call(**row) if row else None

    def put(self, key: str, call: Call) -> None:
        if call.error:
            return
        with self.lock:
            self.data[key] = asdict(call)
            if self.path:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps({"key": key, "call": asdict(call)}) + "\n")


def run(
    llms: list[LLM],
    tools: list[ToolDef],
    intents: list[Intent],
    *,
    cache: Cache,
    on_call: Callable[[Call], None] | None = None,
    cached_only: bool = False,
) -> tuple[list[Call], str | None]:
    """Returns the calls and, if the run stopped early, the reason."""
    by_name = {function_name(t.server, t.name): t for t in tools}
    by_key = {t.key: t for t in tools}
    all_defs = [openai_tool(t) for t in tools]
    all_tokens = sum(t.tokens for t in tools)
    server_defs: dict[str, list[dict]] = {}
    server_tokens: dict[str, int] = {}
    for t in tools:
        server_defs.setdefault(t.server, []).append(openai_tool(t))
        server_tokens[t.server] = server_tokens.get(t.server, 0) + t.tokens
    h = stack_hash(tools)
    todo = _round_robin([i for i in intents if not i.skip and i.tool in by_key])

    calls: list[Call] = []
    lock = threading.Lock()
    stop: dict[str, str | None] = {"reason": None}
    problems: list[str] = []

    def one(llm: LLM, intent: Intent, condition: str) -> Call | None:
        key = _key(llm.spec.id, intent, condition, h)
        cached = cache.get(key)
        if cached or cached_only:
            return cached
        defs = server_defs[intent.server] if condition == "solo" else all_defs
        est = (server_tokens[intent.server] if condition == "solo" else all_tokens) + 200
        system = SYSTEM
        t0 = time.monotonic()
        try:
            out = llm.chat(
                [{"role": "system", "content": system}, {"role": "user", "content": intent.text}],
                tools=defs,
                max_tokens=2048,
                est_prompt_tokens=est,
            )
        except (BudgetExceeded, MissingApiKey, ModelNotFound, ProviderUnreachable):
            raise
        except ToolbloatError as e:
            return Call(
                llm.spec.id,
                intent.id,
                condition,
                None,
                False,
                None,
                0,
                0,
                0.0,
                time.monotonic() - t0,
                error=str(e)[:300],
            )
        chosen = None
        args_valid = None
        if out.tool_name:
            t = by_name.get(out.tool_name)
            if t is None:
                # Some models drop the server prefix; map a unique bare name back.
                bare = [x for x in tools if x.name == out.tool_name]
                t = bare[0] if len(bare) == 1 else None
            chosen = t.key if t else f"?:{out.tool_name}"
            if t is not None:
                args_valid = validate_args(out.arguments, t.input_schema)
        call = Call(
            model=llm.spec.id,
            intent_id=intent.id,
            condition=condition,
            chosen=chosen,
            correct=intent.accepts(chosen),
            args_valid=args_valid,
            prompt_tokens=out.prompt_tokens,
            completion_tokens=out.completion_tokens,
            cost=round(out.cost, 8),
            seconds=round(time.monotonic() - t0, 2),
        )
        cache.put(key, call)
        return call

    def worker(group: list[LLM]) -> None:
        for llm in group:
            try:
                for intent in todo:
                    for condition in CONDITIONS:
                        if stop["reason"]:
                            return
                        c = one(llm, intent, condition)
                        if c is None:
                            continue
                        with lock:
                            calls.append(c)
                        if on_call:
                            on_call(c)
            except BudgetExceeded as e:
                stop["reason"] = str(e)
                return
            except ToolbloatError as e:
                # This model is unusable; the others keep going.
                with lock:
                    problems.append(f"{llm.spec.id}: {e}")

    # One thread per provider, models of a provider run one after another
    # (low concurrency keeps rate limits and local RAM in check).
    groups: dict[str, list[LLM]] = {}
    for llm in llms:
        groups.setdefault(llm.spec.provider, []).append(llm)
    threads = [threading.Thread(target=worker, args=(g,), daemon=True) for g in groups.values()]
    for t in threads:
        t.start()
    try:
        for t in threads:
            while t.is_alive():
                t.join(0.5)
    except KeyboardInterrupt:
        stop["reason"] = "interrupted by the user (Ctrl-C); partial results kept"
    reasons = [r for r in [stop["reason"], *problems] if r]
    return calls, "; ".join(reasons) or None


def _round_robin(items: list[Intent]) -> list[Intent]:
    """Interleave servers so a run stopped early (budget, Ctrl-C) still covers every server."""
    queues: dict[str, list[Intent]] = {}
    for i in items:
        queues.setdefault(i.server, []).append(i)
    out: list[Intent] = []
    while any(queues.values()):
        for q in queues.values():
            if q:
                out.append(q.pop(0))
    return out


def total_calls(llms: list[LLM], tools: list[ToolDef], intents: list[Intent]) -> int:
    keys = {t.key for t in tools}
    return len(llms) * len(CONDITIONS) * sum(1 for i in intents if not i.skip and i.tool in keys)
