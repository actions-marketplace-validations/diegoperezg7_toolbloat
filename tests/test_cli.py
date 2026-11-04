import sys
from pathlib import Path

from typer.testing import CliRunner

from toolbloat.cli import app

runner = CliRunner()
TINY = str(Path(__file__).parent / "fixtures" / "tiny_server.py")


def test_demo_needs_nothing(tmp_path):
    out = tmp_path / "demo.html"
    r = runner.invoke(app, ["demo", "--out", str(out)])
    assert r.exit_code == 0, r.output
    assert "Recorded run" in r.output and out.exists()
    assert "Traceback" not in r.output


def test_run_without_key_is_a_clear_error(monkeypatch, tmp_path):
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    r = runner.invoke(app, ["run", "-m", "mistral:mistral-small-latest", "-s", "x", "--out", str(tmp_path)])
    assert r.exit_code == 1 and "MISTRAL_API_KEY is not set" in r.output
    assert "Traceback" not in r.output


def test_unknown_price_is_refused(tmp_path):
    r = runner.invoke(app, ["run", "-m", "openrouter:some/model", "-s", "x", "--out", str(tmp_path)])
    assert r.exit_code == 1 and "--price" in r.output


def test_tokens_on_tiny_server():
    r = runner.invoke(app, ["tokens", "-s", f"tiny={sys.executable} {TINY}"])
    assert r.exit_code == 0, r.output
    assert "tiny" in r.output and "total" in r.output


def test_missing_command_reports_cleanly():
    r = runner.invoke(app, ["tokens", "-s", "definitely-not-a-command-xyz"])
    assert r.exit_code == 1 and "command not found" in r.output


def test_cloud_provider_needs_confirmation(monkeypatch, tmp_path):
    """Without --yes nothing is sent: the run stops after listing what would be sent and to whom."""
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    sent = []
    monkeypatch.setattr("toolbloat.llm.LLM.chat", lambda self, *a, **k: sent.append(1))
    r = runner.invoke(
        app,
        [
            "run",
            "-m",
            "mistral:ministral-8b-latest",
            "-s",
            f"tiny={sys.executable} {TINY}",
            "--intents",
            str(tmp_path / "i.yaml"),
            "--out",
            str(tmp_path),
        ],
    )
    assert r.exit_code == 1, r.output
    assert "tiny (2 tools) from --server" in r.output and "mistral" in r.output and "--yes" in r.output
    assert sent == [] and not (tmp_path / "i.yaml").exists()


def test_intents_cloud_needs_confirmation(monkeypatch, tmp_path):
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    sent = []
    monkeypatch.setattr("toolbloat.llm.LLM.chat", lambda self, *a, **k: sent.append(1))
    r = runner.invoke(
        app,
        [
            "intents",
            "-m",
            "mistral:ministral-8b-latest",
            "-s",
            f"tiny={sys.executable} {TINY}",
            "--intents",
            str(tmp_path / "i.yaml"),
        ],
    )
    assert r.exit_code == 1 and "--yes" in r.output and sent == []


def test_confirmation_names_the_config_file(monkeypatch, tmp_path):
    import json

    cfg = tmp_path / ".mcp.json"
    cfg.write_text(json.dumps({"mcpServers": {"tiny": {"command": sys.executable, "args": [TINY]}}}))
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    monkeypatch.setattr("toolbloat.llm.LLM.chat", lambda self, *a, **k: (_ for _ in ()).throw(AssertionError))
    r = runner.invoke(
        app, ["intents", "-m", "mistral:ministral-8b-latest", "-c", str(cfg), "--intents", str(tmp_path / "i.yaml")]
    )
    assert r.exit_code == 1
    assert "tiny (2 tools) from" in r.output and ".mcp.json" in r.output.replace("\n", "")


def test_token_budget_and_markdown(tmp_path):
    md = tmp_path / "summary.md"
    ok = runner.invoke(
        app, ["tokens", "-s", f"tiny={sys.executable} {TINY}", "--max-tokens", "100000", "--markdown", str(md)]
    )
    assert ok.exit_code == 0, ok.output
    assert "within budget" in ok.output
    text = md.read_text()
    assert "| tiny |" in text and "within budget" in text
    over = runner.invoke(app, ["tokens", "-s", f"tiny={sys.executable} {TINY}", "--max-tokens", "1"])
    assert over.exit_code == 2
    assert "over budget" in over.output


def test_token_budget_fails_when_a_server_cannot_be_measured():
    r = runner.invoke(app, ["tokens", "-s", "definitely-not-a-command-xyz", "--max-tokens", "100000"])
    assert r.exit_code == 1
