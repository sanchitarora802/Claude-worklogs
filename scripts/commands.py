#!/usr/bin/env python3
# The plugin's commands other than logging itself.
#
#   commands.py check            SessionStart hook: warn when no log folder is set or it's missing;
#                                when it's fine, start a background catch-up of unlogged chats.
#                                Must stay fast and silent, so it never calls the CLI or waits.
#   commands.py setup [PATH]     /worklog:setup. Without PATH: shows a note on choosing a folder,
#                                then this system's folder picker (see platforms.py). Saves the
#                                choice, checks it's writable, then logs every past chat into it.
#                                Then the "repos not to log" checklist, as in `repos`.
#   commands.py repos [PATH…]    /worklog:repos: tick the folders not to log, from every folder
#                                Claude Code has been used in. With paths, sets the list directly;
#                                "none" clears it. Un-ticked folders are logged from now on only.
#   commands.py status           /worklog:status: folder, settings, each computer's latest file.
#   commands.py day DATE         /worklog:day: prints that day's log from every computer, for Claude
#                                to summarise. DATE is YYYY-MM-DD, "today" or "yesterday".
import json
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402
import platforms  # noqa: E402
import worklog  # noqa: E402


def writable(folder):
    try:
        probe = folder / ".worklog-write-test"
        probe.write_text("ok")
        probe.unlink()
        return True
    except OSError:
        return False


REPOS_TITLE = "Worklog: repos not to log"
REPOS_PROMPT = ("Tick the folders worklog should NOT log. A ticked folder covers its subfolders. "
                "Folders you use later are logged unless you tick them here.")


def shown(path):
    home = str(Path.home())
    text = str(path)
    return "~" + text[len(home):] if text == home or text.startswith(home + "/") or text.startswith(home + "\\") else text


def known_folders():
    # Every folder a Claude Code chat has run in (the "cwd" of your messages), plus any already excluded.
    found = set()
    for transcript in worklog.PROJECTS_DIR.glob("*/*.jsonl"):
        with open(transcript, "rb") as f:
            for line in f:
                if b'"human"' not in line or b'"cwd"' not in line:
                    continue
                try:
                    cwd = json.loads(line).get("cwd")
                except json.JSONDecodeError:
                    continue
                if cwd:
                    found.add(str(Path(cwd)))
    found.update(str(p) for p in config.Settings().exclude)
    return sorted(found, key=str.lower)


def choose_repos():
    # Returns a message describing the result.
    folders = known_folders()
    current = [str(p) for p in config.Settings().exclude]
    labels = [shown(f) for f in folders]
    try:
        picked = platforms.choose_many(REPOS_TITLE, REPOS_PROMPT, labels, [shown(c) for c in current])
    except platforms.NoPicker:
        listing = "\n".join(f"  {label}" for label in labels)
        return ("This system can't show a checklist. Folders Claude Code has been used in:\n" + listing
                + "\nSet the ones not to log with: /worklog:repos <folder> [<folder> …]")
    if picked is None:
        return "Repos list unchanged."
    chosen = [folders[labels.index(label)] for label in picked]
    config.save_exclude(chosen)
    return describe_exclude(chosen)


def describe_exclude(paths):
    if not paths:
        return "Logging every repo."
    return "Not logging (subfolders included):\n" + "\n".join(f"  - {shown(p)}" for p in paths)


def repos(args):
    if args:
        chosen = [] if [a.lower() for a in args] == ["none"] else [str(Path(a).expanduser()) for a in args]
        config.save_exclude(chosen)
        print(describe_exclude(chosen))
    else:
        print(choose_repos())
    print("\nChanges apply from now on. Chats already logged stay in the log.")


