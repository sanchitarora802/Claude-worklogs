#!/bin/sh
# Runs one of the plugin's Python scripts with whichever Python 3.8+ this computer has.
#
#   sh run.sh worklog.py [args]      sh run.sh commands.py check
#
# Hooks and slash commands call this instead of `python3` directly: on Windows (in Git Bash,
# which Claude Code uses there) Python is usually `python` or `py -3`, not `python3`.
# On macOS, /usr/bin/python3 is only a stub that opens an "install developer tools" window until
# the Command Line Tools are installed, so it's skipped in that case rather than run from a hook.
# With no usable Python, the start-of-chat check prints one message saying what to install, and
# everything else exits quietly, so Claude Code never shows a hook error.
case "$0" in */*) here=${0%/*} ;; *) here=. ;; esac
here=$(cd "$here" && pwd)
script=$1
shift

usable() {
  if [ "$1" = "/usr/bin/python3" ] && [ "$(uname)" = "Darwin" ] && ! xcode-select -p >/dev/null 2>&1; then
    return 1
  fi
  "$@" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' >/dev/null 2>&1
}

for candidate in python3 python; do
  found=$(command -v "$candidate" 2>/dev/null) || continue
  usable "$found" && exec "$found" "$here/$script" "$@"
done
found=$(command -v py 2>/dev/null) && usable "$found" -3 && exec "$found" -3 "$here/$script" "$@"

if [ "$script" = "commands.py" ] && [ "$1" = "check" ]; then
  echo '{"systemMessage": "worklog needs Python 3.8 or newer, so nothing is being logged. Install it (macOS: xcode-select --install; Windows: python.org or the Microsoft Store; Linux: your package manager), then start a new chat."}'
elif [ "$script" = "commands.py" ]; then
  echo "worklog needs Python 3.8 or newer. Install it, then run this again." >&2
  exit 1
fi
exit 0
