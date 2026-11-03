from dataclasses import asdict

from conftest import FakeLLM

from toolbloat import evaluate
from toolbloat.llm import ModelSpec
from toolbloat.metrics import compute
from toolbloat.report import render


def make_results(tools, intents):
    llm = FakeLLM(
        ModelSpec("ollama", "fake"),
        answers={
            "What is 2 plus 3?": "math__add",
            ("Make HELLO loud", 2): "text__shout",
            ("Make HELLO loud", 3): "math__add",
            "Repeat after me: hi": "text__shout",
        },
    )
    calls, _ = evaluate.run([llm], tools, intents, cache=evaluate.Cache(None))
    servers = {}
    for t in tools:
        servers.setdefault(t.server, []).append(
            {"name": t.name, "description": t.description, "input_schema": t.input_schema, "tokens": t.tokens}
        )
    return {
        "version": "0.1.0",
        "created_at": "2026-11-03T00:00:00Z",
        "environment": {"hardware": "test"},
        "servers": [{"name": k, "command": f"run {k}", "tools": v} for k, v in servers.items()],
        "intents": [asdict(i) for i in intents],
        "models": [{"id": "ollama:fake", "price_in": 0, "price_out": 0}],
        "calls": [asdict(c) for c in calls],
        "settings": {"turns_per_day": 10},
        "spend": {},
    }


def test_compute(tools, intents):
    m = compute(make_results(tools, intents))
    assert m["total_tokens"] == 150 and m["n_tools"] == 3
    row = m["models"][0]
    assert row["solo_acc"] == 2 / 3 and row["stack_acc"] == 1 / 3
    assert row["broken_by_stack"] == 1 and row["cross_server_errors"] == 1
    assert m["matrix"]["cells"]["text"]["math"] == 1
    assert [n["tool"] for n in m["never_chosen"]] == ["text/echo"]
    assert m["never_tokens"] == 50 and m["monthly_tokens"] == 150 * 10 * 30
    assert m["confusions"][0]["cross_server"] in (True, False)


def test_render_has_both_languages_and_no_template_leftovers(tools, intents):
    html = render(make_results(tools, intents))
    assert "toolbloat" in html and "Con la pila" in html and "Stacked" in html
    assert "{{" not in html and "{%" not in html
    assert "prefers-color-scheme: dark" in html and "#000000" in html


def test_truncation_flag(tools, intents):
    r = make_results(tools, intents)
    for c in r["calls"]:
        c["prompt_tokens"] = 10  # far below the 150 tokens of definitions
    assert compute(r)["models"][0]["truncated"] is True


def test_bundled_demo_has_no_secrets_or_private_paths():
    from importlib import resources

    text = resources.files("toolbloat").joinpath("data/demo/results.json").read_text()
    home_dirs = ["/" + "Users" + "/", "/home/", "/" + "private" + "/"]
    for bad in [*home_dirs, "API_KEY", "sk-"]:
        assert bad not in text


def test_numbers_carry_raw_values_for_locale_formatting(tools, intents):
    html = render(make_results(tools, intents))
    assert 'data-n="150" data-f="int">150<' in html
    assert "Intl.NumberFormat" in html and "es-ES" in html
