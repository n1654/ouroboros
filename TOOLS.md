# Tools Reference

This document lists all available tools and their usage.

## Core Tools (always loaded)

- `browse_page` — Open a URL in a headless browser and extract content.
- `browser_action` — Interact with the current browser page (click, fill, screenshot, etc.).
- `claude_code_edit` — Delegate code edits to Claude Code CLI (preferred for multi-file changes).
- `drive_read` / `drive_write` — Read/write files on Google Drive.
- `git_status` / `git_diff` — Git repository status and diffs.
- `knowledge_read` / `knowledge_write` — Access the persistent knowledge base on Drive.
- `list_available_tools` / `enable_tools` — Discover and activate additional tools.
- `repo_read` / `repo_list` / `repo_write_commit` / `repo_commit_push` — GitHub repo file operations and commits.
- `run_shell` — Execute shell commands inside the repo.
- `send_owner_message` — Send a proactive message to the owner (for insights or invitations to collaborate).
- `switch_model` — Change LLM model or reasoning effort level.
- `update_identity` / `update_scratchpad` — Update persistent identity and working memory.

## Additional Tools (available on demand)

Use `list_available_tools()` to see currently available extras. Enable with `enable_tools(tools='name1,name2')`. Examples:
- `multi_model_review` — Get feedback from multiple LLM models on code or text.
- `generate_evolution_stats` — Compute complexity and growth metrics for the codebase.
- ...

## Usage Patterns

1. For small edits: use `repo_write_commit` directly.
2. For multi-file changes: use `claude_code_edit(prompt)` followed by `repo_commit_push(commit_message)`.
3. For research: use `web_search`, then integrate findings into code or docs.
4. For knowledge capture: use `knowledge_write(topic, content)` after completing a task.

## Best Practices

- Always read tool results fully before the next action.
- Avoid redundant tool calls; reuse previous results when possible.
- Keep context size reasonable when passing data between tasks.
- Commit messages should be clear and atomic: one logical change per commit.

## Security

- Never expose secrets, tokens, or private data in logs, chat, or commits.
- Do not run untrusted code from external sources without review.
- Respect the environment constraints and budget limits.

## Troubleshooting

- If a tool fails, inspect the error message and try an alternative approach.
- For browser issues, try increasing timeouts or waiting for specific selectors.
- If context is lost, re-read relevant files or use scratchpad to restore state.

## Contribution

New tools can be added by implementing a function in `ouroboros/tools/` and exporting it via `get_tools()`. The registry will discover them automatically.

## Versioning

Tool interfaces are considered stable. Breaking changes will be documented in the changelog and bump the MINOR version.

