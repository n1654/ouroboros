"""
CLI entry: python -m extensions.metrics --logs <drive>/logs --repo . --out metrics/
"""

from __future__ import annotations

import argparse
import pathlib
import sys

from extensions.metrics.collector import collect
from extensions.metrics.reporter import write_report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m extensions.metrics",
        description="Read-only metrics for comparing LLMs on Ouroboros runs.",
    )
    ap.add_argument(
        "--repo",
        type=pathlib.Path,
        default=pathlib.Path.cwd(),
        help="Path to the Ouroboros repo (for static counts). Default: cwd.",
    )
    ap.add_argument(
        "--logs",
        type=pathlib.Path,
        required=True,
        help="Directory containing events.jsonl and tools.jsonl "
             "(usually <drive_root>/logs).",
    )
    ap.add_argument(
        "--out",
        type=pathlib.Path,
        default=pathlib.Path("metrics"),
        help="Output directory for markdown reports. Default: ./metrics",
    )
    ns = ap.parse_args(argv)

    if not ns.logs.is_dir():
        print(f"error: --logs {ns.logs} is not a directory", file=sys.stderr)
        return 2
    if not ns.repo.is_dir():
        print(f"error: --repo {ns.repo} is not a directory", file=sys.stderr)
        return 2

    metrics = collect(repo=ns.repo, logs=ns.logs)
    report_path = write_report(metrics, ns.out)

    n_models = len(metrics.models)
    print(f"report: {report_path}")
    print(f"        models: {n_models}, "
          f"requests: {metrics.totals.tokens.requests}, "
          f"tool calls: {metrics.totals.tools.calls}, "
          f"commits OK/FAIL: {metrics.totals.commits.ok}/{metrics.totals.commits.failed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
