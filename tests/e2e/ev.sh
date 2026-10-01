#!/bin/bash
# Send one hook event to the installed hook, the way Claude Code would.
#   FAKE_HOME=/path/to/home ./ev.sh EVENT SESSION_ID CWD TRANSCRIPT [extra-json]
# e.g. ./ev.sh UserPromptSubmit "$A" "$REPOS/widget-api" transcripts/widget-api.jsonl '{"prompt":"please do step 1"}'
# FAKE_HOME is the throwaway home install.sh was run in; the hook reads its
# ha-status.env and writes its state there, never in the real ~/.claude.
: "${FAKE_HOME:?set FAKE_HOME to the throwaway home the hook was installed in}"
export HOME="$FAKE_HOME"
x=${5:-'{}'}
jq -nc --arg e "$1" --arg s "$2" --arg c "$3" --arg t "$4" --argjson x "$x" \
  '{hook_event_name:$e,session_id:$s,cwd:$c,transcript_path:$t,permission_mode:"default"}+$x' \
  | "$HOME/.claude/hooks/claude-ha-status.sh"
