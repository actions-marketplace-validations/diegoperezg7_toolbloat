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
