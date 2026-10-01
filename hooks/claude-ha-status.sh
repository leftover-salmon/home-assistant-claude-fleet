#!/usr/bin/env bash
# claude-ha-status.sh — Claude Code hook -> Home Assistant (MQTT discovery)
#
# One HA sensor per Claude Code session. State is one of:
#   working | needs_input | idle | ended
# Attributes: machine, repo, branch, PR, last prompt, PRs touched today,
#             minutes spent waiting on you today, prompts today,
#             stale_after_min (per-machine stale threshold), ...
#
# Wire it to these hook events in ~/.claude/settings.json:
#   SessionStart UserPromptSubmit PreToolUse PostToolUse Notification Stop SessionEnd
#
# Never blocks Claude: reads the hook JSON, forks, exits 0 immediately
# (except SessionEnd, which publishes in the foreground so the exit is recorded).
# Set CLAUDE_HA_SYNC=1 to run in the foreground (debugging).

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

CONF="${CLAUDE_HA_CONF:-$HOME/.claude/ha-status.env}"
# shellcheck disable=SC1090
[ -f "$CONF" ] && . "$CONF"

# ---- installed as a Claude Code plugin ---------------------------------------
# The plugin's install prompt supplies the basics as CLAUDE_PLUGIN_OPTION_*; a
# value set there wins over the env file, which still covers everything else.
# If this computer ALSO has the manual install (install.sh wires the hook into
# settings.json), both would fire on every event and publish twice. The manual
# one is the one someone deliberately put there, so the plugin copy steps aside.
FROM_PLUGIN=""   # which settings the plugin supplied, named in the error log
if [ -n "${CLAUDE_PLUGIN_ROOT:-}" ]; then
  grep -qs 'claude-ha-status' "$HOME/.claude/settings.json" && exit 0
  for o in MQTT_HOST MQTT_PORT MQTT_USER MQTT_PASSWORD MACHINE_NAME STALE_MINUTES SUMMARIES; do
    v="CLAUDE_PLUGIN_OPTION_$o"   # no ${o,,}: macOS ships bash 3.2
    [ -n "${!v:-}" ] && FROM_PLUGIN="$FROM_PLUGIN $(printf '%s' "$o" | tr '[:upper:]' '[:lower:]')"
  done
  [ -n "${CLAUDE_PLUGIN_OPTION_MQTT_HOST:-}" ] && MQTT_HOST="$CLAUDE_PLUGIN_OPTION_MQTT_HOST"
  [ -n "${CLAUDE_PLUGIN_OPTION_MQTT_PORT:-}" ] && MQTT_PORT="$CLAUDE_PLUGIN_OPTION_MQTT_PORT"
  [ -n "${CLAUDE_PLUGIN_OPTION_MQTT_USER:-}" ] && MQTT_USER="$CLAUDE_PLUGIN_OPTION_MQTT_USER"
  [ -n "${CLAUDE_PLUGIN_OPTION_MQTT_PASSWORD:-}" ] && MQTT_PASS="$CLAUDE_PLUGIN_OPTION_MQTT_PASSWORD"
  [ -n "${CLAUDE_PLUGIN_OPTION_MACHINE_NAME:-}" ] && CLAUDE_HA_MACHINE="$CLAUDE_PLUGIN_OPTION_MACHINE_NAME"
  [ -n "${CLAUDE_PLUGIN_OPTION_STALE_MINUTES:-}" ] && CLAUDE_HA_STALE_MINUTES="$CLAUDE_PLUGIN_OPTION_STALE_MINUTES"
  case "${CLAUDE_PLUGIN_OPTION_SUMMARIES:-}" in
    false|0) SUMMARY_ENABLED=0 ;;
    true|1)  SUMMARY_ENABLED=1 ;;
  esac
fi
MQTT_HOST="${MQTT_HOST:-homeassistant.local}"
MQTT_PORT="${MQTT_PORT:-1883}"
MQTT_USER="${MQTT_USER:-}"
MQTT_PASS="${MQTT_PASS:-}"
DISCOVERY_PREFIX="${DISCOVERY_PREFIX:-homeassistant}"
STATE_DIR="${CLAUDE_HA_STATE_DIR:-$HOME/.claude/ha-status}"
TOOL_THROTTLE="${TOOL_THROTTLE:-60}"     # secs between repeat publishes on tool events
PR_RECHECK="${PR_RECHECK:-300}"          # secs between gh PR lookups on the same branch
# minutes a "working" session may stay silent before HA calls it stale; 0 = never
# (use 0 on laptops: a closed lid is not a stuck session)
# Assumed context window, for the percentage on the dashboard. The transcript
# records the model NAME but never its limit, and the same string covers the 200k
# and 1M variants -- so this is an assumption, and the hook corrects it the only
# way it can: a session observed above its assumed limit is demonstrably on the
# larger one, and latches there. Set this if you run long-context sessions and
# want the percentage right from the first turn rather than from the point one
# exceeds 200k.
CONTEXT_LIMIT="${CLAUDE_HA_CONTEXT_LIMIT:-200000}"
case "$CONTEXT_LIMIT" in ''|*[!0-9]*) CONTEXT_LIMIT=200000 ;; esac
STALE_MINUTES="${CLAUDE_HA_STALE_MINUTES:-20}"
case "$STALE_MINUTES" in ''|*[!0-9]*) STALE_MINUTES=20 ;; esac
RTK_THROTTLE="${RTK_THROTTLE:-600}"            # secs between RTK stats publishes per machine
# Claude Code hands the model at most this many characters of a command's output
# (its BASH_MAX_OUTPUT_LENGTH, default 30000). RTK counts a saving against the
# FULL raw output, so a 2MB log it shrank is logged as ~500k tokens saved when
# the model would only ever have been shown ~7.5k of it. The effective figure
# caps both sides at this limit.
BASH_OUTPUT_CAP_CHARS="${BASH_MAX_OUTPUT_LENGTH:-30000}"
case "$BASH_OUTPUT_CAP_CHARS" in ''|*[!0-9]*) BASH_OUTPUT_CAP_CHARS=30000 ;; esac
SUMMARY_THROTTLE="${SUMMARY_THROTTLE:-600}"    # secs between LLM summaries per session
SUMMARY_ENABLED="${SUMMARY_ENABLED:-1}"        # 0 disables the LLM summary entirely
REANNOUNCE_AFTER="${REANNOUNCE_AFTER:-7200}"   # secs of silence after which we re-announce
DISCOVERY_REFRESH="${DISCOVERY_REFRESH:-1800}" # secs between discovery re-sends, however busy

