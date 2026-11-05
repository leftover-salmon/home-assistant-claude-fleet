#!/usr/bin/env bash
# The project a session reports: the repository's origin, else its folder, else
# the bare folder; and a worktree removed after its merge keeps the project it
# had (the dashboard's tokens by project read "marcus-1852" for "marcus" on
# 2026-11-05). Run: tests/repo_of_test.sh (needs git). Exits non-zero on a failure.
set -u
REPO=$(cd "$(dirname "$0")/.." && pwd)
S=$(mktemp -d); trap 'rm -rf "$S"' EXIT
sed -n '/^repo_of()/,/^}/p' "$REPO/hooks/claude-ha-status.sh" > "$S/fn.sh"
. "$S/fn.sh"
check() { # label got want
  [ "$2" = "$3" ] && echo "ok   $1" || { echo "FAIL $1: got '$2' want '$3'"; fail=1; }
}
q() { git -C "$1" "${@:2}" >/dev/null 2>&1; }
mkdir -p "$S/main" "$S/plain" "$S/local-only"
q "$S/main" init -b main; q "$S/main" remote add origin git@example.invalid:acme/shop.git
q "$S/main" -c user.email=t@t -c user.name=t commit --allow-empty -m one
q "$S/main" worktree add "$S/shop-42" -b t42
q "$S/local-only" init -b main

check "a repository: its origin's name"                 "$(repo_of "$S/main" "")"       shop
check "a worktree: the origin's name, not the folder's"  "$(repo_of "$S/shop-42" "")"    shop
check "a repository with no origin: its folder's name"  "$(repo_of "$S/local-only" "")" local-only
check "not a repository: the folder's name"             "$(repo_of "$S/plain" "shop")"  plain
q "$S/main" worktree remove "$S/shop-42"
check "a worktree since removed keeps the project it had" "$(repo_of "$S/shop-42" "shop")" shop
check "  ...and with nothing to keep, the folder's name"  "$(repo_of "$S/shop-42" "")"     shop-42

[ "${fail:-0}" = 0 ] && echo "all passed"
exit "${fail:-0}"
