"""
Reporter — render Metrics to a single human-readable markdown file.

Layout:
  - Header (when / where the data came from)
  - Repository profile  (static, functional facts: principles, tools, tests)
  - LLM comparison summary  (one row per model, all key metrics)
  - Per-model deep dive  (tokens, tool reliability, commits)
  - Event-type histogram  (sanity check on the log)
"""

from __future__ import annotations

import datetime
import pathlib
from typing import Iterable, List

from extensions.metrics.collector import CategoryStats, Metrics, ModelMetrics, RoleConfig

# Stable display order for the agent's purpose categories.
_CATEGORY_ORDER = ("task", "evolution", "consciousness", "review", "summarize", "other")


def _fmt_int(n: int) -> str:
    return f"{n:,}"


def _fmt_float(x: float, digits: int = 2) -> str:
    return f"{x:,.{digits}f}"


def _pct(num: int, denom: int) -> str:
    if not denom:
        return "—"
    return f"{(100.0 * num / denom):.1f}%"


def _table(headers: List[str], rows: Iterable[List[str]]) -> str:
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join(["---"] * len(headers)) + "|"]
    for r in rows:
        out.append("| " + " | ".join(r) + " |")
    return "\n".join(out)


def _summary_row(m: ModelMetrics) -> List[str]:
    t, tl, c = m.tokens, m.tools, m.commits
    return [
        m.model,
        _fmt_int(t.requests),
        _fmt_int(t.tasks),
        _fmt_int(t.prompt_tokens),
        _fmt_int(t.completion_tokens),
        _fmt_int(t.cached_tokens),
        _fmt_int(t.prompt_tokens + t.completion_tokens),
        _fmt_float(m.avg_prompt_per_req, 0),
        _fmt_float(m.avg_completion_per_req, 0),
        _fmt_float(t.cost_usd, 4),
        _fmt_int(tl.calls),
        _pct(tl.ok, tl.calls),
        _fmt_int(tl.hallucinated),
        _fmt_int(tl.arg_errors),
        _fmt_int(tl.runtime_errors + tl.timeouts),
        _fmt_int(c.ok),
        _fmt_int(c.failed),
    ]


