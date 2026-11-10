#!/usr/bin/env python3
# Work log for every Claude Code session, across all projects.
#
# Runs as a Stop hook (after every turn) and a SessionEnd hook. Each run reads only the part of the
# session transcript it has not seen yet and rebuilds that day's file:
#   <log folder>/<machine>/YYYY-MM-DD.md
# The log folder is the plugin's log_dir option, chosen with /worklog:setup (see config.py).
# Until one is set, nothing is logged and nothing is marked as read, so setting it later still
# picks up every chat since.
#
# What a day file holds:
#   - a 4-5 point summary of the day at the top (projects, tickets, PRs, kubectl, notes)
#   - per message: your text in full, with each paste cut down to a one-line summary
#   - from Claude, only: tables (reports, query results), Jira tickets, PR numbers, a one-line
#     summary per kubectl command, and any line starting with "log:" (yours or Claude's)
# Nothing else from replies or tool output is kept, and raw transcripts are never copied.
#
# Each machine writes only its own subfolder, so two Macs syncing one folder never collide.
# Parsed turns live per day as JSON in the plugin's data folder, local to the machine, and the
# Markdown is re-rendered from them atomically. Secrets are masked on render.
#
#   worklog.py               hook mode, reads the hook JSON on stdin
#   worklog.py --backfill    log every transcript under ~/.claude/projects (safe to re-run)
#   worklog.py --rebuild     forget everything parsed so far, then backfill from scratch
#   worklog.py --catchup     log only chats with unlogged lines (run in the background when a chat starts)
#
# Optional env: WORKLOG_JIRA_PREFIXES="BUZZ,OPS" limits ticket detection to those project keys.
import json
import os
import re
import shlex
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402
import platforms  # noqa: E402

MACHINE = config.machine_name()
LOG_DIR = DAYS_DIR = OFFSETS_FILE = LOCK_FILE = None
PROJECTS_DIR = Path.home() / ".claude" / "projects"


def use_folders(log_root):
    global LOG_DIR, DAYS_DIR, OFFSETS_FILE, LOCK_FILE
    state = config.data_dir()
    LOG_DIR = Path(log_root) / MACHINE
    DAYS_DIR, OFFSETS_FILE, LOCK_FILE = state / "days", state / "offsets.json", state / "worklog.lock"
    state.mkdir(parents=True, exist_ok=True)


STRIP_TAGS = re.compile(
    r"<(system-reminder|ide_selection|ide_opened_file|ide_diagnostics|command-message|"
    r"command-args|local-command-stdout|local-command-caveat)>.*?</\1>",
    re.S,
)
PASTE = re.compile(r"<pasted_content[^>]*>(.*?)</pasted_content[^>]*>", re.S)
FENCE = re.compile(r"```[^\n]*\n(.*?)```", re.S)
SECRETS = re.compile(
    r"(sk-ant-[A-Za-z0-9_-]{10,}|sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{20,}|"
    r"xox[abposr]-[A-Za-z0-9-]{10,}|eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})"
)
_prefixes = [p.strip() for p in os.environ.get("WORKLOG_JIRA_PREFIXES", "").split(",") if p.strip()]
TICKET = re.compile(r"\b(?:%s)-\d{1,6}\b" % "|".join(map(re.escape, _prefixes))) if _prefixes \
    else re.compile(r"\b[A-Z][A-Z0-9]{1,9}-\d{2,6}\b")
NOT_TICKETS = re.compile(r"^(UTF|SHA|ISO|RFC|GPT|AES|HTTP|TLS|SSL|MD|ES|IPV|X|PR)\d*-")
PR_URL = re.compile(r"https://github\.com/([\w.-]+/[\w.-]+)/pull/(\d+)")
PR_REF = re.compile(r"\b(?:PR|pull request)\s*#(\d+)", re.I)
INTERRUPTED = re.compile(r"^\[Request interrupted by user( for tool use)?\]")
LOG_LINE = re.compile(r"^\s*(?:[-*]\s*)?log:\s*(.+)$", re.I | re.M)
KUBE_FLAGS_WITH_VALUE = {"-n", "--namespace", "--context", "-c", "--container", "-l", "--selector",
                         "-o", "--output", "--tail", "--since", "-f", "--filename", "--kubeconfig"}
KUBE_WRITES = {"apply", "create", "delete", "edit", "patch", "replace", "scale", "set", "label",
               "annotate", "cordon", "drain", "taint", "cp"}


def mask(text):
    return SECRETS.sub("[redacted]", text)


def one_line(text, limit=90):
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


def paste_summary(body):
    lines = [l for l in body.strip().splitlines() if l.strip()]
    first = one_line(lines[0] if lines else "", 70)
    return f"`[pasted {len(lines)} lines: {first}]`"


