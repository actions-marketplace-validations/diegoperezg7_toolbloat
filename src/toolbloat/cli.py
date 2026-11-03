"""toolbloat command line."""

from __future__ import annotations

import json
import platform
import subprocess
import sys
import webbrowser
from dataclasses import asdict
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from . import __version__
from .errors import ToolbloatError

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    rich_markup_mode="rich",
    help="See what your MCP servers cost in tokens on every turn, and whether stacking them"
    " makes your model pick the wrong tool.",
)
console = Console()
err = Console(stderr=True)

DEFAULT_MODELS = ["ollama:qwen3:4b"]


def _fail(e: Exception) -> None:
    err.print(f"[bold red]error:[/] {e}")
    raise typer.Exit(1)


def _version(value: bool) -> None:
    if value:
        console.print(f"toolbloat {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool, typer.Option("--version", callback=_version, is_eager=True, help="Show the version.")
    ] = False,
) -> None:
    pass


ConfigOpt = Annotated[
    list[Path] | None,
    typer.Option("--config", "-c", help="MCP config file (.mcp.json, Claude Desktop, Cursor). Repeatable."),
]
ServerOpt = Annotated[
    list[str] | None,
    typer.Option("--server", "-s", help='A server to start: "cmd args" or "name=cmd args". Repeatable.'),
]


def _collect_servers(configs: list[Path] | None, servers: list[str] | None):
    from .config import dedupe, default_config_paths, load_config_file, parse_server_flag, skipped_remote

    specs = []
    if servers:
        specs += [parse_server_flag(v, i) for i, v in enumerate(servers)]
    paths = list(configs or [])
    if not specs and not paths:
        paths = [p for p in default_config_paths() if p.exists()]
        if not paths:
            raise ToolbloatError(
                "No MCP config found (.mcp.json, .cursor/mcp.json, Claude Desktop). Pass --config PATH"
                ' or --server "uvx mcp-server-time". Want to see a result first? Run `toolbloat demo`.'
            )
    for p in paths:
        if not p.exists():
            raise ToolbloatError(f"Config file not found: {p}")
        found = load_config_file(p)
        console.print(f"[dim]config[/] {p}  [dim]({len(found)} stdio server(s))[/]")
        remote = skipped_remote(p)
        if remote:
            console.print(f"[yellow]skipped[/] {', '.join(remote)}: HTTP/SSE transports are not supported yet")
        specs += found
    specs = dedupe(specs)
    if not specs:
        raise ToolbloatError("The config files list no stdio MCP servers.")
    return specs


def _discover(configs, servers, timeout):
    from .discover import list_tools
    from .tokens import annotate

    specs = _collect_servers(configs, servers)
    with console.status(f"Starting {len(specs)} MCP server(s) and listing tools (tools are never called)..."):
        results = list_tools(specs, timeout=timeout)
    for r in results:
        annotate(r.tools)
        if r.error:
            console.print(f"[red]failed[/] {r.name} ({r.command}): {r.error}")
    return results


def _confirm_cloud(specs, servers, yes: bool) -> None:
    """Before anything leaves the machine, say which servers go to which provider and ask."""
    cloud = sorted({s.provider for s in specs if s.provider != "ollama"})
    if not cloud:
        return
    sent = [r for r in servers if not r.error and r.tools]
    console.print(
        f"[yellow]privacy:[/] the tool definitions (names, descriptions, JSON Schemas) of these"
        f" {len(sent)} server(s) and the test requests will be sent to [bold]{', '.join(cloud)}[/]:"
    )
    for r in sent:
        console.print(f"  - {r.name} ({len(r.tools)} tools): {r.command}")
    console.print("[dim]  Nothing else leaves this machine: no env values, no files, and no tool is ever executed.[/]")
    if yes:
        return
    if not sys.stdin.isatty():
        raise ToolbloatError(
            "Refusing to send tool definitions to a cloud provider without confirmation."
            " Re-run with --yes to accept, or use a local model (ollama:...)."
        )
    if not typer.confirm("Send them?", default=False):
        raise ToolbloatError("Cancelled: nothing was sent.")


def _hardware() -> str:
    parts = [platform.system(), platform.machine()]
    if sys.platform == "darwin":
        try:
            chip = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True, timeout=5
            ).stdout.strip()
            mem = int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=5).stdout)
            parts = [chip or parts[1], f"{mem // 2**30} GB RAM", f"macOS {platform.mac_ver()[0]}"]
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    return ", ".join(p for p in parts if p)


def _server_dict(r) -> dict:
    return {
        "name": r.name,
        "command": r.command,
        "server_version": r.server_version,
        "error": r.error,
        "tools": [
            {"name": t.name, "description": t.description, "input_schema": t.input_schema, "tokens": t.tokens}
            for t in r.tools
        ],
    }


