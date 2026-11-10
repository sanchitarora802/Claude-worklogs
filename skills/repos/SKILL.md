---
description: Choose which repos worklog should not log, from a tick-box list of every folder Claude Code has been used in.
argument-hint: "[folder …] or none"
disable-model-invocation: true
---

Run this with the Bash tool and wait for it. Without arguments it opens a tick-box list on the
user's screen, so allow up to 5 minutes.

```sh
sh "${CLAUDE_PLUGIN_ROOT}/scripts/run.sh" commands.py repos $ARGUMENTS
```

Then tell the user which folders are now not logged (or that every repo is logged), and that the
change applies from now on: chats already in the log stay there. If the list couldn't be shown,
show the folders from the output and how to pass them as arguments.
