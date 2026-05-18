"""
Collector — read-only static + log analysis.

Two inputs:
  1. The Ouroboros repo (for static counts: tools, principles, tests).
  2. The drive logs directory containing events.jsonl and tools.jsonl
     (the agent appends to these at runtime; we only read).

Output: a Metrics dataclass with everything render_markdown() needs.

Design notes:
  * Pure stdlib. No third-party deps.
  * No `import ouroboros.*` — avoids triggering tool plugin discovery.
  * Tool counts come from regex scanning of ouroboros/tools/*.py.
  * Per-model attribution: events.jsonl `llm_usage` events carry a
    `model` field. tools.jsonl entries do not, so we correlate by
    `task_id` and assign each task its dominant model.
"""

from __future__ import annotations

import json
import pathlib
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Result categorisation
# ---------------------------------------------------------------------------

UNKNOWN_TOOL_PREFIX = "⚠️ Unknown tool"
ARG_ERROR_PREFIXES = ("⚠️ TOOL_ARG_ERROR", "⚠️ TOOL_ARG_ERROR (")
TOOL_ERROR_PREFIX = "⚠️ TOOL_ERROR"
TOOL_TIMEOUT_PREFIX = "⚠️ TOOL_TIMEOUT"
GIT_OK_PREFIX = "OK:"
GIT_FAIL_PREFIXES = (
    "⚠️ GIT_ERROR",
    "⚠️ PRE_PUSH_TESTS_FAILED",
    "⚠️ GIT_NO_CHANGES",
    "⚠️ FILE_WRITE_ERROR",
    "⚠️ PATH_ERROR",
    "⚠️ ERROR: commit_message",
)
COMMIT_TOOLS = frozenset({"repo_commit_push", "repo_write_commit"})


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ToolStats:
    """Tool-call reliability counts for a single model."""
    calls: int = 0
    ok: int = 0
    hallucinated: int = 0       # ⚠️ Unknown tool
    arg_errors: int = 0         # ⚠️ TOOL_ARG_ERROR (missing/invalid arguments)
    runtime_errors: int = 0     # ⚠️ TOOL_ERROR
    timeouts: int = 0           # ⚠️ TOOL_TIMEOUT
    by_tool: Counter = field(default_factory=Counter)


@dataclass
class CommitStats:
    ok: int = 0
    failed: int = 0
    failure_reasons: Counter = field(default_factory=Counter)


@dataclass
class TokenStats:
    """Non-functional: token / request counters for a single model."""
    requests: int = 0           # successful llm_usage events
    api_errors: int = 0         # llm_api_error events
    empty_responses: int = 0    # llm_empty_response events
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0
    tasks: int = 0              # tasks where this model was dominant


@dataclass
class ModelMetrics:
    model: str
    tokens: TokenStats = field(default_factory=TokenStats)
    tools: ToolStats = field(default_factory=ToolStats)
    commits: CommitStats = field(default_factory=CommitStats)

    @property
    def total_tokens(self) -> int:
        return self.tokens.prompt_tokens + self.tokens.completion_tokens

    @property
    def avg_prompt_per_req(self) -> float:
        return self.tokens.prompt_tokens / self.tokens.requests if self.tokens.requests else 0.0

    @property
    def avg_completion_per_req(self) -> float:
        return self.tokens.completion_tokens / self.tokens.requests if self.tokens.requests else 0.0


@dataclass
class RepoProfile:
    """Static facts read from the repo source — independent of any run."""
    bible_principles: int = 0
    bible_principle_titles: List[str] = field(default_factory=list)
    always_on_tools: int = 0
    always_on_tool_names: List[str] = field(default_factory=list)
    total_registered_tools: int = 0
    registered_tool_names: List[str] = field(default_factory=list)
    dynamic_tools: int = 0
    test_files: Dict[str, int] = field(default_factory=dict)   # file → test fn count


@dataclass
class Metrics:
    """Everything the reporter needs."""
    generated_at: str = ""
    repo_root: str = ""
    logs_root: str = ""
    repo: RepoProfile = field(default_factory=RepoProfile)
    models: Dict[str, ModelMetrics] = field(default_factory=dict)
    totals: ModelMetrics = field(default_factory=lambda: ModelMetrics(model="<all>"))
    # event-types histogram from events.jsonl, for sanity
    event_type_counts: Counter = field(default_factory=Counter)
    # parser counters
    events_parsed: int = 0
    tools_parsed: int = 0
    events_malformed: int = 0
    tools_malformed: int = 0


# ---------------------------------------------------------------------------
# Static repo scan (no imports of ouroboros)
# ---------------------------------------------------------------------------

