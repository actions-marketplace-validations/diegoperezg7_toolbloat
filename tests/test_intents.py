from conftest import FakeLLM

from toolbloat import intents as im
from toolbloat.llm import ModelSpec


def test_parse_requests_variants():
    assert im.parse_requests('{"requests": ["a", " b "]}') == ["a", "b"]
    assert im.parse_requests('<think>x</think> sure: {"requests": [{"user_input": "c"}]}') == ["c"]
    assert im.parse_requests("no json") == []
    assert im.parse_requests('{"requests": "nope"}') == []


def test_ensure_generates_missing_and_caches(tmp_path, tools):
    path = tmp_path / "intents.yaml"
    llm = FakeLLM(ModelSpec("ollama", "fake"), text='{"requests": ["do it"]}')
    items = im.ensure(path, tools, llm, 1)
    assert len(items) == 3 and path.exists()
    assert {i.tool for i in items} == {t.key for t in tools}
    # Second run reuses the cache: no new model calls.
    n = len(llm.calls)
    again = im.ensure(path, tools, llm, 1)
    assert len(again) == 3 and len(llm.calls) == n


def test_user_edits_survive(tmp_path, tools):
    path = tmp_path / "intents.yaml"
    im.save(path, [im.Intent("x", "math/add", "sum 1 and 2", also_ok=["text/echo"], skip=True)], {"reviewed": True})
    items, meta = im.load(path)
    assert items[0].also_ok == ["text/echo"] and items[0].skip and meta["reviewed"] is True
    assert items[0].accepts("text/echo") and not items[0].accepts(None)
