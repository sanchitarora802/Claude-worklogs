# worklog

A Claude Code plugin that keeps one readable log file per day of everything you do with Claude Code,
across all your projects and computers.

## Disclaimer and privacy

> **This plugin reads every Claude Code chat on your computer and writes parts of it to a folder you
> choose. Read this before installing.**

- **It reads all your Claude Code chats**, in every project: the transcript files Claude Code already
  keeps in `~/.claude/projects/`. It reads nothing else: no other files, browser data, keychain or
  other apps.
- **It writes your messages in full** to the log folder, along with ticket numbers, PRs and summaries
  of the commands that ran, including commands run against production.
- **Nothing leaves your computer.** There's no network access, no server, no analytics and no AI
  model. The only place data goes is the folder you pick, and from there wherever your sync app
  copies it.
- **No special permissions.** The scripts run as you, with your normal file access, only while a hook
  runs. macOS may ask once whether your editor can access a protected folder such as Documents or
  iCloud Drive; that's the editor asking, not this plugin.
- **Plugin hooks run with your full user permissions.** Installing any plugin means trusting its code.
  This one is about 900 lines of plain Python and shell with no dependencies, so you can read all of it
  first. `/hooks` in Claude Code shows what it registered.
- **Secret masking is best effort.** Strings that look like API keys or tokens (`sk-ant-…`, `AKIA…`,
  `ghp_…`, Slack tokens, JWTs) are masked, but a password or secret in another format would be
  logged as typed. **Never pick a folder that other people can read.**
- **You're responsible for the logs.** Treat the log folder like your chat history: keep it private,
  and follow your employer's rules on where work information may be stored.

Provided as is, without warranty. Uninstalling stops all logging; your existing log files stay until
you delete them.

## What gets logged

- Your messages in full. Anything you paste becomes one line, such as
  `[pasted 186 lines: WORKER INVENTION ASSIGNMENT AGREEMENT]`.
- From Claude's replies, only:
  - tables (reports and query results)
  - Jira tickets
  - PRs
  - one line per kubectl command; commands that change something are marked **(write)**
  - any line that starts with `log:`, from you or from Claude
- **⏹ Stopped by you** on any reply you interrupted (Esc or the stop button).
- A summary of up to 5 points at the top of each day.

Raw transcripts are never copied, and strings that look like API keys or tokens are masked.

## Supported systems

| System | Status | Folder picker in `/worklog:setup` |
|---|---|---|
| macOS | Tested | Finder dialog, with a Help button |
| Linux | Written, untested | `zenity` or `kdialog` in a desktop session; otherwise pass the path |
| Windows | Written, untested | Windows folder dialog (through PowerShell). Needs Git for Windows, which Claude Code uses to run hooks. |

It works wherever Claude Code runs on those systems: the VS Code, Cursor and JetBrains extensions, and
the terminal. It doesn't log Claude Code on the web (cloud sessions) or regular claude.ai chats.

**Requirements:** Python 3.8 or newer, standard library only. The launcher (`scripts/run.sh`) finds it
as `python3`, `python` or `py -3`. Without Python, nothing is logged and a message says what to
install. Nothing breaks in Claude Code.

## Install

In Claude Code:

```
/plugin marketplace add sanchitarora802/Claude-worklogs
/plugin install worklog@worklog
```

Claude Code asks for the log folder when you install. You can leave it blank and run `/worklog:setup`
afterwards to choose it in a folder picker instead. Until a folder is set, nothing is logged, and you'll see a
reminder when a new chat starts.

## Choosing a log folder

- **Pick a folder that syncs between your own computers**, such as one in iCloud Drive, Google Drive,
  Dropbox or OneDrive. Each computer writes only to its own subfolder
  (`<folder>/<computer name>/YYYY-MM-DD.md`), so syncing never conflicts, and you can see every
  computer's logs in one place.
- **Don't pick a folder shared with other people.** Logs hold your full messages, ticket numbers and the
  prod commands that ran.
