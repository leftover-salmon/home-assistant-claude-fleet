# The MQTT protocol

What the hook publishes, for anyone building a view other than the Home Assistant dashboard
(Grafana, a small web app, a terminal display). Home Assistant is one consumer of these
messages; nothing in them requires it.

Every message is **retained**, QoS 1, and published by `hooks/claude-ha-status.sh` on each
computer. A consumer that connects late gets the current state of every session at once.

## Topics

| Topic | Payload | When |
|---|---|---|
| `claude/<machine>/<session_id>/state` | `working`, `needs_input`, `idle` or `ended`, as plain text | every change of state, and at most once a minute during tool calls |
| `claude/<machine>/<session_id>/attrs` | JSON, [below](#session-attributes) | with every state publish, and again when a summary is written |
| `claude/<machine>/rtk/state` | tokens RTK kept out of context today, as a number | at most every 10 minutes, only on computers with RTK |
| `claude/<machine>/rtk/attrs` | JSON, [below](#rtk-attributes) | with the RTK state |
| `homeassistant/sensor/claude_<machine>_<first 8 of session_id>/config` | Home Assistant MQTT discovery | once per session, again when its label changes, and after two hours of silence |
| `homeassistant/sensor/claude_<machine>_rtk/config` | Home Assistant MQTT discovery | with every RTK publish |

`<machine>` is the computer's name (`CLAUDE_HA_MACHINE`, or the hostname) made into a slug:
lower case, every run of characters other than `a-z0-9` replaced by `_`, and leading and
trailing `_` removed. "M4 Mac Mini" becomes `m4_mac_mini`. The readable name is in the
payload as `machine`.

`<session_id>` is Claude Code's session id, a UUID. The `homeassistant/` prefix is
`DISCOVERY_PREFIX` in `ha-status.env`; a consumer other than Home Assistant can ignore those
topics entirely.

To follow everything: subscribe to `claude/#`. Sessions are `claude/+/+/attrs` where the
middle level is not `rtk`.

## Session states

| State | Meaning |
|---|---|
| `working` | Claude is generating or running a tool. |
| `needs_input` | Waiting on the person: a permission prompt, or a question (`AskUserQuestion`, a plan to approve). |
| `idle` | The turn finished; it's the person's move. |
| `ended` | The session exited (`SessionEnd`). A session that was killed never sends this. |

**Stale** is not a state the hook sends; a consumer derives it. A session is stale when its
state is `working` and `last_seen` is older than `stale_after_min` minutes (0 means never
stale). It usually means a stuck tool, or a laptop asleep mid-task.

## Session attributes

The JSON on `…/attrs`. Every field is always present; empty values are `""`, `0`, `[]` or `{}`.

| Field | Type | Meaning |
|---|---|---|
| `claude_session` | `true` | Marks a session payload. |
| `session_id` | string | Claude Code's session id. |
| `label` | string | Display name: `/rename` title, else Claude Code's own title, else `repo · 6-char id`. |
| `machine` | string | The computer's readable name. |
| `repo`, `branch` | string | Where the session is working: the repo of the last file it edited, else where it started. |
| `cwd` | string | Where the session started. |
| `pr_number`, `pr_url`, `pr_title` | string | The open PR for the current branch, via `gh`. Empty on `main` or without `gh`. |
| `prs_today` | array of `"#123"` | PRs this session opened today (local time). |
| `prs_all` | array of `"#123"` | Every PR this session has touched. |
| `last_prompt` | string | The last prompt, first 200 characters, pasted blocks replaced by `[pasted]`. |
| `summary` | string | One line on what the session is doing, written by an LLM. Empty when summaries are off. |
| `last_event` | string | The Claude Code hook event behind this publish: `SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `Notification`, `Stop`, `SessionEnd`. |
| `state_since` | ISO 8601 UTC | When the current state began. |
| `last_seen` | ISO 8601 UTC | When this was published. |
| `day` | `YYYY-MM-DD` | The local date the daily counters below belong to. Compare it with today before using them: a session idle since yesterday still carries yesterday's. |
| `prompts_today` | integer | Prompts sent to this session today. |
| `wait_minutes_today` | integer | Minutes today it spent in `needs_input`. |
| `stale_after_min` | integer | This computer's stale limit, in minutes; 0 = never. |
| `model` | string | Model of the last response, e.g. `claude-opus-5-5`. |
| `cc_version` | string | Claude Code version the session started on. |
| `cli_installed` | string | Claude Code version installed on this computer now, checked at most every 10 minutes. Newer than `cc_version` means the session needs a restart to use it. Empty from hooks older than 0.1.8. |
| `context_tokens` | integer | Context used by the last request: input + cache read + cache write. |
| `context_limit` | integer | Assumed context window: 200000, or 1000000 once the session has been seen above 200k. The transcript does not record the real limit. |
| `tokens_today` | object | Tokens used today, this session and its subagents, [below](#tokens_today). `{}` until the first turn finishes. |
| `compactions_today` | integer | Times the context was compacted today, automatic and manual. |
| `auto_compactions_today` | integer | Of those, how many were automatic, meaning the session hit its limit. |
| `hook_version` | string | The hook's version, e.g. `0.1.7` (the plugin's version; script installs record it at install). Empty from hooks older than 0.1.7. |
| `installed_via` | `plugin` or `script` | How the hook was installed on this computer: the Claude Code plugin, or `install.sh`. |
| `rtk` | `true`, `false` or `null` | RTK's hook is on for this computer / RTK is installed but its hook is off / RTK is not installed. |
| `discovery_topic`, `topic_base` | string | This session's own topics. Useful for a consumer that cleans up: see [Removing a session](#removing-a-session). |

### `tokens_today`

```json
{
  "input": 1406, "output": 61478, "cache_read": 12158661, "cache_write": 227551, "calls": 88,
  "by_model": {
    "opus-5-5":  { "input": 1300, "output": 58000, "cache_read": 11900000, "cache_write": 210000, "calls": 80 },
    "haiku-4-5": { "input": 106,  "output": 3478,  "cache_read": 258661,   "cache_write": 17551,  "calls": 8 }
  }
}
```

Counted from the session's transcript and its subagents' transcripts, each API response once
(the transcript repeats a response for every content block), limited to today in local time.
Model names drop the `claude-` prefix and any date suffix. `cache_read` is usually most of
the total: it is the conversation so far, re-read on every request.

## RTK attributes

The JSON on `claude/<machine>/rtk/attrs`, published only where RTK's ledger exists.

```json
{
  "claude_rtk": true, "machine": "M4 Mac Mini", "rtk_version": "0.39.0", "hook_on": true,
  "day": "2026-09-24", "output_cap_tokens": 7500,
  "today": { "commands": 496, "passthrough": 300, "claimed_saved": 6597636, "saved": 44886, "pct": 13 },
  "week":  { "commands": 20655, "passthrough": 17993, "claimed_saved": 9052280, "saved": 481318, "pct": 14 }
}
```

- `saved`: tokens RTK kept out of what Claude would actually have seen. Both sides of each
  saving are capped at `output_cap_tokens`, the most of one command's output Claude Code
  hands the model (30,000 characters ÷ 4).
- `claimed_saved`: RTK's own figure, measured against the full raw output. Usually far larger,
  and mostly output the model would never have been shown.
- `pct`: `saved` as a share of the capped output of those commands.
- `passthrough`: commands RTK ran unchanged (`rtk proxy`), which save nothing.
- `week` is the last seven days, not the calendar week. `day` works as for sessions: the
  `today` figures are only today's if `day` is today.

## Removing a session

Nothing on the computer ever removes a session: a hook cannot run after its process is gone,
and a session that was killed never sends `ended`. The consumer decides when a session is
finished. Home Assistant's package does it every five minutes:

- `ended` for more than 10 minutes,
- not `working` and silent (`last_seen`) for more than 2 hours,
- anything silent for more than 24 hours,

and removes it by publishing an empty retained payload to `attrs`, `state` and the discovery
topic, which deletes the retained messages. If more than one consumer shares a broker, let one
of them do this.

A session that comes back after a long silence re-announces itself, so removing one that was
only idle does no lasting harm.

## Stability

This is the contract other tools can rely on. Fields may be **added**, so ignore ones you don't
know. A field will not be renamed or change meaning without a note here.
