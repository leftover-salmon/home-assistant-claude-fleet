#!/bin/bash
# Start a throwaway Home Assistant and Mosquitto in Docker and install Claude
# Fleet on it, as the README says, up to an installed and EMPTY dashboard:
#
#   CF_DEMO_DIR=/some/scratch/dir ./up.sh
#
# Safe to re-run: every step checks what is already there. Nothing touches a
# real Home Assistant, broker or ~/.claude. Then run scenario.sh.
set -euo pipefail
. "$(dirname "$0")/lib.sh"
need docker curl jq python3
command -v sha256sum >/dev/null 2>&1 || need shasum

step "0. runtime folder: $CF_DEMO_DIR"
mkdir -p "$CONFIG"/{packages,dashboards,www,themes} "$MOSQ"/{config,data} "$HOMES"
( umask 077; mkdir -p "$SECRETS" ); chmod 700 "$SECRETS"
new_secret "$SECRETS/ha_password"
new_secret "$SECRETS/mqtt_ha_password"
new_secret "$SECRETS/mqtt_fleet_password"
ok "throwaway credentials in $SECRETS (mode 600)"

if [ ! -x "$PY" ]; then
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install -q --disable-pip-version-check websockets pyyaml
fi
"$PY" -c "import websockets, yaml" 2>/dev/null || "$VENV/bin/pip" install -q --disable-pip-version-check websockets pyyaml
ok "python venv with websockets and pyyaml"

step "1. broker"
docker network inspect "$NET" >/dev/null 2>&1 || docker network create "$NET" >/dev/null
# Written only when missing, and the password file below only read when it is
# readable: on Linux the broker's entrypoint chowns /mosquitto to its own user,
# so on a re-run these are no longer ours (and are already hashed).
[ -f "$MOSQ/config/mosquitto.conf" ] || cat > "$MOSQ/config/mosquitto.conf" <<'EOF'
listener 1883
allow_anonymous false
password_file /mosquitto/config/passwd
persistence true
persistence_location /mosquitto/data/
log_dest stdout
EOF
if [ ! -s "$MOSQ/config/passwd" ] || { [ -r "$MOSQ/config/passwd" ] && grep -qv ':\$' "$MOSQ/config/passwd"; }; then
  # plain user:password lines, hashed in place by mosquitto_passwd -U, so no
  # password is ever a process argument
  ( umask 077
    printf 'homeassistant:%s\nclaude-mqtt:%s\n' "$(cat "$SECRETS/mqtt_ha_password")" \
      "$(cat "$SECRETS/mqtt_fleet_password")" > "$MOSQ/config/passwd" )
  docker run --rm -v "$MOSQ/config:/mosquitto/config" "$MQTT_IMAGE" \
    sh -c 'mosquitto_passwd -U /mosquitto/config/passwd && chown mosquitto:mosquitto /mosquitto/config/passwd && chmod 600 /mosquitto/config/passwd' \
    || die "could not hash the broker's password file"
fi
if container_exists "$C_MQTT"; then
  container_running "$C_MQTT" || docker start "$C_MQTT" >/dev/null
else
  docker run -d --name "$C_MQTT" --network "$NET" --restart unless-stopped \
    -p "127.0.0.1:$MQTT_PORT:1883" \
    -v "$MOSQ/config:/mosquitto/config" -v "$MOSQ/data:/mosquitto/data" \
    "$MQTT_IMAGE" >/dev/null
fi
ok "$C_MQTT on 127.0.0.1:$MQTT_PORT, logins homeassistant and claude-mqtt"

step "2. Home Assistant files"
CFG="$CONFIG/configuration.yaml"
MARK="# written by tests/e2e/demo/up.sh"
if [ -f "$CFG" ] && ! grep -qF "$MARK" "$CFG"; then
  die "$CFG exists and was not written by this script; is CF_DEMO_DIR right?"
