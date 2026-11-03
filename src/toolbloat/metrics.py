"""Turn a results file into the numbers the report shows."""

from __future__ import annotations

from collections import Counter, defaultdict
from statistics import mean

NONE = "(no call)"
UNKNOWN = "(unknown tool)"


def _server_of(chosen: str | None) -> str:
    if chosen is None:
        return NONE
    if chosen.startswith("?:"):
        return UNKNOWN
    return chosen.split("/", 1)[0]


def _rate(num: int, den: int) -> float | None:
    return num / den if den else None


def compute(results: dict) -> dict:
    servers = [s for s in results["servers"] if not s.get("error")]
    tools = [(s["name"], t) for s in servers for t in s["tools"]]
    tool_tokens = {f"{srv}/{t['name']}": t["tokens"] for srv, t in tools}
    total_tokens = sum(tool_tokens.values())
    intents = {i["id"]: i for i in results["intents"]}
    calls = [c for c in results["calls"] if c["intent_id"] in intents]
    turns = results.get("settings", {}).get("turns_per_day", 100)
    days = 30

    # Per model, per condition
    models = []
    by_model = defaultdict(list)
    for c in calls:
        by_model[c["model"]].append(c)
    prices = {m["id"]: (m.get("price_in", 0.0), m.get("price_out", 0.0)) for m in results["models"]}
    for m in results["models"]:
        mid = m["id"]
        cs = by_model.get(mid, [])
        row = {"id": mid, "price_in": prices[mid][0], "calls": len(cs), "cost": sum(c["cost"] for c in cs)}
        for cond in ("solo", "stack"):
            cc = [c for c in cs if c["condition"] == cond and not c.get("error")]
            row[f"{cond}_n"] = len(cc)
            row[f"{cond}_acc"] = _rate(sum(c["correct"] for c in cc), len(cc))
            row[f"{cond}_errors"] = sum(1 for c in cs if c["condition"] == cond and c.get("error"))
            row[f"{cond}_prompt_tokens"] = mean([c["prompt_tokens"] for c in cc]) if cc else None
            row[f"{cond}_no_call"] = _rate(sum(c["chosen"] is None for c in cc), len(cc))
            tc = [c for c in cc if c["args_valid"] is not None and c["correct"]]
            row[f"{cond}_args_valid"] = _rate(sum(c["args_valid"] for c in tc), len(tc))
        st = [c for c in cs if c["condition"] == "stack" and not c.get("error")]
        row["stack_server_acc"] = _rate(
            sum(_server_of(c["chosen"]) == intents[c["intent_id"]]["tool"].split("/")[0] for c in st), len(st)
        )
        row["delta"] = (
            row["stack_acc"] - row["solo_acc"] if row["stack_acc"] is not None and row["solo_acc"] is not None else None
        )
        # Intents the model got right alone but wrong once every server is loaded.
        solo_ok = {c["intent_id"] for c in cs if c["condition"] == "solo" and c["correct"]}
        row["broken_by_stack"] = sum(1 for c in st if c["intent_id"] in solo_ok and not c["correct"])
        row["cross_server_errors"] = sum(
            1
            for c in st
            if not c["correct"]
            and _server_of(c["chosen"]) not in (NONE, UNKNOWN, intents[c["intent_id"]]["tool"].split("/")[0])
        )
        # A provider that bills fewer prompt tokens than the definitions alone
        # probably truncated the context (Ollama's default window is small).
        spt = row["stack_prompt_tokens"]
        row["truncated"] = bool(spt is not None and total_tokens and spt < 0.7 * total_tokens)
        monthly = total_tokens * turns * days
        row["monthly_tokens"] = monthly
        row["monthly_usd"] = monthly * prices[mid][0] / 1_000_000
        models.append(row)

    # Cross-server confusion matrix, stack condition, all models
    names = [s["name"] for s in servers]
    cols = names + [NONE, UNKNOWN]
    matrix = {r: Counter() for r in names}
    per_model_matrix = {}
    for c in calls:
        if c["condition"] != "stack" or c.get("error"):
            continue
        exp = intents[c["intent_id"]]["tool"].split("/")[0]
        if exp not in matrix:
            continue
        got = _server_of(c["chosen"])
        matrix[exp][got] += 1
        per_model_matrix.setdefault(c["model"], {r: Counter() for r in names})[exp][got] += 1
    used_cols = [col for col in cols if col in names or any(matrix[r][col] for r in names)]

    # Wrong picks in the stack condition, grouped by (expected tool, chosen tool)
    pairs = Counter()
    pair_models = defaultdict(set)
    for c in calls:
        if c["condition"] == "stack" and not c["correct"] and not c.get("error"):
            exp = intents[c["intent_id"]]["tool"]
            got = c["chosen"] or NONE
            pairs[(exp, got)] += 1
            pair_models[(exp, got)].add(c["model"])
    confusions = [
        {
            "expected": e,
            "chosen": g,
            "count": n,
            "models": sorted(pair_models[(e, g)]),
            "cross_server": _server_of(g) not in (NONE, UNKNOWN, e.split("/")[0]),
        }
        for (e, g), n in pairs.most_common()
    ]

    # Tools never chosen by any model in the stack condition
    chosen_stack = {c["chosen"] for c in calls if c["condition"] == "stack" and c["chosen"]}
    stack_calls = sum(1 for c in calls if c["condition"] == "stack")
    tested = {intents[c["intent_id"]]["tool"] for c in calls if c["condition"] == "stack"}
    untested = sorted(k for k in tool_tokens if k not in tested)
    never = (
        [
            {"tool": k, "tokens": v}
            for k, v in sorted(tool_tokens.items(), key=lambda kv: -kv[1])
            if k not in chosen_stack and k in tested
        ]
        if stack_calls
        else []
    )
    never_tokens = sum(n["tokens"] for n in never)

    per_server = sorted(
        (
            {
                "name": s["name"],
                "command": s["command"],
                "tools": len(s["tools"]),
                "tokens": sum(t["tokens"] for t in s["tools"]),
                "share": (sum(t["tokens"] for t in s["tools"]) / total_tokens) if total_tokens else 0,
            }
            for s in servers
        ),
        key=lambda r: -r["tokens"],
    )
    heaviest_tools = sorted(({"tool": k, "tokens": v} for k, v in tool_tokens.items()), key=lambda r: -r["tokens"])

    # Per-intent outcome table (compact): for each intent, per model solo/stack result
    outcome = defaultdict(dict)
    for c in calls:
        outcome[c["intent_id"]][(c["model"], c["condition"])] = c
    intent_rows = []
    for iid, i in intents.items():
        if i.get("skip"):
            continue
        cells = {}
        for m in results["models"]:
            for cond in ("solo", "stack"):
                c = outcome[iid].get((m["id"], cond))
                cells[f"{m['id']}|{cond}"] = (
                    None if c is None else {"ok": c["correct"], "chosen": c["chosen"], "error": bool(c.get("error"))}
                )
        intent_rows.append({"id": iid, "tool": i["tool"], "text": i["text"], "cells": cells})

    return {
        "total_tokens": total_tokens,
        "n_tools": len(tools),
        "n_servers": len(servers),
        "failed_servers": [s for s in results["servers"] if s.get("error")],
        "n_intents": sum(1 for i in intents.values() if not i.get("skip")),
        "per_server": per_server,
        "heaviest_tools": heaviest_tools,
        "models": models,
        "matrix": {"rows": names, "cols": used_cols, "cells": {r: dict(matrix[r]) for r in names}},
        "per_model_matrix": {m: {r: dict(v) for r, v in mm.items()} for m, mm in per_model_matrix.items()},
        "confusions": confusions,
        "never_chosen": never,
        "never_tokens": never_tokens,
        "untested": untested if stack_calls else [],
        "never_monthly_tokens": never_tokens * turns * days,
        "turns_per_day": turns,
        "monthly_tokens": total_tokens * turns * days,
        "intent_rows": intent_rows,
        "spend": results.get("spend", {}),
        # Cost of every call in this report, including ones reused from the cache.
        "total_spend": sum(c["cost"] for c in calls),
    }
