#!/bin/bash
# List the retained claude/# and homeassistant/# topics on a broker:  ./retained.sh ENV OUT
# ENV is an ha-status.env; the password goes via the options file, off the command line.
set -a; . "$1"; set +a
d=$(umask 077; mktemp -d); printf -- '-P %s\n' "$MQTT_PASS" > "$d/mosquitto_sub"
XDG_CONFIG_HOME="$d" mosquitto_sub -h 127.0.0.1 -p "${MQTT_PORT:-1883}" -u "$MQTT_USER" -t 'claude/#' -t 'homeassistant/#' --retained-only -F '%t' -W 3 2>/dev/null | sort -u > "$2"
rm -rf "$d"
