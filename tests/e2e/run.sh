#!/bin/bash
# The end-to-end test, the way CI runs it:
#
#   tests/e2e/run.sh                  selftest, up, scenario, check, down
#   tests/e2e/run.sh PHASE...         only those phases, in the order given
#
# Phases:
#   selftest   check.py --self-test: every check against synthetic good and bad
#              input, no Home Assistant needed. Run first: a check that cannot
#              fail proves nothing.
#   up         demo/up.sh: a throwaway HA + Mosquitto in Docker, Claude Fleet
#              installed the README's way
#   scenario   demo/scenario.sh with CF_DEMO_FAST=1: six invented sessions
#              through the real hook, the plan source, 30 days of history
#   check      check.py: the assertions against that HA
#   shots      demo/shots.sh: screenshots into tests/e2e/demo/out/ (needs Chrome)
#   logs       HA's and the broker's logs, and the demo's own, into $CF_E2E_OUT
#   down       demo/down.sh: containers, network and CF_DEMO_DIR, all gone
#
# With no phases, a failure anywhere still runs `logs` and `down`
# (CF_E2E_KEEP=1 keeps the demo up to look at it).
#
# Settings (all optional). The defaults stay clear of a demo already running
# with demo/up.sh's own defaults (cf-demo, ports 8123 and 1883):
#   CF_DEMO_NAME       container prefix              default cf-e2e
#   CF_DEMO_HA_PORT    Home Assistant on 127.0.0.1   default 8124
#   CF_DEMO_MQTT_PORT  the broker on 127.0.0.1       default 1884
#   CF_DEMO_DIR        runtime state                 default $TMPDIR/cf-e2e
#   CF_E2E_OUT         logs and the check's output   default tests/e2e/out
set -uo pipefail
E2E="$(cd "$(dirname "$0")" && pwd)"
export CF_DEMO_NAME="${CF_DEMO_NAME:-cf-e2e}"
export CF_DEMO_HA_PORT="${CF_DEMO_HA_PORT:-8124}"
export CF_DEMO_MQTT_PORT="${CF_DEMO_MQTT_PORT:-1884}"
TMP="${TMPDIR:-/tmp}"
export CF_DEMO_DIR="${CF_DEMO_DIR:-${TMP%/}/cf-e2e}"
export CF_DEMO_FAST=1
OUT="${CF_E2E_OUT:-$E2E/out}"
mkdir -p "$OUT"
# shellcheck source=demo/lib.sh
. "$E2E/demo/lib.sh"

phase_selftest() {
  # the demo's venv, made here if up.sh has not made it yet: the self-test
  # needs pyyaml, and runs before anything else
  if ! [ -x "$PY" ] || ! "$PY" -c 'import websockets, yaml' 2>/dev/null; then
    mkdir -p "$CF_DEMO_DIR"
    python3 -m venv "$VENV" && "$VENV/bin/pip" install -q --disable-pip-version-check websockets pyyaml \
      || { say "  FAIL  could not make a venv with websockets and pyyaml in $VENV"; return 1; }
  fi
  "$PY" "$E2E/check.py" --self-test 2>&1 | tee "$OUT/selftest.log"
}
phase_up() {
  if container_exists "$C_HA" && ! [ -d "$CF_DEMO_DIR/secrets" ]; then
    say "  FAIL  a container $C_HA exists that is not this run's ($CF_DEMO_DIR); set CF_DEMO_NAME"
    return 1
  fi
  "$DEMO/up.sh" 2>&1 | tee "$OUT/up.log"
}
phase_scenario() { "$DEMO/scenario.sh" 2>&1 | tee "$OUT/scenario.log"; }
phase_check()    { "$PY" "$E2E/check.py" 2>&1 | tee "$OUT/check.log"; }
phase_shots()    { "$DEMO/shots.sh" 2>&1 | tee "$OUT/shots.log"; }
phase_logs() {
  docker logs "$C_HA" > "$OUT/home-assistant.log" 2>&1 || true
  docker logs "$C_MQTT" > "$OUT/mosquitto.log" 2>&1 || true
  # HA's own file too: rotated at each restart, so both
  for f in home-assistant.log home-assistant.log.1; do
    [ -r "$CONFIG/$f" ] && cp "$CONFIG/$f" "$OUT/config-$f" 2>/dev/null
  done
  say "  ok    logs in $OUT"
}
phase_down() { "$DEMO/down.sh" 2>&1 | tee "$OUT/down.log"; }

run() { # phase
  local t0=$SECONDS rc
  step "e2e: $1"
  "phase_$1"; rc=$?
  printf '  --    %s: %s in %ss\n' "$1" "$([ $rc = 0 ] && echo passed || echo FAILED)" $((SECONDS - t0))
  return $rc
}

if [ $# -gt 0 ]; then
  for p in "$@"; do
    declare -F "phase_$p" >/dev/null || die "no phase '$p' (selftest up scenario check shots logs down)"
    run "$p" || exit 1
  done
  exit 0
fi

T0=$SECONDS
rc=0
for p in selftest up scenario check; do
  run "$p" || { rc=1; failed=$p; break; }
done
run logs
if [ "${CF_E2E_KEEP:-0}" = 1 ]; then
  say "  keep  the demo is still up: $BASE_URL (CF_E2E_KEEP=1); tests/e2e/run.sh down when done"
else
  run down || true
fi
say ""
if [ $rc = 0 ]; then
  say "E2E PASSED in $((SECONDS - T0))s"
else
  say "E2E FAILED at '$failed' after $((SECONDS - T0))s; logs in $OUT"
fi
exit $rc