def clean_user(text):
    text = STRIP_TAGS.sub("", text)
    text = re.sub(r"<command-name>(.*?)</command-name>", r"\1", text, flags=re.S)
    text = PASTE.sub(lambda m: paste_summary(m.group(1)), text)
    text = FENCE.sub(lambda m: paste_summary(m.group(1)) if m.group(1).count("\n") > 4 else m.group(0), text)
    return text.strip()


def interruption(entry):
    # "[Request interrupted by user]" is what Claude Code records when you press Esc or stop a reply.
    # Returns "reply" or "tool" (stopped while a command was running), or None.
    if entry.get("type") != "user":
        return None
    content = entry.get("message", {}).get("content")
    texts = [content] if isinstance(content, str) else [
        b.get("text", "") if b.get("type") == "text" else str(b.get("content", "")) for b in content or []
        if isinstance(b, dict)]
    for text in texts:
        match = INTERRUPTED.match(str(text).strip())
        if match:
            return "tool" if match.group(1) else "reply"
    return None


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
    text = clean_user("\n".join(p for p in parts if p))
    if origin is None and (not text or text.startswith("<")):
        return None
    return text or None


def tables(text):
    found, block = [], []
    for line in text.splitlines() + [""]:
        if line.lstrip().startswith("|"):
            block.append(line.strip())
            continue
        if len(block) >= 3 and re.match(r"^\|[\s:|-]+\|$", block[1]):
            rows = block[:2 + 15]
            if len(block) > len(rows):
                rows.append(f"| … {len(block) - len(rows)} more rows |")
            found.append("\n".join(rows))
        block = []
    return found


def tickets_in(text):
    return [t for t in TICKET.findall(text) if not NOT_TICKETS.match(t)]


def shell_vars(command):
    # NAME=value assignments and "for NAME in a b c" loops, so $NAME in a kubectl line reads well.
    found = {m[0]: m[1].strip("'\"") for m in re.findall(r"(?:^|[\s;&])([A-Za-z_]\w*)=([^\s;()]+)", command)
             if not m[1].startswith("$")}
    for name, items in re.findall(r"\bfor\s+(\w+)\s+in\s+([^;\n]+)", command):
        if "$(" not in items:
            found[name] = ",".join(items.split())
    for _ in range(2):
        found = {k: expand_vars(v, found) for k, v in found.items()}
    return found


def expand_vars(text, names):
    return re.sub(r"\$\{(\w+)\}|\$(\w+)", lambda m: names.get(m.group(1) or m.group(2), m.group(0)), text)


def drop_heredocs(command):
    # Keep the line that starts a heredoc, drop its body (often Ruby, or notes that mention kubectl).
    return re.sub(r"(<<-?\s*['\"]?(\w+)['\"]?[^\n]*)\n.*?\n[ \t]*\2[ \t]*(?=\n|$)", r"\1", command, flags=re.S)


def kube_context(value):
    prod, stag = "prod" in value, "stag" in value
    if prod and stag:
        return "staging+prod"
    return "prod" if prod else "staging" if stag else (value.rsplit("/", 1)[-1] or value or "?")


def kube_summaries(command):
    command = drop_heredocs(command)
    names, out = shell_vars(command), []
    expand = lambda w: expand_vars(w, names)
    for segment in re.split(r"&&|\|\||;|\n|\|", command):
        # Only kubectl in command position, not the word inside an echo or a string.
        hit = re.search(r"(?:^\s*|\$\(|`|\(\s*|\bdo\s+|\bthen\s+|\bxargs\s+|=)kubectl\s+(.*)", segment)
        if not hit:
            continue
        try:
            words = shlex.split(hit.group(1).rstrip(")`\"' "))
        except ValueError:
            words = hit.group(1).split()
        words = [expand(w) for w in words if not re.match(r"^(\d?>|<|&>|/dev/null)", w)]
        context, rest, skip = "current context", [], False
        for i, w in enumerate(words):
            if skip:
                skip = False
                continue
            if w == "--":
                rest.append(" ".join(words[i + 1:i + 3]))
                break
            if w.startswith("--context"):
                skip = "=" not in w
                context = kube_context(w.split("=", 1)[1] if "=" in w else (words[i + 1] if i + 1 < len(words) else ""))
                continue
            if w in KUBE_FLAGS_WITH_VALUE:
                skip = True
                continue
            if w.startswith("-"):
                continue
            rest.append(w)
        if not rest:
            continue
        verb = rest[0]
        if verb in ("config", "version", "api-resources"):
            continue
        write = verb in KUBE_WRITES or (verb == "rollout" and "restart" in rest[1:2])
        summary = f"{context} · {one_line(' '.join(rest[:4]), 80)}"
        out.append(summary + (" **(write)**" if write else ""))
    return out


def local_time(ts):
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone()
    except (AttributeError, ValueError):
        return datetime.now().astimezone()


