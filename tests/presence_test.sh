#!/usr/bin/env bash
# /away and /back are answered by the hook only when they are Fleet's: a
# project's or the person's own command or skill of the same name is left to
# run. Run: tests/presence_test.sh (needs jq). Exits non-zero on a failure.
set -u
REPO=$(cd "$(dirname "$0")/.." && pwd)
S=$(mktemp -d); trap 'rm -rf "$S"' EXIT
mkdir -p "$S/home/.claude/commands" "$S/proj/sub"
sed -n '/^presence_of()/,/^}/p;/^only_fleet_has()/,/^}/p' "$REPO/hooks/claude-ha-status.sh" > "$S/fn.sh"
cp "$REPO/commands/away.md" "$REPO/commands/back.md" "$S/home/.claude/commands/"
. "$S/fn.sh"
check() { # label prompt expect
  INPUT=$(jq -nc --arg c "$S/proj/sub" '{cwd:$c}'); got=$(presence_of "$2")
  [ "$got" = "$3" ] && echo "ok   $1" || { echo "FAIL $1: got '$got' want '$3'"; fail=1; }
}
export HOME=$S/home; unset CLAUDE_CONFIG_DIR
check "ours installed, bare /away"        "<command-name>/away</command-name>" away
check "ours installed, bare /back"        "<command-name>/back</command-name>" back
check "plugin form"                       "<command-name>/claude-fleet:back</command-name>" back
check "ordinary prompt"                   "back to the bug please" ""
mkdir -p $S/proj/.claude/commands; echo "Go back one step in git" > $S/proj/.claude/commands/back.md
check "project's own /back is left alone"  "<command-name>/back</command-name>" ""
check "  ...its /away still ours"          "<command-name>/away</command-name>" away
check "  ...plugin form still ours"        "<command-name>/claude-fleet:back</command-name>" back
rm -r $S/proj/.claude; mkdir -p $S/home/.claude/skills/away; echo "my away skill" > $S/home/.claude/skills/away/SKILL.md
check "personal away skill is left alone" "/away" ""
rm -r $S/home/.claude/skills; rm $S/home/.claude/commands/*.md
check "plugin install, no files: bare"    "<command-name>/away</command-name>" away

[ "${fail:-0}" = 0 ] && echo "all passed"
exit "${fail:-0}"
