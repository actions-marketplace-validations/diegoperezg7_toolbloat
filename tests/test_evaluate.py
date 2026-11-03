from conftest import FakeLLM

from toolbloat import evaluate
from toolbloat.errors import BudgetExceeded
from toolbloat.llm import ModelSpec


def test_validate_args():
    schema = {"type": "object", "properties": {"a": {"type": "integer"}}, "required": ["a"]}
    assert evaluate.validate_args('{"a": 1}', schema)
    assert not evaluate.validate_args('{"a": "x"}', schema)
    assert not evaluate.validate_args("not json", schema)
    assert not evaluate.validate_args("[1]", schema)
    assert evaluate.validate_args("{}", {"type": "bogus"})  # invalid server schema: lenient


def test_solo_vs_stack(tools, intents, tmp_path):
    # Right alone, wrong (other tool on same server) when stacked; echo answered without prefix.
    llm = FakeLLM(
        ModelSpec("ollama", "fake"),
        answers={
            "What is 2 plus 3?": "math__add",
            ("Make HELLO loud", 2): "text__shout",
            ("Make HELLO loud", 3): "text__echo",
            "Repeat after me: hi": "echo",
        },
    )
    calls, stopped = evaluate.run([llm], tools, intents, cache=evaluate.Cache(tmp_path / "c.jsonl"))
    assert stopped is None
    got = {(c.intent_id, c.condition): c for c in calls}
    assert got[("i-add", "solo")].correct and got[("i-add", "stack")].args_valid
    assert got[("i-shout", "solo")].correct and not got[("i-shout", "stack")].correct
    assert got[("i-echo", "stack")].chosen == "text/echo"
    # Nothing is re-asked on a second run.
    n = len(llm.calls)
    evaluate.run([llm], tools, intents, cache=evaluate.Cache(tmp_path / "c.jsonl"))
    assert len(llm.calls) == n


def test_unknown_and_no_call(tools, intents, tmp_path):
    llm = FakeLLM(ModelSpec("ollama", "fake"), answers={"What is 2 plus 3?": "calculator"})
    calls, _ = evaluate.run([llm], tools, intents[:2], cache=evaluate.Cache(None))
    chosen = {c.intent_id: c.chosen for c in calls}
    assert chosen["i-add"] == "?:calculator" and chosen["i-shout"] is None


def test_budget_stops_with_partial_results(tools, intents):
    class Broke(FakeLLM):
        def chat(self, *a, **k):
            if len(self.calls) >= 2:
                raise BudgetExceeded("Budget reached")
            return super().chat(*a, **k)

    llm = Broke(ModelSpec("ollama", "fake"))
    calls, stopped = evaluate.run([llm], tools, intents, cache=evaluate.Cache(None))
    assert len(calls) == 2 and "Budget" in stopped


def test_total_calls(tools, intents):
    llm = FakeLLM(ModelSpec("ollama", "fake"))
    assert evaluate.total_calls([llm, llm], tools, intents) == 12


def test_round_robin_covers_servers_first(intents):
    order = [i.server for i in evaluate._round_robin(intents)]
    assert order[:2] in (["math", "text"], ["text", "math"])
