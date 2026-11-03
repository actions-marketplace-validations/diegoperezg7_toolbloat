"""Static HTML report (single file, no external scripts)."""

from __future__ import annotations

from importlib import resources
from pathlib import Path

from jinja2 import Environment, select_autoescape
from markupsafe import Markup, escape

from .metrics import NONE, UNKNOWN, compute


def _env() -> Environment:
    env = Environment(autoescape=select_autoescape(["html", "j2"]), trim_blocks=True, lstrip_blocks=True)
    env.filters["wbr"] = lambda x: Markup("/<wbr>").join(escape(p) for p in str(x).split("/"))
    env.filters["pct"] = lambda x: "–" if x is None else f"{x * 100:.0f}%"
    env.filters["num"] = lambda x: "–" if x is None else f"{x:,.0f}"
    env.filters["big"] = _big
    env.filters["usd"] = lambda x: f"${x:,.2f}" if x >= 0.01 or x == 0 else f"${x:,.4f}"
    env.filters["pts"] = lambda x: "–" if x is None else f"{x * 100:+.0f}"
    return env


def _big(n: float) -> str:
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
        if n >= div:
            return f"{n / div:.1f}{suf}"
    return f"{n:.0f}"


def render(results: dict) -> str:
    m = compute(results)
    tpl = resources.files("toolbloat").joinpath("templates/report.html.j2").read_text(encoding="utf-8")
    worst = None
    rated = [r for r in m["models"] if r["delta"] is not None]
    if rated:
        worst = min(rated, key=lambda r: r["delta"])
    max_cell = max([v for row in m["matrix"]["cells"].values() for v in row.values()] or [1])
    return (
        _env()
        .from_string(tpl)
        .render(
            r=results,
            m=m,
            worst=worst,
            max_cell=max_cell,
            NONE=NONE,
            UNKNOWN=UNKNOWN,
            max_server_tokens=max([s["tokens"] for s in m["per_server"]] or [1]),
            recorded=results.get("recorded", False),
        )
    )


def write_report(results: dict, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(results), encoding="utf-8")
    return path
