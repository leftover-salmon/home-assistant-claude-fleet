#!/bin/bash
# Create the two throwaway project repos the test sessions "work in":
#   ./make-repos.sh DIR
# widget-api stays on main; garden-planner is on a feature branch, so both the
# main and the branch cases show up on the dashboard. The identity is anonymous
# on purpose: git would otherwise record the real user and host.
set -euo pipefail
dir="${1:?usage: make-repos.sh DIR}"
g() { git -c user.name=fleet-test -c user.email=fleet-test@example.invalid "$@"; }
for r in widget-api garden-planner; do
  mkdir -p "$dir/$r"
  g -C "$dir/$r" init -q -b main
  g -C "$dir/$r" remote add origin "https://example.invalid/acme/$r.git"
  g -C "$dir/$r" commit -q --allow-empty -m init
done
g -C "$dir/garden-planner" checkout -q -b feature/seed-calendar
