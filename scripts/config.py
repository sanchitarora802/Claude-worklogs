# Where worklog reads its settings and keeps its state.
#
# Log folder, first match wins:
#   1. CLAUDE_PLUGIN_OPTION_LOG_DIR: the plugin's "log_dir" option, which Claude Code passes to hooks
#   2. WORKLOG_DIR: manual override, used for tests and running outside the plugin
#   3. the option as stored by Claude Code, read through `claude plugin configure --json`
#      (slow, so only the slash commands use it; hooks always get 1)
#   4. config.json in the data folder: the copy /worklog:setup saves as a fallback
#
# State (parse offsets and per-day JSON) lives in the plugin's data folder, which Claude Code keeps
# across updates and removes on uninstall: CLAUDE_PLUGIN_DATA, or the same path worked out by hand
# when a slash command runs a script outside a hook. WORKLOG_STATE_DIR overrides it for tests.
import glob
import json
import os
import shutil
import socket
import subprocess
from pathlib import Path

PLUGIN_ID = "worklog@worklog"
FOLDER_NOTE = (
    "Pick a folder that syncs between your own Macs (iCloud Drive, Google Drive, Dropbox), so every "
    "Mac's log ends up in one place. Each Mac writes to its own subfolder inside it.\n\n"
    "Don't pick a folder shared with other people: logs hold your full messages, ticket numbers "
    "and the prod commands that ran."
)
HELP_URL = "https://github.com/sanchitarora802/Claude-worklogs#choosing-a-log-folder"


def machine_name():
    try:
        return subprocess.run(["scutil", "--get", "LocalHostName"], capture_output=True, text=True,
                              timeout=5).stdout.strip() or socket.gethostname()
    except (OSError, subprocess.SubprocessError):
        return socket.gethostname()


def data_dir():
    explicit = os.environ.get("WORKLOG_STATE_DIR") or os.environ.get("CLAUDE_PLUGIN_DATA")
    if explicit:
        return Path(explicit).expanduser()
    return Path.home() / ".claude" / "plugins" / "data" / PLUGIN_ID.replace("@", "-")


def claude_cli():
    found = shutil.which("claude")
    if found:
        return found
    for editor in (".vscode", ".cursor", ".vscode-insiders"):
        paths = glob.glob(str(Path.home() / editor / "extensions" / "anthropic.claude-code-*"
                              / "resources" / "native-binary" / "claude"))
        if paths:
            return sorted(paths, key=lambda p: [int(x) if x.isdigit() else x
                                                for x in p.replace("-", ".").split(".")])[-1]
    return None


def saved_option():
    cli = claude_cli()
    if not cli:
        return None
    try:
        out = subprocess.run([cli, "plugin", "configure", PLUGIN_ID, "--json"], capture_output=True,
                             text=True, timeout=20).stdout
        return find_value(json.loads(out), "log_dir")
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return None


def find_value(node, key):
    # The JSON shape of `configure --json` isn't documented; look for the key anywhere in it.
    if isinstance(node, dict):
        if key in node:
            value = node[key]
            if isinstance(value, dict):
                value = value.get("value")
            if isinstance(value, str) and value:
                return value
        if node.get("key") == key and isinstance(node.get("value"), str) and node["value"]:
            return node["value"]
        nodes = node.values()
    elif isinstance(node, list):
        nodes = node
    else:
        return None
    for child in nodes:
        found = find_value(child, key)
        if found:
            return found
    return None


def fallback_file():
    return data_dir() / "config.json"


def log_root(ask_cli=False):
    value = os.environ.get("CLAUDE_PLUGIN_OPTION_LOG_DIR") or os.environ.get("WORKLOG_DIR")
    if not value and ask_cli:
        value = saved_option()
    if not value:
        try:
            value = json.loads(fallback_file().read_text()).get("log_dir")
        except (OSError, json.JSONDecodeError):
            value = None
    return Path(value).expanduser() if value else None


def save_log_root(path):
    # Saves to the plugin option (what hooks read) and to the fallback file. Returns where it went.
    folder = data_dir()
    folder.mkdir(parents=True, exist_ok=True)
    fallback_file().write_text(json.dumps({"log_dir": str(path)}))
    cli = claude_cli()
    if cli:
        try:
            done = subprocess.run([cli, "plugin", "configure", PLUGIN_ID, "--values-stdin"],
                                  input=json.dumps({"log_dir": str(path)}), capture_output=True,
                                  text=True, timeout=30)
            if done.returncode == 0:
                return "plugin option"
            return "fallback file (plugin option not saved: %s)" % (done.stderr.strip() or done.stdout.strip())[:200]
        except (OSError, subprocess.SubprocessError) as e:
            return f"fallback file (plugin option not saved: {e})"
    return "fallback file (Claude Code CLI not found)"
