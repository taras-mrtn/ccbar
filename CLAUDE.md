# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

ccbar is a configurable status line for Claude Code. It's a single-file Python script (`ccbar.py`) with zero dependencies (Python 3.8+ stdlib only). Claude Code invokes it on every status line refresh, passing a JSON object via stdin with model info, working directory, and context window stats. The script fetches usage data from `api.anthropic.com/api/oauth/usage` (cached 30s) using OAuth credentials from macOS Keychain or `~/.claude/.credentials.json`.

## Running

```bash
# Install (writes statusLine config to ~/.claude/settings.json)
python3 ccbar.py --install

# Manual test (pipe JSON context on stdin)
echo '{}' | python3 ccbar.py
```

There are no tests, linter, or build system. The project is a single Python file.

## Architecture

`ccbar.py` is organized into these sections:

- **Config** — `load_config()` reads `config.json` (sibling to the script), merges with `DEFAULT_CONFIG`. Config controls bar style, colors/thresholds, and which sections to show.
- **Credentials** — `get_credentials()` tries macOS Keychain first (`security find-generic-password -s "Claude Code-credentials"`), falls back to `~/.claude/.credentials.json`. Extracts `claudeAiOauth.accessToken` and `rateLimitTier` from the credential data. Returns `(token, plan_display_name)`.
- **Cache** — Usage API responses cached to `~/.cache/ccbar/cache.json` for 30 seconds to avoid rate limiting.
- **Git** — `get_git_info()` runs `git rev-parse` and `git status --porcelain` to get branch name and staged/modified/untracked counts.
- **Rendering** — `make_bar()` produces a colored progress bar string using ANSI escape codes. `BAR_STYLES` dict maps style names to (filled, empty) character pairs.
- **Section renderers** — Each section (`git`, `cwd`, `model`, `session`, `weekly`, `context`, `plan`) has a `render_*` function registered in the `RENDERERS` dict. `build_status_line()` iterates `cfg["sections"]` and joins non-None results with ` | `.
- **Main** — Reads JSON from stdin, checks cache, fetches usage if needed, outputs the status line to stdout.

## Adding a new section

1. Write `render_foo(usage, plan, ctx, cfg) -> Optional[str]`.
2. Add `"foo": render_foo` to the `RENDERERS` dict.
3. Users enable it by adding `"foo"` to `sections` in their `config.json`.

## Key conventions

- All output goes through `sys.stdout.buffer.write()` with explicit UTF-8 encoding (status line contains Unicode box-drawing/block characters).
- Section renderers all have the signature `(usage, plan, ctx, cfg) -> Optional[str]` and return `None` to hide themselves.
- The `config.json` is gitignored — users customize locally.
- `ctx` is the JSON object Claude Code passes via stdin. Key fields: `ctx["cwd"]`, `ctx["model"]["display_name"]`, `ctx["context_window"]["used_percentage"]`.
- `usage` is the API response from `/api/oauth/usage`. Key fields: `usage["five_hour"]["utilization"]`, `usage["seven_day"]["utilization"]`.
