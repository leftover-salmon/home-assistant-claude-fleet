#!/usr/bin/env bash
# diagnose.sh — why isn't this computer reporting to Claude Fleet?
#
#   ./scripts/diagnose.sh            checks, a test publish, and a synthetic hook run
#   ./scripts/diagnose.sh --claude   also runs `claude --debug -p` once and reads its
#                                    hook log (one short model call)
#
# Prints a report and ALSO publishes it, retained, to claude/<machine>/diag, so
# someone at the dashboard end can read it without anything being copied over.
# Changes nothing. Never prints or publishes the MQTT password: settings are
# reported as set / not set.
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLAUDE_DIR="$HOME/.claude"
CONF="${CLAUDE_HA_CONF:-$CLAUDE_DIR/ha-status.env}"
OUT=""
say() { printf '%s\n' "$*"; OUT+="$*"$'\n'; }
has() { command -v "$1" >/dev/null 2>&1; }
# The password goes in mosquitto_pub's options file, never on the command line where
# `ps` shows it; the hook does the same, and says why.
pub_as() { # user mosquitto_pub-args...
  local u="$1" d rc; shift
  if [ -z "$u" ]; then mosquitto_pub "$@"; return; fi
  if [ -z "${MQTT_PASS:-}" ]; then mosquitto_pub -u "$u" "$@"; return; fi
  d=$(umask 077; mktemp -d "${TMPDIR:-/tmp}/claude-fleet.XXXXXX") || return 1
  printf -- '-P %s\n' "$MQTT_PASS" > "$d/mosquitto_pub"
  XDG_CONFIG_HOME="$d" mosquitto_pub -u "$u" "$@"; rc=$?
  rm -rf "$d"
  return $rc
}

say "== Claude Fleet diagnose, $(date '+%Y-%m-%d %H:%M:%S %Z')"
say "system: $(uname -s) $(uname -r) · bash $BASH_VERSION at $(command -v bash)"
for t in jq mosquitto_pub mosquitto_sub sqlite3 gh claude rtk; do
  if has "$t"; then say "  ✓ $t: $(command -v "$t")"; else say "  ✗ $t: not found"; fi
done

say ""
say "== settings"
if [ -f "$CONF" ]; then
  # shellcheck disable=SC1090
  . "$CONF"
  say "env file: $CONF"
  for k in MQTT_HOST MQTT_PORT MQTT_USER MQTT_PASS CLAUDE_HA_MACHINE CLAUDE_HA_STALE_MINUTES SUMMARY_ENABLED; do
    v="${!k:-}"
    case "$k" in
      MQTT_PASS) [ -n "$v" ] && [ "$v" != FILL_ME_IN ] && say "  $k: set" || say "  $k: NOT SET" ;;
      *) say "  $k: ${v:-(not set)}" ;;
    esac
  done
else
  say "env file: none at $CONF (fine with the plugin, if its settings are filled in)"
fi
MQTT_HOST="${MQTT_HOST:-homeassistant.local}"; MQTT_PORT="${MQTT_PORT:-1883}"

S="$CLAUDE_DIR/settings.json"
if [ -f "$S" ] && has jq; then
  say "settings.json:"
  say "  script's hook present: $(grep -c 'claude-ha-status' "$S") mention(s) (0 = not installed by install.sh)"
  say "  enabledPlugins: $(jq -c '[.enabledPlugins // {} | to_entries[] | select(.key | test("claude-fleet")) ] | from_entries' "$S" 2>/dev/null)"
  # the plugin's saved settings, except the password: a wrong host, port or user
  # here overrides the env file, and that's where a plugin install goes wrong
  say "  plugin settings saved: $(jq -c '[.pluginConfigs // {} | to_entries[] | select(.key | test("claude-fleet")) | .value | (.options // .)] | first // {}
      | with_entries(if (.key | test("password"; "i")) then .value = "(in settings.json!)" else . end)' "$S" 2>/dev/null)"
  say "  (a password not listed there is kept in secure storage, as intended)"
  say "  disableAllHooks: $(jq -r '.disableAllHooks // false' "$S" 2>/dev/null)"
else
  say "settings.json: not found"
fi

say ""
say "== plugin"
if has claude; then
  say "$(claude plugin list 2>&1 | grep -i -A3 'claude-fleet' | head -6 | sed 's/^/  /')"
fi
PDIRS=$(find "$CLAUDE_DIR/plugins" -type f -path '*claude-fleet*' -name hooks.json 2>/dev/null | sed 's#/hooks/hooks.json$##')
if [ -z "$PDIRS" ]; then
  say "no installed copy of the plugin found under $CLAUDE_DIR/plugins"
else
  while IFS= read -r d; do
    v=$(jq -r '.version // "?"' "$d/.claude-plugin/plugin.json" 2>/dev/null)
    x=$([ -x "$d/hooks/claude-ha-status.sh" ] && echo executable || echo "NOT executable")
    say "  copy: $d (version $v, hook $x)"
  done <<<"$PDIRS"
fi

say ""
say "== broker"
SLUG=$(printf '%s' "${CLAUDE_HA_MACHINE:-$(scutil --get LocalHostName 2>/dev/null || hostname -s)}" \
       | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9]+/_/g; s/^_+|_+$//g')