_TOOL_ENTRY_RE = re.compile(r'ToolEntry\(\s*["\']([A-Za-z_][A-Za-z0-9_]*)["\']')
_CORE_BLOCK_RE = re.compile(r'CORE_TOOL_NAMES\s*=\s*\{([^}]*)\}', re.DOTALL)
_QUOTED_NAME_RE = re.compile(r'["\']([A-Za-z_][A-Za-z0-9_]*)["\']')
_BIBLE_PRINCIPLE_RE = re.compile(r'^##\s+Principle\s+\d+:\s*(.+)$', re.MULTILINE)
_TEST_FN_RE = re.compile(r'^\s*def\s+(test_[A-Za-z0-9_]+)\s*\(', re.MULTILINE)


def _scan_static_repo(repo_root: pathlib.Path) -> RepoProfile:
    profile = RepoProfile()

    bible = repo_root / "BIBLE.md"
    if bible.is_file():
        text = bible.read_text(encoding="utf-8", errors="replace")
        titles = _BIBLE_PRINCIPLE_RE.findall(text)
        profile.bible_principle_titles = [t.strip() for t in titles]
        profile.bible_principles = len(titles)

    registry = repo_root / "ouroboros" / "tools" / "registry.py"
    if registry.is_file():
        text = registry.read_text(encoding="utf-8", errors="replace")
        m = _CORE_BLOCK_RE.search(text)
        if m:
            names = sorted(set(_QUOTED_NAME_RE.findall(m.group(1))))
            profile.always_on_tool_names = names
            profile.always_on_tools = len(names)

    tools_dir = repo_root / "ouroboros" / "tools"
    if tools_dir.is_dir():
        names: set[str] = set()
        for py in sorted(tools_dir.glob("*.py")):
            if py.name in {"__init__.py", "registry.py"}:
                continue
            text = py.read_text(encoding="utf-8", errors="replace")
            for fn in _TOOL_ENTRY_RE.findall(text):
                names.add(fn)
        profile.registered_tool_names = sorted(names)
        profile.total_registered_tools = len(names)
        profile.dynamic_tools = max(
            0, profile.total_registered_tools - profile.always_on_tools
        )

    tests_dir = repo_root / "tests"
    if tests_dir.is_dir():
        for py in sorted(tests_dir.glob("test_*.py")):
            text = py.read_text(encoding="utf-8", errors="replace")
            profile.test_files[py.name] = len(_TEST_FN_RE.findall(text))

    return profile


# ---------------------------------------------------------------------------
# Log parsing
# ---------------------------------------------------------------------------

def _iter_jsonl(path: pathlib.Path) -> Iterable[Tuple[Optional[Dict[str, Any]], bool]]:
    """Yield (record, malformed_flag) for every non-blank line in path."""
    if not path.is_file():
        return
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    yield obj, False
                else:
                    yield None, True
            except json.JSONDecodeError:
                yield None, True


def _classify_tool_result(result: str) -> str:
    """Categorise a tool result_preview string."""
    if not result:
        return "ok"
    if result.startswith(UNKNOWN_TOOL_PREFIX):
        return "hallucinated"
    if any(result.startswith(p) for p in ARG_ERROR_PREFIXES):
        return "arg_error"
    if result.startswith(TOOL_TIMEOUT_PREFIX):
        return "timeout"
    if result.startswith(TOOL_ERROR_PREFIX):
        return "runtime_error"
    if result.startswith("⚠️"):
        return "runtime_error"
    return "ok"


def _classify_commit_result(result: str) -> Tuple[Optional[bool], str]:
    """Return (success?, reason_tag). success=None means 'not a commit outcome'."""
    if not result:
        return None, ""
    if result.startswith(GIT_OK_PREFIX):
        return True, ""
    for p in GIT_FAIL_PREFIXES:
        if result.startswith(p):
            return False, p
    if result.startswith("⚠️"):
        return False, result.split(":", 1)[0]
    return None, ""


def _dominant_model_per_task(events: Iterable[Dict[str, Any]]) -> Dict[str, str]:
    """task_id → most-used model (by request count) across llm_usage events."""
    per_task: Dict[str, Counter] = defaultdict(Counter)
    for ev in events:
        if ev.get("type") != "llm_usage":
            continue
        tid = ev.get("task_id") or ""
        model = ev.get("model") or ""
        if not tid or not model:
            continue
        per_task[tid][model] += 1
    return {tid: c.most_common(1)[0][0] for tid, c in per_task.items() if c}


# ---------------------------------------------------------------------------
# Main collect()
# ---------------------------------------------------------------------------