def add_unique(items, values):
    for v in values:
        if v not in items:
            items.append(v)


def absorb_assistant(turn, entry):
    content = entry.get("message", {}).get("content")
    if not isinstance(content, list):
        return
    for block in content:
        if block.get("type") == "text":
            text = block.get("text", "")
            add_unique(turn["tables"], tables(text))
            add_unique(turn["tickets"], tickets_in(text))
            add_unique(turn["prs"], [f"{r}#{n}" for r, n in PR_URL.findall(text)])
            add_unique(turn["prs"], [f"#{n}" for n in PR_REF.findall(text)])
            add_unique(turn["notes"], [one_line(n, 200) for n in LOG_LINE.findall(text)])
        elif block.get("type") == "tool_use":
            raw = json.dumps(block.get("input", {}))
            add_unique(turn["tickets"], tickets_in(raw))
            command = (block.get("input") or {}).get("command")
            if block.get("name") == "Bash" and isinstance(command, str) and "kubectl" in command:
                turn["kubectl"].extend(kube_summaries(command))


def tidy_prs(turn):
    # Drop a bare "#824" once the same number is known with its repo.
    full = {p.split("#")[1] for p in turn["prs"] if not p.startswith("#")}
    turn["prs"] = [p for p in turn["prs"] if not (p.startswith("#") and p[1:] in full)]


def new_turn(entry, transcript, you):
    when = local_time(entry.get("timestamp"))
    return {
        "time": when.strftime("%H:%M"),
        "day": when.strftime("%Y-%m-%d"),
        "project": Path(entry["cwd"]).name if entry.get("cwd") else "unknown",
        "branch": "" if entry.get("gitBranch") in (None, "HEAD") else entry["gitBranch"],
        "session": (entry.get("sessionId") or transcript.stem)[:8],
        "you": you,
        "tables": [], "tickets": [], "prs": [], "kubectl": [], "notes": [],
    }


class Days:
    # Loads each day's turns lazily and writes back only the days that changed.
    def __init__(self):
        self.cache, self.dirty = {}, set()

    def turns(self, day):
        if day not in self.cache:
            try:
                self.cache[day] = json.loads((DAYS_DIR / f"{day}.json").read_text())
            except (OSError, json.JSONDecodeError):
                self.cache[day] = []
        return self.cache[day]

    def add(self, turn):
        # A chat re-read from the start (its file was rewritten) reuses its entries, never duplicates.
        turns = self.turns(turn["day"])
        self.dirty.add(turn["day"])
        for i, old in enumerate(turns):
            if (old["session"], old["time"], old["you"]) == (turn["session"], turn["time"], turn["you"]):
                turns[i] = turn
                return [turn["day"], i]
        turns.append(turn)
        return [turn["day"], len(turns) - 1]

    def get(self, ref):
        day, idx = ref
        turns = self.turns(day)
        if 0 <= idx < len(turns):
            self.dirty.add(day)
            return turns[idx]
        return None

    def flush(self):
        DAYS_DIR.mkdir(parents=True, exist_ok=True)
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        for day in sorted(self.dirty):
            turns = self.cache[day]
            turns.sort(key=lambda t: t["time"])
            write_atomic(DAYS_DIR / f"{day}.json", json.dumps(turns))
            write_atomic(LOG_DIR / f"{day}.md", mask(render(day, turns)))


def process(transcript, offsets, days):
    key = str(transcript)
    saved = offsets.get(key, {})
    offset, last, seen_prs = saved.get("offset", 0), saved.get("last"), saved.get("prs", [])
    if offset > transcript.stat().st_size:
        offset, last, seen_prs = 0, None, []
    current = days.get(last) if last else None
    with open(transcript, "rb") as f:
        f.seek(offset)
        for line in iter(f.readline, b""):
            if not line.endswith(b"\n"):
                break
            offset += len(line)
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("isSidechain"):
                continue
            stopped = interruption(entry)
            if stopped:
                if current is not None:
                    current["stopped"] = stopped
                continue
            you = human_text(entry)
            if you:
                current = new_turn(entry, transcript, you)
                add_unique(current["tickets"], tickets_in(you))
                add_unique(current["notes"], [one_line(n, 200) for n in LOG_LINE.findall(you)])
                last = days.add(current)
            elif current is None:
                continue
            elif entry.get("type") == "assistant":
                absorb_assistant(current, entry)
                tidy_prs(current)
            elif entry.get("type") == "pr-link" and entry.get("prNumber"):
                pr = f"{entry.get('prRepository', '')}#{entry['prNumber']}"
                if pr not in seen_prs:
                    seen_prs.append(pr)
                    add_unique(current["prs"], [pr])
                    tidy_prs(current)
    offsets[key] = {"offset": offset, "last": last, "prs": seen_prs}


