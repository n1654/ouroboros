"""
extensions.metrics — Ouroboros LLM comparison metrics.

Read-only. Parses event logs and statically inspects the repo.
Never imports or executes the agent.

Public API:
    collect(repo, logs)      -> Metrics
    render_markdown(m)       -> str
    write_report(m, out_dir) -> pathlib.Path
"""

from extensions.metrics.collector import Metrics, collect
from extensions.metrics.reporter import render_markdown, write_report

__all__ = ["Metrics", "collect", "render_markdown", "write_report"]