def collect(repo: pathlib.Path, logs: pathlib.Path) -> Metrics:
    """Compute all metrics. Pure read; never writes, never imports ouroboros."""
    import datetime

    metrics = Metrics(
        generated_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        repo_root=str(repo.resolve()),
        logs_root=str(logs.resolve()),
        repo=_scan_static_repo(repo),
    )

    events_path = logs / "events.jsonl"
    tools_path = logs / "tools.jsonl"

    # --- Pass 1: load events into memory (we need two passes) ---
    events: List[Dict[str, Any]] = []
    for rec, bad in _iter_jsonl(events_path):
        if bad:
            metrics.events_malformed += 1
            continue
        events.append(rec)
        metrics.events_parsed += 1
        et = rec.get("type") or "?"
        metrics.event_type_counts[et] += 1

    task_model = _dominant_model_per_task(events)

    def _model_bucket(model: str) -> ModelMetrics:
        bucket = metrics.models.get(model)
        if bucket is None:
            bucket = ModelMetrics(model=model)
            metrics.models[model] = bucket
        return bucket

    # --- Pass 2: aggregate non-functional from events ---
    seen_tasks_per_model: Dict[str, set[str]] = defaultdict(set)
    for ev in events:
        et = ev.get("type")
        model = ev.get("model") or ""
        if et == "llm_usage":
            if not model:
                continue
            b = _model_bucket(model)
            b.tokens.requests += 1
            b.tokens.prompt_tokens += int(ev.get("prompt_tokens") or 0)
            b.tokens.completion_tokens += int(ev.get("completion_tokens") or 0)
            b.tokens.cached_tokens += int(ev.get("cached_tokens") or 0)
            b.tokens.cache_write_tokens += int(ev.get("cache_write_tokens") or 0)
            cost = ev.get("cost")
            if cost is None and isinstance(ev.get("usage"), dict):
                cost = ev["usage"].get("cost")
            try:
                b.tokens.cost_usd += float(cost or 0.0)
            except (TypeError, ValueError):
                pass
        elif et == "llm_api_error" and model:
            _model_bucket(model).tokens.api_errors += 1
        elif et == "llm_empty_response" and model:
            _model_bucket(model).tokens.empty_responses += 1
        elif et == "task_done":
            tid = ev.get("task_id") or ""
            m = task_model.get(tid) or ""
            if not m:
                continue
            if tid not in seen_tasks_per_model[m]:
                seen_tasks_per_model[m].add(tid)
                _model_bucket(m).tokens.tasks += 1

    # --- Pass 3: tool reliability + commits from tools.jsonl ---
    for rec, bad in _iter_jsonl(tools_path):
        if bad:
            metrics.tools_malformed += 1
            continue
        metrics.tools_parsed += 1
        tool = rec.get("tool") or ""
        tid = rec.get("task_id") or ""
        result = str(rec.get("result_preview") or "")
        model = task_model.get(tid) or "<unattributed>"
        b = _model_bucket(model)
        b.tools.calls += 1
        b.tools.by_tool[tool] += 1
        cat = _classify_tool_result(result)
        if cat == "ok":
            b.tools.ok += 1
        elif cat == "hallucinated":
            b.tools.hallucinated += 1
        elif cat == "arg_error":
            b.tools.arg_errors += 1
        elif cat == "timeout":
            b.tools.timeouts += 1
        elif cat == "runtime_error":
            b.tools.runtime_errors += 1
        if tool in COMMIT_TOOLS:
            ok, reason = _classify_commit_result(result)
            if ok is True:
                b.commits.ok += 1
            elif ok is False:
                b.commits.failed += 1
                if reason:
                    b.commits.failure_reasons[reason] += 1

    # --- Roll up totals ---
    totals = ModelMetrics(model="<all>")
    for b in metrics.models.values():
        totals.tokens.requests += b.tokens.requests
        totals.tokens.api_errors += b.tokens.api_errors
        totals.tokens.empty_responses += b.tokens.empty_responses
        totals.tokens.prompt_tokens += b.tokens.prompt_tokens
        totals.tokens.completion_tokens += b.tokens.completion_tokens
        totals.tokens.cached_tokens += b.tokens.cached_tokens
        totals.tokens.cache_write_tokens += b.tokens.cache_write_tokens
        totals.tokens.cost_usd += b.tokens.cost_usd
        totals.tokens.tasks += b.tokens.tasks
        totals.tools.calls += b.tools.calls
        totals.tools.ok += b.tools.ok
        totals.tools.hallucinated += b.tools.hallucinated
        totals.tools.arg_errors += b.tools.arg_errors
        totals.tools.runtime_errors += b.tools.runtime_errors
        totals.tools.timeouts += b.tools.timeouts
        totals.commits.ok += b.commits.ok
        totals.commits.failed += b.commits.failed
        totals.tools.by_tool.update(b.tools.by_tool)
        totals.commits.failure_reasons.update(b.commits.failure_reasons)
    metrics.totals = totals

    return metrics
