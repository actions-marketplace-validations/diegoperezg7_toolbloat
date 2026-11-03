from toolbloat.discover import ToolDef
from toolbloat.tokens import annotate, function_name, openai_tool


def test_function_name_is_api_safe():
    assert function_name("everything", "get-sum") == "everything__get-sum"
    assert function_name("a b", "c.d") == "a_b__c_d"
    assert len(function_name("x" * 40, "y" * 40)) == 64


def test_tokens_grow_with_description():
    short = ToolDef("s", "t", "Add.", {"type": "object"})
    long = ToolDef("s", "t", "Add. " * 50, {"type": "object"})
    annotate([short, long])
    assert 0 < short.tokens < long.tokens
    assert openai_tool(short)["function"]["parameters"] == {"type": "object"}
