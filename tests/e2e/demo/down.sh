#!/bin/bash
# Remove everything the demo created:
#
#   CF_DEMO_DIR=/some/scratch/dir ./down.sh
#
# The cf-demo-* containers and network, and CF_DEMO_DIR (HA's config, the
# broker's files, the fake homes, the credentials, the Chrome profile). The
# screenshots in tests/e2e/demo/out/ are kept. CF_DEMO_DIR is deleted only if
# up.sh made it, so a wrong setting cannot delete something else.
set -euo pipefail
. "$(dirname "$0")/lib.sh"
need docker

for c in "$C_HA" "$C_MQTT"; do
  if container_exists "$c"; then docker rm -f "$c" >/dev/null; ok "removed container $c"; fi
done
if docker network inspect "$NET" >/dev/null 2>&1; then docker network rm "$NET" >/dev/null; ok "removed network $NET"; fi

if [ -d "$CF_DEMO_DIR" ]; then
  if grep -qsF "# written by tests/e2e/demo/up.sh" "$CONFIG/configuration.yaml" || [ -d "$SECRETS" ] && [ -d "$MOSQ" ]; then
    # HA and the broker write some of these files as other users
    docker run --rm -v "$CF_DEMO_DIR:/d" --entrypoint sh "$MQTT_IMAGE" -c 'rm -rf /d/config /d/mosquitto' 2>/dev/null || true
    rm -rf "$CF_DEMO_DIR"
    ok "removed $CF_DEMO_DIR"
  else
    die "$CF_DEMO_DIR does not look like a demo folder made by up.sh; not deleting it"
  fi
fi
say "Demo removed. Screenshots, if any, are still in $DEMO/out/"