def render_markdown(m: Metrics) -> str:
    lines: List[str] = []
    push = lines.append

    push("# Ouroboros — LLM Comparison Metrics")
    push("")
    push(f"- Generated: `{m.generated_at}`")
    push(f"- Repo: `{m.repo_root}`")
    push(f"- Logs: `{m.logs_root}`")
    push(f"- Events parsed: {_fmt_int(m.events_parsed)} "
         f"(malformed: {m.events_malformed}) — "
         f"Tool entries parsed: {_fmt_int(m.tools_parsed)} "
         f"(malformed: {m.tools_malformed})")
    push("")

    # --- Repository profile (functional, static) ---
    push("## 1. Repository profile (functional structure)")
    push("")
    rp = m.repo
    push(_table(
        ["Item", "Count"],
        [
            ["BIBLE.md principles", _fmt_int(rp.bible_principles)],
            ["Always-on (core) tools", _fmt_int(rp.always_on_tools)],
            ["Registered tools (total)", _fmt_int(rp.total_registered_tools)],
            ["Dynamically-enabled tools", _fmt_int(rp.dynamic_tools)],
            ["Test files", _fmt_int(len(rp.test_files))],
            ["Test functions (total)", _fmt_int(sum(rp.test_files.values()))],
        ],
    ))
    push("")
    if rp.bible_principle_titles:
        push("**Principles (BIBLE.md):**")
        for i, title in enumerate(rp.bible_principle_titles):
            push(f"  {i}. {title}")
        push("")
    if rp.test_files:
        push("**Test files:**")
        push("")
        push(_table(
            ["File", "test_* functions"],
            [[f, _fmt_int(c)] for f, c in sorted(rp.test_files.items())],
        ))
        push("")
    if rp.always_on_tool_names:
        push("<details><summary>Always-on tool names</summary>")
        push("")
        push(", ".join(f"`{n}`" for n in rp.always_on_tool_names))
        push("")
        push("</details>")
        push("")
    if rp.registered_tool_names:
        dyn = sorted(set(rp.registered_tool_names) - set(rp.always_on_tool_names))
        push("<details><summary>Dynamically-enabled tool names</summary>")
        push("")
        push(", ".join(f"`{n}`" for n in dyn) if dyn else "_(none)_")
        push("")
        push("</details>")
        push("")

    # --- Per-model comparison table ---
    push("## 2. LLM comparison summary (non-functional + outcomes)")
    push("")
    headers = [
        "Model",
        "LLM reqs",
        "Tasks",
        "Prompt tok",
        "Completion tok",
        "Cached tok",
        "Total tok",
        "Avg in/req",
        "Avg out/req",
        "Cost (USD)",
        "Tool calls",
        "Tool OK %",
        "Hallucinated",
        "Arg errors",
        "Runtime+timeout",
        "Commits OK",
        "Commits FAILED",
    ]
    if not m.models:
        push("_No `llm_usage` events found in events.jsonl. "
             "Run the agent first or check the --logs path._")
        push("")
    else:
        rows = [_summary_row(m.totals)] + [
            _summary_row(mm)
            for mm in sorted(m.models.values(),
                             key=lambda x: x.tokens.requests,
                             reverse=True)
        ]
        push(_table(headers, rows))
        push("")
        push("_Row `<all>` is the across-model total. "
             "Tool/commit attribution uses the dominant model per `task_id`._")
        push("")

    # --- Configured roles ---
    _render_roles_section(m.roles, m.models, lines)

    # --- Per-category breakdown + model × category matrix ---
    _render_category_section(m, lines)

    # --- Per-model deep dive ---
    if m.models:
        push("## 5. Per-model breakdown")
        push("")
        for mm in sorted(m.models.values(),
                         key=lambda x: x.tokens.requests, reverse=True):
            _render_model_section(mm, lines, m.roles)

    # --- Event-type histogram ---
    push("## 6. Event-type histogram (events.jsonl)")
    push("")
    if not m.event_type_counts:
        push("_(no events)_")
    else:
        push(_table(
            ["Event type", "Count"],
            [[et, _fmt_int(c)] for et, c in m.event_type_counts.most_common()],
        ))
    push("")

    return "\n".join(lines) + "\n"


def _ordered_categories(present: Iterable[str]) -> List[str]:
    """Stable ordering: known categories first, then anything else alphabetically."""
    present_set = set(present)
    head = [c for c in _CATEGORY_ORDER if c in present_set]
    tail = sorted(present_set - set(head))
    return head + tail


def _role_label(roles: RoleConfig, model: str) -> str:
    """Render a role tag like `main, code` for the configured roles of a model."""
    rs = roles.roles_by_model.get(model, [])
    return ", ".join(rs) if rs else "—"


