import httpx2 as httpx
import openai
import pytest

from toolbloat.errors import BudgetExceeded, MissingApiKey, ModelNotFound, ProviderUnreachable, ToolbloatError
from toolbloat.llm import LLM, Budget, ModelSpec, ollama_base_url

REQ = httpx.Request("POST", "http://x/v1/chat/completions")


def resp(code):
    return httpx.Response(code, request=REQ, json={"error": {"message": "boom"}})


def test_model_spec_parse():
    assert ModelSpec.parse("ollama:qwen3:4b") == ModelSpec("ollama", "qwen3:4b")
    with pytest.raises(ToolbloatError, match="provider:model"):
        ModelSpec.parse("qwen3")
    with pytest.raises(ToolbloatError, match="Unknown provider"):
        ModelSpec.parse("groq:llama")
    assert ModelSpec.parse("ollama:x").price() == (0.0, 0.0)
    assert not ModelSpec.parse("openrouter:some/model").has_known_price()


def test_missing_key(monkeypatch):
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    with pytest.raises(MissingApiKey, match="MISTRAL_API_KEY"):
        LLM(ModelSpec("mistral", "mistral-small-latest"), Budget(1))


def test_budget():
    b = Budget(0.01)
    b.add("mistral", 0.009)
    b.check(0.0005)
    with pytest.raises(BudgetExceeded, match="--max-cost"):
        b.check(0.002)


def test_ollama_host(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "10.0.0.2:11434")
    assert ollama_base_url() == "http://10.0.0.2:11434/v1"


def _llm(monkeypatch, exc, provider="ollama"):
    monkeypatch.setenv("MISTRAL_API_KEY", "test")
    llm = LLM(ModelSpec(provider, "m"), Budget(1), max_retries=1)
    monkeypatch.setattr("toolbloat.llm.time.sleep", lambda s: None)

    def boom(**kw):
        raise exc

    monkeypatch.setattr(llm.client.chat.completions, "create", boom)
    return llm


def test_ollama_unreachable(monkeypatch):
    llm = _llm(monkeypatch, openai.APIConnectionError(request=REQ))
    with pytest.raises(ProviderUnreachable, match="ollama serve"):
        llm.chat([{"role": "user", "content": "x"}])


def test_model_not_found(monkeypatch):
    llm = _llm(monkeypatch, openai.NotFoundError("nope", response=resp(404), body=None))
    with pytest.raises(ModelNotFound, match="ollama pull m"):
        llm.chat([{"role": "user", "content": "x"}])


def test_rate_limit_retries_then_fails(monkeypatch):
    llm = _llm(monkeypatch, openai.RateLimitError("Rate limit exceeded", response=resp(429), body=None), "mistral")
    with pytest.raises(ProviderUnreachable, match="kept failing"):
        llm.chat([{"role": "user", "content": "x"}])


def test_bad_key(monkeypatch):
    llm = _llm(monkeypatch, openai.AuthenticationError("bad", response=resp(401), body=None), "mistral")
    with pytest.raises(MissingApiKey, match="rejected the API key"):
        llm.chat([{"role": "user", "content": "x"}])


def test_budget_checked_before_call(monkeypatch):
    monkeypatch.setenv("MISTRAL_API_KEY", "test")
    llm = LLM(ModelSpec("mistral", "mistral-medium-latest"), Budget(0.000001))
    called = []
    monkeypatch.setattr(llm.client.chat.completions, "create", lambda **kw: called.append(1))
    with pytest.raises(BudgetExceeded):
        llm.chat([{"role": "user", "content": "x"}], est_prompt_tokens=10_000)
    assert not called
