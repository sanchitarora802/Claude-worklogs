# claude-worklog

A Claude Code plugin that keeps one readable log file per day of everything done with Claude Code,
across all projects and both Macs.

## What gets logged

- Your messages in full. Anything pasted is reduced to a one-line summary.
- From Claude's replies, only: reports and query results, Jira tickets, PR numbers, a summary of the
  kubectl commands that ran, and anything you start with `log:`.
- A 4–5 point summary of the day at the top of each file.

## Where

Outside this repo, in a folder shared between the Macs: `$WORKLOG_DIR/<machine>/YYYY-MM-DD.md`.
Each Mac writes only to its own subfolder, so syncing never conflicts. This repo holds only the plugin
code. Raw transcripts are never copied to the shared folder.

## Running it by hand

```sh
python3 scripts/worklog.py --backfill   # log every transcript not logged yet (safe to re-run)
python3 scripts/worklog.py --rebuild    # forget what was parsed and rebuild every day file
```

`WORKLOG_DIR` sets where logs go (default `~/worklog`). `WORKLOG_JIRA_PREFIXES=BUZZ,OPS` limits ticket
detection to those keys. Parse state stays on each machine in `~/.claude/worklog/`.

## Status

Work in progress: the logger works (rule-based), not installed as a plugin yet.