# Recursion guard: the summariser shells out to `claude -p`, whose own hooks fire.
# That nested run inherits this variable and exits here, so it cannot summarise
# itself forever. Removing this makes the hook fork-bomb.
[ -n "${CLAUDE_HA_SUMMARIZING:-}" ] && exit 0

command -v jq >/dev/null 2>&1 || exit 0
command -v mosquitto_pub >/dev/null 2>&1 || exit 0

INPUT="$(cat)"
RUN_START=$(date +%s)   # when this run began; see ended_already

# The password reaches mosquitto_pub through its options file, never as -P. Every
# argument of a running process is readable by anyone on the computer (`ps`), and
# this hook publishes many times a minute, so -P put the password on show all day.
# mosquitto_pub reads $XDG_CONFIG_HOME/mosquitto_pub (every version does; the newer
# -o flag is missing from the 2.0 that Debian and Ubuntu ship), and the rest of a
# line is the value, spaces and all. The directory is private and lives for the one
# call. printf is a builtin, so writing the file starts no process to be seen either.
mqtt_pub() { # mosquitto_pub args..., with -u MQTT_USER and the password added
  if [ -z "$MQTT_USER" ]; then mosquitto_pub "$@"; return; fi
  if [ -z "$MQTT_PASS" ]; then mosquitto_pub -u "$MQTT_USER" "$@"; return; fi
  local d rc
  d=$(umask 077; mktemp -d "${TMPDIR:-/tmp}/claude-fleet.XXXXXX") || return 1
  printf -- '-P %s\n' "$MQTT_PASS" > "$d/mosquitto_pub"
  XDG_CONFIG_HOME="$d" mosquitto_pub -u "$MQTT_USER" "$@"; rc=$?
  rm -rf "$d"
  return $rc
}

pub() { # topic payload
  local args=(-h "$MQTT_HOST" -p "$MQTT_PORT" -r -q 1 -t "$1" -m "$2") err
  local try=1 tries="${PUB_TRIES:-3}" t0
  # Retry only a FAST failure. An mDNS name (homeassistant.local) occasionally
  # misses a lookup and fails at once; a second try usually lands. A SLOW
  # failure is the resolver timing out (~5s), and retrying it only multiplies
  # the wait: unconditional retries took a run against a bad host from ~33s
  # to 99s, all of it holding the session's lock (measured on the Neo, whose
  # Claude session proposed this). A refused login is fast but permanent, so it
  # isn't retried either. SessionEnd's fast path allows one attempt only.
  while :; do
    t0=$SECONDS
    err=$(mqtt_pub "${args[@]}" 2>&1) && return 0
    [ "$try" -ge "$tries" ] && break
    [ $((SECONDS - t0)) -ge 2 ] && break
    case "$err" in *[Nn]ot\ authori[sz]ed*) break ;; esac
    sleep "$([ "$try" -eq 1 ] && echo 0.4 || echo 1)"
    try=$((try + 1))
  done
  # A failed publish used to vanish: the hook runs detached with its output
  # thrown away, and Claude Code saw a hook that exited 0. So it goes in a log,
  # with where it was sent and which settings came from the plugin, never the
  # password. scripts/diagnose.sh prints the end of it.
  local log="${STATE_DIR:-$HOME/.claude/ha-status}/errors.log"
  mkdir -p "$(dirname "$log")"
  printf '%s publish to %s:%s as %s failed after %s attempt(s)%s: %s\n' "$(date '+%F %T')" "$MQTT_HOST" "$MQTT_PORT" \
    "${MQTT_USER:-(no user)}" "$try" "${FROM_PLUGIN:+ (from plugin:$FROM_PLUGIN)}" \
    "$(printf '%s' "$err" | tr '\n' ' ' | cut -c1-200)" >> "$log"
  tail -n 50 "$log" > "$log.tmp" 2>/dev/null && mv "$log.tmp" "$log"
  return 1
}

slug() { tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9]+/_/g; s/^_+|_+$//g'; }

