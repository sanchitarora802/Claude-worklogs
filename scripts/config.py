# Where worklog reads its settings and keeps its state.
#
# Plugin options (plugin.json "userConfig"), each read the same way, first match wins:
#   1. CLAUDE_PLUGIN_OPTION_<KEY>: what Claude Code passes to hooks
#   2. WORKLOG_<KEY>: manual override, used for tests and running outside the plugin
#   3. the option as stored by Claude Code, read through `claude plugin configure --json`
#      (slow, so only slash commands and manual runs ask; hooks always get 1)
#   4. config.json in the data folder, where /worklog:setup keeps a copy
#   5. the default below
# Options: log_dir (folder), logging_enabled (on/off), keep_days (0 = forever),
# backfill_from (YYYY-MM-DD, empty = everything still on disk).
#
# The repos not to log ("exclude", a list of folder paths) live only in config.json: Claude Code's
# saved options take single-line strings, which doesn't suit a list of paths.
#
# State (parse offsets and per-day JSON) lives in the plugin's data folder, which Claude Code keeps
# across updates and removes on uninstall: CLAUDE_PLUGIN_DATA, or the same path worked out by hand
# when a slash command runs a script outside a hook. WORKLOG_STATE_DIR overrides it for tests.
import glob
import json
import os
import shutil
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import platforms  # noqa: E402

PLUGIN_ID = "worklog@sanchitarora802"
DEFAULTS = {"log_dir": "", "logging_enabled": True, "keep_days": 0, "backfill_from": ""}
FOLDER_NOTE = (
    "Pick a folder that syncs between your own computers (iCloud Drive, Google Drive, Dropbox, "
    "OneDrive), so every computer's log ends up in one place. Each writes to its own subfolder inside it.\n\n"
    "Don't pick a folder shared with other people: logs hold your full messages, ticket numbers "
    "and the prod commands that ran."
)
HELP_URL = "https://github.com/sanchitarora802/Claude-worklogs#choosing-a-log-folder"


def machine_name():
    return platforms.machine_name()


def claude_home():
    # Claude Code's own folder: ~/.claude, unless moved with CLAUDE_CONFIG_DIR.
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude").expanduser()


def data_dir():
    explicit = os.environ.get("WORKLOG_STATE_DIR") or os.environ.get("CLAUDE_PLUGIN_DATA")
    if explicit:
        return Path(explicit).expanduser()
    return claude_home() / "plugins" / "data" / PLUGIN_ID.replace("@", "-")


def claude_cli():
    for name in platforms.cli_names():
        found = shutil.which(name)
        if found:
            return found
    for editor in (".vscode", ".cursor", ".vscode-insiders"):
        for name in platforms.cli_names():
            paths = glob.glob(str(Path.home() / editor / "extensions" / "anthropic.claude-code-*"
                                  / "resources" / "native-binary" / name))
            if paths:
                return sorted(paths, key=version_key)[-1]
    return None


def version_key(path):
    # Sorts extension folders like anthropic.claude-code-2.1.319-darwin-arm64 by version number.
    return [int(x) if x.isdigit() else x for x in path.replace("-", ".").split(".")]


_cli_values = None


def saved_options():
    # Claude Code's stored values for this plugin, fetched once per run.
    global _cli_values
    if _cli_values is None:
        _cli_values = {}
        cli = claude_cli()
        if cli:
            try:
                out = subprocess.run([cli, "plugin", "configure", PLUGIN_ID, "--json"], capture_output=True,
                                     text=True, timeout=20).stdout
                tree = json.loads(out)
                # Saved values sit under "inputs", as strings; an empty string means "not set".
                inputs = tree.get("inputs") if isinstance(tree, dict) else None
                for key in DEFAULTS:
                    found = inputs.get(key) if isinstance(inputs, dict) else find_value(tree, key)
                    if found not in (None, ""):
                        _cli_values[key] = found
            except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
                pass
    return _cli_values


