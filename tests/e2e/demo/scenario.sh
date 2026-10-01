#!/bin/bash
# Load the demo scenario into the Home Assistant that up.sh started:
#
#   CF_DEMO_DIR=/some/scratch/dir ./scenario.sh
#
# Three invented computers (fake homes, each with the hook installed by
# install.sh), six invented sessions driven through the real hook, 30 days of
# invented history, a fake plan-usage source, RTK ledgers and one aside. Safe to
# re-run: it clears the previous run's sessions first. Takes about 2 minutes, much
# of it waiting for the stale session to go stale for real.
#
# Take the screenshots within 10 minutes of this finishing: the ended session is
# removed by the package's cleanup after that, as it would be on a real install.
set -euo pipefail
. "$(dirname "$0")/lib.sh"
need docker jq git sqlite3 mosquitto_pub mosquitto_sub
need_venv
container_running "$C_HA" && container_running "$C_MQTT" || die "the demo is not running; run up.sh first"
[ -s "$SECRETS/token" ] || die "no HA token in $SECRETS; run up.sh first"

# The hook reads the day in local time; so does HA, in its own time zone. Make
# them agree whatever this computer's zone is.
export TZ="$TZ_NAME"

# ---- the fake claude (and rtk) first on PATH -------------------------------
FAKEBIN="$CF_DEMO_DIR/bin"
mkdir -p "$FAKEBIN"
cp "$DEMO/fake-claude" "$FAKEBIN/claude"; cp "$DEMO/fake-rtk" "$FAKEBIN/rtk"
chmod +x "$FAKEBIN/claude" "$FAKEBIN/rtk"
export PATH="$FAKEBIN:$PATH"
# The hook puts these folders IN FRONT of the PATH it is given. A real claude in
# one of them would answer the summary calls instead of the fake, from a real login.
for d in /opt/homebrew/bin /usr/local/bin /usr/bin /bin; do
  [ -x "$d/claude" ] && die "a real claude in $d comes before the fake one on the hook's PATH; the demo would call it for summaries"
done
# gh: no login in the fake homes, and nothing to find at example.invalid anyway
export GH_CONFIG_DIR="$CF_DEMO_DIR/gh" GH_PROMPT_DISABLED=1 GH_NO_UPDATE_NOTIFIER=1
mkdir -p "$GH_CONFIG_DIR"
G() { "$PY" "$DEMO/gen.py" "$@"; }
H() { "$PY" "$DEMO/ha.py" "$@"; }

step "1. clear the previous run"
# every retained session topic and discovery entry on the demo broker; HA drops
# the entities when their discovery entry is emptied
d=$(umask 077; mktemp -d)
printf -- '-P %s\n' "$(cat "$SECRETS/mqtt_fleet_password")" > "$d/mosquitto_sub"
# discovery entries first, so HA drops the entities before their attributes go
# empty (an empty attributes payload on a live entity is logged as bad JSON)
for t in 'homeassistant/sensor/#' 'claude/#'; do
  XDG_CONFIG_HOME="$d" mosquitto_sub -h 127.0.0.1 -p "$MQTT_PORT" -u claude-mqtt \
    -t "$t" --retained-only --remove-retained -W 3 >/dev/null 2>&1 || true
done
rm -rf "$d"
rm -rf "$HOMES" "$CF_DEMO_DIR/scenario" "$CF_DEMO_DIR/repos"
mkdir -p "$HOMES"
ok "broker topics, fake homes and repos cleared"

step "2. three computers"
# name:stale-minutes:rtk
for spec in studio:20:on laptop:0:off mini:1:on; do
  IFS=: read -r m stale rtk <<EOF
$spec
EOF
  home="$HOMES/$m"; mkdir -p "$home"
  ( cd "$REPO" && HOME="$home" ./install.sh >/dev/null ) || die "install.sh failed for $m"
  ( umask 077
    cat > "$home/.claude/ha-status.env" <<EOF
# demo: $m
MQTT_HOST=127.0.0.1
MQTT_PORT=$MQTT_PORT
MQTT_USER=claude-mqtt
MQTT_PASS='$(cat "$SECRETS/mqtt_fleet_password")'
CLAUDE_HA_MACHINE="$m"
CLAUDE_HA_STALE_MINUTES=$stale
SUMMARY_ENABLED=1
EOF
  )
  if [ "$rtk" = on ]; then
    # RTK's own Claude Code hook, as `rtk init -g` adds it; the fleet hook only
    # looks for it here to say whether RTK is on for this computer
    jq '.hooks.PreToolUse += [{"matcher":"Bash","hooks":[{"type":"command","command":"rtk hook claude"}]}]' \
      "$home/.claude/settings.json" > "$home/.claude/settings.json.new" && mv "$home/.claude/settings.json.new" "$home/.claude/settings.json"
  fi
  ok "$m: hook installed by install.sh, stale limit $stale, RTK $rtk"
