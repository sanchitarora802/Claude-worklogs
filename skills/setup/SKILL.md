---
description: Choose the folder where worklog saves your daily work logs (opens a Finder folder picker), then log every past chat into it.
argument-hint: "[folder path, optional]"
disable-model-invocation: true
---

Run this command with the Bash tool and wait for it. On a Mac it opens a note about choosing a
folder, then a Finder folder picker. The user is clicking through it, so allow up to 5 minutes.

```sh
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/commands.py" setup $ARGUMENTS
```

Then tell the user, in a few lines:
- the folder chosen and the subfolder this Mac writes to
- how many past chats were logged
- the note from the output: pick a folder that syncs between their own Macs, and not one shared with
  other people, because logs hold their full messages

If they cancelled, say nothing changed. If it failed, show the error and what to do about it.
