---
description: Show where worklog is saving logs, whether it's working, and which Macs have logged.
disable-model-invocation: true
---

Run this with the Bash tool:

```sh
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/commands.py" status
```

Show the result as a short status: the log folder, each Mac with its day count and latest update,
and today's message count. If the folder isn't set or is missing, say so first and point to
`/worklog:setup`.