# ---- RTK savings, per machine ----------------------------------------------
# RTK keeps its own ledger (SQLite). Published as one sensor per machine,
# throttled, because it is the same figure for every session on the Mac.
# Two numbers, and the difference matters: "claimed" is RTK's own count against
# the full raw output; "effective" caps both sides at what Claude Code would
# actually have shown the model. Over a week here that was 2.69M vs 0.53M.
# Commands RTK only passed through (rtk proxy) save nothing and are counted apart.
rtk_publish() { # machine mslug now
  command -v sqlite3 >/dev/null 2>&1 || return 0
  local db="${RTK_DB:-}"
  [ -z "$db" ] && for db in "$HOME/Library/Application Support/rtk/history.db" \
                            "${XDG_DATA_HOME:-$HOME/.local/share}/rtk/history.db"; do
    [ -f "$db" ] && break
  done
  [ -f "$db" ] || return 0
  local rf="$STATE_DIR/rtk.json" last
  last=$(jq -r '.last_pub // 0' "$rf" 2>/dev/null); case "$last" in ''|*[!0-9]*) last=0 ;; esac
  [ $(($3 - last)) -lt "$RTK_THROTTLE" ] && return 0
  printf '{"last_pub":%s}\n' "$3" > "$rf"

  local cap=$((BASH_OUTPUT_CAP_CHARS / 4)) q
  q() { # sqlite time expression for the window start
    printf "select count(*), coalesce(sum(case when rtk_cmd like 'rtk proxy%%' then 1 else 0 end),0),
      coalesce(sum(saved_tokens),0),
      coalesce(sum(max(0, min(input_tokens,%d) - min(output_tokens,%d))),0),
      coalesce(sum(min(input_tokens,%d)),0)
      from commands where timestamp >= strftime('%%Y-%%m-%%dT%%H:%%M:%%S', %s);" \
      "$cap" "$cap" "$cap" "$1"
  }
  # A plain open, not read-only: the ledger is in WAL mode, and a read-only
  # connection cannot create the shared-memory file WAL readers need.
  local d w
  d=$(sqlite3 -cmd '.timeout 2000' -separator ' ' "$db" "$(q "'now','localtime','start of day','utc'")" 2>/dev/null)
  w=$(sqlite3 -cmd '.timeout 2000' -separator ' ' "$db" "$(q "'now','-7 days'")" 2>/dev/null)
  [ -z "$d" ] && return 0
  local ver; ver=$(rtk --version 2>/dev/null | awk '{print $2}')
  local attrs
  attrs=$(jq -nc --arg machine "$1" --arg d "$d" --arg w "$w" --arg ver "$ver" \
    --argjson on "$RTK_ON" --argjson cap "$cap" '
    def row: split(" ") | map(tonumber? // 0)
      | {commands: .[0], passthrough: .[1], claimed_saved: .[2], saved: .[3],
         pct: (if .[4] > 0 then (100 * .[3] / .[4] | round) else 0 end)};
    { claude_rtk: true, machine: $machine, rtk_version: $ver, hook_on: $on,
      day: (now | strflocaltime("%Y-%m-%d")),
      output_cap_tokens: $cap, today: ($d | row), week: ($w | row) }')
  local uid="claude_${2}_rtk" base="claude/${2}/rtk"
  local cfg
  cfg=$(jq -nc --arg uid "$uid" --arg base "$base" --arg dev "claude_code_${2}" --arg machine "$1" '{
      name: ("RTK saved today · " + $machine), unique_id: $uid, icon: "mdi:content-cut",
      state_topic: ($base + "/state"), json_attributes_topic: ($base + "/attrs"),
      unit_of_measurement: "tokens", state_class: "total_increasing",
      device: { identifiers: [$dev], name: ("Claude Code · " + $machine),
                manufacturer: "Claude Fleet", model: "Claude Code sessions" } }')
  pub "${DISCOVERY_PREFIX}/sensor/${uid}/config" "$cfg"
  pub "$base/attrs" "$attrs"
  pub "$base/state" "$(jq -r '.today.saved' <<<"$attrs")"
}

# The Claude Code version INSTALLED on this machine, next to the one each
# session started on. Without it, "this session is on an old build" could only
# be spotted once some session had started on the new one: after an overnight
# update, every open session was 2.1.280 against 2.1.283 installed, and none
# was flagged. Cached, refreshed at most every 10 minutes (it takes ~15ms).
cli_installed() { # refresh?
  local f="$STATE_DIR/cli_version" v
  if [ "$1" = 1 ] && { [ ! -s "$f" ] || [ -n "$(find "$f" -mmin +10 2>/dev/null)" ]; }; then
    v=$(claude --version 2>/dev/null | awk '{print $1}')
    case "$v" in [0-9]*.[0-9]*.[0-9]*) printf '%s\n' "$v" > "$f" ;; esac
  fi
  cat "$f" 2>/dev/null
}

# The version lives in ONE place, .claude-plugin/plugin.json. Under the plugin
# it is read from there; install.sh copies it into a file beside the script.
hook_version() {
  if [ -n "${CLAUDE_PLUGIN_ROOT:-}" ]; then
    jq -r '.version // empty' "$CLAUDE_PLUGIN_ROOT/.claude-plugin/plugin.json" 2>/dev/null
  else
    cat "$(dirname "$0")/claude-ha-status.version" 2>/dev/null
  fi
}

# ---- SessionEnd: the fast path ------------------------------------------------
# Claude Code gives a hook at exit very little time, and cancels it when it runs
# over. The full routine (transcript scan, a gh lookup, RTK's ledger) took 1.3s
# on a 26MB transcript here, and a laptop with a PR to look up ran past the
# limit: "hook cancelled", and the session never showed as ended. None of that
# work matters to a session that is over, so this republishes the attributes
# already sent, marked ended: two small publishes, no lock to wait for.
session_end_fast() { # state_file
  local sf="$1" last now_iso base attrs PUB_TRIES=1   # no retries on the way out
  last=$(jq -c '.last_attrs // empty' "$sf" 2>/dev/null)
  [ -z "$last" ] && return 0   # never published; there is nothing to end
  now_iso=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  attrs=$(jq -c --arg t "$now_iso" '.last_event = "SessionEnd" | .state_since = $t | .last_seen = $t' <<<"$last")
  base=$(jq -r '.topic_base // empty' <<<"$attrs")
  [ -z "$base" ] && return 0
  # in parallel: one publish is ~0.07s but now and then ~0.7s (connection
  # setup to the broker), and two in a row could hit it twice
  pub "$base/state" ended & pub "$base/attrs" "$attrs" & wait
  jq -c --argjson a "$attrs" --argjson now "$(date +%s)" \
     '.state = "ended" | .state_since = $now | .last_pub = $now | .last_attrs = $a' \
     "$sf" > "$sf.end.tmp" 2>/dev/null && mv "$sf.end.tmp" "$sf"
}

