"""One OpenAI-compatible client with provider presets, a budget and backoff."""

from __future__ import annotations

import os
import random
import threading
import time
from dataclasses import dataclass, field

from .errors import (
    BudgetExceeded,
    MissingApiKey,
    ModelNotFound,
    ProviderUnreachable,
    ToolbloatError,
)

# base_url, env var holding the key (None = no key needed)
PROVIDERS: dict[str, tuple[str, str | None]] = {
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY"),
    "mistral": ("https://api.mistral.ai/v1", "MISTRAL_API_KEY"),
    "ollama": ("http://localhost:11434/v1", None),
}

# USD per million tokens (input, output). Used for --max-cost and the monthly
# projection. Unknown models fall back to --price-in/--price-out or 0 with a warning.
# Mistral list prices from https://docs.mistral.ai/inference/pricing (checked 2026-11-03):
# mistral-small-latest = Mistral Small 4, ministral-8b-latest = Ministral 3 8B.
# Mistral Medium 3.5 is not listed there; we budget it at the Mistral Large 4 price
# as a conservative upper bound.
PRICES: dict[str, tuple[float, float]] = {
    "mistral:mistral-small-latest": (0.15, 0.60),
    "mistral:ministral-8b-latest": (0.15, 0.15),
    "mistral:ministral-14b-latest": (0.20, 0.20),
    "mistral:ministral-3b-latest": (0.10, 0.10),
    "mistral:codestral-latest": (0.30, 0.90),
    "mistral:mistral-medium-latest": (0.70, 2.10),
}


def ollama_base_url() -> str:
    host = os.environ.get("OLLAMA_HOST", "").strip()
    if not host:
        return PROVIDERS["ollama"][0]
    if not host.startswith("http"):
        host = "http://" + host
    host = host.rstrip("/")
    return host if host.endswith("/v1") else host + "/v1"


@dataclass(frozen=True)
class ModelSpec:
    provider: str
    model: str

    @property
    def id(self) -> str:
        return f"{self.provider}:{self.model}"

    @classmethod
    def parse(cls, text: str) -> ModelSpec:
        provider, sep, model = text.partition(":")
        if not sep or not model:
            raise ToolbloatError(
                f"Model '{text}' must look like provider:model, e.g. ollama:qwen3:4b "
                f"or mistral:mistral-small-latest. Providers: {', '.join(PROVIDERS)}."
            )
        if provider not in PROVIDERS:
            raise ToolbloatError(f"Unknown provider '{provider}'. Use one of: {', '.join(PROVIDERS)}.")
        return cls(provider, model)

    def price(self) -> tuple[float, float]:
        if self.provider == "ollama":
            return (0.0, 0.0)
        return PRICES.get(self.id, (0.0, 0.0))

    def has_known_price(self) -> bool:
        return self.provider == "ollama" or self.id in PRICES


@dataclass
class Budget:
    """Tracks spend and refuses a call once the cap would be passed."""

    max_cost: float
    spent: dict[str, float] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def total(self) -> float:
        return sum(self.spent.values())

    def check(self, estimate: float = 0.0) -> None:
        if self.total + estimate > self.max_cost:
            raise BudgetExceeded(
                f"Budget reached: spent ${self.total:.4f} of --max-cost ${self.max_cost:.2f}"
                f" (next call estimated at ${estimate:.4f}). Partial results were kept;"
                " raise --max-cost to continue."
            )

    def add(self, provider: str, usd: float) -> None:
        with self._lock:
            self.spent[provider] = self.spent.get(provider, 0.0) + usd


@dataclass
class Completion:
    tool_name: str | None
    arguments: str | None
    text: str
    prompt_tokens: int
    completion_tokens: int
    cost: float


