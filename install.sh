#!/bin/sh
# Installs (or updates) the worklog plugin into Claude Code from a terminal.
#
#   sh install.sh                       then choose the log folder with /worklog:setup
#   sh install.sh "/path/to/log folder"  sets the log folder now
#   curl -fsSL https://raw.githubusercontent.com/sanchitarora802/Claude-worklogs/main/install.sh | sh
#
# Why a script: the /plugin command isn't available in every Claude Code surface (the VS Code
# extension, for one), and many people only have Claude Code inside their editor, with no `claude`
# command on their PATH. This finds Claude Code wherever it is (PATH, the native installer's
# folders, or the copy bundled in the VS Code or Cursor extension) and runs its own
# `claude plugin` commands: add this repo as a marketplace, then install at user scope so every
# project gets it. Running it again updates to the latest version.
#
# Claude Code loads plugins when a chat starts: afterwards, run /reload-plugins or open a new chat.
set -e

REPO="sanchitarora802/Claude-worklogs"
PLUGIN="worklog@sanchitarora802"

find_claude() {
  if command -v claude >/dev/null 2>&1; then command -v claude; return; fi
  for path in "$HOME/.local/bin/claude" "$HOME/.claude/local/claude"; do
    [ -x "$path" ] && { echo "$path"; return; }
  done
  for editor in .vscode .cursor .vscode-insiders .windsurf; do
    newest=$(ls -d "$HOME/$editor/extensions/"anthropic.claude-code-*/resources/native-binary/claude 2>/dev/null \
      | sort -V | tail -n 1)
    [ -n "$newest" ] && [ -x "$newest" ] && { echo "$newest"; return; }
  done
  return 1
}

claude=$(find_claude) || {
  echo "Claude Code wasn't found. Install it first (https://code.claude.com/docs), then run this again." >&2
  exit 1
}
echo "Using Claude Code at: $claude"

if "$claude" plugin marketplace list 2>/dev/null | grep -q "$REPO"; then
  echo "Updating the worklog marketplace..."
  "$claude" plugin marketplace update sanchitarora802
else
  echo "Adding the worklog marketplace..."
  "$claude" plugin marketplace add "$REPO"
fi

folder=""
if [ -n "$1" ]; then
  folder=$1
  case "$folder" in "~"/*) folder="$HOME/${folder#\~/}" ;; esac
  if [ ! -d "$folder" ]; then
    echo "Log folder not found: $folder" >&2
    exit 1
  fi
fi

if "$claude" plugin list 2>/dev/null | grep -q "$PLUGIN"; then
  echo "Updating worklog..."
  "$claude" plugin update "$PLUGIN" </dev/null
  if [ -n "$folder" ]; then
    # JSON-escape the path (backslashes, then quotes) for --values-stdin.
    escaped=$(printf '%s' "$folder" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g')
    printf '{"log_dir": "%s"}' "$escaped" | "$claude" plugin configure "$PLUGIN" --values-stdin
  fi
else
  # Defaults for the other options are saved too, so Claude Code doesn't list them as "not set".
  # (backfill_from has no default value to save: Claude Code rejects an empty one.)
  if [ -n "$folder" ]; then
    echo "Installing worklog (log folder: $folder)..."
    set -- --config "log_dir=$folder"
  else
    echo "Installing worklog..."
    set --
  fi
  "$claude" plugin install "$PLUGIN" --scope user "$@" \
    --config logging_enabled=true --config keep_days=0 </dev/null
fi

cat <<'EOF'

Done. Next, in Claude Code:
  1. Load the plugin: start a new chat (in VS Code, or click "Restart Claude" if it offers),
     or run /reload-plugins in a terminal session that's already open.
  2. Run /worklog:setup to choose the log folder (skip this if you passed one) and the repos not to log.
  3. Run /worklog:status to check it's working.
EOF
