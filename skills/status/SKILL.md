---
description: Show where worklog is saving logs, whether it's working, and which computers have logged.
disable-model-invocation: true
---

Run this with the Bash tool:

```sh
sh "${CLAUDE_PLUGIN_ROOT}/scripts/run.sh" commands.py status
```

Show the result as a short status: the log folder, each computer with its day count and latest update,
and today's message count. If the folder isn't set or is missing, say so first and point to
`/worklog:setup`.
