#!/usr/bin/env python3
"""ccbar — configurable status line for Claude Code."""

VERSION = "0.4.0"

import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

def _enable_ansi_windows():
    """Enable ANSI escape code processing on Windows."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_ulong()
        kernel32.GetConsoleMode(handle, ctypes.byref(mode))
        kernel32.SetConsoleMode(handle, mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    except Exception:
        pass


DIM = "\033[2m"
RESET = "\033[0m"

COLORS = {
    # Basic ANSI (follow terminal theme)
    "green": "\033[32m",
    "yellow": "\033[33m",
    "red": "\033[31m",
    "cyan": "\033[36m",
    "blue": "\033[34m",
    "magenta": "\033[35m",
    "white": "\033[37m",
    "bright_green": "\033[92m",
    "bright_yellow": "\033[93m",
    "bright_red": "\033[91m",
    # Fixed 256-color (consistent across terminals)
    "orange": "\033[38;5;208m",
    "teal": "\033[38;5;45m",
    "royal_blue": "\033[38;5;33m",
    "purple": "\033[38;5;129m",
    "gold": "\033[38;5;220m",
    "coral": "\033[38;5;203m",
    "lime": "\033[38;5;118m",
    "sky": "\033[38;5;117m",
    "gray": "\033[38;5;252m",
}

BAR_STYLES = {
    "default": ("\u2501", "\u2500"),       # ━ ─
    "blocks": ("\u2588", "\u2591"),         # █ ░
    "shaded": ("\u2593", "\u2591"),         # ▓ ░
    "dots": ("\u25cf", "\u25cb"),           # ● ○
    "squares": ("\u25a0", "\u25a1"),        # ■ □
    "diamonds": ("\u25c6", "\u25c7"),       # ◆ ◇
    "parallelogram": ("\u25b0", "\u25b1"),  # ▰ ▱
    "pipes": ("\u2503", "\u254c"),          # ┃ ╌
    "braille": ("\u28ff", "\u2880"),        # ⣿ ⢀
    "ascii": ("#", "-"),                    # # -
    "mini": ("\u25aa", "\u25ab"),           # ▪ ▫  (small squares, lighter weight)
    "bullets": ("\u2022", "\u25e6"),        # • ◦  (bullet + white bullet, circular)
}

THEMES = {
    "default": {"low": "green", "mid": "yellow", "high": "red"},
    "ocean":   {"low": "teal", "mid": "royal_blue", "high": "purple"},
    "sunset":  {"low": "gold", "mid": "orange", "high": "coral"},
    "mono":    {"low": "gray", "mid": "gray", "high": "coral"},
    "neon":    {"low": "lime", "mid": "gold", "high": "coral"},
    "frost":   {"low": "sky", "mid": "gold", "high": "orange"},
    "ember":   {"low": "gold", "mid": "orange", "high": "coral"},
}

PLAN_NAMES = {
    "default_claude_pro": "Pro",
    "default_claude_max_5x": "Max 5x",
    "default_claude_max_20x": "Max 20x",
}

GITHUB_REPO = "taras-mrtn/ccbar"
UPDATE_CHECK_INTERVAL = 86400  # 24 hours

DEFAULT_CONFIG = {
    "bar": {
        "style": "default",
        "width": 8,
    },
    "colors": {
        "low": "green",
        "mid": "yellow",
        "high": "red",
        "threshold_mid": 50,
        "threshold_high": 80,
    },
    "layout": "standard",
    "cache_ttl": 30,
    "error_cache_ttl": 120,
    "update_check": True,
    "update_interval": UPDATE_CHECK_INTERVAL,
    "sections": ["git", "cwd", "model", "session", "weekly", "context", "credits", "plan"],
    "git_status": False,
}


# --- Config ---

def get_config_path():
    return Path(__file__).resolve().parent / "config.json"


def load_config():
    config_path = get_config_path()
    user = {}
    try:
        with open(config_path) as f:
            user = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        try:
            with open(config_path, "w") as f:
                json.dump(DEFAULT_CONFIG, f, indent=2)
        except OSError:
            pass

    cfg = {}
    cfg["bar"] = {**DEFAULT_CONFIG["bar"], **user.get("bar", {})}
    theme_colors = THEMES.get(user.get("theme", ""), {})
    cfg["colors"] = {**DEFAULT_CONFIG["colors"], **theme_colors, **user.get("colors", {})}
    cfg["layout"] = user.get("layout", DEFAULT_CONFIG["layout"])
    cfg["cache_ttl"] = user.get("cache_ttl", DEFAULT_CONFIG["cache_ttl"])
    cfg["error_cache_ttl"] = user.get("error_cache_ttl", DEFAULT_CONFIG["error_cache_ttl"])
    cfg["update_check"] = user.get("update_check", DEFAULT_CONFIG["update_check"])
    cfg["update_interval"] = user.get("update_interval", DEFAULT_CONFIG["update_interval"])
    cfg["sections"] = user.get("sections", DEFAULT_CONFIG["sections"])
    cfg["git_status"] = user.get("git_status", DEFAULT_CONFIG["git_status"])
    return cfg


# --- Credentials ---

def get_credentials():
    data = None
    if sys.platform == "darwin":
        try:
            result = subprocess.run(
                ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                data = json.loads(result.stdout.strip())
        except Exception:
            pass
    if data is None:
        try:
            with open(Path.home() / ".claude" / ".credentials.json") as f:
                data = json.load(f)
        except Exception:
            return None, None
    try:
        oauth = data.get("claudeAiOauth", {})
        token = oauth.get("accessToken")
        tier = oauth.get("rateLimitTier", "")
        if not token:
            return None, None
        plan = PLAN_NAMES.get(tier, tier.replace("default_claude_", "").replace("_", " ").title())
        return token, plan
    except Exception:
        return None, None


def fetch_usage(token):
    req = urllib.request.Request(
        "https://api.anthropic.com/api/oauth/usage",
        headers={
            "Authorization": f"Bearer {token}",
            "anthropic-beta": "oauth-2025-04-20",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read())


# --- Cache ---

def get_cache_path():
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        cache_dir = base / "ccbar"
    else:
        cache_dir = Path.home() / ".cache" / "ccbar"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / "cache.json"


def read_cache(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def write_cache(path, usage=None, plan=None, error=None):
    try:
        with open(path, "w") as f:
            json.dump({"timestamp": time.time(), "usage": usage, "plan": plan, "error": error}, f)
    except OSError:
        pass


# --- Update check ---

def get_update_cache_path():
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        cache_dir = base / "ccbar"
    else:
        cache_dir = Path.home() / ".cache" / "ccbar"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / "update.json"


def read_update_cache(path, interval):
    try:
        with open(path) as f:
            cached = json.load(f)
        if time.time() - cached.get("timestamp", 0) < interval:
            return cached
    except Exception:
        pass
    return None


def write_update_cache(path, latest):
    try:
        with open(path, "w") as f:
            json.dump({"timestamp": time.time(), "latest": latest}, f)
    except OSError:
        pass


def parse_version(v):
    try:
        return tuple(int(x) for x in v.lstrip("v").split("."))
    except (ValueError, AttributeError):
        return (0,)


def fetch_latest_version():
    req = urllib.request.Request(
        f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest",
        headers={"Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(req, timeout=3) as resp:
        data = json.loads(resp.read())
    return data.get("tag_name", "").lstrip("v")


def check_for_update(cfg):
    if not cfg.get("update_check", True):
        return None
    path = get_update_cache_path()
    cached = read_update_cache(path, cfg.get("update_interval", UPDATE_CHECK_INTERVAL))
    if cached:
        latest = cached.get("latest", "")
        if latest and parse_version(latest) > parse_version(VERSION):
            return latest
        return None
    try:
        latest = fetch_latest_version()
        write_update_cache(path, latest)
        if latest and parse_version(latest) > parse_version(VERSION):
            return latest
    except Exception:
        write_update_cache(path, "")
    return None


def do_update():
    script_dir = Path(__file__).resolve().parent
    try:
        result = subprocess.run(
            ["git", "-C", str(script_dir), "pull"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            print(result.stdout.strip())
        else:
            print(f"Update failed:\n{result.stderr.strip()}")
    except Exception as e:
        print(f"Update failed: {e}")


# --- Git ---

def get_git_info(cwd):
    if not cwd:
        return None, None
    try:
        branch_r = subprocess.run(
            ["git", "--no-optional-locks", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=3, cwd=cwd,
        )
        if branch_r.returncode != 0:
            return None, None
        branch = branch_r.stdout.strip()

        status_r = subprocess.run(
            ["git", "--no-optional-locks", "status", "--porcelain"],
            capture_output=True, text=True, timeout=3, cwd=cwd,
        )
        if status_r.returncode != 0:
            return branch, None

        staged = modified = untracked = 0
        for line in status_r.stdout.splitlines():
            if len(line) < 2:
                continue
            x, y = line[0], line[1]
            if x == '?':
                untracked += 1
            else:
                if x in 'MADRC':
                    staged += 1
                if y in 'MD':
                    modified += 1

        parts = []
        if staged:
            parts.append(f"+{staged}")
        if modified:
            parts.append(f"*{modified}")
        if untracked:
            parts.append(f"?{untracked}")
        return branch, " ".join(parts) if parts else None
    except Exception:
        return None, None


# --- Rendering ---

def make_bar(pct, cfg):
    bar_cfg = cfg["bar"]
    colors_cfg = cfg["colors"]
    fill, empty = BAR_STYLES.get(bar_cfg["style"], BAR_STYLES["default"])
    width = bar_cfg["width"]
    filled = max(0, min(width, round(pct / 100 * width)))

    if pct >= colors_cfg["threshold_high"]:
        color = COLORS.get(colors_cfg["high"], COLORS["red"])
    elif pct >= colors_cfg["threshold_mid"]:
        color = COLORS.get(colors_cfg["mid"], COLORS["yellow"])
    else:
        color = COLORS.get(colors_cfg["low"], COLORS["green"])

    return f"{color}{fill * filled}{DIM}{empty * (width - filled)}{RESET}"


def format_reset_time(resets_at_str):
    if not resets_at_str:
        return None
    try:
        resets_at = datetime.fromisoformat(resets_at_str)
        total_seconds = int((resets_at - datetime.now(timezone.utc)).total_seconds())
        if total_seconds <= 0:
            return "now"
        h, m = total_seconds // 3600, (total_seconds % 3600) // 60
        return f"{h}h {m:02d}m" if h > 0 else f"{m}m"
    except Exception:
        return None


LABELS = {
    "standard": {"session": "Session", "weekly": "Weekly", "context": "Ctx", "credits": "Credits"},
    "compact":  {"session": "Ses",     "weekly": "Wk",     "context": "Ctx", "credits": "Cr"},
}


def section_label(name, cfg):
    layout = cfg.get("layout", "standard")
    if layout == "minimal":
        return ""
    return LABELS.get(layout, LABELS["standard"]).get(name, name)


# --- Section renderers ---

def render_git(usage, plan, ctx, cfg):
    cwd = ctx.get("cwd") or (ctx.get("workspace") or {}).get("current_dir")
    branch, git_status = get_git_info(cwd)
    if not branch:
        return None
    part = f"\u2387 {branch}"
    if cfg.get("git_status") and git_status:
        part += f" {git_status}"
    return part


def render_cwd(usage, plan, ctx, cfg):
    cwd = ctx.get("cwd") or (ctx.get("workspace") or {}).get("current_dir")
    if not cwd:
        return None
    return f"📁 {Path(cwd).name}"


def render_model(usage, plan, ctx, cfg):
    model = (ctx.get("model") or {}).get("display_name") or (ctx.get("model") or {}).get("id")
    if not model:
        return None
    if model.startswith("Claude "):
        model = model[7:]
    return model


def render_session(usage, plan, ctx, cfg):
    five = (usage or {}).get("five_hour")
    if not five:
        return None
    pct = five.get("utilization", 0)
    reset = format_reset_time(five.get("resets_at"))
    reset_str = f" {reset}" if reset else ""
    label = section_label("session", cfg)
    prefix = f"{label} " if label else ""
    return f"{prefix}{make_bar(pct, cfg)} {pct:.0f}%{reset_str}"


def render_weekly(usage, plan, ctx, cfg):
    seven = (usage or {}).get("seven_day")
    if not seven:
        return None
    pct = seven.get("utilization", 0)
    label = section_label("weekly", cfg)
    prefix = f"{label} " if label else ""
    return f"{prefix}{make_bar(pct, cfg)} {pct:.0f}%"


def render_context(usage, plan, ctx, cfg):
    ctx_win = ctx.get("context_window") or {}
    used_pct = ctx_win.get("used_percentage")
    if used_pct is None:
        return None
    label = section_label("context", cfg)
    prefix = f"{label} " if label else ""
    return f"{prefix}{make_bar(used_pct, cfg)} {used_pct:.0f}%"


def render_credits(usage, plan, ctx, cfg):
    bonus = (usage or {}).get("bonus") or (usage or {}).get("extra_credits")
    if not bonus:
        return None
    pct = bonus.get("utilization", 0)
    label = section_label("credits", cfg)
    prefix = f"{label} " if label else ""
    return f"{prefix}{make_bar(pct, cfg)} {pct:.0f}%"


def render_plan(usage, plan, ctx, cfg):
    return plan or None


RENDERERS = {
    "git": render_git,
    "cwd": render_cwd,
    "model": render_model,
    "session": render_session,
    "weekly": render_weekly,
    "context": render_context,
    "credits": render_credits,
    "plan": render_plan,
}


_API_SECTIONS = {"session", "weekly", "credits"}


def build_status_line(usage, plan, ctx, cfg, error=None):
    parts = []
    error_shown = False
    for section in cfg["sections"]:
        if error and section in _API_SECTIONS:
            if not error_shown:
                parts.append(error)
                error_shown = True
            continue
        renderer = RENDERERS.get(section)
        if renderer:
            result = renderer(usage, plan, ctx, cfg)
            if result:
                parts.append(result)
    latest = check_for_update(cfg)
    if latest:
        parts.append(f"{DIM}ccbar update available{RESET}")
    return " | ".join(parts)


# --- TUI helpers ---

def _tui_available():
    """Check if TUI mode is possible (Unix with termios, both stdin and stdout are TTYs)."""
    try:
        import termios, tty  # noqa: F401
        return sys.stdin.isatty() and sys.stdout.isatty()
    except ImportError:
        return False


_in_raw_mode = False


def _enter_raw_mode():
    """Enter raw terminal mode. Returns (fd, old_settings) for restoring later."""
    global _in_raw_mode
    import termios, tty
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    tty.setraw(fd)
    _in_raw_mode = True
    return fd, old


def _exit_raw_mode(fd, old):
    """Restore terminal to previous settings."""
    global _in_raw_mode
    import termios
    termios.tcsetattr(fd, termios.TCSADRAIN, old)
    _in_raw_mode = False


def _read_key():
    """Read a single keypress. Caller must already be in raw mode.

    Uses os.read() on the raw fd (not sys.stdin.read()) because Python's
    buffered text stream doesn't work reliably after tty.setraw().
    Uses select() with a short timeout to distinguish a bare Escape keypress
    from the start of an arrow-key escape sequence (ESC [ A/B/C/D).
    """
    import select
    fd = sys.stdin.fileno()
    b = os.read(fd, 1)
    if not b:
        raise EOFError
    ch = b[0]
    if ch == 0x1b:  # ESC
        r, _, _ = select.select([fd], [], [], 0.05)
        if r:
            seq = os.read(fd, 2)
            if seq == b'[A':
                return 'up'
            if seq == b'[B':
                return 'down'
            if seq == b'[C':
                return 'right'
            if seq == b'[D':
                return 'left'
        return 'escape'
    if ch in (0x0d, 0x0a):  # CR, LF
        return 'enter'
    if ch == 0x20:  # space
        return 'space'
    if ch == 0x03:  # Ctrl+C
        raise KeyboardInterrupt
    if ch == 0x04:  # Ctrl+D
        raise EOFError
    if ch == ord('q'):
        return 'q'
    return chr(ch)


# ANSI escape helpers
_CLEAR_LINE = '\033[2K'
_CURSOR_HOME = '\r'
_HIDE_CURSOR = '\033[?25l'
_SHOW_CURSOR = '\033[?25h'
_BOLD = '\033[1m'
_CLEAR_SCREEN = '\033[2J\033[H'
_BACK = object()  # sentinel: widget returns this to signal "go to previous step"


def _clear_below():
    """Clear from cursor to end of screen."""
    _write('\033[J')


def _write(text=''):
    """Write text and flush. Translates \\n to \\r\\n when in raw mode."""
    if _in_raw_mode:
        text = text.replace('\n', '\r\n')
    sys.stdout.write(text)
    sys.stdout.flush()


def _tui_select(title, options, default_idx=0, preview_fn=None):
    """Arrow-key single-select widget. Returns (key, display) of selected option.

    Args:
        title: Header text for this step
        options: List of (key, display_label) tuples
        default_idx: Initially highlighted index
        preview_fn: Called as preview_fn(highlighted_key) before rendering.
                    Should print the preview and return number of lines printed.
                    The highlighted_key lets the preview show what the bar would
                    look like if the user confirms this option.
    """
    cursor = default_idx
    total_lines_printed = 0

    while True:
        # Move back to overwrite previous render
        if total_lines_printed > 0:
            _write(f'\033[{total_lines_printed}A')
        _write(_CURSOR_HOME)
        _clear_below()

        lines = 0

        # Preview with speculative value
        if preview_fn:
            lines += preview_fn(options[cursor][0])

        # Title
        _write(f'{_BOLD}{title}{RESET}\n')
        lines += 1

        # Hint
        _write(f'{DIM}  ↑/↓ navigate  ←/→ back/next  ⏎ select  esc/q cancel{RESET}\n')
        lines += 1

        # Options
        for i, (key, display) in enumerate(options):
            if i == cursor:
                _write(f'  {COLORS["cyan"]}▸ {display}{RESET}\n')
            else:
                _write(f'    {DIM}{display}{RESET}\n')
            lines += 1

        total_lines_printed = lines

        key = _read_key()
        if key == 'up':
            cursor = (cursor - 1) % len(options)
        elif key == 'down':
            cursor = (cursor + 1) % len(options)
        elif key in ('enter', 'right'):
            return options[cursor]
        elif key == 'left':
            return _BACK
        elif key == 'q' or key == 'escape':
            raise KeyboardInterrupt


def _tui_int_input(title, default, min_val=1, max_val=50, preview_fn=None):
    """Arrow-key integer input. Left/Right or Up/Down to change, Enter to confirm.

    Args:
        preview_fn: Called as preview_fn(current_value) on each render.
    """
    value = default
    total_lines_printed = 0

    while True:
        if total_lines_printed > 0:
            _write(f'\033[{total_lines_printed}A')
        _write(_CURSOR_HOME)
        _clear_below()

        lines = 0

        if preview_fn:
            lines += preview_fn(value)

        _write(f'{_BOLD}{title}{RESET}\n')
        lines += 1

        _write(f'{DIM}  ↑/↓ adjust  ←/→ back/next  ⏎ confirm  esc/q cancel{RESET}\n')
        lines += 1

        bar_visual = '█' * value + '░' * (max_val - value)
        _write(f'  {COLORS["cyan"]}{value}{RESET}  {DIM}{bar_visual[:30]}{RESET}\n')
        lines += 1

        total_lines_printed = lines

        key = _read_key()
        if key == 'down':
            value = max(min_val, value - 1)
        elif key == 'up':
            value = min(max_val, value + 1)
        elif key in ('enter', 'right'):
            return value
        elif key == 'left':
            return _BACK
        elif key == 'q' or key == 'escape':
            raise KeyboardInterrupt


def _tui_toggle(title, items, enabled, preview_fn=None):
    """Arrow-key multi-toggle widget. Returns list of enabled item names.

    Args:
        title: Header text
        items: List of item name strings
        enabled: Set of initially enabled item names
        preview_fn: Called as preview_fn(current_enabled_list) on each render.
    """
    selected = set(enabled)
    cursor = 0
    # items + 1 for "Done" row
    total_rows = len(items) + 1
    total_lines_printed = 0

    while True:
        if total_lines_printed > 0:
            _write(f'\033[{total_lines_printed}A')
        _write(_CURSOR_HOME)
        _clear_below()

        lines = 0

        if preview_fn:
            current_list = [s for s in items if s in selected]
            lines += preview_fn(current_list)

        _write(f'{_BOLD}{title}{RESET}\n')
        lines += 1

        _write(f'{DIM}  ↑/↓ navigate  ⏎/space toggle  ←/→ back/next  esc/q cancel{RESET}\n')
        lines += 1

        for i, name in enumerate(items):
            check = '✓' if name in selected else ' '
            if i == cursor:
                _write(f'  {COLORS["cyan"]}▸ [{check}] {name}{RESET}\n')
            else:
                _write(f'    [{check}] {DIM}{name}{RESET}\n')
            lines += 1

        # Done row
        done_idx = len(items)
        if cursor == done_idx:
            _write(f'  {COLORS["cyan"]}▸ Done{RESET}\n')
        else:
            _write(f'    {DIM}Done{RESET}\n')
        lines += 1

        total_lines_printed = lines

        key = _read_key()
        if key == 'up':
            cursor = (cursor - 1) % total_rows
        elif key == 'down':
            cursor = (cursor + 1) % total_rows
        elif key in ('enter', 'space'):
            if cursor == done_idx:
                if not selected:
                    selected.add(items[0])
                return [s for s in items if s in selected]
            else:
                name = items[cursor]
                if name in selected:
                    selected.discard(name)
                else:
                    selected.add(name)
        elif key == 'right':
            if not selected:
                selected.add(items[0])
            return [s for s in items if s in selected]
        elif key == 'left':
            return _BACK
        elif key == 'q' or key == 'escape':
            raise KeyboardInterrupt


# --- Install ---

def prompt_choice(label, options, default=None):
    """Prompt user to pick from a list. Returns selected value."""
    print(f"\n{label}")
    default_idx = 0
    for i, (key, display) in enumerate(options):
        marker = " (default)" if key == default else ""
        print(f"  {i + 1}. {display}{marker}")
        if key == default:
            default_idx = i
    while True:
        raw = input(f"Choose [1-{len(options)}] (default: {default_idx + 1}): ").strip()
        if not raw:
            return options[default_idx][0]
        try:
            idx = int(raw) - 1
            if 0 <= idx < len(options):
                return options[idx][0]
        except ValueError:
            pass
        print(f"  Please enter a number between 1 and {len(options)}.")


def prompt_int(label, default, min_val=1, max_val=50):
    """Prompt user for an integer value."""
    while True:
        raw = input(f"{label} (default: {default}): ").strip()
        if not raw:
            return default
        try:
            val = int(raw)
            if min_val <= val <= max_val:
                return val
        except ValueError:
            pass
        print(f"  Please enter a number between {min_val} and {max_val}.")


def prompt_sections(available, default):
    """Prompt user to toggle sections on/off."""
    print("\nSections (toggle on/off):")
    selected = set(default)
    for i, name in enumerate(available):
        state = "ON" if name in selected else "OFF"
        print(f"  {i + 1}. {name} [{state}]")
    print("  Enter numbers to toggle, or press Enter to accept.")
    while True:
        raw = input("Toggle (e.g. '3 5') or Enter to accept: ").strip()
        if not raw:
            return [s for s in available if s in selected]
        for part in raw.split():
            try:
                idx = int(part) - 1
                if 0 <= idx < len(available):
                    name = available[idx]
                    if name in selected:
                        selected.discard(name)
                    else:
                        selected.add(name)
            except ValueError:
                pass
        if not selected:
            print("  At least one section must be enabled.")
            selected.add(available[0])
        for i, name in enumerate(available):
            state = "ON" if name in selected else "OFF"
            print(f"  {i + 1}. {name} [{state}]")


def install():
    settings_path = Path.home() / ".claude" / "settings.json"
    script_path = Path(__file__).resolve()

    settings = {}
    if settings_path.exists():
        try:
            with open(settings_path) as f:
                settings = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass

    python_cmd = sys.executable
    settings["statusLine"] = {
        "type": "command",
        "command": f'"{python_cmd}" "{script_path}"',
    }

    settings_path.parent.mkdir(parents=True, exist_ok=True)
    with open(settings_path, "w") as f:
        json.dump(settings, f, indent=2)

    print(f"Installed ccbar to {settings_path}")
    print(f"Command: \"{python_cmd}\" \"{script_path}\"")
    print("Restart Claude Code to see the status line.")


def _interactive_install_fallback():
    """Fallback interactive install using input() for non-TTY environments."""
    try:
        import readline  # noqa: F401 — enables line editing in input() prompts
    except ImportError:
        pass
    try:
        print(f"ccbar {VERSION} — Interactive Setup\n")
        print("Configure your status bar. Press Enter to accept defaults.\n")

        theme_options = [("default", "default (green/yellow/red)")] + [
            (k, k) for k in THEMES if k != "default"
        ]
        theme = prompt_choice("Theme:", theme_options, default="default")

        style_options = [(k, f"{k}  {f}{f}{f}{e}{e}") for k, (f, e) in BAR_STYLES.items()]
        bar_style = prompt_choice("Bar style:", style_options, default="default")

        bar_width = prompt_int("\nBar width", default=8, min_val=3, max_val=30)

        layout_options = [
            ("standard", "standard — full labels (Session, Weekly, ...)"),
            ("compact", "compact — short labels (Ses, Wk, ...)"),
            ("minimal", "minimal — no labels, bars only"),
        ]
        layout = prompt_choice("Layout:", layout_options, default="standard")

        all_sections = list(RENDERERS.keys())
        sections = prompt_sections(all_sections, DEFAULT_CONFIG["sections"])

        git_status_options = [
            (False, "branch only — ⎇ main"),
            (True, "branch + status — ⎇ main +2 *1 ?3"),
        ]
        git_status = prompt_choice("Git status:", git_status_options, default=False)

        user_config = {
            "theme": theme,
            "bar": {"style": bar_style, "width": bar_width},
            "layout": layout,
            "sections": sections,
            "git_status": git_status,
        }

        preview_cfg = dict(DEFAULT_CONFIG)
        preview_cfg["bar"] = user_config["bar"]
        theme_colors = THEMES.get(theme, {})
        preview_cfg["colors"] = {**DEFAULT_CONFIG["colors"], **theme_colors}
        preview_cfg["layout"] = layout
        preview_cfg["sections"] = sections
        preview_cfg["update_check"] = False

        preview_usage = {
            "five_hour": {"utilization": 42, "resets_at": None},
            "seven_day": {"utilization": 15},
        }
        preview_ctx = {
            "cwd": os.getcwd(),
            "model": {"display_name": "Claude Sonnet 4"},
            "context_window": {"used_percentage": 28},
        }
        line = build_status_line(preview_usage, "Pro", preview_ctx, preview_cfg)
        print(f"\nPreview: {line}\n")

        confirm = input("Apply this configuration? [Y/n] ").strip().lower()
        if confirm and confirm != "y":
            print("Installation cancelled.")
            return

        config_path = get_config_path()
        with open(config_path, "w") as f:
            json.dump(user_config, f, indent=2)
        print(f"\nConfig saved to {config_path}")
        install()
        print("\nRestart Claude Code to activate.")
    except (KeyboardInterrupt, EOFError):
        print("\n\nInstallation cancelled.")
        sys.exit(1)


def interactive_install():
    """Interactive installation wizard for ccbar."""
    if not _tui_available():
        _interactive_install_fallback()
        return
    fd, old_term = _enter_raw_mode()
    cancelled = False
    user_config = None
    try:
        _write(_HIDE_CURSOR)
        user_config = _interactive_install_inner()
    except (KeyboardInterrupt, EOFError):
        cancelled = True
    finally:
        _write(_SHOW_CURSOR)
        _exit_raw_mode(fd, old_term)

    # All print() / install() calls happen here, after terminal is restored
    if cancelled:
        print("\n\nInstallation cancelled.")
        sys.exit(1)

    if user_config is None:
        print("\nInstallation cancelled.")
        return

    config_path = get_config_path()
    with open(config_path, "w") as f:
        json.dump(user_config, f, indent=2)
    print(f"\nConfig saved to {config_path}")
    install()
    print("\nRestart Claude Code to activate.")


def _interactive_install_inner():
    """TUI-based interactive install with live preview. Returns config dict or None to cancel."""
    # Load existing config as defaults when re-running
    existing = {}
    try:
        with open(get_config_path()) as f:
            existing = json.load(f)
    except Exception:
        pass

    state = {
        "theme": existing.get("theme", "default"),
        "bar_style": existing.get("bar", {}).get("style", "default"),
        "bar_width": existing.get("bar", {}).get("width", 8),
        "layout": existing.get("layout", "standard"),
        "sections": existing.get("sections", list(DEFAULT_CONFIG["sections"])),
        "git_status": existing.get("git_status", False),
    }

    preview_usage = {
        "five_hour": {"utilization": 42, "resets_at": None},
        "seven_day": {"utilization": 15},
    }
    preview_ctx = {
        "cwd": os.getcwd(),
        "model": {"display_name": "Claude Sonnet 4"},
        "context_window": {"used_percentage": 28},
    }

    def _build_preview_cfg(overrides=None):
        """Build a resolved config from state with optional overrides."""
        s = dict(state)
        if overrides:
            s.update(overrides)
        cfg = dict(DEFAULT_CONFIG)
        cfg["bar"] = {"style": s["bar_style"], "width": s["bar_width"]}
        theme_colors = THEMES.get(s["theme"], {})
        cfg["colors"] = {**DEFAULT_CONFIG["colors"], **theme_colors}
        cfg["layout"] = s["layout"]
        cfg["sections"] = s["sections"]
        cfg["git_status"] = s.get("git_status", False)
        cfg["update_check"] = False
        return cfg

    def _render_preview_with(overrides=None):
        """Print the preview bar with optional overrides applied. Returns line count."""
        cfg = _build_preview_cfg(overrides)
        line = build_status_line(preview_usage, "Pro", preview_ctx, cfg)
        _write(f'{DIM}{"─" * 60}{RESET}\n')
        _write(f'  {line}\n')
        _write(f'{DIM}{"─" * 60}{RESET}\n')
        _write('\n')
        return 4

    # Define wizard steps. Each step is a function that calls a widget and
    # applies the result to state. Returns _BACK to go back, or anything else to advance.
    theme_options = [("default", "default (green/yellow/red)")] + [
        (k, k) for k in THEMES if k != "default"
    ]
    style_options = [(k, f"{k}  {f}{f}{f}{e}{e}") for k, (f, e) in BAR_STYLES.items()]
    layout_options = [
        ("standard", "standard — full labels (Session, Weekly, ...)"),
        ("compact", "compact — short labels (Ses, Wk, ...)"),
        ("minimal", "minimal — no labels, bars only"),
    ]
    all_sections = list(RENDERERS.keys())
    git_status_options = [
        (False, "branch only — ⎇ main"),
        (True, "branch + status — ⎇ main +2 *1 ?3"),
    ]

    def _find_idx(options, value):
        for i, (v, _) in enumerate(options):
            if v == value:
                return i
        return 0

    def step_theme():
        result = _tui_select(
            "Theme:", theme_options, default_idx=_find_idx(theme_options, state["theme"]),
            preview_fn=lambda val: _render_preview_with({"theme": val}),
        )
        if result is _BACK:
            return _BACK
        state["theme"] = result[0]

    def step_bar_style():
        result = _tui_select(
            "Bar style:", style_options, default_idx=_find_idx(style_options, state["bar_style"]),
            preview_fn=lambda val: _render_preview_with({"bar_style": val}),
        )
        if result is _BACK:
            return _BACK
        state["bar_style"] = result[0]

    def step_bar_width():
        result = _tui_int_input(
            "Bar width:", default=state["bar_width"], min_val=3, max_val=30,
            preview_fn=lambda val: _render_preview_with({"bar_width": val}),
        )
        if result is _BACK:
            return _BACK
        state["bar_width"] = result

    def step_layout():
        result = _tui_select(
            "Layout:", layout_options, default_idx=_find_idx(layout_options, state["layout"]),
            preview_fn=lambda val: _render_preview_with({"layout": val}),
        )
        if result is _BACK:
            return _BACK
        state["layout"] = result[0]

    def step_sections():
        result = _tui_toggle(
            "Sections:", all_sections, set(state["sections"]),
            preview_fn=lambda val: _render_preview_with({"sections": val}),
        )
        if result is _BACK:
            return _BACK
        state["sections"] = result

    def step_git_status():
        default_idx = 1 if state["git_status"] else 0
        result = _tui_select(
            "Git status:", git_status_options, default_idx=default_idx,
            preview_fn=lambda val: _render_preview_with({"git_status": val}),
        )
        if result is _BACK:
            return _BACK
        state["git_status"] = result[0]

    def step_confirm():
        _write(f'\n{_BOLD}Final preview:{RESET}\n\n')
        _render_preview_with()
        _write('\n')
        confirm_options = [("yes", "Yes — apply and install"), ("no", "No — cancel")]
        result = _tui_select("Apply this configuration?", confirm_options, default_idx=0, preview_fn=None)
        if result is _BACK:
            return _BACK
        if result[0] != "yes":
            return "cancel"
        return "done"

    steps = [step_theme, step_bar_style, step_bar_width, step_layout,
             step_sections, step_git_status, step_confirm]

    # Step header
    _write(f'\n{_BOLD}ccbar {VERSION} — Interactive Setup{RESET}\n\n')

    i = 0
    while i < len(steps):
        _write(_CLEAR_SCREEN)
        result = steps[i]()
        if result is _BACK:
            if i > 0:
                i -= 1
            continue
        if result == "cancel":
            return None
        if result == "done":
            return {
                "theme": state["theme"],
                "bar": {"style": state["bar_style"], "width": state["bar_width"]},
                "layout": state["layout"],
                "sections": state["sections"],
                "git_status": state["git_status"],
            }
        # Normal step completion (returns None implicitly) — advance
        i += 1

    # Should not reach here, but just in case
    return None


# --- Main ---

def parse_argv(argv):
    """Parse --show and --hide flags from argv."""
    show = hide = None
    i = 1
    while i < len(argv):
        if argv[i] == "--show" and i + 1 < len(argv):
            show = [s.strip() for s in argv[i + 1].split(",") if s.strip()]
            i += 2
        elif argv[i] == "--hide" and i + 1 < len(argv):
            hide = [s.strip() for s in argv[i + 1].split(",") if s.strip()]
            i += 2
        else:
            i += 1
    return show, hide


def main():
    _enable_ansi_windows()

    if "--version" in sys.argv:
        print(f"ccbar {VERSION}")
        cfg = load_config()
        latest = check_for_update(cfg)
        if latest:
            script_dir = Path(__file__).resolve().parent
            print(f"Update available: {VERSION} -> {latest}")
            print(f"Run: git -C {script_dir} pull")
        return

    if "--update" in sys.argv:
        do_update()
        return

    if "--install" in sys.argv:
        if "--default" in sys.argv:
            install()
        else:
            interactive_install()
        return

    if "--config" in sys.argv:
        print(get_config_path())
        return

    cfg = load_config()

    show, hide = parse_argv(sys.argv)
    if show is not None:
        cfg["sections"] = show
    elif hide is not None:
        cfg["sections"] = [s for s in cfg["sections"] if s not in hide]

    ctx = {}
    try:
        raw = sys.stdin.read()
        if raw.strip():
            ctx = json.loads(raw)
    except Exception:
        pass

    cache_path = get_cache_path()
    cached = read_cache(cache_path)

    if cached is not None:
        is_error = cached.get("usage") is None
        ttl = cfg["error_cache_ttl"] if is_error else cfg["cache_ttl"]
        if time.time() - cached.get("timestamp", 0) < ttl:
            if not is_error:
                line = build_status_line(cached["usage"], cached.get("plan", ""), ctx, cfg)
            else:
                error = cached.get("error", "Usage unavailable")
                line = build_status_line(None, cached.get("plan", ""), ctx, cfg, error=error)
            sys.stdout.buffer.write((line + "\n").encode("utf-8"))
            return

    token, plan = get_credentials()
    if not token:
        sys.stdout.buffer.write(b"No credentials found\n")
        return

    error = None
    try:
        usage = fetch_usage(token)
        line = build_status_line(usage, plan, ctx, cfg)
    except urllib.error.HTTPError as e:
        usage = None
        error = f"API error: {e.code}"
        line = build_status_line(usage, plan, ctx, cfg, error=error)
    except Exception:
        usage = None
        error = "Usage unavailable"
        line = build_status_line(usage, plan, ctx, cfg, error=error)

    write_cache(cache_path, usage, plan, error=error)
    sys.stdout.buffer.write((line + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