- Avoid putting it inside a git repository, so logs don't get committed by accident.

If the folder goes missing, for example because a sync drive isn't mounted, logging pauses. It catches up
on its own once the folder is back.

## Commands

| Command | What it does |
|---|---|
| `/worklog:setup` | Choose or change the log folder in a folder picker, then log your past chats into it. You can also pass a path: `/worklog:setup ~/Dropbox/logs`. |
| `/worklog:status` | Where logs are going, each computer's latest log, and today's message count. |
| `/worklog:day 2026-11-06` | A summary of what you did that day, from every computer. `today` and `yesterday` work too, and you can just ask "what did I do last Friday?". |

You can also change the folder with `/plugin configure worklog@worklog` or in `/config`.

## How it works

worklog is a few small Python scripts that Claude Code runs at fixed moments (hooks). There is no
server or background process, nothing is sent over the network, and no AI model is used: every rule
is plain pattern matching. Only Python 3's standard library is needed.

### Files

```
.claude-plugin/
  plugin.json        name, version, and the log_dir setting (type: directory)
  marketplace.json   lets Claude Code install the plugin straight from this repo
hooks/hooks.json     when Claude Code runs the scripts
skills/              the slash commands: setup, status, day
scripts/
  run.sh             launcher: finds Python 3.8+ (python3, python or py -3), then runs a script
  worklog.py         the logger: reads chats, writes the day files
  commands.py        setup, status, day, and the start-of-chat check
  config.py          finds the log folder and the state folder
  platforms.py       everything that differs between macOS, Windows and Linux
```

### When it runs

| Moment | Hook | What runs |
|---|---|---|
| A chat starts | `SessionStart` | `commands.py check`: warns if no folder is set or the folder is missing. When all is well it's silent, and starts a background catch-up of any chat with unlogged lines. |
| After every Claude reply | `Stop` (async) | `worklog.py` logs what's new in this chat. It runs in the background, so replies never wait for it. |
| A chat ends | `SessionEnd` | `worklog.py` makes a final pass for anything the last reply's run missed. |

Each run gets the path of the chat's transcript, the JSONL file Claude Code already keeps in
`~/.claude/projects/<project>/<chat-id>.jsonl`. The folder you chose arrives as the environment
variable `CLAUDE_PLUGIN_OPTION_LOG_DIR`.

Every hook and command starts through `run.sh`, which Claude Code runs with `sh` on macOS and Linux
and with Git Bash on Windows. The launcher picks a Python that works. On macOS it skips Apple's
`/usr/bin/python3` placeholder when the developer tools aren't installed, because that placeholder
would open an install window on every reply.

### What one run does

```
transcript.jsonl → read new lines only → keep what matters → per-day JSON → rebuild the day's .md
                   (saved byte offset)   (rules below)      (state, local)  (your log folder)
```

1. **Reads only what's new.** For each transcript it saves how far it has read, as a byte offset, and
   starts from there next time. A run takes a few milliseconds; reading all of a month's chats takes
   about half a second.
2. **Sorts each line by type:**
   - **Your message** starts a new entry with the time, project folder, git branch and chat id.
     Editor and system tags are removed. Each `<pasted_content>` block, and each code block over 5
     lines, becomes `[pasted N lines: first line]`.
   - **An interruption** (`[Request interrupted by user]`, which Claude Code records when you stop a
     reply) marks the current entry **⏹ Stopped by you**, noting when you stopped it during a command.
   - **Claude's text** is searched for markdown tables, Jira keys (`BUZZ-1234`), PR links or
     `PR #123`, and lines starting with `log:`. The rest of the reply is dropped.
   - **Claude's tool calls** are searched for Jira keys and kubectl commands. Each kubectl command
     becomes one line, such as `prod · set env deployment/… (write)`. To get there, shell variables
     and loop variables (`--context $PROD`) are filled in, the word "kubectl" inside text or heredocs
     is ignored, and commands that change something are marked **(write)**.
   - **PR links** that Claude Code records when a PR is created give the repo and number. Each is
     logged once per chat.
   - Tool output, Claude's thinking and subagent chats are skipped.