say "machine slug: $SLUG"
if has mosquitto_pub; then
  if err=$(pub_as "${MQTT_USER:-}" -h "$MQTT_HOST" -p "$MQTT_PORT" -q 1 -t "claude/$SLUG/diag/ping" -m ping 2>&1); then
    say "  ✓ publish with the env file's settings ($MQTT_HOST:$MQTT_PORT) works"
    pub_as "${MQTT_USER:-}" -h "$MQTT_HOST" -p "$MQTT_PORT" -r -n -t "claude/$SLUG/diag/ping" >/dev/null 2>&1
  else
    say "  ✗ publish with the env file's settings ($MQTT_HOST:$MQTT_PORT) failed: $err"
    [ "$(uname -s)" = Darwin ] && say "    (on a Mac, 'Bad file descriptor' or 'No route to host' usually means Local Network permission; see the README)"
  fi
fi

# The plugin's saved host/port/user override the env file inside real sessions,
# so test those too. Testing only the env file's settings said "publish works"
# on a Mac whose plugin host was misspelt (homeassitant.local), which is the
# one case this script most needed to catch.
if [ -f "$S" ] && has jq && has mosquitto_pub; then
  P=$(jq -c '[.pluginConfigs // {} | to_entries[] | select(.key | test("claude-fleet")) | .value | (.options // .)] | first // {}' "$S" 2>/dev/null)
  ph=$(jq -r '.mqtt_host // empty' <<<"$P"); pp=$(jq -r '.mqtt_port // empty' <<<"$P" | sed 's/\.0$//'); pu=$(jq -r '.mqtt_user // empty' <<<"$P")
  if [ -n "$ph$pp$pu" ]; then
    h="${ph:-$MQTT_HOST}"; po="${pp:-$MQTT_PORT}"; u="${pu:-${MQTT_USER:-}}"
    if err=$(pub_as "$u" -h "$h" -p "$po" -q 1 -t "claude/$SLUG/diag/ping" -m ping 2>&1); then
      say "  ✓ publish with the PLUGIN's settings ($h:$po as ${u:-no user}) works"
      pub_as "$u" -h "$h" -p "$po" -r -n -t "claude/$SLUG/diag/ping" >/dev/null 2>&1
    else
      say "  ✗ publish with the PLUGIN's settings ($h:$po as ${u:-no user}) FAILED: $err"
      say "    real sessions use these. Fix them in /plugin → Installed → claude-fleet → Configure options"
    fi
    say "    (the password for this test comes from the env file; a plugin-saved one lives in secure storage and can't be read here)"
  fi
fi

say ""
say "== the hook's error log (failed publishes; newest last)"
EL="${CLAUDE_HA_STATE_DIR:-$CLAUDE_DIR/ha-status}/errors.log"
if [ -s "$EL" ]; then say "$(tail -n 8 "$EL" | sed 's/^/  /')"; else say "  empty: no failed publish recorded (hooks from plugin 0.1.3 on log them)"; fi

say ""
say "== the hook, run by hand as the plugin would"
for d in $PDIRS; do
  printf '{"hook_event_name":"SessionStart","session_id":"diag-%s-0000-0000","cwd":"%s","transcript_path":""}' "$SLUG" "$PWD" \
    | CLAUDE_HA_SYNC=1 SUMMARY_ENABLED=0 CLAUDE_PLUGIN_ROOT="$d" bash "$d/hooks/claude-ha-status.sh" >/tmp/fleet-diag.$$ 2>&1
  rc=$?
  say "  $d: exit $rc$( [ -s /tmp/fleet-diag.$$ ] && echo "; output: $(head -c 400 /tmp/fleet-diag.$$ | tr '\n' ' ')")"
  rm -f /tmp/fleet-diag.$$
done
[ -z "$PDIRS" ] && say "  (no plugin copy to run)"
# take the test session back off the broker, or it sits on the dashboard as a
# phantom "MacBook Neo" row until cleanup gets round to it
if [ -n "$PDIRS" ] && has mosquitto_pub; then
  sid="diag-$SLUG-0000-0000"
  for t in "claude/$SLUG/$sid/attrs" "claude/$SLUG/$sid/state" "homeassistant/sensor/claude_${SLUG}_${sid:0:8}/config"; do
    pub_as "${MQTT_USER:-}" -h "$MQTT_HOST" -p "$MQTT_PORT" -r -n -t "$t" >/dev/null 2>&1
  done
  rm -f "${CLAUDE_HA_STATE_DIR:-$CLAUDE_DIR/ha-status}/$sid.json"
  say "  (the test session was published, then removed again)"
fi

if [ "${1:-}" = "--claude" ] && has claude; then
  say ""
  say "== a real Claude Code run with --debug"
  t=$(mktemp -d); ( cd "$t" && claude --debug -p "reply with ok" >/dev/null 2>&1 ); rm -rf "$t"
  f=$(ls -t "$CLAUDE_DIR"/debug/*.txt 2>/dev/null | head -1)
  if [ -n "$f" ]; then
    say "  debug log: $f"
    say "$(grep -i -E 'claude-ha-status|claude-fleet|hook' "$f" | grep -v -i 'password' \
           | sed -E 's/(-P|MQTT_PASS=|mqtt_password["=: ]+)[^ ]*/\1***/g' | tail -25 | cut -c1-220 | sed 's/^/  /')"
  else
    say "  no debug log written"
  fi
fi

# the report itself, for whoever is at the dashboard end
if has mosquitto_pub; then
  pub_as "${MQTT_USER:-}" -h "$MQTT_HOST" -p "$MQTT_PORT" -r -q 1 -t "claude/$SLUG/diag/report" -m "$OUT" >/dev/null 2>&1 \
    && printf '\n(report also published to claude/%s/diag/report)\n' "$SLUG"
fi
