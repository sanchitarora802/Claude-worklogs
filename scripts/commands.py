#!/usr/bin/env python3
# The plugin's commands other than logging itself.
#
#   commands.py check            SessionStart hook: warn when no log folder is set or it's missing.
#                                Must stay fast and silent when all is well, so it never calls the CLI.
#   commands.py setup [PATH]     /worklog:setup. Without PATH (on a Mac): shows a note on
#                                choosing a folder (with a Help button that opens the README), then
#                                the Finder folder picker. Saves the choice, checks it's writable,
#                                then logs every past chat into it.
#   commands.py status           /worklog:status: folder, this Mac's latest file, other Macs.
#   commands.py day DATE         /worklog:day: prints that day's log from every Mac, for Claude
#                                to summarise. DATE is YYYY-MM-DD, "today" or "yesterday".
import json
import subprocess
import sys
import webbrowser
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402
import worklog  # noqa: E402


def applescript_text(text):
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def osascript(script):
    done = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    return done.returncode, done.stdout.strip(), done.stderr.strip()


def pick_folder(current):
    # Returns a folder path, or None if the user cancelled.
    while True:
        code, out, _ = osascript(
            "display dialog " + applescript_text("Choose where your daily Claude work logs are saved.\n\n"
                                                 + config.FOLDER_NOTE)
            + ' with title "Worklog setup" with icon note'
            + ' buttons {"Help", "Cancel", "Choose Folder…"} default button "Choose Folder…"'
            + ' cancel button "Cancel"')
        if code != 0:
            return None
        if "Help" not in out:
            break
        webbrowser.open(config.HELP_URL)
    start = current if current and current.is_dir() else Path.home() / "Documents"
    code, out, _ = osascript(
        "POSIX path of (choose folder with prompt "
        + applescript_text("Choose a folder that syncs between your own Macs (not one shared with other people)")
        + " default location (POSIX file " + applescript_text(str(start)) + "))")
    return Path(out.rstrip("/")) if code == 0 and out else None


def writable(folder):
    try:
        probe = folder / ".worklog-write-test"
        probe.write_text("ok")
        probe.unlink()
        return True
    except OSError:
        return False


def setup(args):
    current = config.log_root(ask_cli=True)
    if args:
        folder = Path(" ".join(args)).expanduser()
    elif sys.platform == "darwin":
        folder = pick_folder(current)
        if folder is None:
            print("Setup cancelled. Nothing changed." + (f" Logs still go to {current}" if current else ""))
            return
    else:
        sys.exit("No folder picker on this system. Run: /worklog:setup /path/to/folder")
    if not folder.is_dir():
        sys.exit(f"Folder not found: {folder}")
    if not writable(folder):
        sys.exit(f"Can't write to {folder}. Pick another folder.")
    where = config.save_log_root(folder)
    read, wrote = worklog.run(folder, "backfill")
    print(f"Log folder: {folder}")
    print(f"Saved to: {where}")
    print(f"This Mac writes to: {folder / worklog.MACHINE}")
    print(f"Logged past chats: read {read} transcripts, wrote {wrote} day files.")
    print("\nNote: " + config.FOLDER_NOTE)


def status(_args):
    root = config.log_root(ask_cli=True)
    if not root:
        print("No log folder set, so nothing is being logged. Run /worklog:setup.")
        return
    print(f"Log folder: {root}" + ("" if root.is_dir() else "  (MISSING: is the sync drive available?)"))
    print(f"This Mac: {worklog.MACHINE}")
    if not root.is_dir():
        return
    for machine in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")):
        files = sorted(machine.glob("????-??-??.md"))
        if not files:
            continue
        latest = files[-1]
        when = datetime.fromtimestamp(latest.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        size = sum(f.stat().st_size for f in files) // 1024
        mark = " (this Mac)" if machine.name == worklog.MACHINE else ""
        print(f"- {machine.name}{mark}: {len(files)} days, {size} KB, latest {latest.name} updated {when}")
    today = root / worklog.MACHINE / f"{date.today():%Y-%m-%d}.md"
    if today.exists():
        count = today.read_text(encoding="utf-8").count("\n**You:**")
        print(f"Today on this Mac: {count} messages logged")
    print("\nNote: " + config.FOLDER_NOTE)


def day(args):
    word = (args[0] if args else "today").lower()
    if word == "today":
        wanted = date.today()
    elif word == "yesterday":
        wanted = date.today() - timedelta(days=1)
    else:
        try:
            wanted = date.fromisoformat(word)
        except ValueError:
            sys.exit("Give the date as YYYY-MM-DD, today or yesterday.")
    root = config.log_root(ask_cli=True)
    if not root:
        sys.exit("No log folder set. Run /worklog:setup first.")
    found = sorted(root.glob(f"*/{wanted:%Y-%m-%d}.md"))
    if not found:
        print(f"No log for {wanted:%Y-%m-%d} on any Mac in {root}.")
        return
    for path in found:
        print(f"===== {path.parent.name} =====")
        print(path.read_text(encoding="utf-8"))


def check(_args):
    # SessionStart: silent when fine; otherwise a one-line warning shown to the user.
    root = config.log_root()
    if root and root.is_dir():
        return
    if root:
        message = (f"worklog: log folder {root} is missing (sync drive not mounted?), so this "
                   "session isn't being logged yet. It catches up once the folder is back.")
    else:
        message = "worklog: no log folder set, so nothing is logged. Run /worklog:setup."
    print(json.dumps({"systemMessage": message}))


COMMANDS = {"check": check, "setup": setup, "status": status, "day": day}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        sys.exit("usage: commands.py check|setup [PATH]|status|day DATE")
    COMMANDS[sys.argv[1]](sys.argv[2:])