3. **Saves** each day's entries as `days/YYYY-MM-DD.json` in the plugin's data folder.
4. **Rebuilds that day's Markdown file** from the JSON: the summary on top (messages per project,
   tickets, PRs, kubectl counts and writes, notes), then the timeline. Secrets are masked at this
   point. The file is written under a temporary name and then renamed, so a sync app never uploads
   half a file.

The day file is rebuilt rather than appended to, so the summary at the top stays current.

### Where data lives

| What | Where | Synced? |
|---|---|---|
| Day files (what you read) | `<log folder>/<computer name>/YYYY-MM-DD.md` | Yes, by your sync app |
| State (read offsets, per-day JSON) | `~/.claude/plugins/data/worklog-worklog/` | No, local to each computer |
| The folder setting | Claude Code's plugin settings, plus a copy in the data folder | No |
| Raw transcripts | Where Claude Code keeps them | Never copied |

Each computer writes only to its own subfolder, so two computers never write the same file and syncing can't
conflict.

### The commands

Each slash command is a short instruction file that tells Claude which script to run and how to
explain the result.

- **`/worklog:setup`**:
  1. Shows a note on choosing a folder, then this system's folder picker: a Finder dialog with Help,
     Cancel and Choose Folder… buttons on macOS (`osascript`), the Windows folder dialog
     (PowerShell), or `zenity` or `kdialog` on Linux. Python's `tkinter` is the fallback. Without
     any of them, it asks you to pass the path.
  2. Checks the folder you picked can be written to.
  3. Saves it with `claude plugin configure worklog@worklog --values-stdin`.
  4. Logs every past chat into it.
- **`/worklog:status`**: lists each computer's subfolder with its day count, size and last update, plus
  today's message count.
- **`/worklog:day`**: prints that day's file from every computer, and Claude summarises it into tasks by
  project, tickets and PRs, prod actions and open items. This is the only step where a model is
  involved, and it's the Claude chat you're already in.

### Safety

- **Missing folder** (a sync drive not mounted): logging pauses without creating a stand-in folder,
  and the read offset doesn't move, so it catches up once the folder is back.
- **No folder set:** nothing is logged and nothing is marked as read, so choosing a folder later
  still picks up every chat.
- **Missed replies:** stopping a reply with Esc skips that reply's hook, and a force-closed window
  skips the end-of-chat pass. The catch-up at the start of the next chat, in any repo, logs those
  lines. It runs in the background, so the new chat doesn't wait.
- **Two runs at once:** a file lock (`fcntl` on macOS and Linux, `msvcrt` on Windows) lets only one write at a time, so parallel chats in several
  editors don't clash.
- **Re-runs** never duplicate entries: a run only reads past the saved offset, and a chat that has
  to be re-read from the start reuses its existing entries.
- **Uninstalling** removes the data folder. Your day files stay where they are.

### Limits

- Reports written as prose, not tables, aren't captured.
- Ticket and PR numbers are found by pattern, so an example in a reply (like `BUZZ-1234`) counts
  as a real one.
- A kubectl command run without `--context` shows as "current context", not the cluster's name.
- Claude Code deletes transcripts after 30 days by default (`cleanupPeriodDays`). Anything already
  logged stays logged, but you can't rebuild from chats older than that.

### Options

- `WORKLOG_JIRA_PREFIXES=BUZZ,OPS` limits ticket detection to those project keys.
- `WORKLOG_DIR` and `WORKLOG_STATE_DIR` override the log and state folders, for testing.

Run by hand:

```sh
sh scripts/run.sh worklog.py --backfill   # log every chat not logged yet (safe to re-run)
sh scripts/run.sh worklog.py --catchup    # log only chats with unlogged lines
sh scripts/run.sh worklog.py --rebuild    # forget what was parsed and rebuild every day file
```