@app.command()
def tokens(
    config: ConfigOpt = None,
    server: ServerOpt = None,
    turns_per_day: Annotated[int, typer.Option(help="Turns per day for the monthly projection.")] = 100,
    json_out: Annotated[bool, typer.Option("--json", help="Print JSON instead of tables.")] = False,
    timeout: Annotated[float, typer.Option(help="Seconds to wait for each server.")] = 90,
) -> None:
    """Count the tokens your MCP tool definitions add to every turn. No model calls."""
    from .terminal import print_tokens

    try:
        results = _discover(config, server, timeout)
    except ToolbloatError as e:
        _fail(e)
    data = {
        "servers": [_server_dict(r) for r in results],
        "settings": {"turns_per_day": turns_per_day},
        "intents": [],
        "models": [],
        "calls": [],
    }
    if json_out:
        console.print_json(json.dumps({"tokenizer": "o200k_base (approximate)", **data}))
        return
    print_tokens(console, data)


@app.command()
def run(
    model: Annotated[
        list[str] | None,
        typer.Option(
            "--model", "-m", help="provider:model, e.g. ollama:qwen3:4b, mistral:mistral-small-latest. Repeatable."
        ),
    ] = None,
    config: ConfigOpt = None,
    server: ServerOpt = None,
    intents: Annotated[Path, typer.Option(help="Intents YAML (created if missing, then reused).")] = Path(
        ".toolbloat/intents.yaml"
    ),
    intent_model: Annotated[
        str | None, typer.Option(help="Model that writes missing intents (default: first --model).")
    ] = None,
    intents_per_tool: Annotated[int, typer.Option(min=1, max=10, help="Intents to generate per tool.")] = 1,
    out: Annotated[Path, typer.Option(help="Output directory for results.json and report.html.")] = Path(
        "toolbloat-report"
    ),
    turns_per_day: Annotated[int, typer.Option(help="Turns per day for the monthly projection.")] = 100,
    max_cost: Annotated[float, typer.Option(help="Stop before spending more than this many USD.")] = 1.0,
    price: Annotated[
        list[str] | None,
        typer.Option(help="Price for a model without a built-in one: provider:model=IN,OUT (USD per 1M tokens)."),
    ] = None,
    timeout: Annotated[float, typer.Option(help="Seconds to wait for each server.")] = 90,
    cached_only: Annotated[
        bool, typer.Option("--cached-only", help="Make no model calls: build the report from cached answers only.")
    ] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Do not ask before sending data to a cloud API.")] = False,
    open_report: Annotated[bool, typer.Option("--open/--no-open", help="Open the HTML report.")] = False,
) -> None:
    """Measure tokens and tool-selection accuracy per model, alone and stacked."""
    from . import evaluate
    from . import intents as intents_mod
    from .llm import LLM, PRICES, Budget, ModelSpec
    from .report import write_report
    from .terminal import print_summary

    try:
        for p in price or []:
            mid, _, vals = p.partition("=")
            pin, _, pout = vals.partition(",")
            try:
                PRICES[ModelSpec.parse(mid).id] = (float(pin), float(pout))
            except ValueError as e:
                raise ToolbloatError(f"--price '{p}' must look like provider:model=IN,OUT") from e
        specs = [ModelSpec.parse(m) for m in (model or DEFAULT_MODELS)]
        for s in specs:
            if not s.has_known_price():
                raise ToolbloatError(
                    f"No price known for {s.id}, so --max-cost cannot protect you. Add"
                    f" --price {s.id}=IN,OUT (USD per million tokens)."
                )
        budget = Budget(max_cost=max_cost)
        llms = [LLM(s, budget) for s in specs]
        if not cached_only:
            for llm in llms:
                llm.preflight()
        results_servers = _discover(config, server, timeout)
        tools = [t for r in results_servers for t in r.tools]
        if not tools:
            raise ToolbloatError("The servers started but exposed no tools.")

        gen = LLM(ModelSpec.parse(intent_model), budget) if intent_model else llms[0]
        if not cached_only:
            _confirm_cloud([*specs, gen.spec], results_servers, yes)
        with console.status("Preparing intents...") as status:
            items = intents_mod.ensure(
                intents,
                tools,
                gen,
                intents_per_tool,
                on_progress=lambda n, total, key: status.update(f"Writing intents {n}/{total}: {key}"),
            )
        console.print(f"[dim]intents[/] {intents} ({len(items)} total, edit it and rerun to change them)")

        cache = evaluate.Cache(out / "calls.jsonl")
        total = evaluate.total_calls(llms, tools, items)
        done = {"n": 0}
        with console.status(f"Asking models which tool they would call (0/{total})...") as status:

            def tick(c):
                done["n"] += 1
                status.update(
                    f"Asking models which tool they would call ({done['n']}/{total}) · spent ${budget.total:.4f}"
                )

            calls, stopped = evaluate.run(llms, tools, items, cache=cache, on_call=tick, cached_only=cached_only)
        if stopped and not calls:
            raise ToolbloatError(stopped)
    except ToolbloatError as e:
        _fail(e)

    results = {
        "schema_version": 1,
        "tool": "toolbloat",
        "version": __version__,
        "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "environment": {"hardware": _hardware(), "python": platform.python_version()},
        "tokenizer": "o200k_base",
        "servers": [_server_dict(r) for r in results_servers],
        "intents": [asdict(i) for i in items],
        "intents_meta": intents_mod.load(intents)[1],
        "models": [{"id": s.id, "price_in": s.price()[0], "price_out": s.price()[1]} for s in specs],
        "calls": [asdict(c) for c in calls],
        "settings": {"turns_per_day": turns_per_day, "max_cost": max_cost},
        "spend": {k: round(v, 6) for k, v in budget.spent.items()},
        "stopped": stopped,
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")
    html = write_report(results, out / "report.html")
    print_summary(console, results)
    if stopped:
        err.print(f"[bold yellow]stopped early:[/] {stopped}")
    console.print(f"\n[bold]Report:[/] {html}\n[dim]Data:[/]   {out / 'results.json'}")
    if open_report:
        webbrowser.open(html.resolve().as_uri())
    if stopped and not calls:
        raise typer.Exit(1)


@app.command(name="intents")
def intents_cmd(
    model: Annotated[str, typer.Option("--model", "-m", help="Model that writes the requests.")] = DEFAULT_MODELS[0],
    config: ConfigOpt = None,
    server: ServerOpt = None,
    intents: Annotated[Path, typer.Option(help="Intents YAML to create or complete.")] = Path(
        ".toolbloat/intents.yaml"
    ),
    intents_per_tool: Annotated[int, typer.Option(min=1, max=10)] = 1,
    max_cost: Annotated[float, typer.Option(help="Stop before spending more than this many USD.")] = 1.0,
    timeout: Annotated[float, typer.Option(help="Seconds to wait for each server.")] = 90,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Do not ask before sending data to a cloud API.")] = False,
) -> None:
    """Write (or complete) the intents YAML without running the evaluation, so you can review it first."""
    from . import intents as intents_mod
    from .llm import LLM, Budget, ModelSpec

    try:
        budget = Budget(max_cost=max_cost)
        gen = LLM(ModelSpec.parse(model), budget)
        found = _discover(config, server, timeout)
        tools = [t for r in found for t in r.tools]
        _confirm_cloud([gen.spec], found, yes)
        with console.status("Writing intents...") as status:
            items = intents_mod.ensure(
                intents,
                tools,
                gen,
                intents_per_tool,
                on_progress=lambda n, total, key: status.update(f"Writing intents {n}/{total}: {key}"),
            )
    except ToolbloatError as e:
        _fail(e)
    missing = sorted({t.key for t in tools} - {i.tool for i in items})
    console.print(f"{len(items)} intents in {intents} · spent ${budget.total:.4f}")
    if missing:
        console.print(f"[yellow]no intent yet for:[/] {', '.join(missing)} (rerun to retry)")


@app.command()
def report(
    results_file: Annotated[Path, typer.Argument(help="results.json written by `toolbloat run`.")],
    out: Annotated[Path | None, typer.Option(help="HTML file to write (default: next to results).")] = None,
    open_report: Annotated[bool, typer.Option("--open/--no-open")] = False,
) -> None:
    """Rebuild the HTML report and terminal summary from a results.json."""
    from .report import write_report
    from .terminal import print_summary

    try:
        results = json.loads(results_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        _fail(ToolbloatError(f"Cannot read {results_file}: {e}"))
    html = write_report(results, out or results_file.with_name("report.html"))
    print_summary(console, results)
    console.print(f"\n[bold]Report:[/] {html}")
    if open_report:
        webbrowser.open(html.resolve().as_uri())


@app.command()
def demo(
    out: Annotated[Path | None, typer.Option(help="Where to write the demo report HTML.")] = None,
    open_report: Annotated[bool, typer.Option("--open/--no-open", help="Open the HTML report in a browser.")] = False,
) -> None:
    """Show a RECORDED real run on five MCP reference servers. No keys, no setup."""
    from .report import write_report
    from .terminal import print_summary

    data = resources.files("toolbloat").joinpath("data/demo/results.json").read_text(encoding="utf-8")
    results = json.loads(data)
    results["recorded"] = True
    console.print(
        f"[bold magenta]Recorded run[/] from {results['created_at'][:10]} · toolbloat {results['version']} ·"
        f" {results['environment']['hardware']}\n[dim]This replays a real `toolbloat run`; nothing is executed"
        " now. Measure your own stack with `toolbloat run`.[/]"
    )
    print_summary(console, results)
    target = out or Path("toolbloat-demo") / "report.html"
    html = write_report(results, target)
    console.print(f"\n[bold]HTML report:[/] {html}")
    if open_report:
        webbrowser.open(html.resolve().as_uri())


def entry() -> None:
    try:
        app()
    except KeyboardInterrupt:
        err.print("interrupted")
        sys.exit(130)


if __name__ == "__main__":
    entry()
