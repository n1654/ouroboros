# extensions/metrics

A **read-only**, **dependency-free** extension that compares LLMs on the same
Ouroboros run.

It never imports the agent and never executes anything. It just:

1. **Statically scans the repo** for functional facts that do not depend on a
   run — tool registry, `BIBLE.md` principles, test suites.
2. **Parses the agent's existing logs** (`events.jsonl`, `tools.jsonl`, which
   the agent already writes to `<drive_root>/logs/`) and aggregates them
   per model.
3. **Writes a single markdown report** to `<out>/<UTC-timestamp>/report.md`
   plus a `latest.md` symlink-equivalent (overwritten copy).

## Run it

```bash
# from the repo root
python -m extensions.metrics \
    --repo . \
    --logs /content/drive/MyDrive/Ouroboros/logs \
    --out  metrics/
```

| Flag    | Meaning                                                 | Default |
|---------|---------------------------------------------------------|---------|
| `--repo`| Repo root for static analysis (BIBLE.md, tools, tests). | `cwd`   |
| `--logs`| Directory with `events.jsonl` and `tools.jsonl`.        | required|
| `--out` | Where reports are written.                              | `./metrics` |

The CLI prints the path of the new report. The full output goes to
`metrics/<timestamp>/report.md` and `metrics/latest.md`.

## Programmatic use

```python
from pathlib import Path
from extensions.metrics import collect, render_markdown, write_report

m = collect(repo=Path("."), logs=Path("/.../Ouroboros/logs"))
print(render_markdown(m))           # in-memory markdown
write_report(m, Path("metrics"))    # persist to disk
```

## What it measures

### Functional (structural / outcome-based)

| Metric | Source |
|---|---|
| Always-on (core) tools count | `CORE_TOOL_NAMES` set in `ouroboros/tools/registry.py` |
| Total registered tools | regex scan of `ToolEntry("…"` in `ouroboros/tools/*.py` |
| Dynamically-enabled tools | total − always-on |
| `BIBLE.md` principles count + titles | `^## Principle N: …` headings |
| Test functions per `tests/test_*.py` | regex `^def test_…` |
| Tool calls **OK** | `tools.jsonl.result_preview` does **not** start with `⚠️` |
| Tool calls **hallucinated** | result starts with `⚠️ Unknown tool` |
| Tool calls **arg errors** | result starts with `⚠️ TOOL_ARG_ERROR` |
| Tool calls **runtime errors** | result starts with `⚠️ TOOL_ERROR` |
| Tool calls **timeouts** | result starts with `⚠️ TOOL_TIMEOUT` |
| Commits OK | `repo_commit_push` / `repo_write_commit` results starting with `OK:` |
| Commits FAILED | same tools with results starting with `⚠️ GIT_ERROR`, `⚠️ PRE_PUSH_TESTS_FAILED`, `⚠️ GIT_NO_CHANGES`, … |

### Non-functional (per model)

| Metric | Source |
|---|---|
| LLM requests (successful) | count of `llm_usage` events |
| Empty responses | `llm_empty_response` events |
| API errors | `llm_api_error` events |
| Prompt / completion / cached / cache-write tokens | sums from `llm_usage` |
| Tokens per request (in/out) | totals ÷ requests |
| Cost (USD) | sum of `cost` (or `usage.cost`) on `llm_usage` |
| Tasks attributed to model | `task_done` events whose dominant model in `llm_usage` is this model |

## Per-model attribution

`events.jsonl` carries `model` on every `llm_usage` event. `tools.jsonl`
does not carry `model`, but every entry has `task_id`. We compute the
**dominant model per task** (the model used in the majority of that task's
LLM calls) and use it to attribute tool calls and commits.

Tool calls from tasks that never produced an `llm_usage` event are bucketed
under `<unattributed>`.

## Output shape (excerpt)

```markdown
# Ouroboros — LLM Comparison Metrics

## 1. Repository profile (functional structure)
| Item | Count |
|---|---|
| BIBLE.md principles | 9 |
| Always-on (core) tools | 29 |
| Registered tools (total) | 52 |
| Dynamically-enabled tools | 23 |
| Test files | 4 |
| Test functions (total) | 34 |

## 2. LLM comparison summary (non-functional + outcomes)
| Model | LLM reqs | Tasks | Prompt tok | Completion tok | … | Commits OK | Commits FAILED |
|---|---|---|---|---|---|---|---|
| <all> | 1,820 | 41 | 8,640,221 | 412,008 | … | 24 | 3 |
| anthropic/claude-sonnet-4.6 | 1,033 | 22 | 5,201,447 | 244,610 | … | 16 | 1 |
| openai/o3                   |   612 | 14 | 2,711,902 | 132,008 | … |  7 | 2 |
| google/gemini-3-pro-preview |   175 |  5 |   726,872 |  35,390 | … |  1 | 0 |

## 3. Per-model breakdown
…
```

## Adding / changing metrics

- New static metric → edit `_scan_static_repo` in `collector.py` and add a row
  in `render_markdown`.
- New per-model metric → extend `TokenStats`, `ToolStats`, or `CommitStats`,
  populate it in the relevant pass in `collect()`, then add a row in
  `_render_model_section`.

No agent code needs to change: the agent already writes everything we need.
