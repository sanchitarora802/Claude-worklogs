#!/usr/bin/env python3
# Work log for every Claude Code session, across all projects.
#
# Runs as a Stop hook (after every turn) and a SessionEnd hook. Each run reads only the part of
# the session transcript it has not seen yet (byte offset kept in ~/.claude/worklog-state.json,
# local to this machine) and appends, to $WORKLOG_DIR/<machine>/YYYY-MM-DD.md (local date of the
# message; WORKLOG_DIR defaults to ~/worklog):
#   - your messages, in full (IDE/system tags stripped, images noted as [image])
#   - Claude's replies, text only (no tool calls or tool output)
# SessionEnd also copies the raw transcript to $WORKLOG_DIR/<machine>/transcripts/<project>/, so
# the full detail survives Claude Code's own transcript cleanup. One folder per machine keeps two
# Macs syncing the same WORKLOG_DIR (Google Drive, iCloud) from ever writing the same file.
# Strings that look like API keys or tokens are masked before anything is written.
#
#   worklog.py               hook mode, reads the hook JSON on stdin
#   worklog.py --backfill    log every transcript under ~/.claude/projects (safe to re-run)
import fcntl
import json
import os
import re
import shutil
import socket
import subprocess
import sys
from datetime import datetime
from pathlib import Path

def machine_name():
    try:
        return subprocess.run(["scutil", "--get", "LocalHostName"], capture_output=True, text=True,
                              timeout=5).stdout.strip() or socket.gethostname()
    except (OSError, subprocess.SubprocessError):
        return socket.gethostname()


ROOT_DIR = Path(os.environ.get("WORKLOG_DIR") or Path.home() / "worklog").expanduser()
LOG_DIR = ROOT_DIR / machine_name()
STATE_FILE = Path.home() / ".claude" / "worklog-state.json"
LOCK_FILE = Path.home() / ".claude" / "worklog.lock"
PROJECTS_DIR = Path.home() / ".claude" / "projects"

STRIP_TAGS = re.compile(
    r"<(system-reminder|ide_selection|ide_opened_file|ide_diagnostics|command-message|"
    r"command-args|local-command-stdout|local-command-caveat)>.*?</\1>",
    re.S,
)
SECRETS = re.compile(
    r"(sk-ant-[A-Za-z0-9_-]{10,}|sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{20,}|"
    r"xox[abposr]-[A-Za-z0-9-]{10,}|eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})"
)


def clean(text):
    text = STRIP_TAGS.sub("", text)
    text = re.sub(r"<command-name>(.*?)</command-name>", r"\1", text, flags=re.S)
    return SECRETS.sub("[redacted]", text).strip()


def human_text(entry):
    if entry.get("type") != "user" or entry.get("isMeta") or entry.get("isCompactSummary"):
        return None
    if entry.get("isSidechain"):
        return None
    origin = (entry.get("origin") or {}).get("kind")
    if origin not in (None, "human"):
        return None
    content = entry.get("message", {}).get("content")
    if isinstance(content, str):
        parts = [content]
    elif isinstance(content, list):
        if any(b.get("type") == "tool_result" for b in content):
            return None
        parts = ["[image]" if b.get("type") == "image" else b.get("text", "") for b in content]
    else:
        return None
    text = clean("\n".join(p for p in parts if p))
    if origin is None and (not text or text.startswith("<")):
        return None
    return text or None


def assistant_text(entry):
    if entry.get("type") != "assistant" or entry.get("isSidechain"):
        return None
    content = entry.get("message", {}).get("content")
    if not isinstance(content, list):
        return None
    text = "\n\n".join(b.get("text", "") for b in content if b.get("type") == "text")
    return clean(text) or None


def local_time(ts):
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone()
    except (AttributeError, ValueError):
        return datetime.now().astimezone()


def project_name(cwd):
    return Path(cwd).name if cwd else "unknown"


def append(day, text):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / f"{day}.md"
    with open(path, "a", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        if f.tell() == 0:
            f.write(f"# Work log {day}\n")
        f.write(text)


def turn_block(turn):
    when = turn["time"]
    head = f"\n### {when:%H:%M} · {turn['project']}"
    if turn["branch"]:
        head += f" · {turn['branch']}"
    head += f"  <sub>{turn['session'][:8]}</sub>\n\n"
    body = f"**You:** {turn['you']}\n"
    if turn["claude"]:
        body += "\n**Claude:** " + "\n\n".join(turn["claude"]) + "\n"
    return head + body


def process(transcript, state):
    key = str(transcript)
    size = transcript.stat().st_size
    offset = state.get(key, 0)
    if offset > size:
        offset = 0
    turns, current = [], None
    with open(transcript, "rb") as f:
        f.seek(offset)
        while True:
            line = f.readline()
            if not line:
                break
            if not line.endswith(b"\n"):
                break
            offset = f.tell()
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            you = human_text(entry)
            if you:
                current = {
                    "time": local_time(entry.get("timestamp")),
                    "project": project_name(entry.get("cwd")),
                    "branch": entry.get("gitBranch") or "",
                    "session": entry.get("sessionId") or transcript.stem,
                    "you": you,
                    "claude": [],
                }
                turns.append(current)
                continue
            said = assistant_text(entry)
            if said:
                if current is None:
                    current = {
                        "time": local_time(entry.get("timestamp")),
                        "project": project_name(entry.get("cwd")),
                        "branch": entry.get("gitBranch") or "",
                        "session": entry.get("sessionId") or transcript.stem,
                        "you": "(continued)",
                        "claude": [],
                    }
                    turns.append(current)
                current["claude"].append(said)
    for turn in turns:
        append(turn["time"].strftime("%Y-%m-%d"), turn_block(turn))
    state[key] = offset


def load_state():
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(state):
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state))
    tmp.replace(STATE_FILE)


def archive(transcript):
    dest = LOG_DIR / "transcripts" / transcript.parent.name / transcript.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(transcript, dest)


def main():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOCK_FILE, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = load_state()
        if "--backfill" in sys.argv:
            files = sorted(PROJECTS_DIR.glob("*/*.jsonl"), key=lambda p: p.stat().st_mtime)
            for transcript in files:
                process(transcript, state)
                archive(transcript)
            save_state(state)
            print(f"logged {len(files)} transcripts into {LOG_DIR}")
            return
        hook = json.load(sys.stdin)
        path = hook.get("transcript_path")
        if not path or not Path(path).is_file():
            return
        transcript = Path(path)
        process(transcript, state)
        save_state(state)
        if hook.get("hook_event_name") == "SessionEnd":
            archive(transcript)


if __name__ == "__main__":
    main()