# A background run started just before the session ended (a Stop fires right
# before SessionEnd) can finish after the fast path. It must not publish "idle"
# over "ended". But only a run that BEGAN before the end: `claude --continue`
# resumes with the same session id and state file, and the first version of
# this guard, which checked "ended" alone, silenced every event of the resumed
# session for good (found on the Neo by its own Claude session). So: suppress
# only when the end was recorded at or after this run started.
ended_already() {
  [ "$ev" = SessionEnd ] && return 1
  local st since
  st=$(jq -r '.state // empty' "$1" 2>/dev/null); since=$(jq -r '.state_since // 0' "$1" 2>/dev/null)
  [ "$st" = ended ] && [ "${since:-0}" -ge "$RUN_START" ] 2>/dev/null
}

main() {
  local j="$INPUT"
  local ev sid cwd transcript tool msg ntype prompt
  ev=$(jq -r '.hook_event_name // empty' <<<"$j")
  sid=$(jq -r '.session_id // empty' <<<"$j")
  cwd=$(jq -r '.cwd // empty' <<<"$j")
  transcript=$(jq -r '.transcript_path // empty' <<<"$j")
  tool=$(jq -r '.tool_name // empty' <<<"$j")
  msg=$(jq -r '.message // empty' <<<"$j")
  ntype=$(jq -r '.notification_type // empty' <<<"$j")
  prompt=$(jq -r '.prompt // empty' <<<"$j")
  [ -z "$sid" ] && return
  [ -z "$cwd" ] && cwd="$PWD"

  # ---- map hook event -> state ------------------------------------------
  local new
  case "$ev" in
    SessionStart)     new=idle ;;
    UserPromptSubmit) new=working ;;
    PreToolUse)
      case "$tool" in AskUserQuestion|ExitPlanMode) new=needs_input ;; *) new=working ;; esac ;;
    PostToolUse)      new=working ;;
    Notification)
      if [ "$ntype" = "idle_prompt" ] || grep -qi 'waiting for your input' <<<"$msg"; then
        new=idle            # "your turn" nudge, not a blocker
      else
        new=needs_input     # permission prompt / question
      fi ;;
    Stop)             new=idle ;;
    SessionEnd)       new=ended ;;
    *)                return ;;
  esac

  mkdir -p "$STATE_DIR"
  local sf="$STATE_DIR/$sid.json" i
  if [ "$ev" = SessionEnd ]; then session_end_fast "$sf"; return; fi
  LOCK="$STATE_DIR/$sid.lock"
  # stale lock (crashed run) older than ~1 min? clear it
  [ -d "$LOCK" ] && [ -n "$(find "$LOCK" -maxdepth 0 -mmin +1 2>/dev/null)" ] && rmdir "$LOCK" 2>/dev/null
  for i in $(seq 1 50); do mkdir "$LOCK" 2>/dev/null && break; sleep 0.1; done
  trap 'rmdir "$LOCK" 2>/dev/null' EXIT

  local prev now now_iso today
  prev=$(cat "$sf" 2>/dev/null); [ -z "$prev" ] && prev='{}'
  now=$(date +%s); now_iso=$(date -u +%Y-%m-%dT%H:%M:%SZ); today=$(date +%F)
  g() { jq -r --arg k "$1" '.[$k] // empty' <<<"$prev"; }

  local p_state p_since p_pub
  p_state=$(g state); p_since=$(g state_since); p_pub=$(g last_pub)
  # throttle chatty tool events when nothing changed
  if [ "$new" = "$p_state" ] && { [ "$ev" = PreToolUse ] || [ "$ev" = PostToolUse ]; } \
     && [ -n "$p_pub" ] && [ $((now - p_pub)) -lt "$TOOL_THROTTLE" ]; then
    return
  fi

  # ---- machine / repo / branch -------------------------------------------
  local machine mslug repo branch
  machine="${CLAUDE_HA_MACHINE:-$(scutil --get LocalHostName 2>/dev/null || hostname -s)}"
  mslug=$(slug <<<"$machine")
  # ---- where the session is actually WORKING -------------------------------
  # The payload's cwd is where the session STARTED, and it never moves. Under a
  # worktree workflow — one checkout per ticket, each on its own branch — cwd is
  # permanently the shared checkout sitting on main, so the branch guard below
  # short-circuits and `gh pr view` never runs once. Every session reported
  # branch=main, pr_checked=0, PRs 0, for as long as this has existed.
  # Track the last file the session actually touched instead, and resolve it to
  # its repo root. A path outside any repo leaves the previous value alone.
  local workdir tpath top
  workdir=$(g workdir)
  # WRITE tools only. Reading is incidental -- a session working in a worktree
  # that reads one file from the shared checkout would otherwise have its workdir
  # yanked back to main, and with it the branch and the PR lookup. Where a session
  # EDITS is what says which branch it is on.
  case "$tool" in
    Edit|Write|MultiEdit|NotebookEdit)
      tpath=$(jq -r '.tool_input.file_path // .tool_input.notebook_path // empty' <<<"$j") ;;
    *) tpath="" ;;
  esac
  if [ -n "$tpath" ]; then
    top=$(git -C "$(dirname "$tpath")" rev-parse --show-toplevel 2>/dev/null)
    [ -n "$top" ] && workdir="$top"
  fi
  [ -z "$workdir" ] && workdir="$cwd"

  branch=$(git -C "$workdir" rev-parse --abbrev-ref HEAD 2>/dev/null)
  repo=$(git -C "$workdir" remote get-url origin 2>/dev/null | sed -E 's#\.git$##; s#.*[/:]##')
  [ -z "$repo" ] && repo=$(basename "$(git -C "$workdir" rev-parse --show-toplevel 2>/dev/null || echo "$workdir")")

  # ---- PR for current branch (cached) --------------------------------------
  local pr_num pr_url pr_title pr_checked
  pr_num=$(g pr_number); pr_url=$(g pr_url); pr_title=$(g pr_title); pr_checked=$(g pr_checked)
  if [ -n "$branch" ] && [ "$branch" != "$(g branch)" ]; then
    pr_num=""; pr_url=""; pr_title=""; pr_checked=0
  fi
  case "$branch" in ""|HEAD|main|master|develop|trunk) pr_num=""; pr_url=""; pr_title="" ;;
    *)
      # A PR is usually opened in the last seconds of a session, so the full
      # recheck interval can expire only after nobody is left to ask. Poll
      # harder while we have nothing, and always on the way out.
      local recheck="$PR_RECHECK" prj
      [ -z "$pr_num" ] && recheck=60
      [ "$ev" = SessionEnd ] && recheck=0
      if command -v gh >/dev/null 2>&1 && [ $((now - ${pr_checked:-0})) -ge "$recheck" ]; then
        prj=$(cd "$workdir" 2>/dev/null && gh pr view --json number,url,title 2>/dev/null)
        if [ -n "$prj" ]; then
          pr_num=$(jq -r .number <<<"$prj"); pr_url=$(jq -r .url <<<"$prj"); pr_title=$(jq -r .title <<<"$prj")
        fi
        pr_checked=$now
      fi ;;
  esac

  # ---- PRs, model and CLI version, straight from the transcript -------------
  # Claude Code writes a `pr-link` record for every PR a session OPENS, and every
  # assistant turn carries the model and the CLI version. This is the session
  # stating what it did, rather than us inferring it from a branch that may since
  # have moved -- and it needs neither `gh` nor a git repo to be right.
  local model ccver tprs="" ctx="" tok="" compact=""
  if [ -f "$transcript" ]; then
    # Model and version are current state, so the tail is enough. Drop its first
    # line: a byte-offset tail lands mid-record nearly every time.
    local tb
    tb=$(tail -c 131072 "$transcript" 2>/dev/null | tail -n +2)
    model=$(printf '%s\n' "$tb" | jq -rR 'fromjson? | .message?.model? // empty' 2>/dev/null | tail -1)
    ccver=$(printf '%s\n' "$tb" | jq -rR 'fromjson? | select(.version) | .version' 2>/dev/null | tail -1)
    # A tail can hold no complete record at all: one tool output over 128KB is
    # the whole tail, and dropping its partial first line leaves nothing. Then
    # look through the whole file. A raw grep is safe here: inside a record's
    # string values quotes are escaped (\"model\"), so an unescaped
    # "model":"claude-..." can only be the transcript's own field, never
    # something a tool printed.
    [ -z "$model" ] && model=$(grep -o '"model":"claude-[^"]*"' "$transcript" 2>/dev/null | tail -1 | sed 's/.*:"//; s/"$//')
    [ -z "$ccver" ] && ccver=$(grep -o '"version":"[0-9][0-9.]*"' "$transcript" 2>/dev/null | tail -1 | sed 's/.*:"//; s/"$//')
    # PRs need the WHOLE file -- one opened early in a long session is nowhere
    # near the tail. grep first and hand jq the handful of lines that match:
    # over a 14MB transcript that is milliseconds against seconds. Only on the
    # infrequent events, because those are the ones that are not per-tool-call.
    case "$ev" in
      Stop|SessionEnd|SessionStart)
        # Each PR is dated by its EARLIEST record, in local time: the record is
        # re-emitted every turn, so only the first one says when it was opened.
        tprs=$(grep -F '"pr-link"' "$transcript" 2>/dev/null \
               | jq -cR 'fromjson? | select(.prNumber and .timestamp)
                         | {n: "#\(.prNumber)", t: .timestamp}' 2>/dev/null \
               | jq -sc 'group_by(.n) | map({n: .[0].n, d: (min_by(.t).t
                         | sub("\\.[0-9]+Z$"; "Z") | fromdateiso8601
                         | strflocaltime("%Y-%m-%d"))})' 2>/dev/null)
        # Context in use = what the last turn actually sent: fresh input plus
        # everything read from or written to cache. Taken from ONE record, never
        # by grepping the three numbers separately -- they would come from
        # different turns and sum to a figure no request ever had.
        ctx=$(grep '"cache_read_input_tokens"' "$transcript" 2>/dev/null | tail -1 \
              | jq -r '.message.usage
                       | ((.input_tokens // 0) + (.cache_read_input_tokens // 0)
                          + (.cache_creation_input_tokens // 0))' 2>/dev/null)
        # Tokens spent TODAY, this session and its subagents. Each API response
        # is written once per content block with the same id and identical
        # usage, so dedupe by id or a turn with thinking + text + a tool call
        # counts three times. Dated in local time, like the PRs.
        tok=$( { grep -h '"usage"' "$transcript" "${transcript%.jsonl}"/subagents/*.jsonl 2>/dev/null; } \
              | jq -cR --arg today "$today" 'fromjson? | select(.message.usage? and .message.id? and .timestamp?)
                  | select((.timestamp | sub("\\.[0-9]+Z$"; "Z") | fromdateiso8601
                            | strflocaltime("%Y-%m-%d")) == $today)
                  | {id: .message.id, m: (.message.model // "unknown"), u: .message.usage}' 2>/dev/null \
              | jq -sc 'def sums: {
                  input: (map(.u.input_tokens // 0) | add // 0),
                  output: (map(.u.output_tokens // 0) | add // 0),
                  cache_read: (map(.u.cache_read_input_tokens // 0) | add // 0),
                  cache_write: (map(.u.cache_creation_input_tokens // 0) | add // 0),
                  calls: length };
                unique_by(.id) | sums + {by_model: (
                  # "<synthetic>" is a message Claude Code wrote itself (an error,
                  # an interruption), never a model call; it carries zero usage.
                  map(select(.m != "<synthetic>")) | group_by(.m)
                  | map({key: (.[0].m | sub("^claude-"; "") | sub("-[0-9]{8}$"; "")), value: sums})
                  | from_entries)}' 2>/dev/null)
        # Compactions today: each one throws away the context the cache was built on.
        # Split by trigger: "manual" is you running /compact, a choice; "auto" is
        # the session hitting its context limit, and more than one in a day is
        # the sign it should have been restarted instead.
        compact=$(grep -F '"compact_boundary"' "$transcript" 2>/dev/null \
                  | jq -cR --arg today "$today" 'fromjson? | select(.subtype? == "compact_boundary" and .timestamp?)
                      | select((.timestamp | sub("\\.[0-9]+Z$"; "Z") | fromdateiso8601
                                | strflocaltime("%Y-%m-%d")) == $today)
                      | (.compactMetadata.trigger? // "auto")' 2>/dev/null \
                  | jq -sc '{auto: map(select(. != "manual")) | length,
                             manual: map(select(. == "manual")) | length}' 2>/dev/null) ;;
    esac
  fi
  case "$tprs" in ''|null) tprs='[]' ;; esac
  case "$tok" in ''|null) tok='null' ;; esac
  case "$compact" in ''|null) compact='null' ;; esac
  case "$ctx" in ''|null) ctx=0 ;; esac
  case "$ctx" in *[!0-9]*) ctx=0 ;; esac

  # ---- label: env override > /rename title > repo·shortid -------------------
  local label="${CLAUDE_SESSION_LABEL:-}"
  if [ -z "$label" ] && [ -f "$transcript" ]; then
    label=$(grep -F '"custom-title"' "$transcript" 2>/dev/null | tail -1 | jq -r '.customTitle // .title // empty' 2>/dev/null)
  fi
  if [ -z "$label" ] && [ -f "$transcript" ]; then
    # Claude Code's own auto-title. Stale (set once, early) but human-readable,
    # which beats "myrepo · a6d4be" when seven sessions are on screen.
    label=$(grep -F '"ai-title"' "$transcript" 2>/dev/null | tail -1 | jq -r '.aiTitle // empty' 2>/dev/null)
  fi
  [ -z "$label" ] && label="$repo · ${sid:0:6}"

  # ---- daily counters -------------------------------------------------------
  local day wait_today prompts_today
  day=$(g day); wait_today=$(g wait_today); prompts_today=$(g prompts_today)
  if [ "$day" != "$today" ]; then
    day=$today; wait_today=0; prompts_today=0
    prev=$(jq '.prs_today = [] | del(.tokens_today) | del(.compactions)' <<<"$prev")
  fi
  wait_today=${wait_today:-0}; prompts_today=${prompts_today:-0}
  local since=${p_since:-$now}
  if [ "$new" != "$p_state" ]; then
    if [ "$p_state" = needs_input ] && [ -n "$p_since" ]; then
      wait_today=$((wait_today + now - p_since))
    fi
    since=$now
  fi
  [ "$ev" = UserPromptSubmit ] && prompts_today=$((prompts_today + 1))

  local last_prompt
  last_prompt=$(g last_prompt)
  if [ -n "$prompt" ]; then
    # Redact pasted blocks rather than publishing them. Whatever gets pasted into a
    # prompt -- an email, an appointment, a log with a key in it -- would otherwise go
    # to MQTT, into HA's recorder database and onto whatever wall display shows the
    # dashboard. The wrapper also rendered literally in the table, which is how this
    # was noticed. Done in jq because it is already a dependency and its regex engine
    # has lazy quantifiers; sed would swallow everything between the FIRST open tag
    # and the LAST close tag when a prompt carries two blocks.
    # NOTE: this only covers the last-prompt line. The LLM summary reads the
    # transcript itself, so SUMMARY_ENABLED=0 is still the switch for that.
    last_prompt=$(jq -rn --arg p "$prompt" '
      $p
      | gsub("(?s)<pasted_content[^>]*>.*?</pasted_content[^>]*>"; "[pasted]")
      | sub("(?s)<pasted_content[^>]*>.*"; "[pasted]")
      | gsub("(?s)</?pasted_content[^>]*>"; "")
      # prompts Claude Code writes itself, which would otherwise show raw:
      # a message from another Claude session, a background task finishing,
      # a slash command
      | sub("(?s)^\\s*<cross-session-message[^>]*from-name=\"(?<n>[^\"]*)\".*"; "[message from \(.n)]")
      | sub("(?s)^\\s*<cross-session-message.*"; "[message from another session]")
      | sub("(?s)^\\s*<task-notification.*"; "[background task finished]")
      | sub("(?s)^\\s*<command-name>(?<c>[^<]*)</command-name>.*"; "\(.c)")
      | gsub("[\n\t]"; " ") | gsub(" +"; " ")
      | sub("^ +"; "") | sub(" +$"; "")
      | .[0:200]')
  fi

  # Is RTK rewriting this machine's commands? RTK is optional: null = not
  # installed (the dashboard then hides everything RTK), false = installed but
  # its Claude Code hook is not in settings.json, true = on. Per machine, so
  # every session on it gets the same answer. Older RTK releases installed the
  # hook as a script, rtk-rewrite.sh, rather than as `rtk hook claude`.
  RTK_ON=null
  if command -v rtk >/dev/null 2>&1; then
    RTK_ON=false
    grep -qsE '"rtk hook|rtk-rewrite' "$HOME/.claude/settings.json" && RTK_ON=true
  fi

  # ---- topics ---------------------------------------------------------------
  local sid8="${sid:0:8}"
  local uid="claude_${mslug}_${sid8}"
  local base="claude/${mslug}/${sid}"
  local disc="${DISCOVERY_PREFIX}/sensor/${uid}/config"

  # ---- new local state ---------------------------------------------------------
  local state
  state=$(jq -c \
    --arg state "$new" --argjson since "$since" --argjson now "$now" \
    --arg branch "$branch" --arg repo "$repo" --arg label "$label" \
    --arg pr_num "$pr_num" --arg pr_url "$pr_url" --arg pr_title "$pr_title" \
    --argjson pr_checked "${pr_checked:-0}" --arg day "$day" --arg workdir "$workdir" \
    --argjson wait "$wait_today" --argjson prompts "$prompts_today" \
    --arg model "${model:-}" --arg ccver "${ccver:-}" --argjson tprs "$tprs" --arg today "$today" \
    --argjson ctx "$ctx" --argjson ctxlimit "$CONTEXT_LIMIT" \
    --argjson tok "$tok" --argjson compact "$compact" \
    --arg last_prompt "$last_prompt" '
    .state=$state | .state_since=$since | .last_pub=$now
    | .branch=$branch | .repo=$repo | .label=$label | .workdir=$workdir
    | .pr_number=$pr_num | .pr_url=$pr_url | .pr_title=$pr_title | .pr_checked=$pr_checked
    | .day=$day | .wait_today=$wait | .prompts_today=$prompts | .last_prompt=$last_prompt
    | .model = $model | .cc_version = $ccver
    | .tokens_today = ($tok // .tokens_today // null)
    | .compactions = ($compact // .compactions // {auto: 0, manual: 0})
    | .context_tokens = (if $ctx > 0 then $ctx else (.context_tokens // 0) end)
    | .context_limit = ((.context_limit // $ctxlimit) as $cur
                        | if (.context_tokens // 0) > $cur then 1000000 else $cur end)
    # A pr-link record is a LATCH: it is re-emitted every turn for as long as the
    # session lives, so "seen in the transcript" cannot mean "opened today". The
    # transcript dates each PR by its first record, and that date alone decides
    # whether it counts today. It must not be "not seen before": the first run
    # over an old session has seen nothing, and credited three weeks of PRs to
    # today. A PR the transcript knows is re-decided on every read, so a wrong
    # entry heals. Only a PR found by gh alone, which carries no date, falls
    # back to "not recorded before".
    # (No apostrophes in here: this comment sits inside a SINGLE-QUOTED jq
    # program, and one would close the shell string and break the whole file.)
    | ($tprs | map(.n)) as $tnames
    | ($tprs | map(select(.d == $today) | .n)) as $tnew
    | ((if $pr_num != "" then ["#"+$pr_num] else [] end) - $tnames) as $ghonly
    | .prs_today = (((.prs_today // []) - $tnames) + $tnew
                    + ($ghonly - (.prs_all // [])) | unique)
    | .prs_all   = ((.prs_all // []) + $tnames + $ghonly | unique)
    ' <<<"$prev")

  # ---- discovery (once per session, when the label changes, or after a long
  # silence) ------------------------------------------------------------------
  # HA's cleanup removes any non-working session silent for a couple of hours,
  # which includes one merely left idle over lunch. Without this, such a session
  # would come back, publish forever and stay invisible: HA dropped its entity
  # and nothing would re-create it.
  if [ -n "$p_pub" ] && [ $((now - p_pub)) -gt "$REANNOUNCE_AFTER" ]; then
    state=$(jq -c 'del(.discovered_label)' <<<"$state")
  fi
  # Silence was the only trigger, so a discovery entry removed any other way (a
  # cleanup racing the session, someone clearing retained topics) stayed gone:
  # the M2's session published state for hours with no sensor in HA to show it.
  # So re-send it every half hour too. It's retained and unchanged, so HA does
  # nothing with it, unless it had been lost.
  local d_at; d_at=$(jq -r '.discovered_at // 0' <<<"$state")
  if [ $((now - ${d_at:-0})) -gt "$DISCOVERY_REFRESH" ] 2>/dev/null; then
    state=$(jq -c 'del(.discovered_label)' <<<"$state")
  fi
  if [ "$(jq -r '.discovered_label // empty' <<<"$state")" != "$label" ]; then
    local cfg
    cfg=$(jq -nc --arg name "$label" --arg uid "$uid" --arg base "$base" \
      --arg dev "claude_code_${mslug}" --arg machine "$machine" '{
        name: $name, unique_id: $uid, icon: "mdi:robot-outline",
        state_topic: ($base + "/state"),
        json_attributes_topic: ($base + "/attrs"),
        device: { identifiers: [$dev], name: ("Claude Code · " + $machine),
                  manufacturer: "Claude Fleet", model: "Claude Code sessions" } }')
    # Only mark discovery done if it LANDED. Recording an unchecked publish
    # makes a transient failure permanent: the session never re-announces and
    # stays invisible in HA while still publishing state.
    if pub "$disc" "$cfg"; then
      state=$(jq -c --arg l "$label" --argjson t "$now" '.discovered_label=$l | .discovered_at=$t' <<<"$state")
    fi
  fi

  # no `case` inside $( ): bash 3.2 reads the pattern's ) as the end of it
  local cli_refresh=0 cli_now
  case "$ev" in Stop|SessionStart) cli_refresh=1 ;; esac
  cli_now=$(cli_installed "$cli_refresh")

  local attrs
  attrs=$(jq -c --arg sid "$sid" --arg machine "$machine" --arg ev "$ev" \
    --arg now_iso "$now_iso" --arg disc "$disc" --arg base "$base" --arg cwd "$cwd" \
    --argjson stale "$STALE_MINUTES" --argjson rtk "$RTK_ON" \
    --arg via "$([ -n "${CLAUDE_PLUGIN_ROOT:-}" ] && echo plugin || echo script)" \
    --arg hv "$(hook_version)" \
    --arg cli "$cli_now" '{
      claude_session: true, session_id: $sid, label, machine: $machine, repo, branch,
      cwd: $cwd, pr_number, pr_url, pr_title, last_prompt, last_event: $ev,
      state_since: (.state_since | todate), last_seen: $now_iso,
      prs_today, prs_all, wait_minutes_today: ((.wait_today / 60) | floor),
      prompts_today, day, stale_after_min: $stale,
      model: (.model // ""), cc_version: (.cc_version // ""),
      context_tokens: (.context_tokens // 0),
      context_limit: (.context_limit // 200000),
      tokens_today: (.tokens_today // {}),
      compactions_today: ((.compactions.auto // 0) + (.compactions.manual // 0)),
      auto_compactions_today: (.compactions.auto // 0),
      rtk: $rtk, installed_via: $via, hook_version: $hv, cli_installed: $cli,
      discovery_topic: $disc, topic_base: $base,
      summary: (.summary // "") }' <<<"$state")

  ended_already "$sf" && return 0
  pub "$base/attrs" "$attrs"
  pub "$base/state" "$new"

  # keep what was published: the SessionEnd fast path republishes it
  state=$(jq -c --argjson a "$attrs" '.last_attrs = $a' <<<"$state")
  printf '%s\n' "$state" > "$sf.tmp" && mv "$sf.tmp" "$sf"

  case "$ev" in Stop|SessionStart|SessionEnd) rtk_publish "$machine" "$mslug" "$now" ;; esac

  # housekeeping
  [ "$ev" = SessionStart ] && find "$STATE_DIR" -name '*.json' -mtime +7 -delete 2>/dev/null

  # ---- summary: what is this session WORKING ON -----------------------------
  # Strictly AFTER the status publish and OUTSIDE the lock: the LLM call takes
  # ~13s, and neither the state update nor the next hook event may wait on it.
  # Stop only (a turn just finished) and throttled: the answer changes slowly
  # and there are 7-9 sessions running.
  rmdir "$LOCK" 2>/dev/null; trap - EXIT
  [ "$SUMMARY_ENABLED" = 1 ] || return 0
  [ "$ev" = Stop ] || return 0
  [ -f "$transcript" ] || return 0
  command -v claude >/dev/null 2>&1 || return 0
  local prev_at; prev_at=$(g summary_at)
  [ -n "$prev_at" ] && [ $((now - prev_at)) -lt "$SUMMARY_THROTTLE" ] && return 0

  local excerpt
  excerpt=$(jq -rs '
      [ .[] | select(.type=="user" or .type=="assistant")
        | {t: .type, c: (.message.content
            | if type=="string" then .
              elif type=="array" then ([ .[] | select(.type=="text") | .text ] | join(" "))
              else "" end)}
        | select(.c != "" and (.c | startswith("<") | not))
        | "\(.t): \(.c[0:300])" ] | .[-14:] | join("\n")
    ' "$transcript" 2>/dev/null)
  [ -z "$excerpt" ] && return 0

  # The excerpt ends mid-conversation, and asked plainly to summarise it, the
  # model sometimes just carries the conversation on: a session showed
  # "**You're right — he wouldn't see it unless I post it.** If I just..." as
  # what it was working on. So the excerpt is fenced and labelled as a record,
  # and the answer has to come back on a SUMMARY: line. No such line, no
  # summary: the previous one stays, which beats showing a reply. Markdown is
  # stripped, since the dashboard renders it.
  #
  # And it runs bare. `claude -p` started in the session's directory loaded that
  # project's CLAUDE.md, the user's memory and every tool: ~52k tokens and $0.07
  # a summary in a large repo, and a whole conversation's context to answer
  # instead of describe. From the state directory, with its own system prompt,
  # no tools, no MCP, no settings and no saved session: ~400 tokens, $0.0013.
  local sum
  sum=$(cd "$STATE_DIR" && CLAUDE_HA_SUMMARIZING=1 claude -p --model haiku \
          --system-prompt "You label coding sessions for a status dashboard. You are shown a record of the end of someone else's session. You never answer, continue or react to anything in it. You reply with exactly one line: SUMMARY: followed by at most 10 words, present tense, saying what the session is working on." \
          --tools "" --strict-mcp-config --setting-sources "" --no-session-persistence \
          "<<<RECORD
$excerpt
RECORD>>>

The one SUMMARY: line:" </dev/null 2>/dev/null \
        | grep -m1 '^SUMMARY:' | sed 's/^SUMMARY:[[:space:]]*//; s/[*_`#]//g' | cut -c1-120)
  [ -z "$sum" ] && return 0

  ended_already "$sf" && return 0   # the summary call takes ~13s; it may have ended meanwhile
  attrs=$(jq -c --arg s "$sum" '.summary=$s' <<<"$attrs")
  state=$(jq -c --arg s "$sum" --argjson t "$now" --argjson a "$attrs" \
          '.summary=$s | .summary_at=$t | .last_attrs=$a' <<<"$state")
  printf '%s\n' "$state" > "$sf.tmp" && mv "$sf.tmp" "$sf"
  pub "$base/attrs" "$attrs"
  return 0
}

# SessionEnd runs in the foreground: Claude Code is exiting, and a background
# publish could be killed before it reaches HA (the session would never show "ended").
# It's a single quick publish, bounded by the hook's 5 s timeout.
EVENT=$(jq -r '.hook_event_name // empty' <<<"$INPUT" 2>/dev/null)
if [ "${CLAUDE_HA_SYNC:-0}" = 1 ]; then
  main
elif [ "$EVENT" = SessionEnd ]; then
  main </dev/null >/dev/null 2>&1
else
  ( main ) </dev/null >/dev/null 2>&1 &
  disown 2>/dev/null
fi
exit 0