done

step "3. invented data"
G prepare

step "4. plan usage"
G plan

step "5. sessions, through the real hook"
G run

step "6. recorded history and the day-scoped sensors (restarts Home Assistant)"
rm -f "$CONFIG/packages/cf_demo.yaml"   # an earlier version of this demo used one
docker stop -t 60 "$C_HA" >/dev/null
# Both written from inside a container, with the HA image's own Python and
# sqlite. The recorder database: sqlite from the host, across Docker's shared
# folder, corrupted it (the locking does not carry over). The restore state:
# on Linux, HA runs as root and owns everything it wrote in /config, so the
# host user cannot write it.
docker run --rm -v "$CONFIG:/cfd/config" -v "$CF_DEMO_DIR/scenario:/cfd/scenario:ro" -v "$DEMO:/demo:ro" \
  -e CF_DEMO_DIR=/cfd -e "TZ_NAME=$TZ_NAME" --entrypoint sh "$HA_IMAGE" \
  -c 'python3 /demo/gen.py restore && python3 /demo/gen.py inject'
docker start "$C_HA" >/dev/null
wait_ha 240
H wait 300
ok "restarted"

step "7. 30 days of long-term statistics"
ids=$(jq -c 'keys' "$CF_DEMO_DIR/scenario/stats.json")
H ws "[{\"type\": \"recorder/clear_statistics\", \"statistic_ids\": $ids}]" >/dev/null
G import-stats

step "8. the aside"
quip=$("$PY" -c 'import sys; sys.path.insert(0, sys.argv[1]); import gen; print(gen.QUIP)' "$DEMO")
H call input_text set_value "$(jq -nc --arg v "$quip" '{entity_id: "input_text.claude_fleet_quip", value: $v}')"
ok "\"$quip\""

step "9. waiting for the stale session to go quiet for real, and for the burn rate"
since=$(cat "$CF_DEMO_DIR/scenario/stale_since")
# stale is decided on seconds of silence (a 1-minute limit: stale from 61 s),
# in the sessions table and the sensors alike; 70 s leaves some margin
while [ $(( $(date +%s) - since )) -lt 70 ]; do sleep 5; done
ok "the stale session has been silent for 70 s"
# The plan burn rate needs five minutes of readings; the restore state seeds
# ten, so it is normally known on the first minute tick. CF_DEMO_FAST=1 (the
# e2e test) skips this wait: check.py waits for the rate itself, and fails if
# it never comes.
if [ "${CF_DEMO_FAST:-0}" = 1 ]; then
  say "  skip  burn-rate wait (CF_DEMO_FAST=1)"
else
  for i in $(seq 1 60); do
    H states sensor.claude_plan_usage_rate | awk '{print $2}' | grep -Eq '^-?[0-9.]+$' && break
    sleep 10
  done
  H states sensor.claude_plan_usage_rate | awk '{print $2}' | grep -Eq '^-?[0-9.]+$' \
    || die "sensor.claude_plan_usage_rate still unknown after 10 minutes"
  ok "burn rate: $(H states sensor.claude_plan_usage_rate | awk '{print $2}') %/min"
fi

step "10. the ended session, last, so it is still on the dashboard for the screenshots"
G run ended
G check
st=$(H states sensor.claude_fleet_status | awk '{print $2}')
stale=$(H states sensor.claude_sessions_stale | awk '{print $2}')
[ "$stale" = 1 ] || die "expected 1 stale session, HA says $stale"
ok "fleet status: $st, stale sessions: $stale"
say ""
say "Scenario loaded: $BASE_URL/claude-fleet/sessions"
say "  screenshots now (the ended session is cleaned up ~10 minutes after it ended):"
say "  CF_DEMO_DIR=$CF_DEMO_DIR $DEMO/shots.sh"