def find_value(node, key):
    # The JSON shape of `configure --json` isn't documented; look for the key anywhere in it.
    if isinstance(node, dict):
        if key in node:
            value = node[key]
            if isinstance(value, dict):
                value = value.get("value")
            if value not in (None, "", [], {}):
                return value
        if node.get("key") == key and node.get("value") not in (None, ""):
            return node["value"]
        nodes = node.values()
    elif isinstance(node, list):
        nodes = node
    else:
        return None
    for child in nodes:
        found = find_value(child, key)
        if found is not None:
            return found
    return None


def fallback_file():
    return data_dir() / "config.json"


def local_config():
    try:
        return json.loads(fallback_file().read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def update_local_config(**values):
    folder = data_dir()
    folder.mkdir(parents=True, exist_ok=True)
    merged = dict(local_config(), **values)
    tmp = fallback_file().with_suffix(".tmp")
    tmp.write_text(json.dumps(merged, indent=2))
    tmp.replace(fallback_file())


def option(key, ask_cli=False):
    for name in (f"CLAUDE_PLUGIN_OPTION_{key.upper()}", f"WORKLOG_{key.upper()}"):
        if os.environ.get(name, "") != "":
            return os.environ[name]
    if ask_cli and key in saved_options():
        return saved_options()[key]
    if local_config().get(key) not in (None, ""):
        return local_config()[key]
    return DEFAULTS[key]


def as_bool(value):
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in ("false", "0", "no", "off", "")


def as_int(value):
    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        return 0


def as_date(value):
    try:
        return date.fromisoformat(str(value).strip()) if value else None
    except ValueError:
        return None


def log_root(ask_cli=False):
    # WORKLOG_DIR is the long-standing test override; keep it working alongside WORKLOG_LOG_DIR.
    value = os.environ.get("CLAUDE_PLUGIN_OPTION_LOG_DIR") or os.environ.get("WORKLOG_DIR") \
        or option("log_dir", ask_cli)
    return Path(str(value)).expanduser() if value else None


class Settings:
    # Everything that decides whether a message gets logged.
    def __init__(self, ask_cli=False):
        self.enabled = as_bool(option("logging_enabled", ask_cli))
        self.keep_days = as_int(option("keep_days", ask_cli))
        self.backfill_from = as_date(option("backfill_from", ask_cli))
        self.exclude = [Path(p).expanduser() for p in local_config().get("exclude", []) if p]

    def oldest_day(self, today=None):
        # The first day that may be logged: the later of "backfill from" and "keep logs for N days".
        today = today or date.today()
        limits = [d for d in (self.backfill_from,
                              today - timedelta(days=self.keep_days - 1) if self.keep_days else None) if d]
        return max(limits) if limits else None

    def excluded(self, cwd):
        if not cwd:
            return False
        here = os.path.normcase(str(Path(cwd)))
        for folder in self.exclude:
            root = os.path.normcase(str(folder)).rstrip("/\\")
            if here == root or here.startswith(root + os.sep) or here.startswith(root + "/"):
                return True
        return False


def save_option(key, value):
    # Saves to Claude Code's plugin options (what hooks read) and to config.json. Returns where it went.
    update_local_config(**{key: value})
    cli = claude_cli()
    if not cli:
        return "fallback file (Claude Code CLI not found)"
    try:
        done = subprocess.run([cli, "plugin", "configure", PLUGIN_ID, "--values-stdin"],
                              input=json.dumps({key: str(value)}), capture_output=True, text=True, timeout=30)
        if done.returncode == 0:
            return "plugin option"
        return "fallback file (plugin option not saved: %s)" % (done.stderr.strip() or done.stdout.strip())[:200]
    except (OSError, subprocess.SubprocessError) as e:
        return f"fallback file (plugin option not saved: {e})"


def save_log_root(path):
    return save_option("log_dir", str(path))


def save_exclude(paths):
    update_local_config(exclude=[str(p) for p in paths])