def setup(args):
    current = config.log_root(ask_cli=True)
    if args:
        folder = Path(" ".join(args)).expanduser()
    else:
        note = "Choose where your daily Claude work logs are saved.\n\n" + config.FOLDER_NOTE
        try:
            folder = platforms.pick_folder(current, note, config.HELP_URL)
        except platforms.NoPicker:
            sys.exit("This system can't show a folder picker (no desktop session found). "
                     "Run it with the path instead: /worklog:setup /path/to/folder")
        if folder is None:
            print("Setup cancelled. Nothing changed." + (f" Logs still go to {current}" if current else ""))
            return
    if not folder.is_dir():
        sys.exit(f"Folder not found: {folder}")
    if not writable(folder):
        sys.exit(f"Can't write to {folder}. Pick another folder.")
    where = config.save_log_root(folder)
    repos_result = choose_repos() if not args else describe_exclude([str(p) for p in config.Settings().exclude])
    read, wrote = worklog.run(folder, "backfill")
    print(f"Log folder: {folder}")
    print(f"Saved to: {where}")
    print(f"This computer writes to: {folder / worklog.MACHINE}")
    print(f"Logged past chats: read {read} transcripts, wrote {wrote} day files.")
    print(repos_result)
    print("\nNote: " + config.FOLDER_NOTE)


def describe_settings(settings):
    oldest = settings.oldest_day()
    lines = [f"Logging: {'on' if settings.enabled else 'OFF (chats while off are never logged)'}",
             f"Keep logs for: {f'{settings.keep_days} days' if settings.keep_days else 'forever'}",
             f"Backfill from: {settings.backfill_from or 'everything still on disk'}"
             + (f" (oldest day logged: {oldest})" if oldest else ""),
             describe_exclude([str(p) for p in settings.exclude])]
    return "\n".join(lines)


def status(_args):
    root = config.log_root(ask_cli=True)
    if not root:
        print("No log folder set, so nothing is being logged. Run /worklog:setup.")
        return
    print(f"Log folder: {root}" + ("" if root.is_dir() else "  (MISSING: is the sync drive available?)"))
    print(f"This computer: {worklog.MACHINE}")
    print(describe_settings(config.Settings(ask_cli=True)))
    print("Change these with /config, /plugin configure worklog@worklog, or /worklog:repos.\n")
    if not root.is_dir():
        return
    for machine in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")):
        files = sorted(machine.glob("????-??-??.md"))
        if not files:
            continue
        latest = files[-1]
        when = datetime.fromtimestamp(latest.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        size = sum(f.stat().st_size for f in files) // 1024
        mark = " (this computer)" if machine.name == worklog.MACHINE else ""
        print(f"- {machine.name}{mark}: {len(files)} days, {size} KB, latest {latest.name} updated {when}")
    today = root / worklog.MACHINE / f"{date.today():%Y-%m-%d}.md"
    if today.exists():
        count = today.read_text(encoding="utf-8").count("\n**You:**")
        print(f"Today on this computer: {count} messages logged")
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
        print(f"No log for {wanted:%Y-%m-%d} from any computer in {root}.")
        return
    for path in found:
        print(f"===== {path.parent.name} =====")
        print(path.read_text(encoding="utf-8"))


def catch_up():
    # Detached so the new chat never waits; the lock in worklog.py keeps it from racing a hook.
    subprocess.Popen([sys.executable, str(Path(__file__).resolve().parent / "worklog.py"), "--catchup"],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     **platforms.detached())


def check(_args):
    # SessionStart: silent when fine; otherwise a one-line warning shown to the user.
    root = config.log_root()
    settings = config.Settings()
    if not settings.enabled:
        catch_up()  # marks chats as read without logging them, so they're skipped for good
        print(json.dumps({"systemMessage": "worklog: logging is off, so this chat won't be logged. "
                                            "Turn it on in /config (worklog: Logging)."}))
        return
    if root and root.is_dir():
        catch_up()
        return
    if root:
        message = (f"worklog: log folder {root} is missing (sync drive not mounted?), so this "
                   "session isn't being logged yet. It catches up once the folder is back.")
    else:
        message = "worklog: no log folder set, so nothing is logged. Run /worklog:setup."
    print(json.dumps({"systemMessage": message}))


COMMANDS = {"check": check, "setup": setup, "repos": repos, "status": status, "day": day}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        sys.exit("usage: commands.py check|setup [PATH]|repos [PATH…|none]|status|day DATE")
    COMMANDS[sys.argv[1]](sys.argv[2:])