fi
# HA's own defaults, plus the README's two keys
cat > "$CFG" <<EOF
$MARK
default_config:
frontend:
  themes: !include_dir_merge_named themes
automation: !include automations.yaml
script: !include scripts.yaml
scene: !include scenes.yaml

# ---- from the Claude Fleet README
homeassistant:
  packages: !include_dir_named packages
lovelace:
  dashboards:
    claude-fleet:
      mode: yaml
      title: Claude Fleet
      icon: mdi:robot-outline
      show_in_sidebar: true
      filename: dashboards/claude_fleet.yaml
EOF
for f in automations scenes; do [ -f "$CONFIG/$f.yaml" ] || echo '[]' > "$CONFIG/$f.yaml"; done
[ -f "$CONFIG/scripts.yaml" ] || echo '{}' > "$CONFIG/scripts.yaml"
cp "$REPO/homeassistant/packages/claude_fleet.yaml" "$REPO/homeassistant/packages/claude_fleet_quip.yaml" "$CONFIG/packages/"
cp "$REPO/homeassistant/dashboards/claude_fleet.yaml" "$CONFIG/dashboards/"
ok "configuration.yaml, the package, the aside package and the dashboard"

HX="$CONFIG/www/history-explorer-card.js"
if [ ! -f "$HX" ] || [ "$(sha256 "$HX")" != "$HX_SHA256" ]; then
  curl -fsSL -o "$HX.tmp" "https://github.com/alexarch21/history-explorer-card/releases/download/$HX_VERSION/history-explorer-card.js" \
    || die "could not download the history explorer card"
  [ "$(sha256 "$HX.tmp")" = "$HX_SHA256" ] || { rm -f "$HX.tmp"; die "history explorer card: checksum mismatch"; }
  mv "$HX.tmp" "$HX"
fi
ok "history explorer card $HX_VERSION in /config/www"

step "3. Home Assistant container"
if container_exists "$C_HA"; then
  img=$(docker inspect -f '{{.Config.Image}}' "$C_HA")
  [ "$img" = "$HA_IMAGE" ] || die "$C_HA runs $img, not $HA_IMAGE; run down.sh first"
  container_running "$C_HA" || docker start "$C_HA" >/dev/null
else
  docker run -d --name "$C_HA" --network "$NET" --restart unless-stopped \
    -p "127.0.0.1:$HA_PORT:8123" -e "TZ=$TZ_NAME" -v "$CONFIG:/config" \
    "$HA_IMAGE" >/dev/null
fi
wait_ha 240
ok "$C_HA ($HA_IMAGE) answering on $BASE_URL"

step "4. onboarding"
"$PY" "$DEMO/ha.py" onboard
"$PY" "$DEMO/ha.py" wait 240
"$PY" "$DEMO/ha.py" core-config

step "5. MQTT integration"
"$PY" "$DEMO/ha.py" mqtt "$C_MQTT" 1883

step "6. history explorer card resource"
"$PY" "$DEMO/ha.py" resource "/local/history-explorer-card.js?v=${HX_VERSION#v}"

step "7. check and restart"
"$PY" "$DEMO/ha.py" check-config
"$PY" "$DEMO/ha.py" settle
"$PY" "$DEMO/ha.py" restart

step "8. installed?"
for e in sensor.claude_fleet_status sensor.claude_sessions_running input_text.claude_fleet_quip; do
  "$PY" "$DEMO/ha.py" states "$e" | grep -q . || die "$e is missing: the package did not load (docker logs $C_HA)"
done
n=$("$PY" "$DEMO/ha.py" states sensor. | while read -r id st; do
      echo "$id"; done | grep -c '^sensor\.claude_' || true)
ok "package loaded: $n claude sensors"
say ""
say "Claude Fleet is installed and empty: $BASE_URL/claude-fleet/sessions"
say "  login: demo / the password in $SECRETS/ha_password"
say "  next:  CF_DEMO_DIR=$CF_DEMO_DIR $DEMO/scenario.sh"
