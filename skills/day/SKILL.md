---
description: Summarise what was done with Claude Code on a given date, from the worklog daily logs of every computer. Use when the user asks what they worked on, or what was done, on a date.
argument-hint: "[YYYY-MM-DD | today | yesterday]"
---

Work out the date the user means and write it as YYYY-MM-DD (use `today` or `yesterday` when that's
what they said; resolve weekday names like "last Friday" to a date yourself). Then run:

```sh
sh "${CLAUDE_PLUGIN_ROOT}/scripts/run.sh" commands.py day <date>
```

From the output, write a summary of that day for the user:
- the tasks done, grouped by project, one line each, in plain words
- Jira tickets and PRs touched
- anything run against prod, and anything that changed something (lines marked "(write)")
- open items, if the log shows something left unfinished

Use only what the log says. If no log exists for that date, say so.

Arguments: $ARGUMENTS