class LLM:
    """Thin wrapper around openai.OpenAI for one model."""

    def __init__(self, spec: ModelSpec, budget: Budget, *, max_retries: int = 9, timeout: float | None = None):
        from openai import OpenAI

        base_url, key_var = PROVIDERS[spec.provider]
        if spec.provider == "ollama":
            base_url = ollama_base_url()
            api_key = "ollama"
        else:
            api_key = os.environ.get(key_var or "", "").strip()
            if not api_key:
                raise MissingApiKey(
                    f"{key_var} is not set. Export it to use {spec.id}, or pick a local model such as ollama:qwen3:4b."
                )
        self.spec = spec
        self.budget = budget
        self.max_retries = max_retries
        self.base_url = base_url
        # Local models can be slow (long prompts, thinking, a busy GPU): give them more time.
        if timeout is None:
            timeout = 1200.0 if spec.provider == "ollama" else 180.0
        self.client = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout, max_retries=0)

    def estimate_cost(self, prompt_tokens: int, max_out: int) -> float:
        pin, pout = self.spec.price()
        return (prompt_tokens * pin + max_out * pout) / 1_000_000

    def chat(
        self,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.0,
        est_prompt_tokens: int = 0,
        json_mode: bool = False,
    ) -> Completion:
        import openai

        self.budget.check(self.estimate_cost(est_prompt_tokens, max_tokens))
        kwargs: dict = dict(
            model=self.spec.model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        delay = 2.0
        last_err: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = self.client.chat.completions.create(**kwargs)
                break
            except openai.AuthenticationError as e:
                raise MissingApiKey(
                    f"{self.spec.provider} rejected the API key (401). Check {PROVIDERS[self.spec.provider][1]}."
                ) from e
            except openai.NotFoundError as e:
                hint = f" Run `ollama pull {self.spec.model}` first." if self.spec.provider == "ollama" else ""
                raise ModelNotFound(f"Model '{self.spec.model}' not found at {self.spec.provider}.{hint}") from e
            except openai.APIConnectionError as e:
                last_err = e
                if self.spec.provider == "ollama":
                    raise ProviderUnreachable(
                        f"Cannot reach Ollama at {self.base_url}. Start it with `ollama serve` or set OLLAMA_HOST."
                    ) from e
                if attempt >= self.max_retries:
                    raise ProviderUnreachable(f"Cannot reach {self.spec.provider} at {self.base_url}.") from e
            except openai.PermissionDeniedError as e:
                raise ToolbloatError(f"{self.spec.provider} refused the request (403): {_short(e)}") from e
            except openai.BadRequestError as e:
                msg = _short(e)
                if "model" in msg.lower() and ("not" in msg.lower() or "invalid" in msg.lower()):
                    raise ModelNotFound(f"Model '{self.spec.model}' rejected by {self.spec.provider}: {msg}") from e
                raise ToolbloatError(f"{self.spec.id} rejected the request: {msg}") from e
            except (openai.RateLimitError, openai.InternalServerError, openai.APITimeoutError) as e:
                last_err = e
                if isinstance(e, openai.RateLimitError) and _is_quota(e):
                    raise BudgetExceeded(
                        f"{self.spec.provider} says the account has no credit left: {_short(e)}"
                    ) from e
                if attempt >= self.max_retries:
                    raise ProviderUnreachable(
                        f"{self.spec.id} kept failing after {self.max_retries} retries: {_short(e)}"
                    ) from e
            wait = delay + random.uniform(0, delay / 2)
            headers = getattr(getattr(last_err, "response", None), "headers", None) or {}
            retry_after = headers.get("retry-after")
            if retry_after:
                try:
                    wait = max(wait, min(float(retry_after), 120))
                except ValueError:
                    pass
            time.sleep(wait)
            delay = min(delay * 2, 60)

        usage = resp.usage
        pt = getattr(usage, "prompt_tokens", 0) or 0
        ct = getattr(usage, "completion_tokens", 0) or 0
        pin, pout = self.spec.price()
        cost = (pt * pin + ct * pout) / 1_000_000
        self.budget.add(self.spec.provider, cost)

        msg = resp.choices[0].message
        tool_name = arguments = None
        if msg.tool_calls:
            fn = msg.tool_calls[0].function
            tool_name, arguments = fn.name, fn.arguments
        return Completion(tool_name, arguments, msg.content or "", pt, ct, cost)


def _short(e: Exception) -> str:
    text = str(e)
    return text if len(text) < 300 else text[:300] + "..."


def _is_quota(e: Exception) -> bool:
    text = str(e).lower()
    return any(w in text for w in ("insufficient", "quota", "credit", "spending limit", "payment"))
