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

## Details

- The logger runs after every Claude reply, plus a final pass when a chat ends. Each run reads only
  what's new.
- Parse state stays on each Mac in the plugin's data folder (`~/.claude/plugins/data/`), which is
  removed when you uninstall.
- `WORKLOG_JIRA_PREFIXES=BUZZ,OPS` limits ticket detection to those project keys.
- Claude Code deletes transcripts after 30 days by default (`cleanupPeriodDays`). Your logs keep
  everything already logged; only rebuilding from older chats isn't possible.

Run by hand:

```sh
python3 scripts/worklog.py --backfill   # log every chat not logged yet (safe to re-run)
python3 scripts/worklog.py --rebuild    # forget what was parsed and rebuild every day file
```
