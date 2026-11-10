# worklog

A Claude Code plugin that keeps one readable log file per day of everything you do with Claude Code,
across all your projects and Macs.

## What gets logged

- Your messages in full. Anything you paste becomes one line, such as
  `[pasted 186 lines: WORKER INVENTION ASSIGNMENT AGREEMENT]`.
- From Claude's replies, only:
  - tables (reports and query results)
  - Jira tickets
  - PRs
  - one line per kubectl command; commands that change something are marked **(write)**
  - any line that starts with `log:`, from you or from Claude
- A summary of up to 5 points at the top of each day.

Raw transcripts are never copied, and strings that look like API keys or tokens are masked.

## Install

In Claude Code:

```
/plugin marketplace add sanchitarora802/Claude-worklogs
/plugin install worklog@worklog
```

Claude Code asks for the log folder when you install. You can leave it blank and run `/worklog:setup`
afterwards to choose it in Finder instead. Until a folder is set, nothing is logged, and you'll see a
reminder when a new chat starts.

## Choosing a log folder

- **Pick a folder that syncs between your own Macs**, such as one in iCloud Drive, Google Drive or Dropbox.
  Each Mac writes only to its own subfolder (`<folder>/<Mac name>/YYYY-MM-DD.md`), so syncing never
  conflicts, and you can see every Mac's logs in one place.
- **Don't pick a folder shared with other people.** Logs hold your full messages, ticket numbers and the
  prod commands that ran.
- Avoid putting it inside a git repository, so logs don't get committed by accident.

If the folder goes missing, for example because a sync drive isn't mounted, logging pauses. It catches up
on its own once the folder is back.

## Commands

| Command | What it does |
|---|---|
| `/worklog:setup` | Choose or change the log folder in Finder, then log your past chats into it. You can also pass a path: `/worklog:setup ~/Dropbox/logs`. |
| `/worklog:status` | Where logs are going, each Mac's latest log, and today's message count. |
| `/worklog:day 2026-11-06` | A summary of what you did that day, from every Mac. `today` and `yesterday` work too, and you can just ask "what did I do last Friday?". |

You can also change the folder with `/plugin configure worklog@worklog` or in `/config`.

## How it works

worklog is three small Python scripts that Claude Code runs at fixed moments (hooks). There is no
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
  worklog.py         the logger: reads chats, writes the day files
  commands.py        setup, status, day, and the start-of-chat check
  config.py          finds the log folder and the state folder
```

### When it runs

| Moment | Hook | What runs |
|---|---|---|
| A chat starts | `SessionStart` | `commands.py check`: silent when all is well; otherwise warns that no folder is set or the folder is missing. |
| After every Claude reply | `Stop` (async) | `worklog.py` logs what's new in this chat. It runs in the background, so replies never wait for it. |
| A chat ends | `SessionEnd` | `worklog.py` makes a final pass for anything the last reply's run missed. |

Each run gets the path of the chat's transcript, the JSONL file Claude Code already keeps in
`~/.claude/projects/<project>/<chat-id>.jsonl`. The folder you chose arrives as the environment
variable `CLAUDE_PLUGIN_OPTION_LOG_DIR`.

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
| Day files (what you read) | `<log folder>/<Mac name>/YYYY-MM-DD.md` | Yes, by your sync app |
| State (read offsets, per-day JSON) | `~/.claude/plugins/data/worklog-worklog/` | No, local to each Mac |
| The folder setting | Claude Code's plugin settings, plus a copy in the data folder | No |
| Raw transcripts | Where Claude Code keeps them | Never copied |

Each Mac writes only to its own subfolder, so two Macs never write the same file and syncing can't
conflict.

### The commands

Each slash command is a short instruction file that tells Claude which script to run and how to
explain the result.

- **`/worklog:setup`**:
  1. Shows a macOS dialog (with `osascript`) holding the note on choosing a folder, with Help,
     Cancel and Choose Folder… buttons, then opens the Finder folder picker.
  2. Checks the folder you picked can be written to.
  3. Saves it with `claude plugin configure worklog@worklog --values-stdin`.
  4. Logs every past chat into it.
- **`/worklog:status`**: lists each Mac's subfolder with its day count, size and last update, plus
  today's message count.
- **`/worklog:day`**: prints that day's file from every Mac, and Claude summarises it into tasks by
  project, tickets and PRs, prod actions and open items. This is the only step where a model is
  involved, and it's the Claude chat you're already in.

### Safety

- **Missing folder** (a sync drive not mounted): logging pauses without creating a stand-in folder,
  and the read offset doesn't move, so it catches up once the folder is back.
- **No folder set:** nothing is logged and nothing is marked as read, so choosing a folder later
  still picks up every chat.
- **Two runs at once:** a file lock lets only one write at a time.
- **Re-runs** never duplicate entries, because a run only reads past the saved offset.
- **Uninstalling** removes the data folder. Your day files stay where they are.

### Limits

- Reports written as prose, not tables, aren't captured.
- A kubectl command run without `--context` shows as "current context", not the cluster's name.
- Claude Code deletes transcripts after 30 days by default (`cleanupPeriodDays`). Anything already
  logged stays logged, but you can't rebuild from chats older than that.

### Options

- `WORKLOG_JIRA_PREFIXES=BUZZ,OPS` limits ticket detection to those project keys.
- `WORKLOG_DIR` and `WORKLOG_STATE_DIR` override the log and state folders, for testing.

Run by hand:

```sh
python3 scripts/worklog.py --backfill   # log every chat not logged yet (safe to re-run)
python3 scripts/worklog.py --rebuild    # forget what was parsed and rebuild every day file
```
