# Shared settings for the demo scripts. Sourced, never run.
#
# Everything the demo creates lives in two places: Docker containers named
# cf-demo-*, and CF_DEMO_DIR. Nothing here reads or writes a real ~/.claude, a
# real broker or a real Home Assistant.

DEMO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$DEMO/../../.." && pwd)"
E2E="$REPO/tests/e2e"

# ---- settings (override in the environment) --------------------------------
# Runtime state: HA's /config, the broker's files, the fake homes, credentials.
CF_DEMO_DIR="${CF_DEMO_DIR:-${TMPDIR:-/tmp}/claude-fleet-demo}"
CF_DEMO_DIR="${CF_DEMO_DIR%/}"
HA_IMAGE="${CF_DEMO_HA_IMAGE:-ghcr.io/home-assistant/home-assistant:2026.9.4}"
MQTT_IMAGE="${CF_DEMO_MQTT_IMAGE:-eclipse-mosquitto:2}"
HA_PORT="${CF_DEMO_HA_PORT:-8123}"           # on 127.0.0.1 only
MQTT_PORT="${CF_DEMO_MQTT_PORT:-1883}"        # on 127.0.0.1 only
TZ_NAME="America/Los_Angeles"
# history explorer card, the release the demo was built against
HX_VERSION="v1.0.51"
HX_SHA256="5951eb8e61f40a9fdb9fe06791a2931fdebd9167dfc67982db55ab465c71e942"

# Docker names: <name>-ha, <name>-mqtt, <name>-net. A second demo beside a
# running one needs its own name, ports and CF_DEMO_DIR.
CF_DEMO_NAME="${CF_DEMO_NAME:-cf-demo}"
NET="$CF_DEMO_NAME-net"
C_HA="$CF_DEMO_NAME-ha"
C_MQTT="$CF_DEMO_NAME-mqtt"

CONFIG="$CF_DEMO_DIR/config"
MOSQ="$CF_DEMO_DIR/mosquitto"
SECRETS="$CF_DEMO_DIR/secrets"
HOMES="$CF_DEMO_DIR/homes"
VENV="$CF_DEMO_DIR/venv"
PY="$VENV/bin/python"
BASE_URL="http://localhost:$HA_PORT"

export CF_DEMO_DIR SECRETS BASE_URL TZ_NAME C_HA

# OrbStack's docker, if it is there
[ -d "$HOME/.orbstack/bin" ] && PATH="$HOME/.orbstack/bin:$PATH"

say()  { printf '%s\n' "$*"; }
step() { printf '\n== %s ==\n' "$*"; }
ok()   { printf '  ok    %s\n' "$*"; }
die()  { printf '  FAIL  %s\n' "$*" >&2; exit 1; }

# sha256 of a file: shasum on macOS, sha256sum on Linux (either on either)
sha256() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1
  else shasum -a 256 "$1" | cut -d' ' -f1; fi
}

need() { for t in "$@"; do command -v "$t" >/dev/null 2>&1 || die "missing: $t"; done; }

# A throwaway password: 24 random alphanumerics, no process argument ever holds it.
new_secret() { # file
  [ -s "$1" ] && return 0
  ( umask 077; python3 -c 'import secrets; print(secrets.token_hex(12))' > "$1" )
}

container_running() { [ "$(docker inspect -f '{{.State.Running}}' "$1" 2>/dev/null)" = true ]; }
container_exists()  { docker inspect "$1" >/dev/null 2>&1; }

# Wait until HA's web server answers. Not /api/: an unauthenticated request there
# is logged as a failed login.
wait_ha() { # [seconds]
  local i code
  for i in $(seq 1 "${1:-180}"); do
    code=$(curl -s -o /dev/null -w '%{http_code}' "$BASE_URL/manifest.json" 2>/dev/null) || true
    [ "$code" = 200 ] && return 0
    sleep 1
  done
  die "Home Assistant did not come up on $BASE_URL (see: docker logs $C_HA)"
}

need_venv() { [ -x "$PY" ] || die "no venv at $VENV; run up.sh first"; }