def _render_roles_section(roles: RoleConfig, models: dict, out: List[str]) -> None:
    """Show what the user configured (env vars) vs whether each role was used."""
    out.append("## 3. Configured model roles")
    out.append("")
    any_role = any([roles.main, roles.code, roles.light, roles.websearch, roles.fallback])
    if not any_role:
        out.append("_No role config found. Pass `--config <file>` with `OUROBOROS_MODEL` / "
                   "`OUROBOROS_MODEL_CODE` / `OUROBOROS_MODEL_LIGHT` / "
                   "`OUROBOROS_WEBSEARCH_MODEL` / `OUROBOROS_MODEL_FALLBACK_LIST` "
                   "keys, or set the env vars before running._")
        out.append("")
        return

    def _seen(model: str) -> str:
        mm = models.get(model)
        if mm is None:
            return "not used"
        return f"{_fmt_int(mm.tokens.requests)} reqs · ${_fmt_float(mm.tokens.cost_usd, 4)}"

    rows: List[List[str]] = []
    rows.append(["main (`OUROBOROS_MODEL`)", f"`{roles.main}`" if roles.main else "—",
                 _seen(roles.main) if roles.main else "—"])
    rows.append(["code (`OUROBOROS_MODEL_CODE`)", f"`{roles.code}`" if roles.code else "—",
                 _seen(roles.code) if roles.code else "—"])
    rows.append(["light (`OUROBOROS_MODEL_LIGHT`)", f"`{roles.light}`" if roles.light else "—",
                 _seen(roles.light) if roles.light else "—"])
    rows.append(["websearch (`OUROBOROS_WEBSEARCH_MODEL`)", f"`{roles.websearch}`" if roles.websearch else "—",
                 _seen(roles.websearch) if roles.websearch else "—"])
    for i, fm in enumerate(roles.fallback):
        rows.append([f"fallback #{i+1}", f"`{fm}`", _seen(fm)])
    out.append(_table(["Role", "Configured model", "Actual usage"], rows))
    out.append("")
    # Flag unconfigured models that were nevertheless used (e.g. via LLM-driven switch_model)
    configured = {m for m in (roles.main, roles.code, roles.light, roles.websearch) if m}
    configured.update(roles.fallback)
    unconfigured = sorted(set(models) - configured)
    if unconfigured:
        out.append("_Models used but **not** in role config (likely set via "
                   "`switch_model` at runtime):_ "
                   + ", ".join(f"`{m}`" for m in unconfigured))
        out.append("")


def _render_category_section(m: Metrics, out: List[str]) -> None:
    """Per-purpose totals + model × category matrix."""
    out.append("## 4. Per-purpose (category) breakdown")
    out.append("")
    if not m.by_category:
        out.append("_No `llm_usage` events with a `category` field were found._")
        out.append("")
        return

    cats = _ordered_categories(m.by_category.keys())

    # 4a. Totals across all models, one row per category.
    out.append("**Totals across all models** — purpose categories come from "
               "`llm_usage.category` (set in `loop.py:_call_llm_with_retry`).")
    out.append("")
    cat_rows: List[List[str]] = []
    for cat in cats:
        cs = m.by_category[cat]
        total_t = cs.prompt_tokens + cs.completion_tokens
        cat_rows.append([
            cat,
            _fmt_int(cs.requests),
            _fmt_int(cs.prompt_tokens),
            _fmt_int(cs.completion_tokens),
            _fmt_int(cs.cached_tokens),
            _fmt_int(total_t),
            _fmt_float(cs.cost_usd, 4),
        ])
    out.append(_table(
        ["Category", "Requests", "Prompt tok", "Completion tok",
         "Cached tok", "Total tok", "Cost (USD)"],
        cat_rows,
    ))
    out.append("")

    # 4b. Model × category matrix: requests in each cell, cost beneath.
    out.append("**Model × category matrix** (cells: `requests` / `$cost`)")
    out.append("")
    header = ["Model \\ category"] + cats + ["Total reqs", "Total cost"]
    matrix_rows: List[List[str]] = []
    for mm in sorted(m.models.values(),
                     key=lambda x: x.tokens.requests, reverse=True):
        row = [f"`{mm.model}`"]
        for cat in cats:
            cs = mm.by_category.get(cat)
            if cs is None or cs.requests == 0:
                row.append("—")
            else:
                row.append(f"{_fmt_int(cs.requests)} / ${_fmt_float(cs.cost_usd, 4)}")
        row.append(_fmt_int(mm.tokens.requests))
        row.append(f"${_fmt_float(mm.tokens.cost_usd, 4)}")
        matrix_rows.append(row)
    out.append(_table(header, matrix_rows))
    out.append("")


