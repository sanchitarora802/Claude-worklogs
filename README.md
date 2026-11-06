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

## Status

Work in progress: skeleton only, not installed yet.
