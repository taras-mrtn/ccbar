#!/usr/bin/env python3
"""ccbar — configurable status line for Claude Code."""

VERSION = "0.2.0"

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
    "sections": ["git", "cwd", "model", "session", "weekly", "context", "credits", "plan"],
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
    cfg["sections"] = user.get("sections", DEFAULT_CONFIG["sections"])
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


def read_cache(path, ttl=30):
    try:
        with open(path) as f:
            cached = json.load(f)
        if time.time() - cached.get("timestamp", 0) < ttl:
            return cached
    except Exception:
        pass
    return None


def write_cache(path, usage=None, plan=None):
    try:
        with open(path, "w") as f:
            json.dump({"timestamp": time.time(), "usage": usage, "plan": plan}, f)
    except OSError:
        pass


# --- Git ---

def get_git_info(cwd):
    if not cwd:
        return None, None
    try:
        branch_r = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=3, cwd=cwd,
        )
        if branch_r.returncode != 0:
            return None, None
        branch = branch_r.stdout.strip()

        status_r = subprocess.run(
            ["git", "status", "--porcelain"],
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
    if git_status:
        part += f" {git_status}"
    return part


def render_cwd(usage, plan, ctx, cfg):
    cwd = ctx.get("cwd") or (ctx.get("workspace") or {}).get("current_dir")
    if not cwd:
        return None
    return Path(cwd).name


def render_model(usage, plan, ctx, cfg):
    model = (ctx.get("model") or {}).get("display_name") or (ctx.get("model") or {}).get("id")
    return model or None


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


def build_status_line(usage, plan, ctx, cfg):
    parts = []
    for section in cfg["sections"]:
        renderer = RENDERERS.get(section)
        if renderer:
            result = renderer(usage, plan, ctx, cfg)
            if result:
                parts.append(result)
    return " | ".join(parts)


# --- Install ---

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
        return

    if "--install" in sys.argv:
        install()
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
    cached = read_cache(cache_path, cfg["cache_ttl"])

    if cached and cached.get("usage") is not None:
        line = build_status_line(cached["usage"], cached.get("plan", ""), ctx, cfg)
        sys.stdout.buffer.write((line + "\n").encode("utf-8"))
        return

    token, plan = get_credentials()
    if not token:
        sys.stdout.buffer.write(b"No credentials found\n")
        return

    try:
        usage = fetch_usage(token)
        line = build_status_line(usage, plan, ctx, cfg)
    except urllib.error.HTTPError as e:
        usage = None
        line = f"API error: {e.code}"
    except Exception:
        usage = None
        line = "Usage unavailable"

    write_cache(cache_path, usage, plan)
    sys.stdout.buffer.write((line + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
