#!/usr/bin/env bash
# Install the Claude Fleet hook on this computer (macOS or Linux). Safe to re-run: it backs up what it
# touches, never overwrites an existing env file, and the settings merge is a no-op
# if the hook is already wired.
#
#   ./install.sh
#
# It deliberately does NOT ask for the MQTT password. It creates the env file from
# the template and stops, so the secret is only ever typed by you into your editor.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLAUDE_DIR="$HOME/.claude"
TS="$(date +%Y%m%d-%H%M%S)"
say() { printf '%s\n' "$*"; }
ok()  { printf '  ok    %s\n' "$*"; }
warn(){ printf '  WARN  %s\n' "$*"; }

OS="$(uname -s)"

# --uninstall: take the hook back OUT of settings.json, e.g. to switch to the
# plugin (which steps aside while these entries exist). Only entries running
# claude-ha-status are removed; every other hook stays. The env file and the
# script are left: harmless unused, and the env file still feeds the plugin.
if [ "${1:-}" = "--uninstall" ]; then
  SETTINGS="$CLAUDE_DIR/settings.json"
  [ -f "$SETTINGS" ] || { ok "no $SETTINGS; nothing to remove"; exit 0; }
  cp "$SETTINGS" "$SETTINGS.bak-$TS"
  # per event: drop our command from each group, then any group left empty,
  # then any event left with no groups
  if ! jq '
    if .hooks then
      .hooks |= (map_values(
                   map(.hooks |= map(select((.command // "") | test("claude-ha-status") | not)))
                   | map(select((.hooks | length) > 0)))
                 | with_entries(select((.value | length) > 0)))
    else . end
  ' "$SETTINGS" > /tmp/settings.new.$$ || ! jq empty /tmp/settings.new.$$; then
    rm -f /tmp/settings.new.$$ "$SETTINGS.bak-$TS"
    warn "could not rewrite $SETTINGS; it is unchanged"; exit 1
  fi
  mv /tmp/settings.new.$$ "$SETTINGS"
  if diff -q <(jq -S . "$SETTINGS.bak-$TS") <(jq -S . "$SETTINGS") >/dev/null; then
    rm -f "$SETTINGS.bak-$TS"; ok "the hook was not in settings.json; no change"
  else
    ok "removed the hook from settings.json (backup: $(basename "$SETTINGS").bak-$TS)"
  fi
  say "  New sessions no longer report through the script. If you use the plugin, it takes over."
  exit 0
fi
# the install hint for this system's package manager
pkg_hint() { # packages...
  if [ "$OS" = Darwin ]; then say "  install with: brew install $*"
  elif command -v apt-get >/dev/null 2>&1; then say "  install with: sudo apt-get install $*"
  elif command -v dnf >/dev/null 2>&1; then say "  install with: sudo dnf install $*"
  else say "  install these with your package manager: $*"; fi
}

say "== 1. prerequisites =="
missing=()
for t in jq mosquitto_pub; do command -v "$t" >/dev/null 2>&1 || missing+=("$t"); done
if [ ${#missing[@]} -gt 0 ]; then
  say "  missing: ${missing[*]}"
  # mosquitto_pub comes in Homebrew's "mosquitto", Debian's "mosquitto-clients"
  if [ "$OS" = Darwin ]; then pkg_hint mosquitto jq
  elif command -v apt-get >/dev/null 2>&1; then pkg_hint mosquitto-clients jq
  else pkg_hint mosquitto jq; fi
  say "  (only the client is needed; do NOT start a local broker, the broker runs in Home Assistant)"
  exit 1
fi
ok "jq, mosquitto_pub"
command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1 \
  && ok "gh authenticated (PR numbers will be populated)" \
  || warn "gh missing or not logged in — optional, only supplies PR numbers"

say ""
say "== 2. hook script =="
mkdir -p "$CLAUDE_DIR/hooks"
DEST="$CLAUDE_DIR/hooks/claude-ha-status.sh"
if [ -f "$DEST" ] && ! diff -q "$DEST" "$REPO/hooks/claude-ha-status.sh" >/dev/null; then
  cp "$DEST" "$DEST.bak-$TS"; ok "backed up existing hook -> $(basename "$DEST").bak-$TS"
fi
cp "$REPO/hooks/claude-ha-status.sh" "$DEST"; chmod +x "$DEST"
# the version the dashboard shows, from the one place it is kept
jq -r '.version' "$REPO/.claude-plugin/plugin.json" > "$CLAUDE_DIR/hooks/claude-ha-status.version" 2>/dev/null
ok "installed $DEST"

say ""
say "== 3. env file =="
ENV="$CLAUDE_DIR/ha-status.env"
if [ -f "$ENV" ]; then
  ok "$ENV already exists — leaving its values alone"
  for k in MQTT_HOST MQTT_USER MQTT_PASS CLAUDE_HA_MACHINE CLAUDE_HA_STALE_MINUTES; do
    grep -q "^$k=" "$ENV" || warn "missing $k — see ha-status.env.example"
  done
  # BSD stat (macOS) first, GNU stat (Linux) second
  perm=$(stat -f '%Lp' "$ENV" 2>/dev/null || stat -c '%a' "$ENV" 2>/dev/null || echo "")
  [ "$perm" = "600" ] || { chmod 600 "$ENV"; ok "tightened permissions to 600"; }
else
  cp "$REPO/ha-status.env.example" "$ENV"; chmod 600 "$ENV"
  ok "created $ENV from the template (0600)"
  NEEDS_EDIT=1
fi

say ""
say "== 4. settings.json hooks =="
SETTINGS="$CLAUDE_DIR/settings.json"
[ -f "$SETTINGS" ] || echo '{}' > "$SETTINGS"
cp "$SETTINGS" "$SETTINGS.bak-$TS"
H='{"matcher":"*","hooks":[{"type":"command","command":"$HOME/.claude/hooks/claude-ha-status.sh","timeout":5}]}'
# A failed jq must say so. Chained with && alone, a failure left the file as
# it was, and the comparison below then reported "already wired, no change".
if ! jq --argjson h "$H" '
  .hooks = (.hooks // {}) |
  reduce ("SessionStart","UserPromptSubmit","PreToolUse","PostToolUse","Notification","Stop","SessionEnd") as $e (.;
    if ((.hooks[$e] // []) | tostring | test("claude-ha-status")) then .
    else .hooks[$e] = ((.hooks[$e] // []) + [$h]) end)
' "$SETTINGS" > /tmp/settings.new.$$ || ! jq empty /tmp/settings.new.$$; then
  rm -f /tmp/settings.new.$$ "$SETTINGS.bak-$TS"
  warn "could not merge into $SETTINGS (is it valid JSON?); it is unchanged"; exit 1
fi
mv /tmp/settings.new.$$ "$SETTINGS"
# compare SEMANTICALLY: jq reformats the file, so a byte diff reports a change
# on every run even when nothing was added.
if diff -q <(jq -S . "$SETTINGS.bak-$TS") <(jq -S . "$SETTINGS") >/dev/null; then
  rm -f "$SETTINGS.bak-$TS"; ok "already wired on all 7 events — no change"
else
  ok "hooks merged (backup: $(basename "$SETTINGS").bak-$TS)"
  say "  existing hooks preserved:"
  jq -r '[.hooks[][].hooks[]?.command] | map(select(test("claude-ha-status") | not)) | unique | .[]' \
     "$SETTINGS" 2>/dev/null | sed 's/^/    /' || true
fi

say ""
say "== next =="
if [ "${NEEDS_EDIT:-0}" = 1 ]; then
  say "  1. Edit $ENV:"
  say "       MQTT_PASS                 the claude-mqtt password"
  say "       CLAUDE_HA_MACHINE         UNIQUE to this computer — it is the dashboard's Machine column"
  say "       CLAUDE_HA_STALE_MINUTES   20 on an always-on machine, 0 on a laptop"
  say "  2. Test:  $REPO/scripts/diagnose.sh   (a test publish, plus everything else worth checking)"
fi
if [ "$OS" = Darwin ]; then
  say "  macOS Local Network: the terminal app launching Claude Code must be allowed under"
  say "  System Settings > Privacy & Security > Local Network, then FULLY QUIT (Cmd-Q) and"
  say "  relaunched. A new tab is not enough. Symptom otherwise: mosquitto_pub fails with"
  say "  'Bad file descriptor' while curl and nc to the same host work."
fi
say "  Hooks load at session start — only NEW sessions report."