def _render_model_section(mm: ModelMetrics, out: List[str],
                          roles: RoleConfig = RoleConfig()) -> None:
    role_tag = _role_label(roles, mm.model)
    suffix = f" — roles: {role_tag}" if role_tag != "—" else ""
    out.append(f"### `{mm.model}`{suffix}")
    out.append("")
    t, tl, c = mm.tokens, mm.tools, mm.commits

    out.append("**Non-functional**")
    out.append("")
    out.append(_table(
        ["Metric", "Value"],
        [
            ["Requests (successful)", _fmt_int(t.requests)],
            ["Empty responses", _fmt_int(t.empty_responses)],
            ["API errors", _fmt_int(t.api_errors)],
            ["Tasks (dominant model)", _fmt_int(t.tasks)],
            ["Prompt tokens (total in)", _fmt_int(t.prompt_tokens)],
            ["Completion tokens (total out)", _fmt_int(t.completion_tokens)],
            ["Cached tokens", _fmt_int(t.cached_tokens)],
            ["Cache write tokens", _fmt_int(t.cache_write_tokens)],
            ["Total tokens (in+out)", _fmt_int(t.prompt_tokens + t.completion_tokens)],
            ["Avg prompt tokens / request", _fmt_float(mm.avg_prompt_per_req, 1)],
            ["Avg completion tokens / request", _fmt_float(mm.avg_completion_per_req, 1)],
            ["Cost (USD)", _fmt_float(t.cost_usd, 4)],
        ],
    ))
    out.append("")

    if mm.by_category:
        out.append("**Usage by purpose (category)**")
        out.append("")
        cats = _ordered_categories(mm.by_category.keys())
        rows = []
        for cat in cats:
            cs = mm.by_category[cat]
            rows.append([
                cat,
                _fmt_int(cs.requests),
                _fmt_int(cs.prompt_tokens),
                _fmt_int(cs.completion_tokens),
                _fmt_int(cs.cached_tokens),
                _fmt_float(cs.cost_usd, 4),
            ])
        out.append(_table(
            ["Category", "Requests", "Prompt tok", "Completion tok",
             "Cached tok", "Cost (USD)"],
            rows,
        ))
        out.append("")

    out.append("**Functional — tool-call reliability**")
    out.append("")
    out.append(_table(
        ["Outcome", "Count", "Share"],
        [
            ["OK", _fmt_int(tl.ok), _pct(tl.ok, tl.calls)],
            ["Hallucinated tool name", _fmt_int(tl.hallucinated), _pct(tl.hallucinated, tl.calls)],
            ["Argument errors (missing/invalid)", _fmt_int(tl.arg_errors), _pct(tl.arg_errors, tl.calls)],
            ["Runtime errors", _fmt_int(tl.runtime_errors), _pct(tl.runtime_errors, tl.calls)],
            ["Timeouts", _fmt_int(tl.timeouts), _pct(tl.timeouts, tl.calls)],
            ["**Total**", _fmt_int(tl.calls), "100%" if tl.calls else "—"],
        ],
    ))
    out.append("")

    out.append("**Functional — commits**")
    out.append("")
    out.append(_table(
        ["Outcome", "Count"],
        [
            ["Successful commits (repo_commit_push / repo_write_commit → OK)", _fmt_int(c.ok)],
            ["Failed commits", _fmt_int(c.failed)],
        ],
    ))
    if c.failure_reasons:
        out.append("")
        out.append("_Failure reasons:_")
        for reason, n in c.failure_reasons.most_common():
            out.append(f"  - `{reason}` × {n}")
    out.append("")

    if tl.by_tool:
        top = tl.by_tool.most_common(10)
        out.append("<details><summary>Top tools used</summary>")
        out.append("")
        out.append(_table(
            ["Tool", "Calls"],
            [[name, _fmt_int(n)] for name, n in top],
        ))
        out.append("")
        out.append("</details>")
        out.append("")


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

def write_report(m: Metrics, out_dir: pathlib.Path) -> pathlib.Path:
    """
    Write the markdown report to <out_dir>/<UTC-timestamp>/report.md and also
    refresh <out_dir>/latest.md (a copy of the newest report). Returns the
    timestamped path.
    """
    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = out_dir / stamp
    run_dir.mkdir(parents=True, exist_ok=True)

    text = render_markdown(m)
    report = run_dir / "report.md"
    report.write_text(text, encoding="utf-8")

    latest = out_dir / "latest.md"
    latest.write_text(text, encoding="utf-8")

    return report
