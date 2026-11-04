"""Rich terminal summary."""

from __future__ import annotations

from rich.console import Console
from rich.table import Table

from .metrics import compute


def pct(x: float | None) -> str:
    return "-" if x is None else f"{x * 100:.0f}%"


def fmt_int(n: float | int | None) -> str:
    return "-" if n is None else f"{n:,.0f}"


def fmt_big(n: float) -> str:
    if n >= 1e9:
        return f"{n / 1e9:.1f}B"
    if n >= 1e6:
        return f"{n / 1e6:.1f}M"
    if n >= 1e3:
        return f"{n / 1e3:.1f}k"
    return f"{n:.0f}"


def print_tokens(console: Console, results: dict) -> None:
    m = compute(results)
    t = Table(title="Tokens per server (o200k, approx.)", title_justify="left")
    t.add_column("server")
    t.add_column("tools", justify="right")
    t.add_column("tokens", justify="right")
    t.add_column("share", justify="right")
    for s in m["per_server"]:
        t.add_row(s["name"], str(s["tools"]), fmt_int(s["tokens"]), pct(s["share"]))
    t.add_section()
    t.add_row("[bold]total[/]", f"[bold]{m['n_tools']}[/]", f"[bold]{fmt_int(m['total_tokens'])}[/]", "100%")
    console.print(t)
    console.print(
        f"Every turn re-sends ~[bold]{fmt_int(m['total_tokens'])}[/] tokens of tool definitions:"
        f" ~[bold]{fmt_big(m['monthly_tokens'])}[/] tokens/month at {m['turns_per_day']} turns/day"
        " (before prompt caching)."
    )


def markdown_tokens(results: dict, max_tokens: int | None = None) -> str:
    """Markdown version of the tokens table, for CI job summaries and PR comments."""
    m = compute(results)
    total = m["total_tokens"]
    lines = ["## toolbloat: MCP tool definition tokens", ""]
    if max_tokens is not None:
        verdict = "over budget" if total > max_tokens else "within budget"
        lines += [f"**{fmt_int(total)}** of **{fmt_int(max_tokens)}** tokens: {verdict}.", ""]
    lines += ["| server | tools | tokens | share |", "|---|---:|---:|---:|"]
    for s in m["per_server"]:
        lines.append(f"| {s['name']} | {s['tools']} | {fmt_int(s['tokens'])} | {pct(s['share'])} |")
    lines.append(f"| **total** | **{m['n_tools']}** | **{fmt_int(total)}** | 100% |")
    failed = [s["name"] for s in results["servers"] if s.get("error")]
    if failed:
        lines += ["", f"Could not measure: {', '.join(failed)}."]
    lines += [
        "",
        f"Every turn re-sends ~{fmt_int(total)} tokens of tool definitions"
        f" (~{fmt_big(m['monthly_tokens'])} tokens/month at {m['turns_per_day']} turns/day, before prompt caching)."
        " Tokenizer: o200k_base, approximate.",
        "",
    ]
    return "\n".join(lines)


def print_summary(console: Console, results: dict) -> None:
    m = compute(results)
    print_tokens(console, results)
    if not m["models"]:
        return
    t = Table(title="Tool selection accuracy (alone vs. whole stack)", title_justify="left")
    t.add_column("model", no_wrap=True)
    t.add_column("alone", justify="right")
    t.add_column("stacked", justify="right")
    t.add_column("change", justify="right", no_wrap=True)
    t.add_column("broken", justify="right")
    t.add_column("args ok", justify="right")
    t.add_column("$/month", justify="right")
    for r in m["models"]:
        d = r["delta"]
        dtxt = "-" if d is None else f"{d * 100:+.0f} pts"
        color = "red" if d is not None and d < -0.02 else "green" if d is not None and d > 0.02 else "white"
        partial = min(r["solo_n"], r["stack_n"])
        t.add_row(
            r["id"] + (f" [dim]({partial}/{m['n_intents']})[/]" if partial < m["n_intents"] else ""),
            pct(r["solo_acc"]),
            pct(r["stack_acc"]),
            f"[{color}]{dtxt}[/]",
            str(r["broken_by_stack"]),
            pct(r["stack_args_valid"]),
            "local" if r["id"].startswith("ollama:") else f"${r['monthly_usd']:,.2f}",
        )
    console.print(t)
    console.print(
        "[dim]broken = right alone, wrong with the whole stack · args ok = correct calls whose arguments"
        " match the JSON Schema[/]"
    )
    if m["confusions"]:
        c = Table(title="Most common wrong picks with the whole stack", title_justify="left")
        c.add_column("expected")
        c.add_column("model chose")
        c.add_column("times", justify="right")
        for row in m["confusions"][:8]:
            mark = " [magenta](other server)[/]" if row["cross_server"] else ""
            c.add_row(row["expected"], row["chosen"] + mark, str(row["count"]))
        console.print(c)
    if m["never_chosen"]:
        names = ", ".join(n["tool"] for n in m["never_chosen"][:10])
        more = "" if len(m["never_chosen"]) <= 10 else f" and {len(m['never_chosen']) - 10} more"
        console.print(
            f"[bold]{len(m['never_chosen'])}[/] tool(s) were never chosen with the whole stack: {names}{more}."
            f" Removing them saves ~[bold]{fmt_int(m['never_tokens'])}[/] tokens per turn"
            f" (~{fmt_big(m['never_monthly_tokens'])}/month)."
        )
    for r in m["models"]:
        if r["truncated"]:
            console.print(
                f"[yellow]warning:[/] {r['id']} billed only ~{fmt_int(r['stack_prompt_tokens'])} prompt tokens for a"
                f" stack of ~{fmt_int(m['total_tokens'])}; its context window probably cut the tool list."
                " For Ollama, use a model with a larger num_ctx (see README)."
            )
    if m["untested"]:
        console.print(f"[yellow]{len(m['untested'])} tool(s) had no request to test:[/] {', '.join(m['untested'])}")
    if m["total_spend"]:
        console.print(f"[dim]API cost of the calls in this report: ${m['total_spend']:.4f}[/]")
