---
description: Choose a day in a calendar and summarise what was done with Claude Code that day, from the worklog logs of every computer.
argument-hint: "[YYYY-MM-DD | today | yesterday]  (empty opens a calendar)"
disable-model-invocation: true
---

If the arguments below are empty, use `pick` as the date: it opens a calendar for the user to choose
a day. Otherwise turn what they wrote into YYYY-MM-DD (`today` and `yesterday` work as they are;
resolve weekday names like "last Friday" yourself). Then run, with a timeout of 10 minutes so the
user has time to choose:

```sh
sh "${CLAUDE_PLUGIN_ROOT}/scripts/run.sh" commands.py day <date>
```

If it says the user cancelled, just say no date was chosen. If it says no calendar can open, ask
them for the date and run it again with that date.

Otherwise write a summary of that day (the "Date:" line says which day was picked):
- the tasks done, grouped by project, one line each, in plain words
- Jira tickets and PRs touched
- anything run against prod, and anything that changed something (lines marked "(write)")
- open items, if the log shows something left unfinished

Use only what the log says. If no log exists for that date, say so.

Arguments: $ARGUMENTS
