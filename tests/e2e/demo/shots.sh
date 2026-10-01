#!/bin/bash
# Screenshots of the loaded demo, in headless Chrome:
#
#   CF_DEMO_DIR=/some/scratch/dir ./shots.sh
#
# Writes tests/e2e/demo/out/ (ignored by git): the three tabs at 1600px, and the
# whole Sessions tab at phone width (390px, 2x). Run it within 10 minutes of scenario.sh, while the
# ended session is still on the dashboard. Uses its own Chrome profile in
# CF_DEMO_DIR, never yours.
set -euo pipefail
. "$(dirname "$0")/lib.sh"
need_venv
container_running "$C_HA" || die "the demo is not running; run up.sh and scenario.sh first"
# Chrome: $CHROME, else the macOS app, else the usual Linux names
if [ -z "${CHROME:-}" ]; then
  for c in "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
           google-chrome google-chrome-stable chromium chromium-browser; do
    c=$(command -v "$c" 2>/dev/null || true)
    [ -n "$c" ] && [ -x "$c" ] && { CHROME=$c; break; }
  done
fi
[ -n "${CHROME:-}" ] && [ -x "$CHROME" ] || die "no Chrome found; set CHROME to its executable"
# Ubuntu 24.04 (GitHub's ubuntu-latest) restricts the user namespaces Chrome's
# sandbox needs; in CI, where every page is the throwaway demo, run without it.
SANDBOX=()
[ "$(uname -s)" = Linux ] && [ -n "${CI:-}" ] && SANDBOX=(--no-sandbox)
OUTDIR="$DEMO/out"
mkdir -p "$OUTDIR"
export CDP_PORT="${CDP_PORT:-9333}"

# the ended session only stays ten minutes; say so rather than shoot without it
"$PY" "$DEMO/gen.py" check | grep -q " ended " \
  || say "  note  the ended session has already been cleaned up; re-run scenario.sh for a full set"

curl -s "http://127.0.0.1:$CDP_PORT/json/version" >/dev/null 2>&1 \
  && die "something already listens on port $CDP_PORT (another headless Chrome?); set CDP_PORT"
"$CHROME" ${SANDBOX[@]+"${SANDBOX[@]}"} --headless=new --disable-gpu --hide-scrollbars --no-first-run --no-default-browser-check \
  --user-data-dir="$CF_DEMO_DIR/chrome" --remote-debugging-port="$CDP_PORT" about:blank \
  >"$CF_DEMO_DIR/chrome.log" 2>&1 &
CPID=$!
trap 'kill $CPID 2>/dev/null; wait $CPID 2>/dev/null || true' EXIT
# A cold Chrome on a CI runner has taken longer than the 15 s this used to allow,
# and the screenshots then failed with "connection refused" (2026-10-01). Wait a
# minute, and if it never answers, say so with Chrome's own output.
up=
for i in $(seq 1 120); do
  curl -s "http://127.0.0.1:$CDP_PORT/json/version" >/dev/null 2>&1 && { up=1; break; }
  kill -0 $CPID 2>/dev/null || break
  sleep 0.5
done
[ -n "$up" ] || { tail -20 "$CF_DEMO_DIR/chrome.log" >&2; die "Chrome never answered on port $CDP_PORT (its log is above)"; }

"$PY" "$DEMO/shots.py" "$OUTDIR" \
  sessions:sessions:1600 tokens:tokens:1600 setup:setup:1600 sessions-phone:sessions:390:phone
ok "screenshots in $OUTDIR"
