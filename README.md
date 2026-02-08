# ccbar

A configurable status line for [Claude Code](https://docs.anthropic.com/en/docs/claude-code).

Shows git branch, directory, model, usage limits, and context window — all in one bar at the bottom of your terminal.

```
⎇ main *2 | my-project | Opus | Session ■■□□□□□□ 12% 1h 22m | Weekly ■□□□□□□□ 7% | Ctx ■■■■□□□□ 56% | Max 5x
```

## Features

- **Session & weekly usage** with progress bars and reset timer
- **Context window** usage percentage
- **Git branch** with staged/modified/untracked counts
- **Current directory** and **model name**
- **Configurable** — bar style, colors, thresholds, section order
- **Zero dependencies** — Python 3.8+ stdlib only
- **macOS Keychain support** — works with latest Claude Code auth

## Install

```bash
git clone https://github.com/taras-mrtn/ccbar.git ~/.ccbar
python3 ~/.ccbar/ccbar.py --install
```

Restart Claude Code. That's it.

## Requirements

- Claude Code with an active Pro or Max subscription (OAuth login)
- Python 3.8+
- macOS or Linux

## Configuration

On first run, `config.json` is created next to `ccbar.py`:

```json
{
  "bar": {
    "style": "default",
    "width": 8
  },
  "colors": {
    "low": "green",
    "mid": "yellow",
    "high": "red",
    "threshold_mid": 50,
    "threshold_high": 80
  },
  "layout": "standard",
  "cache_ttl": 30,
  "sections": ["git", "cwd", "model", "session", "weekly", "context", "credits", "plan"]
}
```

### Bar styles

| Style | Preview |
|-------|---------|
| `default` | `━━━─────` |
| `blocks` | `███░░░░░` |
| `shaded` | `▓▓▓░░░░░` |
| `dots` | `●●●○○○○○` |
| `squares` | `■■■□□□□□` |
| `diamonds` | `◆◆◆◇◇◇◇◇` |
| `parallelogram` | `▰▰▰▱▱▱▱▱` |
| `pipes` | `┃┃┃╌╌╌╌╌` |
| `braille` | `⣿⣿⣿⢀⢀⢀⢀⢀` |

### Themes

Set `"theme"` in config to apply a preset color scheme. Explicit `colors` overrides still win.

| Theme | Low | Mid | High |
|-------|-----|-----|------|
| `default` | green | yellow | red |
| `ocean` | cyan | blue | magenta |
| `sunset` | bright yellow | orange | red |
| `mono` | white | white | bright red |
| `neon` | bright green | bright yellow | bright red |
| `frost` | cyan | bright yellow | orange |
| `ember` | yellow | orange | bright red |

```json
{
  "theme": "ocean"
}
```

### Colors

You can also set colors individually (overrides theme):

Available: `green`, `yellow`, `red`, `cyan`, `blue`, `magenta`, `white`, `bright_green`, `bright_yellow`, `bright_red`, `orange`

### Layout

Controls label verbosity across all sections.

| Layout | Example |
|--------|---------|
| `standard` | `Session ■■□□□□□□ 12%` |
| `compact` | `Ses ■■□□□□□□ 12%` |
| `minimal` | `■■□□□□□□ 12%` |

### Cache TTL

`cache_ttl` controls how many seconds the usage API response is cached (default `30`).

### Sections

The `sections` array controls **what** is shown and **in what order**. Remove a section to hide it, reorder to change position.

Available sections: `git`, `cwd`, `model`, `session`, `weekly`, `context`, `credits`, `plan`

The `credits` section shows extra/bonus credits when available (hidden automatically if not).

### Examples

Minimal — just usage bars:
```json
{
  "sections": ["session", "weekly", "context"]
}
```

Developer — git first, ocean theme:
```json
{
  "bar": { "style": "blocks" },
  "theme": "ocean",
  "sections": ["git", "cwd", "model", "session", "context"]
}
```

## CLI flags

```bash
# Install status line into Claude Code settings
python3 ccbar.py --install

# Print path to config.json
python3 ccbar.py --config

# Show only specific sections (overrides config)
echo '{}' | python3 ccbar.py --show session,weekly,context

# Hide specific sections (overrides config)
echo '{}' | python3 ccbar.py --hide git,cwd
```

## How it works

Claude Code calls the script on every status line refresh, passing a JSON object via stdin with model info, working directory, and context window stats. The script also fetches usage data from `api.anthropic.com/api/oauth/usage` (cached, configurable via `cache_ttl`) using your existing OAuth credentials.

## Acknowledgments

Inspired by [claude-pulse](https://github.com/NoobyGains/claude-pulse).

## License

MIT