def count_by(values):
    counts = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    return counts


def short_pr(pr):
    repo, num = pr.split("#")
    return f"{repo.split('/')[-1]}#{num}" if repo else f"#{num}"


def summary(turns):
    projects = count_by(t["project"] for t in turns)
    by_project = ", ".join(f"{p} ({n})" for p, n in sorted(projects.items(), key=lambda x: -x[1]))
    points = [f"{len(turns)} messages across {len(projects)} project(s): {by_project}"]
    tickets = []
    for t in turns:
        add_unique(tickets, t["tickets"])
    if tickets:
        points.append("Tickets: " + ", ".join(sorted(tickets)))
    prs = []
    for t in turns:
        add_unique(prs, t["prs"])
    if prs:
        points.append("PRs: " + ", ".join(short_pr(p) for p in prs))
    kube = [k for t in turns for k in t["kubectl"]]
    if kube:
        envs = count_by(k.split(" · ")[0] for k in kube)
        writes = sum("(write)" in k for k in kube)
        line = f"kubectl: {len(kube)} commands (" + ", ".join(f"{e} {n}" for e, n in envs.items()) + ")"
        points.append(line + (f", {writes} changing something" if writes else ", all read-only"))
    notes = [n for t in turns for n in t["notes"]]
    stopped = sum(1 for t in turns if t.get("stopped"))
    extras = ([f"{len(notes)} `log:` notes"] if notes else []) + ([f"{stopped} " + ("reply" if stopped == 1 else "replies") + " stopped by you"] if stopped else [])
    if extras:
        points.append("Also: " + ", ".join(extras))
    return points[:5]


def render(day, turns):
    out = [f"# Work log {day} · {MACHINE}", "", "## Summary", ""]
    out += [f"- {p}" for p in summary(turns)]
    out += ["", "## Timeline"]
    for t in turns:
        head = f"### {t['time']} · {t['project']}"
        if t["branch"]:
            head += f" · {t['branch']}"
        out += ["", head + f"  <sub>{t['session']}</sub>", "", f"**You:** {t['you']}"]
        facts = []
        if t.get("stopped"):
            facts.append("⏹ **Stopped by you**" + (" while a command was running" if t["stopped"] == "tool" else ""))
        if t["tickets"]:
            facts.append("Tickets: " + ", ".join(t["tickets"]))
        if t["prs"]:
            facts.append("PRs: " + ", ".join(t["prs"]))
        for k, n in count_by(t["kubectl"]).items():
            facts.append(f"kubectl {k}" + (f" (×{n})" if n > 1 else ""))
        facts += [f"log: {n}" for n in t["notes"]]
        if facts:
            out += [""] + [f"- {f}" for f in facts]
        for table in t["tables"]:
            out += ["", table]
    return "\n".join(out) + "\n"


def write_atomic(path, text):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def load_offsets():
    try:
        return json.loads(OFFSETS_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def unread(transcript, offsets):
    # True when a chat has lines not logged yet: an Esc-stopped reply, or a window force-closed
    # before its end-of-chat pass ran.
    return transcript.stat().st_size != offsets.get(str(transcript), {}).get("offset", 0)


def run(log_root, mode="hook", transcript=None):
    use_folders(log_root)
    with platforms.locked(LOCK_FILE):
        if mode == "rebuild":
            shutil.rmtree(DAYS_DIR, ignore_errors=True)
            OFFSETS_FILE.unlink(missing_ok=True)
        offsets, days = load_offsets(), Days()
        if mode in ("backfill", "rebuild", "catchup"):
            files = sorted(PROJECTS_DIR.glob("*/*.jsonl"), key=lambda p: p.stat().st_mtime)
            if mode == "catchup":
                files = [f for f in files if unread(f, offsets)]
        else:
            files = [transcript]
        for path in files:
            process(path, offsets, days)
        days.flush()
        write_atomic(OFFSETS_FILE, json.dumps(offsets))
        return len(files), len(days.dirty)


def main():
    flags = {"--rebuild": "rebuild", "--backfill": "backfill", "--catchup": "catchup"}
    mode = next((m for f, m in flags.items() if f in sys.argv), "hook")
    root = config.log_root(ask_cli=mode in ("rebuild", "backfill"))
    if mode == "hook":
        path = json.load(sys.stdin).get("transcript_path")
        # A missing folder (sync drive not mounted) is skipped, not created; it catches up later.
        if root and root.is_dir() and path and Path(path).is_file():
            run(root, "hook", Path(path))
        return
    if mode == "catchup":
        if root and root.is_dir():
            run(root, mode)
        return
    if not root:
        sys.exit("No log folder set. Run /worklog:setup first.")
    read, wrote = run(root, mode)
    print(f"read {read} transcripts, wrote {wrote} day files to {LOG_DIR}")


if __name__ == "__main__":
    main()
